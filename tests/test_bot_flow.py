import re
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

from batchwatch import bot, db
from tests.fixtures import STOCK, seed_match

APPROVER, VIEWER = 111, 222


def make_context(conn):
    return SimpleNamespace(bot_data={"conn": conn, "approvers": {APPROVER}}, bot=AsyncMock())


def press(user_id, data, text="original message"):
    query = SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=user_id), answer=AsyncMock(), edit_message_text=AsyncMock(),
        message=SimpleNamespace(text_html=text, reply_text=AsyncMock()))
    return SimpleNamespace(callback_query=query, effective_user=query.from_user), query


def command(user_id):
    message = SimpleNamespace(reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_user=SimpleNamespace(id=user_id),
                           effective_chat=SimpleNamespace(id=42)), message


class Parsing(unittest.TestCase):
    def test_approver_ids(self):
        self.assertEqual(bot.parse_approver_ids("111, 222,,x, 333 "), {111, 222, 333})
        self.assertEqual(bot.parse_approver_ids(""), set())
        self.assertEqual(bot.parse_approver_ids(None), set())


class Rendering(unittest.TestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")

    def test_message_has_required_fields_and_escapes_html(self):
        mid = seed_match(self.conn, {**STOCK, "drug_name": "Telmisartan & Amlodipine <b>Tablets</b>"})
        text = bot.format_match(db.get_match(self.conn, mid))
        for needle in ("E7-02", "21", "dlt 260356", "DLT-260356", "Dissolution", "LUCKNOW", "score"):
            self.assertIn(needle, text)
        self.assertIn("&amp;", text)
        self.assertNotIn("<b>Tablets</b>", text)

    def test_buttons_by_status_and_callback_data_shape(self):
        strong = db.get_match(self.conn, seed_match(self.conn))
        labels = [b.text for b in bot.keyboard_for(strong).inline_keyboard[0]]
        self.assertEqual(labels, ["Approve", "Reject"])
        weak = db.get_match(self.conn, seed_match(self.conn, {**STOCK, "drug_name": "Dolo 650", "batch_no": "DLT-260356"}))
        self.assertEqual(weak["status"], "uncertain")
        buttons = bot.keyboard_for(weak).inline_keyboard[0]
        self.assertEqual([b.text for b in buttons], ["Confirm match", "Not a match"])
        for b in buttons:
            self.assertRegex(b.callback_data, bot.CALLBACK_PATTERN)
            self.assertLess(len(b.callback_data.encode()), 64)
        self.assertEqual({b.callback_data.split(":")[1] for b in buttons}, {str(weak["id"])})


class Buttons(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")
        self.mid = seed_match(self.conn)
        self.ctx = make_context(self.conn)

    async def test_viewer_is_refused_and_attempt_is_logged(self):
        update, query = press(VIEWER, f"ok:{self.mid}")
        await bot.on_decision(update, self.ctx)
        text = query.answer.await_args.args[0]
        self.assertIn("view-only", text.lower())
        self.assertTrue(query.answer.await_args.kwargs.get("show_alert"))
        self.assertEqual(db.get_match(self.conn, self.mid)["decision"], "pending")
        query.edit_message_text.assert_not_awaited()
        entry = db.recent_log(self.conn, 1)[0]
        self.assertEqual((entry["actor"], entry["action"]), (f"user:{VIEWER}", "denied"))
        self.assertIn(str(self.mid), entry["detail"])

    async def test_approve_marks_logs_and_sends_return_request(self):
        update, query = press(APPROVER, f"ok:{self.mid}")
        await bot.on_decision(update, self.ctx)
        m = db.get_match(self.conn, self.mid)
        self.assertEqual((m["decision"], m["decided_by"]), ("approved", str(APPROVER)))
        self.assertEqual(db.recent_log(self.conn, 1)[0]["action"], "approved")
        edited = query.edit_message_text.await_args
        self.assertIn("APPROVED", edited.args[0] if edited.args else edited.kwargs["text"])
        sent = query.message.reply_text.await_args
        sent_text = sent.args[0] if sent.args else sent.kwargs["text"]
        self.assertIn("SENT (demo)", sent_text)
        self.assertIn("Return request", sent_text)

    async def test_reject_sends_nothing_to_supplier(self):
        update, query = press(APPROVER, f"no:{self.mid}")
        await bot.on_decision(update, self.ctx)
        self.assertEqual(db.get_match(self.conn, self.mid)["decision"], "rejected")
        query.message.reply_text.assert_not_awaited()
        self.assertEqual(db.recent_log(self.conn, 1)[0]["action"], "rejected")

    async def test_second_press_is_ignored(self):
        first, _ = press(APPROVER, f"ok:{self.mid}")
        await bot.on_decision(first, self.ctx)
        update, query = press(APPROVER, f"no:{self.mid}")
        await bot.on_decision(update, self.ctx)
        self.assertIn("already", query.answer.await_args.args[0].lower())
        query.message.reply_text.assert_not_awaited()
        self.assertEqual(db.get_match(self.conn, self.mid)["decision"], "approved")

    async def test_unknown_match_id(self):
        update, query = press(APPROVER, "ok:999")
        await bot.on_decision(update, self.ctx)
        self.assertIn("unknown", query.answer.await_args.args[0].lower())


class Commands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")
        self.ctx = make_context(self.conn)

    async def test_start_tells_anyone_their_id_and_role(self):
        update, message = command(VIEWER)
        await bot.start(update, self.ctx)
        text = message.reply_text.await_args.args[0]
        self.assertIn(str(VIEWER), text)
        self.assertIn("view-only", text.lower())

    async def test_run_is_refused_for_non_approvers_and_logged(self):
        update, message = command(VIEWER)
        with mock.patch.object(bot.orchestrator, "run_pipeline") as pipeline:
            await bot.run_cmd(update, self.ctx)
        pipeline.assert_not_called()
        self.assertEqual(db.recent_log(self.conn, 1)[0]["action"], "denied")

    async def test_run_posts_summary_then_one_message_per_pending_match(self):
        seed_match(self.conn)
        state = {"new_alert_rows": [1], "alert_rows": [1, 2], "stock_rows": [1],
                 "counts": {"confirmed": 1, "uncertain": 0}, "new_match_ids": [1],
                 "pending_total": 1, "stop_reason": None}
        update, message = command(APPROVER)
        with mock.patch.object(bot.orchestrator, "run_pipeline", return_value=state):
            await bot.run_cmd(update, self.ctx)
        self.assertIn("1 confirmed", message.reply_text.await_args.args[0])
        self.assertEqual(self.ctx.bot.send_message.await_count, 1)
        sent = self.ctx.bot.send_message.await_args.kwargs
        self.assertEqual(sent["chat_id"], 42)
        self.assertEqual(sent["parse_mode"], "HTML")
        self.assertRegex(sent["reply_markup"].inline_keyboard[0][0].callback_data, bot.CALLBACK_PATTERN)

    async def test_run_reports_pipeline_failure_without_crashing(self):
        update, message = command(APPROVER)
        with mock.patch.object(bot.orchestrator, "run_pipeline", side_effect=RuntimeError("fetch failed")):
            await bot.run_cmd(update, self.ctx)
        self.assertIn("failed", message.reply_text.await_args.args[0].lower())
        self.assertEqual(db.recent_log(self.conn, 1)[0]["action"], "pipeline_error")

    async def test_log_shows_last_ten_entries(self):
        for i in range(12):
            db.log_event(self.conn, "t", "step", f"entry{i}")
        update, message = command(APPROVER)
        await bot.log_cmd(update, self.ctx)
        text = message.reply_text.await_args.args[0]
        self.assertEqual(len(re.findall(r"entry\d+", text)), 10)
        self.assertIn("entry11", text)
        self.assertIsNone(re.search(r"entry[01]\b", text), "oldest two entries should be cut")

    async def test_log_is_refused_for_non_approvers(self):
        update, message = command(VIEWER)
        await bot.log_cmd(update, self.ctx)
        self.assertIn("view-only", message.reply_text.await_args.args[0].lower())


if __name__ == "__main__":
    unittest.main()

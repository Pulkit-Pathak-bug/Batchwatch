import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from batchwatch import bot, db
from tests.fixtures import seed_match
from tests.test_bot_flow import command, press

ADMIN, GRANTED, STRANGER = 111, 333, 444


def ctx(conn, *args):
    return SimpleNamespace(bot_data={"conn": conn, "approvers": {ADMIN}}, bot=AsyncMock(), args=list(args))


class GrantStorage(unittest.TestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")

    def test_grant_is_idempotent_and_visible(self):
        self.assertTrue(db.grant_approver(self.conn, GRANTED, ADMIN))
        self.assertFalse(db.grant_approver(self.conn, GRANTED, ADMIN))
        self.assertTrue(db.is_granted(self.conn, GRANTED))
        self.assertFalse(db.is_granted(self.conn, STRANGER))

    def test_revoke_then_regrant(self):
        db.grant_approver(self.conn, GRANTED, ADMIN)
        self.assertTrue(db.revoke_approver(self.conn, GRANTED))
        self.assertFalse(db.revoke_approver(self.conn, GRANTED))
        self.assertFalse(db.is_granted(self.conn, GRANTED))
        self.assertTrue(db.grant_approver(self.conn, GRANTED, ADMIN))
        self.assertTrue(db.is_granted(self.conn, GRANTED))

    def test_list_shows_only_active_with_granter(self):
        db.grant_approver(self.conn, GRANTED, ADMIN)
        db.grant_approver(self.conn, STRANGER, ADMIN)
        db.revoke_approver(self.conn, STRANGER)
        rows = db.list_approvers(self.conn)
        self.assertEqual([(r["user_id"], r["granted_by"]) for r in rows], [(GRANTED, ADMIN)])


class RoleChecks(unittest.TestCase):
    def test_parse_user_id(self):
        self.assertEqual(bot.parse_user_id(["123456789"]), 123456789)
        for bad in ([], ["abc"], ["-5"], ["0"], ["1", "2"], ["12.5"], [""]):
            self.assertIsNone(bot.parse_user_id(bad), bad)


class GrantCommands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = db.init_db(":memory:")

    def last_log(self):
        return db.recent_log(self.conn, 1)[0]

    async def say(self, user_id, handler, *args):
        update, message = command(user_id)
        await handler(update, ctx(self.conn, *args))
        return message.reply_text.await_args.args[0]

    async def test_admin_grants_and_logs_who_granted_whom(self):
        text = await self.say(ADMIN, bot.grant_cmd, str(GRANTED))
        self.assertIn("Granted", text)
        self.assertTrue(db.is_granted(self.conn, GRANTED))
        entry = self.last_log()
        self.assertEqual((entry["actor"], entry["action"]), (f"user:{ADMIN}", "granted"))
        self.assertIn(str(GRANTED), entry["detail"])

    async def test_granting_twice_does_not_log_twice(self):
        await self.say(ADMIN, bot.grant_cmd, str(GRANTED))
        text = await self.say(ADMIN, bot.grant_cmd, str(GRANTED))
        self.assertIn("already", text.lower())
        self.assertEqual(sum(r["action"] == "granted" for r in db.recent_log(self.conn, 50)), 1)

    async def test_stranger_cannot_grant_and_attempt_is_logged(self):
        text = await self.say(STRANGER, bot.grant_cmd, str(STRANGER))
        self.assertIn("admin", text.lower())
        self.assertFalse(db.is_granted(self.conn, STRANGER))
        self.assertEqual((self.last_log()["actor"], self.last_log()["action"]), (f"user:{STRANGER}", "denied"))

    async def test_granted_approver_cannot_grant_others(self):
        db.grant_approver(self.conn, GRANTED, ADMIN)
        text = await self.say(GRANTED, bot.grant_cmd, str(STRANGER))
        self.assertIn("admin", text.lower())
        self.assertFalse(db.is_granted(self.conn, STRANGER))

    async def test_bad_arguments_show_usage_and_change_nothing(self):
        for args in ([], ["abc"], ["-5"], ["1", "2"]):
            text = await self.say(ADMIN, bot.grant_cmd, *args)
            self.assertIn("Usage", text, args)
        self.assertEqual(db.list_approvers(self.conn), [])

    async def test_granting_an_env_admin_is_a_noop(self):
        text = await self.say(ADMIN, bot.grant_cmd, str(ADMIN))
        self.assertIn("admin", text.lower())
        self.assertEqual(db.list_approvers(self.conn), [])

    async def test_grant_takes_effect_on_the_next_button_press_without_restart(self):
        mid = seed_match(self.conn)
        update, query = press(GRANTED, f"ok:{mid}")
        await bot.on_decision(update, ctx(self.conn))
        self.assertEqual(db.get_match(self.conn, mid)["decision"], "pending")
        await self.say(ADMIN, bot.grant_cmd, str(GRANTED))
        update, query = press(GRANTED, f"ok:{mid}")
        await bot.on_decision(update, ctx(self.conn))
        self.assertEqual(db.get_match(self.conn, mid)["decision"], "approved")

    async def test_revoke_removes_access_and_logs(self):
        mid = seed_match(self.conn)
        await self.say(ADMIN, bot.grant_cmd, str(GRANTED))
        text = await self.say(ADMIN, bot.revoke_cmd, str(GRANTED))
        self.assertIn("Revoked", text)
        self.assertEqual(self.last_log()["action"], "revoked")
        update, query = press(GRANTED, f"ok:{mid}")
        await bot.on_decision(update, ctx(self.conn))
        self.assertEqual(db.get_match(self.conn, mid)["decision"], "pending")

    async def test_revoke_guards(self):
        self.assertIn("not an approver", (await self.say(ADMIN, bot.revoke_cmd, str(GRANTED))).lower())
        self.assertIn(".env", await self.say(ADMIN, bot.revoke_cmd, str(ADMIN)))
        self.assertIn("admin", (await self.say(STRANGER, bot.revoke_cmd, str(GRANTED))).lower())
        self.assertIn("Usage", await self.say(ADMIN, bot.revoke_cmd))

    async def test_granted_approver_can_use_run_and_log(self):
        await self.say(ADMIN, bot.grant_cmd, str(GRANTED))
        text = await self.say(GRANTED, bot.log_cmd)
        self.assertIn("audit", text.lower())

    async def test_approvers_lists_both_groups_for_admins_only(self):
        await self.say(ADMIN, bot.grant_cmd, str(GRANTED))
        text = await self.say(ADMIN, bot.approvers_cmd)
        self.assertIn(str(ADMIN), text)
        self.assertIn(str(GRANTED), text)
        self.assertIn("admin", (await self.say(GRANTED, bot.approvers_cmd)).lower())
        self.assertNotIn(str(ADMIN), await self.say(GRANTED, bot.approvers_cmd))

    async def test_start_reports_the_three_roles(self):
        db.grant_approver(self.conn, GRANTED, ADMIN)
        self.assertIn("admin", await self.say(ADMIN, bot.start))
        self.assertIn("Role: approver", await self.say(GRANTED, bot.start))
        self.assertIn("view-only", await self.say(STRANGER, bot.start))


if __name__ == "__main__":
    unittest.main()

"""Telegram bot (python-telegram-bot v22, async): the human approval step."""
import asyncio
import html
import logging
import os

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import InvalidToken
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from batchwatch import db, orchestrator

CALLBACK_PATTERN = r"^(ok|no):\d+$"
DENIED_TEXT = "View-only: you are not an approver."


def parse_approver_ids(raw: str | None) -> set[int]:
    """'111, 222' -> {111, 222}; blanks and junk are ignored."""
    return {int(p) for p in (raw or "").split(",") if p.strip().isdigit()}


def _e(value: object) -> str:
    """HTML-escape for Telegram's HTML parse mode."""
    return html.escape(str(value or "").strip())


def format_match(m: dict) -> str:
    """Message body for one match: what we hold, why it matched, and the alert as evidence."""
    a = m["alert"]
    icon, title = ("\U0001F534", "CONFIRMED MATCH") if m["status"] == "confirmed" else ("\U0001F7E1", "NEEDS HUMAN CHECK")
    brand = f"\n• Brand: {_e(a.get('brand_name'))}" if a.get("brand_name") else ""
    maker = _e(a.get("manufacturer"))[:120]
    return (f"{icon} <b>{title} #{m['id']}</b>\n\n"
            f"<b>Drug:</b> {_e(m['stock_drug'])}\n"
            f"<b>Batch:</b> {_e(m['stock_batch'])}\n"
            f"<b>Shelf:</b> {_e(m['shelf_location'])}    <b>Qty:</b> {_e(m['quantity'])}\n\n"
            f"<b>Why it matched</b> (score {m['score']:.0f})\n{_e(m['reason'])}\n\n"
            f"<b>Alert evidence (CDSCO {_e(a.get('report_month'))})</b>\n"
            f"• Product: {_e(a['drug_name'])}{brand}\n"
            f"• Batch: {_e(a['batch_no'])}\n"
            f"• Maker: {maker}\n"
            f"• NSQ result: {_e(a.get('reason'))}\n"
            f"• Reported by: {_e(a.get('source'))}, {_e(a.get('lab_state'))}")


def keyboard_for(m: dict) -> InlineKeyboardMarkup:
    """Two buttons; callback_data is 'ok:<match id>' or 'no:<match id>'."""
    yes, no = ("Approve", "Reject") if m["status"] == "confirmed" else ("Confirm match", "Not a match")
    return InlineKeyboardMarkup([[InlineKeyboardButton(yes, callback_data=f"ok:{m['id']}"),
                                  InlineKeyboardButton(no, callback_data=f"no:{m['id']}")]])


async def post_match(bot, chat_id: int, m: dict) -> None:
    """Send one match with its buttons."""
    await bot.send_message(chat_id=chat_id, text=format_match(m), parse_mode="HTML",
                           reply_markup=keyboard_for(m))


async def _require_approver(update: Update, context: ContextTypes.DEFAULT_TYPE, what: str) -> bool:
    """True for approvers; otherwise log the attempt, tell the user, and return False."""
    user = update.effective_user
    if user.id in context.bot_data["approvers"]:
        return True
    db.log_event(context.bot_data["conn"], f"user:{user.id}", "denied", what)
    await update.message.reply_text(DENIED_TEXT)
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tell anyone their Telegram ID and role (the ID is what goes into APPROVER_IDS)."""
    uid = update.effective_user.id
    role = "approver" if uid in context.bot_data["approvers"] else "view-only"
    await update.message.reply_text(f"BatchWatch bot\nYour Telegram ID: {uid}\nRole: {role}\n\n"
                                    f"/run - check stock against the latest NSQ alerts\n"
                                    f"/log - last 10 audit entries")


def _summary(state: dict, pending: int) -> str:
    c = state["counts"]
    lines = [f"Run complete.",
             f"New alert rows: {len(state['new_alert_rows'])} (stored: {len(state['alert_rows'] or [])})",
             f"Stock rows checked: {len(state['stock_rows'] or [])}",
             f"Matches: {c['confirmed']} confirmed, {c['uncertain']} need a human check",
             f"Newly queued: {len(state['new_match_ids'])}, waiting for a decision: {pending}"]
    if state["stop_reason"]:
        lines.append(f"Stopped early: {state['stop_reason']}")
    return "\n".join(lines)


async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/run: execute the pipeline off the event loop, post a summary, then every pending match."""
    if not await _require_approver(update, context, "/run"):
        return
    conn = context.bot_data["conn"]
    db.log_event(conn, f"user:{update.effective_user.id}", "run_requested", "/run")
    await update.message.reply_text("Running the pipeline...")
    try:
        state = await asyncio.to_thread(orchestrator.run_pipeline)
    except Exception as exc:
        db.log_event(conn, "bot", "pipeline_error", repr(exc))
        await update.message.reply_text(f"Pipeline failed: {exc}")
        return
    pending = db.pending_matches(conn)
    await update.message.reply_text(_summary(state, len(pending)))
    for m in pending:
        await post_match(context.bot, update.effective_chat.id, m)


async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log: the last 10 audit_log entries, newest first."""
    if not await _require_approver(update, context, "/log"):
        return
    rows = db.recent_log(context.bot_data["conn"], 10)
    lines = [f"{r['ts']}  {r['actor']}  {r['action']}: {str(r['detail'])[:100]}" for r in rows]
    await update.message.reply_text(("Last audit entries (newest first):\n" + "\n".join(lines))[:4000])


async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Button press. callback_data 'ok:17' / 'no:17' names the match row, so no state lives in memory."""
    query, user, conn = update.callback_query, update.effective_user, context.bot_data["conn"]
    verb, match_id = query.data.split(":")
    match_id = int(match_id)

    if user.id not in context.bot_data["approvers"]:
        db.log_event(conn, f"user:{user.id}", "denied", f"{verb} on match {match_id}")
        await query.answer(DENIED_TEXT, show_alert=True)
        return
    match = db.get_match(conn, match_id)
    if match is None:
        await query.answer("Unknown match.", show_alert=True)
        return
    decision = "approved" if verb == "ok" else "rejected"
    if not db.decide_match(conn, match_id, decision, user.id):
        await query.answer("Already decided.", show_alert=True)
        return

    db.log_event(conn, f"user:{user.id}", decision, f"match {match_id} batch {match['stock_batch']}")
    await query.answer(decision.capitalize())
    mark = "✅ APPROVED" if decision == "approved" else "⛔ REJECTED"
    await query.edit_message_text(f"{query.message.text_html}\n\n<b>{mark}</b> by user {user.id}", parse_mode="HTML")
    if decision == "approved":
        await query.message.reply_text("SENT (demo) - nothing was actually emailed.\n\n" + match["return_text"])


async def _post_init(app: Application) -> None:
    """Register the command menu and log which bot is live."""
    await app.bot.set_my_commands([("run", "Check stock against NSQ alerts"), ("log", "Last 10 audit entries"),
                                   ("start", "Show your Telegram ID and role")])
    me = await app.bot.get_me()
    db.log_event(app.bot_data["conn"], "bot", "started", f"@{me.username}")


def main() -> None:
    """Load config from .env, register handlers, start long polling."""
    load_dotenv()
    logging.basicConfig(format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # INFO would print the token inside request URLs
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing - add it to .env (see .env.example)")
    approvers = parse_approver_ids(os.getenv("APPROVER_IDS"))
    if not approvers:
        print("WARNING: APPROVER_IDS is empty, so every button press will be refused as view-only.")

    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data.update(conn=db.init_db(), approvers=approvers)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CallbackQueryHandler(on_decision, pattern=CALLBACK_PATTERN))
    try:
        app.run_polling()
    except InvalidToken:
        raise SystemExit("Telegram rejected TELEGRAM_BOT_TOKEN - check .env") from None


if __name__ == "__main__":
    main()

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


def parse_user_id(args: list[str]) -> int | None:
    """Exactly one positive whole number (a Telegram user ID), else None."""
    if len(args) == 1 and args[0].isascii() and args[0].isdigit() and int(args[0]) > 0:
        return int(args[0])
    return None


def is_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Admins are the IDs in .env; only they can manage approvers."""
    return user_id in context.bot_data["approvers"]


def is_approver(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Admins plus anyone granted via /grant; checked on every press so grants apply instantly."""
    return is_admin(context, user_id) or db.is_granted(context.bot_data["conn"], user_id)


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
    if is_approver(context, user.id):
        return True
    db.log_event(context.bot_data["conn"], f"user:{user.id}", "denied", what)
    await update.message.reply_text(DENIED_TEXT)
    return False


async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, what: str) -> bool:
    """True for admins; otherwise log the attempt, tell the user, and return False."""
    user = update.effective_user
    if is_admin(context, user.id):
        return True
    db.log_event(context.bot_data["conn"], f"user:{user.id}", "denied", what)
    await update.message.reply_text("Only admins can manage approvers.")
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tell anyone their Telegram ID and role (an admin passes that ID to /grant)."""
    uid = update.effective_user.id
    role = "admin" if is_admin(context, uid) else "approver" if is_approver(context, uid) else "view-only"
    await update.message.reply_text(f"BatchWatch bot\nYour Telegram ID: {uid}\nRole: {role}\n\n"
                                    f"/run - check stock against the latest NSQ alerts\n"
                                    f"/log - last 10 audit entries")


def _summary(state: dict, pending: int) -> str:
    c = state["counts"]
    source = {"live": "live CDSCO feed", "cache": "cached copy (CDSCO unreachable or offline mode)"}
    lines = [f"Run complete.",
             f"Data source: {source.get(state.get('data_source'), 'unknown')}",
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

    if not is_approver(context, user.id):
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


async def grant_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/grant <telegram_id>: admins make someone an approver. Takes effect immediately."""
    if not await _require_admin(update, context, "/grant"):
        return
    target, admin_id, conn = parse_user_id(context.args), update.effective_user.id, context.bot_data["conn"]
    if target is None:
        await update.message.reply_text("Usage: /grant <telegram_id>  (digits only; they get theirs from /start)")
    elif is_admin(context, target):
        await update.message.reply_text(f"{target} is already an admin (set in .env).")
    elif not db.grant_approver(conn, target, admin_id):
        await update.message.reply_text(f"{target} is already an approver.")
    else:
        db.log_event(conn, f"user:{admin_id}", "granted", f"approver user:{target}")
        await update.message.reply_text(f"Granted: {target} can now press buttons and use /run and /log. "
                                        f"Undo with /revoke {target}.")


async def revoke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/revoke <telegram_id>: admins remove a granted approver. Admins from .env cannot be revoked here."""
    if not await _require_admin(update, context, "/revoke"):
        return
    target, admin_id, conn = parse_user_id(context.args), update.effective_user.id, context.bot_data["conn"]
    if target is None:
        await update.message.reply_text("Usage: /revoke <telegram_id>")
    elif is_admin(context, target):
        await update.message.reply_text(f"{target} is an admin set in .env; remove them from .env instead.")
    elif not db.revoke_approver(conn, target):
        await update.message.reply_text(f"{target} is not an approver.")
    else:
        db.log_event(conn, f"user:{admin_id}", "revoked", f"approver user:{target}")
        await update.message.reply_text(f"Revoked: {target} can no longer approve.")


async def approvers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approvers: who can approve (admins only)."""
    if not await _require_admin(update, context, "/approvers"):
        return
    admins = ", ".join(str(i) for i in sorted(context.bot_data["approvers"])) or "none"
    granted = [f"{r['user_id']} (granted by {r['granted_by']})" for r in db.list_approvers(context.bot_data["conn"])]
    await update.message.reply_text(f"Admins (.env): {admins}\nGranted approvers: {', '.join(granted) or 'none'}")


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
    app.add_handler(CommandHandler("grant", grant_cmd))
    app.add_handler(CommandHandler("revoke", revoke_cmd))
    app.add_handler(CommandHandler("approvers", approvers_cmd))
    app.add_handler(CallbackQueryHandler(on_decision, pattern=CALLBACK_PATTERN))
    try:
        app.run_polling()
    except InvalidToken:
        raise SystemExit("Telegram rejected TELEGRAM_BOT_TOKEN - check .env") from None


if __name__ == "__main__":
    main()

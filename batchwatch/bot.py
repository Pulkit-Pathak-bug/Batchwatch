"""Telegram bot (python-telegram-bot v22, async): human approval step."""
from telegram import Update
from telegram.ext import Application, ContextTypes


async def send_for_approval(app: Application, chat_id: int, match: dict, draft: str) -> None:
    """Send a draft with Approve / Reject buttons."""
    raise NotImplementedError


async def on_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a button press and write the decision to audit_log."""
    raise NotImplementedError


def main() -> None:
    """Load the token from .env, register handlers, start polling."""
    raise NotImplementedError


if __name__ == "__main__":
    main()

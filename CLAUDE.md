# BatchWatch

Agentic system that checks a pharmacy's stock list against India's CDSCO
"Not of Standard Quality" (NSQ) drug alert PDFs and acts on matches.
A human approves every action. Built for a hackathon.

## Pipeline
watch -> parse -> match -> act, run by `orchestrator.py` with one `state` dict.
Stop rules live in the orchestrator only.

## Layout
```
batchwatch/
  agents/watcher.py   fetches the CDSCO NSQ JSON feed for a month, returns rows not yet in DB;
                      falls back to the cached response in data/alerts/nsq_YYYY-MM.json
  agents/parser.py    feed JSON (or old PDF, pdfplumber then Gemini) -> AlertRow dicts:
                      {drug_name, brand_name, batch_no, manufacturer, mfg_date, exp_date,
                       reason, source, lab_state, report_month}
  agents/matcher.py   stock rows vs alert rows -> confirmed / uncertain / none
  agents/actor.py     drafts alert + return-request text per match
  orchestrator.py     plain Python pipeline
  db.py               SQLite: processed_alerts, alert_rows, matches, audit_log (append-only)
  bot.py              Telegram bot (python-telegram-bot v22, async): /start /run /log + Approve/Reject buttons
                      (callback_data 'ok:<match id>' / 'no:<match id>'); only APPROVER_IDS may press
  dashboard.py        Streamlit read-only view (later)
  data/alerts/, data/stock.csv
requirements.txt, .env (never read or print)
```

## Config (.env, never committed)
`TELEGRAM_BOT_TOKEN` (from @BotFather) and `APPROVER_IDS` (comma-separated Telegram user IDs).
Run the dashboard with `streamlit run batchwatch/dashboard.py --server.address localhost` (no login, so keep it local).
Run the bot with `python -m batchwatch.bot`; run tests with `python -m unittest discover -s tests -t .`

## Rules
- Plain Python. No agent frameworks.
- LLM calls use `google-genai` (`from google import genai`), never `google-generativeai`.
- Load secrets with `python-dotenv`. Never read, print or commit `.env`.
- Human approval required before any outbound action.
- `audit_log` is append-only: insert functions only, no update or delete.
- Keep files short. Docstrings on every function, minimal comments.
- PEP 8, type hints, f-strings. Ask before adding dependencies.

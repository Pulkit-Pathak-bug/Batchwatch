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
                      (callback_data 'ok:<match id>' / 'no:<match id>'); roles: admins = APPROVER_IDS in
                      .env (can /grant /revoke /approvers), approvers = granted in the approvers table
  dashboard.py        Streamlit read-only view (later)
  data/alerts/, data/stock.csv
requirements.txt, .env (never read or print)
```

## Config (.env, never committed)
`TELEGRAM_BOT_TOKEN` (from @BotFather), `APPROVER_IDS` (comma-separated Telegram user IDs) and `GEMINI_API_KEY`.
Optional: `GEMINI_MODEL` (default gemini-3.5-flash-lite), `BATCHWATCH_AI=off` (no AI calls), `BATCHWATCH_OFFLINE=1` (also no AI).
AI (google-genai): agents/reviewer.py = advisory second opinion on uncertain matches; agents/assistant.py = /ask with 4
read-only tools. The AI must never change a flag, approve, or write. Tests set BATCHWATCH_AI=off and use fake clients.
VS Code: Terminal > Run Task (.vscode/tasks.json) wraps all of the commands below; `scripts/stop_demo.ps1` stops the bot + dashboard.
Run the dashboard with `streamlit run batchwatch/dashboard.py --server.address localhost` (no login, so keep it local).
Demo reset: `python -m scripts.reset_demo` (dry run) / `--yes` (backs up, then clears). `BATCHWATCH_OFFLINE=1` forces the cached feed.
Run the bot with `python -m batchwatch.bot`; run tests with `python -m unittest discover -s tests -t .`

## Rules
- Plain Python. No agent frameworks.
- LLM calls use `google-genai` (`from google import genai`), never `google-generativeai`.
- Load secrets with `python-dotenv`. Never read, print or commit `.env`.
- Human approval required before any outbound action.
- `audit_log` is append-only: insert functions only, no update or delete.
- Keep files short. Docstrings on every function, minimal comments.
- PEP 8, type hints, f-strings. Ask before adding dependencies.

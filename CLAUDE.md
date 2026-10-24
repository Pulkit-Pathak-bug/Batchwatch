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
  agents/watcher.py   new alert PDFs in data/alerts/ not yet in DB
  agents/parser.py    PDF -> list of {drug_name, batch_no, manufacturer, mfg_date, exp_date, reason}
  agents/matcher.py   stock rows vs alert rows -> confirmed / uncertain / none
  agents/actor.py     drafts alert + return-request text per match
  orchestrator.py     plain Python pipeline
  db.py               SQLite: processed_alerts, alert_rows, matches, audit_log (append-only)
  bot.py              Telegram bot (python-telegram-bot v22, async)
  dashboard.py        Streamlit read-only view (later)
  data/alerts/, data/stock.csv
requirements.txt, .env (never read or print)
```

## Rules
- Plain Python. No agent frameworks.
- LLM calls use `google-genai` (`from google import genai`), never `google-generativeai`.
- Load secrets with `python-dotenv`. Never read, print or commit `.env`.
- Human approval required before any outbound action.
- `audit_log` is append-only: insert functions only, no update or delete.
- Keep files short. Docstrings on every function, minimal comments.
- PEP 8, type hints, f-strings. Ask before adding dependencies.

# ProfitPilot AI layer (Gemini)

The engine decides every number; Gemini explains, reads text and photos, and picks which engine tool to call.
Without a key, every feature falls back to the rule-based engine / keyword baseline (the app keeps working).

## Turn it on
1. Get a key: https://aistudio.google.com/apikey
2. `cp .env.example .env` and set `GEMINI_API_KEY=...` (optional: `PP_GEMINI_MODEL=gemini-2.5-flash`)
3. `pip install -r requirements.txt && python run.py` → open http://localhost:8000
4. Check: `GET /api/v1/ai/status` shows `"provider": "gemini"`.

## What uses Gemini
| Feature | Endpoint | Backup when no key / error |
|---|---|---|
| Coach (any question, Hindi / Hinglish / English) | `POST /api/v1/coach/chat` | rule-based Coach, now product-aware |
| Listing check (title, description, photo) | `POST /api/v1/ai/listing/analyse` | keyword baseline |
| Return reasons → groups + fix | `POST /api/v1/ai/returns/classify` | keyword baseline |
| Review insights + early alert | `POST /api/v1/ai/reviews/insights` | keyword baseline |
| Plain-language Why | `GET /api/v1/products/{id}/explain` | engine templates |

Safety: the Coach calls 18 read-only engine tools (function calling). A grounding guard rejects any ₹ / % / count not produced by a tool
(one repair try, then rules). It can only *propose* a Yes / No card; the price changes only through `POST /decisions`, which re-runs
the guardrails. Buyer text is passed as data (prompt-injection safe); counts are computed in Python.

## Host it (so the QR opens the live app)
- Render: push this repo to GitHub → New → Blueprint (uses `render.yaml` + Dockerfile) → set `GEMINI_API_KEY`.
- Or Docker anywhere: `docker build -t profitpilot . && docker run -p 8000:8000 -e GEMINI_API_KEY=... profitpilot`
- Static copy (CodeSandbox): set `const PP_SERVER='https://your-backend'` in `frontend/index.html`, or open the page with `?api=https://your-backend`.
- Admin / tech screen (time travel, audit log) is hidden from sellers: open with `?admin=1` or `#server`.

# Vera Signal Engine

Vera Signal Engine is a FastAPI bot for the magicpin AI Challenge. It is built to decide when to message, extract grounded evidence from pushed context, compose a short WhatsApp-style response with Gemini via LangChain when available, and fall back to deterministic templates when the LLM is unavailable.

## Architecture

- **Signal Selector** scores active triggers for urgency, merchant/customer fit, evidence quality, active offers, peer benchmarks, duplicate suppression, and recent conversation risk.
- **Evidence Extractor** creates a compact evidence pack from category, merchant, trigger, and customer context. Generated messages use only this pack.
- **Gemini Composer** uses `langchain-google-genai` and `ChatGoogleGenerativeAI` with low temperature and JSON-only prompting.
- **Rule Fallback + Validator** keeps the bot useful without an API key and repairs unsafe, repeated, taboo, URL, fake-claim, or ungrounded-price messages.
- **Reply Handler** detects auto-replies, hard no, positive intent, price/time/info questions, off-topic requests, and hostile replies.

## Why It Scores Well

- **Decision quality:** sends only top-ranked non-duplicate triggers and allows empty ticks.
- **Specificity:** anchors on metrics, digest sources, offers, slots, customer state, and trigger payload facts.
- **Category fit:** uses category voice rules and taboos for dentists, salons, restaurants, gyms, and pharmacies.
- **Merchant fit:** uses owner names, locality/city, active offers, performance, peer stats, and aggregate signals.
- **Engagement compulsion:** uses one low-friction CTA and offers to prepare the next artifact for the merchant.

## Endpoints

- `GET /v1/healthz`
- `GET /v1/metadata`
- `POST /v1/context`
- `POST /v1/tick`
- `POST /v1/reply`
- `POST /v1/teardown`

## Setup

```bash
python -m venv veravenv
source veravenv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `GOOGLE_API_KEY` or `GEMINI_API_KEY` for Gemini. Without a key, the deterministic fallback still runs.

## Run Locally

```bash
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Then verify:

```bash
python scripts/smoke_test.py
```

## Judge Simulator

From the extracted challenge folder, set the simulator's `BOT_URL` to `http://localhost:8080`, configure its judge LLM key, then run:

```bash
python judge_simulator.py
```

## Deployment

Docker:

```bash
docker build -t vera-signal-engine .
docker run -p 8080:8080 --env-file .env vera-signal-engine
```

Render can use the included `render.yaml`. Railway can run the same start command:

```bash
uvicorn bot:app --host 0.0.0.0 --port $PORT
```

## Safety And Grounding

The bot does not invent facts, prices, slots, competitors, medical claims, or citations. It validates generated text and falls back to templates if Gemini is missing, slow, or unsafe. State is in memory and can be wiped with `/v1/teardown`.

## Limitations

In-memory state is ideal for the challenge run but not multi-instance production deployment. LLM quality depends on Gemini availability; fallback is robust but less expressive.


from __future__ import annotations

import asyncio
from datetime import timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .composer import GeminiComposer
from .config import STARTED_AT, settings
from .models import ContextRequest, ReplyRequest, TickRequest
from .reply_handler import ReplyHandler
from .signal_selector import SignalSelector
from .store import store
from .utils import iso_now, utc_now


app = FastAPI(title="Vera Signal Engine", version=settings.bot_version)
selector = SignalSelector(store)
composer = GeminiComposer(settings, store)
reply_handler = ReplyHandler(store)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)[:200]})


@app.get("/v1/healthz")
async def healthz() -> dict[str, Any]:
    uptime = int((utc_now() - STARTED_AT).total_seconds())
    return {"status": "ok", "uptime_seconds": uptime, "contexts_loaded": store.counts()}


@app.get("/v1/metadata")
async def metadata() -> dict[str, Any]:
    submitted_at = STARTED_AT.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "team_name": settings.team_name,
        "team_members": settings.team_members,
        "model": f"{settings.gemini_model} via LangChain",
        "approach": "Vera Signal Engine: evidence-grounded trigger selection + Gemini composer + deterministic validator/fallback",
        "contact_email": settings.contact_email,
        "version": settings.bot_version,
        "submitted_at": submitted_at,
    }


@app.post("/v1/context")
async def push_context(body: ContextRequest) -> JSONResponse:
    accepted, record = store.put_context(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
        delivered_at=body.delivered_at,
    )
    if not accepted:
        current_version = record.version if record else None
        return JSONResponse(
            status_code=409,
            content={"accepted": False, "reason": "stale_version", "current_version": current_version},
        )
    return JSONResponse(
        status_code=200,
        content={"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}", "stored_at": record.stored_at},
    )


@app.post("/v1/tick")
async def tick(body: TickRequest) -> dict[str, Any]:
    try:
        limit = min(settings.max_actions_per_tick, 20)
        selected = selector.select(body.available_triggers, body.now, limit)
        if not selected or limit <= 0:
            return {"actions": []}

        tasks = [
            composer.compose_action(
                candidate.trigger_context_id,
                candidate.trigger,
                candidate.merchant,
                candidate.category,
                candidate.customer,
                candidate.rationale,
            )
            for candidate in selected[:limit]
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        actions: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, Exception):
                continue
            action, evidence_pack = result
            if not action.get("body"):
                continue
            actions.append(action)
            store.record_action(action, evidence_pack.to_prompt_dict())
            if len(actions) >= limit:
                break
        return {"actions": actions}
    except Exception:
        return {"actions": []}


@app.post("/v1/reply")
async def reply(body: ReplyRequest) -> dict[str, Any]:
    try:
        response = reply_handler.handle(body)
        if response.get("action") == "send" and not response.get("body"):
            return {"action": "end", "rationale": "Safe close because reply body would be empty."}
        return response
    except Exception:
        return {"action": "end", "rationale": "Safe fallback after reply handling error; closing to avoid invalid output."}


@app.post("/v1/teardown")
async def teardown() -> dict[str, bool]:
    store.clear()
    return {"cleared": True}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "vera-signal-engine", "health": "/v1/healthz"}

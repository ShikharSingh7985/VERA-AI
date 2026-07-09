from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from .config import Settings
from .evidence import EvidencePack, extract_evidence
from .fallback_templates import compose_fallback, repair_with_rules
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .store import InMemoryStore
from .utils import safe_slug, stable_hash, truncate
from .validator import validate_action


class GeminiComposer:
    def __init__(self, settings: Settings, store: InMemoryStore) -> None:
        self.settings = settings
        self.store = store
        self._llm: Any | None = None

    async def compose_action(
        self,
        trigger_context_id: str,
        trigger: dict[str, Any],
        merchant: dict[str, Any],
        category: dict[str, Any],
        customer: dict[str, Any] | None,
        selector_rationale: str,
    ) -> tuple[dict[str, Any], EvidencePack]:
        pack = extract_evidence(category, merchant, trigger, customer)
        audience = "customer" if pack.ids.get("customer_id") else "merchant"
        candidate = await self._compose_with_llm(pack, audience)
        if not candidate:
            candidate = compose_fallback(pack)

        action = self._build_action(candidate, pack, trigger_context_id, trigger, selector_rationale)
        history = []
        state = self.store.conversations.get(action["conversation_id"])
        if state:
            history = state.sent_bodies
        validation = validate_action(action, pack, history)
        if not validation.valid:
            repaired = repair_with_rules(pack, "; ".join(validation.reasons))
            action = self._build_action(repaired, pack, trigger_context_id, trigger, selector_rationale)
            second = validate_action(action, pack, history)
            if not second.valid:
                action["body"] = truncate(action["body"], 700)
                action["rationale"] = f"Safe deterministic fallback after validation repair: {'; '.join(second.reasons)}"
        return action, pack

    async def _compose_with_llm(self, pack: EvidencePack, audience: str) -> dict[str, Any] | None:
        if not self.settings.use_llm or not self.settings.google_api_key:
            return None
        try:
            llm = self._get_llm()
            prompt = build_user_prompt(pack, audience)
            response = await asyncio.wait_for(
                llm.ainvoke([("system", SYSTEM_PROMPT), ("human", prompt)]),
                timeout=self.settings.llm_timeout_seconds,
            )
            content = getattr(response, "content", response)
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            return _parse_json_object(str(content))
        except Exception:
            return None

    def _get_llm(self) -> Any:
        if self._llm is not None:
            return self._llm
        from langchain_google_genai import ChatGoogleGenerativeAI

        self._llm = ChatGoogleGenerativeAI(
            model=self.settings.gemini_model,
            google_api_key=self.settings.google_api_key,
            temperature=0.25,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=1,
        )
        return self._llm

    def _build_action(
        self,
        candidate: dict[str, Any],
        pack: EvidencePack,
        trigger_context_id: str,
        trigger: dict[str, Any],
        selector_rationale: str,
    ) -> dict[str, Any]:
        trigger_id = trigger.get("id") or trigger_context_id
        merchant_id = pack.ids.get("merchant_id") or ""
        customer_id = pack.ids.get("customer_id")
        suppression_key = trigger.get("suppression_key") or f"{trigger_id}:{stable_hash(merchant_id, customer_id)}"
        conversation_id = _conversation_id(merchant_id, customer_id, trigger_id, trigger.get("kind"), suppression_key)
        body = str(candidate.get("body") or "").strip()
        cta = _normalize_cta(str(candidate.get("cta") or ""), body)
        rationale = str(candidate.get("rationale") or "Composed from evidence pack.")
        if selector_rationale:
            rationale = truncate(f"{rationale} Selector: {selector_rationale}", 420)
        return {
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": "merchant_on_behalf" if customer_id else "vera",
            "trigger_id": trigger_id,
            "template_name": str(candidate.get("template_name") or _template_name(trigger.get("kind"), customer_id)),
            "template_params": [str(item) for item in candidate.get("template_params", [])][:5],
            "body": body,
            "cta": cta,
            "suppression_key": suppression_key,
            "rationale": rationale,
        }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _normalize_cta(raw: str, body: str) -> str:
    text = raw.strip().lower()
    if text in {"open_ended", "binary_yes", "booking_choice", "none"}:
        return text
    body_l = body.lower()
    if "reply 1" in body_l or "slot" in body_l:
        return "booking_choice"
    if "reply yes" in body_l or "reply confirm" in body_l or "yes" in text or "binary" in text:
        return "binary_yes"
    if text in {"", "null", "no"}:
        return "none" if not body.strip().endswith("?") else "open_ended"
    return "open_ended"


def _template_name(kind: str | None, customer_id: str | None) -> str:
    prefix = "merchant" if customer_id else "vera"
    return f"{prefix}_{safe_slug(kind or 'message')}_v1"


def _conversation_id(merchant_id: str, customer_id: str | None, trigger_id: str, kind: str | None, suppression_key: str) -> str:
    subject = customer_id or merchant_id
    return "conv_" + "_".join(
        [
            safe_slug(subject, "subject")[:36],
            safe_slug(kind or "trigger")[:24],
            stable_hash(trigger_id, suppression_key, length=8),
        ]
    )

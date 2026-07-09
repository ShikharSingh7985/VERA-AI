from __future__ import annotations

import re
from typing import Any

from .store import InMemoryStore
from .utils import normalize_text, truncate

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None


POSITIVE_PATTERNS = [
    "yes",
    "ok",
    "okay",
    "send",
    "do it",
    "go ahead",
    "interested",
    "chalo",
    "haan",
    "ha",
    "please share",
    "lets do",
    "let s do",
    "confirm",
    "proceed",
    "start",
]

NEGATIVE_PATTERNS = [
    "not interested",
    "stop",
    "dont message",
    "do not message",
    "don't message",
    "band karo",
    "unsubscribe",
    "no thanks",
    "leave me",
    "remove me",
]

AUTO_REPLY_PATTERNS = [
    "thank you for contacting",
    "thanks for contacting",
    "automated assistant",
    "auto reply",
    "business auto reply",
    "out of office",
    "will respond shortly",
    "our team will respond",
    "team tak pahuncha",
    "hamari team tak",
    "aapki jaankari ke liye",
    "currently unavailable",
]

HOSTILE_PATTERNS = ["useless", "spam", "bothering me", "bakwas", "annoying", "fraud"]
PRICE_PATTERNS = ["price", "cost", "kitna", "rate", "charges", "amount", "fee", "renewal"]
TIME_PATTERNS = ["time", "how long", "when", "kab", "slot", "schedule", "appointment"]
INFO_PATTERNS = ["what is this", "how it helps", "explain", "kaise", "why", "details"]
OFF_TOPIC_PATTERNS = ["gst", "tax filing", "income tax", "loan", "job opening", "salary", "personal loan"]


class ReplyHandler:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = store

    def handle(self, request: Any) -> dict[str, Any]:
        conversation_id = request.conversation_id
        state = self.store.append_reply(conversation_id, request.from_role, request.message, request.received_at)
        merchant_id = request.merchant_id or state.merchant_id
        classification = classify_reply(request.message, [turn.get("body", "") for turn in state.turns if turn.get("from") == request.from_role])

        if classification == "auto_reply":
            state.auto_reply_count += 1
            merchant_auto_count = self.store.record_auto_reply(merchant_id, request.message)
            if state.auto_reply_count >= 3 or merchant_auto_count >= 3:
                self.store.mark_ended(conversation_id)
                response = {"action": "end", "rationale": "Auto-reply repeated; closing instead of wasting turns."}
            elif state.auto_reply_count >= 2 or merchant_auto_count >= 2:
                response = {"action": "wait", "wait_seconds": 86400, "rationale": "Same or similar auto-reply repeated; waiting 24h for a real owner reply."}
            else:
                body = "Looks like an auto-reply. When the owner sees this, reply YES and I'll prepare the draft from the same details."
                response = {"action": "send", "body": body, "cta": "binary_yes", "rationale": "Detected an auto-reply and left one owner-facing prompt."}
            self._record_response(conversation_id, response)
            return response

        if classification in {"negative_intent", "hostile"}:
            self.store.opt_out_merchant(merchant_id)
            self.store.mark_ended(conversation_id)
            response = {"action": "end", "rationale": "Merchant/customer declined or showed frustration; ending politely and suppressing follow-up."}
            self._record_response(conversation_id, response)
            return response

        if classification == "positive_intent":
            body = "Done. I'll prepare it from the same details and keep it short. You can review before anything goes live."
            response = {"action": "send", "body": body, "cta": "none", "rationale": "Explicit positive intent; moved directly to action without another qualifying question."}
            self._record_response(conversation_id, response)
            return response

        if classification == "price_question":
            response = self._answer_price(state, merchant_id)
            self._record_response(conversation_id, response)
            return response

        if classification == "timing_question":
            response = self._answer_timing(state)
            self._record_response(conversation_id, response)
            return response

        if classification == "info_question":
            response = self._answer_info(state)
            self._record_response(conversation_id, response)
            return response

        if classification == "off_topic":
            state.off_topic_count += 1
            if state.off_topic_count >= 2:
                response = {"action": "end", "rationale": "Repeated off-topic request; closing to stay on mission."}
            else:
                body = "I can't help with that directly from this Vera flow. Coming back to the current business nudge, reply YES if you want me to prepare the draft."
                response = {"action": "send", "body": body, "cta": "binary_yes", "rationale": "Politely declined off-topic ask and returned to the original mission."}
            self._record_response(conversation_id, response)
            return response

        response = {"action": "wait", "wait_seconds": 1800, "rationale": "No clear intent detected; backing off instead of over-messaging."}
        self._record_response(conversation_id, response)
        return response

    def _answer_price(self, state: Any, merchant_id: str | None) -> dict[str, Any]:
        offers = []
        if state.evidence:
            offers = state.evidence.get("active_offers") or []
        if not offers and merchant_id:
            merchant = self.store.get_payload("merchant", merchant_id) or {}
            offers = [offer.get("title") for offer in merchant.get("offers", []) if offer.get("status") == "active" and offer.get("title")]
        if offers:
            body = f"The current catalog offer I have is: {offers[0]}. I won't quote any other price without context. Reply YES and I'll draft around this."
        else:
            body = "I don't have a confirmed price in the current context, so I won't invent one. I can draft the message without a price first."
        return {"action": "send", "body": body, "cta": "binary_yes" if offers else "none", "rationale": "Answered price question using only active offer context."}

    def _answer_timing(self, state: Any) -> dict[str, Any]:
        slots = []
        if state.evidence:
            slots = state.evidence.get("slots") or []
        if slots:
            body = f"The slot I have in context is {', '.join(slots[:2])}. Reply YES and I'll use that."
            cta = "booking_choice" if len(slots) > 1 else "binary_yes"
        else:
            body = "I don't have a confirmed time or slot in this context, so I won't invent one. I'll keep the draft timing-free for review."
            cta = "none"
        return {"action": "send", "body": body, "cta": cta, "rationale": "Answered timing question without inventing unavailable slots."}

    def _answer_info(self, state: Any) -> dict[str, Any]:
        evidence = state.evidence or {}
        kind = evidence.get("raw_trigger_kind") or state.trigger_id or "this trigger"
        facts = evidence.get("trigger_facts") or []
        first_fact = facts[0] if facts else ""
        body = f"This is about {kind}. {first_fact} I can keep the next message short and grounded in these same details."
        return {"action": "send", "body": truncate(body, 500), "cta": "none", "rationale": "Explained the trigger briefly using stored evidence only."}

    def _record_response(self, conversation_id: str, response: dict[str, Any]) -> None:
        self.store.record_bot_reply(conversation_id, response.get("body"), response.get("action", "send"))


def classify_reply(message: str, previous_messages: list[str] | None = None) -> str:
    normalized = normalize_text(message)
    previous_messages = previous_messages or []
    if not normalized:
        return "uncertain"
    if _matches(normalized, AUTO_REPLY_PATTERNS) or _similar_to_previous(normalized, previous_messages):
        return "auto_reply"
    if _matches(normalized, HOSTILE_PATTERNS):
        if _matches(normalized, NEGATIVE_PATTERNS) or "stop" in normalized:
            return "negative_intent"
        return "hostile"
    if normalized in {"no", "nahi", "nahin"} or _matches(normalized, NEGATIVE_PATTERNS):
        return "negative_intent"
    if _matches(normalized, OFF_TOPIC_PATTERNS):
        return "off_topic"
    if _matches(normalized, PRICE_PATTERNS):
        return "price_question"
    if _matches(normalized, TIME_PATTERNS):
        return "timing_question"
    if _matches(normalized, INFO_PATTERNS) or normalized.endswith("?"):
        return "info_question"
    if _positive_match(normalized):
        return "positive_intent"
    return "uncertain"


def _matches(normalized: str, patterns: list[str]) -> bool:
    return any(normalize_text(pattern) in normalized for pattern in patterns)


def _positive_match(normalized: str) -> bool:
    for pattern in POSITIVE_PATTERNS:
        token = normalize_text(pattern)
        if re.search(rf"\b{re.escape(token)}\b", normalized):
            return True
    return False


def _similar_to_previous(normalized: str, previous_messages: list[str]) -> bool:
    if len(previous_messages) < 2:
        return False
    for previous in previous_messages[:-1]:
        prev = normalize_text(previous)
        if not prev:
            continue
        if prev == normalized:
            return True
        if fuzz is not None and fuzz.ratio(prev, normalized) >= 96:
            return True
    return False

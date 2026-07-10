from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Any

from .utils import iso_now, normalize_text, utc_now


@dataclass
class ContextRecord:
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str | None
    stored_at: str


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    trigger_id: str | None = None
    suppression_key: str | None = None
    evidence: dict[str, Any] | None = None
    sent_bodies: list[str] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)
    auto_reply_count: int = 0
    off_topic_count: int = 0
    ended: bool = False
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.contexts: dict[str, dict[str, ContextRecord]] = {
            "category": {},
            "merchant": {},
            "customer": {},
            "trigger": {},
        }
        self.conversations: dict[str, ConversationState] = {}
        self.sent_trigger_ids: set[str] = set()
        self.sent_suppression_keys: dict[str, str] = {}
        self.merchant_last_sent_at: dict[str, str] = {}
        self.merchant_auto_replies: dict[tuple[str, str], int] = {}
        self.merchant_opt_outs: set[str] = set()

    def clear(self) -> None:
        with self._lock:
            for bucket in self.contexts.values():
                bucket.clear()
            self.conversations.clear()
            self.sent_trigger_ids.clear()
            self.sent_suppression_keys.clear()
            self.merchant_last_sent_at.clear()
            self.merchant_auto_replies.clear()
            self.merchant_opt_outs.clear()

    def put_context(
        self,
        scope: str,
        context_id: str,
        version: int,
        payload: dict[str, Any],
        delivered_at: str | None = None,
    ) -> tuple[bool, ContextRecord | None]:
        with self._lock:
            bucket = self.contexts.setdefault(scope, {})
            current = bucket.get(context_id)
            if current:
                if current.version > version:
                    return False, current
                if current.version == version:
                    return True, current
            record = ContextRecord(
                scope=scope,
                context_id=context_id,
                version=version,
                payload=payload,
                delivered_at=delivered_at,
                stored_at=iso_now(),
            )
            bucket[context_id] = record
            return True, record

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {scope: len(self.contexts.get(scope, {})) for scope in ("category", "merchant", "customer", "trigger")}

    def get_record(self, scope: str, context_id: str | None) -> ContextRecord | None:
        if not context_id:
            return None
        with self._lock:
            return self.contexts.get(scope, {}).get(context_id)

    def get_payload(self, scope: str, context_id: str | None) -> dict[str, Any] | None:
        record = self.get_record(scope, context_id)
        return record.payload if record else None

    def find_trigger(self, trigger_ref: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
        with self._lock:
            direct = self.contexts.get("trigger", {}).get(trigger_ref)
            if direct:
                return trigger_ref, direct.payload
            for context_id, record in self.contexts.get("trigger", {}).items():
                if record.payload.get("id") == trigger_ref:
                    return context_id, record.payload
        return None, None

    def all_payloads(self, scope: str) -> list[dict[str, Any]]:
        with self._lock:
            return [record.payload for record in self.contexts.get(scope, {}).values()]

    def get_or_create_conversation(self, conversation_id: str) -> ConversationState:
        with self._lock:
            state = self.conversations.get(conversation_id)
            if state is None:
                state = ConversationState(conversation_id=conversation_id)
                self.conversations[conversation_id] = state
            return state

    def record_action(self, action: dict[str, Any], evidence: dict[str, Any] | None = None) -> None:
        with self._lock:
            conversation_id = action["conversation_id"]
            state = self.conversations.get(conversation_id) or ConversationState(conversation_id=conversation_id)
            state.merchant_id = action.get("merchant_id")
            state.customer_id = action.get("customer_id")
            state.trigger_id = action.get("trigger_id")
            state.suppression_key = action.get("suppression_key")
            state.evidence = evidence or state.evidence
            state.sent_bodies.append(action.get("body", ""))
            state.turns.append({"from": "bot", "body": action.get("body", ""), "ts": iso_now()})
            state.updated_at = iso_now()
            self.conversations[conversation_id] = state
            trigger_id = action.get("trigger_id")
            if trigger_id:
                self.sent_trigger_ids.add(trigger_id)
            suppression_key = action.get("suppression_key")
            if suppression_key:
                self.sent_suppression_keys[suppression_key] = iso_now()
            merchant_id = action.get("merchant_id")
            if merchant_id:
                self.merchant_last_sent_at[merchant_id] = iso_now()

    def append_reply(self, conversation_id: str, role: str, message: str, received_at: str | None = None) -> ConversationState:
        with self._lock:
            state = self.get_or_create_conversation(conversation_id)
            state.turns.append({"from": role, "body": message, "ts": received_at or iso_now()})
            state.updated_at = iso_now()
            return state

    def body_was_sent(self, conversation_id: str, body: str) -> bool:
        normalized = normalize_text(body)
        with self._lock:
            state = self.conversations.get(conversation_id)
            if not state:
                return False
            return any(normalize_text(existing) == normalized for existing in state.sent_bodies)

    def record_auto_reply(self, merchant_id: str | None, message: str) -> int:
        merchant = merchant_id or "unknown"
        key = (merchant, normalize_text(message)[:160])
        with self._lock:
            count = self.merchant_auto_replies.get(key, 0) + 1
            self.merchant_auto_replies[key] = count
            return count

    def mark_ended(self, conversation_id: str) -> None:
        with self._lock:
            state = self.get_or_create_conversation(conversation_id)
            state.ended = True
            state.updated_at = iso_now()

    def record_bot_reply(self, conversation_id: str, body: str | None, action: str) -> None:
        with self._lock:
            state = self.get_or_create_conversation(conversation_id)
            if body:
                state.sent_bodies.append(body)
                state.turns.append({"from": "bot", "body": body, "ts": iso_now(), "action": action})
            if action == "end":
                state.ended = True
            state.updated_at = iso_now()

    def opt_out_merchant(self, merchant_id: str | None) -> None:
        if not merchant_id:
            return
        with self._lock:
            self.merchant_opt_outs.add(merchant_id)

    def merchant_opted_out(self, merchant_id: str | None) -> bool:
        if not merchant_id:
            return False
        with self._lock:
            return merchant_id in self.merchant_opt_outs

    def last_sent_datetime(self, merchant_id: str | None) -> datetime | None:
        if not merchant_id:
            return None
        from .utils import parse_datetime

        with self._lock:
            return parse_datetime(self.merchant_last_sent_at.get(merchant_id))


store = InMemoryStore()

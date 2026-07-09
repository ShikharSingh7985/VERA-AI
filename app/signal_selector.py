from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .store import InMemoryStore
from .utils import first_present, get_in, parse_datetime, pct_to_text, utc_now


@dataclass
class CandidateSignal:
    trigger_context_id: str
    trigger: dict[str, Any]
    merchant: dict[str, Any]
    category: dict[str, Any]
    customer: dict[str, Any] | None
    score: float
    rationale: str


class SignalSelector:
    def __init__(self, store: InMemoryStore) -> None:
        self.store = store

    def select(self, available_triggers: list[str], now: str | None, limit: int) -> list[CandidateSignal]:
        now_dt = parse_datetime(now) or utc_now()
        candidates: list[CandidateSignal] = []
        for trigger_ref in available_triggers:
            context_id, trigger = self.store.find_trigger(trigger_ref)
            if not context_id or not trigger:
                continue
            merchant_id = merchant_id_from_trigger(trigger)
            merchant = self.store.get_payload("merchant", merchant_id)
            category_slug = first_present(
                get_in(trigger, ["payload", "category"]),
                merchant.get("category_slug") if merchant else None,
            )
            category = self.store.get_payload("category", category_slug)
            customer_id = customer_id_from_trigger(trigger)
            customer = self.store.get_payload("customer", customer_id) if customer_id else None
            score, rationale = score_trigger(trigger, merchant, category, customer, now_dt, self.store)
            if score < 18:
                continue
            candidates.append(
                CandidateSignal(
                    trigger_context_id=context_id,
                    trigger=trigger,
                    merchant=merchant or {},
                    category=category or {},
                    customer=customer,
                    score=score,
                    rationale=rationale,
                )
            )

        candidates.sort(key=lambda item: item.score, reverse=True)
        selected: list[CandidateSignal] = []
        used_merchants: set[str] = set()
        for candidate in candidates:
            merchant_id = merchant_id_from_trigger(candidate.trigger)
            if merchant_id and merchant_id in used_merchants:
                continue
            selected.append(candidate)
            if merchant_id:
                used_merchants.add(merchant_id)
            if len(selected) >= min(max(limit, 0), 20):
                break
        return selected


def merchant_id_from_trigger(trigger: dict[str, Any]) -> str | None:
    return first_present(trigger.get("merchant_id"), get_in(trigger, ["payload", "merchant_id"]))


def customer_id_from_trigger(trigger: dict[str, Any]) -> str | None:
    return first_present(trigger.get("customer_id"), get_in(trigger, ["payload", "customer_id"]), get_in(trigger, ["payload", "patient_id"]))


def score_trigger(
    trigger: dict[str, Any],
    merchant: dict[str, Any] | None,
    category: dict[str, Any] | None,
    customer: dict[str, Any] | None,
    now: datetime | None = None,
    store: InMemoryStore | None = None,
) -> tuple[float, str]:
    now = now or utc_now()
    merchant_id = merchant_id_from_trigger(trigger)
    customer_id = customer_id_from_trigger(trigger)
    kind = str(trigger.get("kind") or "").lower()
    score = float(trigger.get("urgency") or 1) * 10.0
    reasons = [f"urgency {trigger.get('urgency', 1)}"]

    if store and store.merchant_opted_out(merchant_id):
        return -100.0, "merchant opted out"

    expires_at = parse_datetime(trigger.get("expires_at"))
    if expires_at and expires_at < now:
        score -= 8
        reasons.append("expired trigger")

    if customer_id:
        if customer:
            score += 20
            reasons.append("customer context present")
            if not _customer_can_receive(customer):
                score -= 45
                reasons.append("customer opt-in absent")
        else:
            score -= 20
            reasons.append("customer context missing")

    if kind in {"research_digest", "category_research_digest_release", "cde_opportunity"}:
        if _has_matching_digest(trigger, category):
            score += 15
            reasons.append("matching digest item")
        else:
            score += 4
            reasons.append("knowledge trigger")

    if kind in {"regulation_change", "compliance_alert", "supply_alert"}:
        if _has_matching_digest(trigger, category) or (trigger.get("payload") or {}):
            score += 15
            reasons.append("compliance or alert evidence")

    if "perf" in kind or kind in {"gbp_unverified", "review_theme_emerged", "milestone_reached"}:
        if _has_metric_movement(trigger, merchant):
            score += 15
            reasons.append("concrete metric movement")

    if kind in {"active_planning_intent", "curious_ask_due"}:
        score += 12
        reasons.append("high engagement conversational trigger")

    if kind in {"recall_due", "customer_lapsed_soft", "customer_lapsed_hard", "chronic_refill_due", "appointment_tomorrow", "trial_followup", "wedding_package_followup"}:
        score += 16
        reasons.append("relationship-state trigger")

    if _has_active_offer(merchant):
        score += 10
        reasons.append("active merchant offer available")

    if _signal_matches_kind(kind, merchant):
        score += 10
        reasons.append("merchant signal matches trigger")

    if category and category.get("peer_stats"):
        score += 8
        reasons.append("peer benchmark available")

    if _recent_engagement(merchant):
        score += 6
        reasons.append("recent merchant engagement")

    if store:
        trigger_id = trigger.get("id")
        suppression_key = trigger.get("suppression_key")
        if suppression_key and suppression_key in store.sent_suppression_keys:
            score -= 50
            reasons.append("suppression key already sent")
        if trigger_id and trigger_id in store.sent_trigger_ids:
            score -= 30
            reasons.append("trigger already sent")
        last_sent = store.last_sent_datetime(merchant_id)
        if last_sent and (now - last_sent).total_seconds() < 60 * 60 * 4:
            score -= 10
            reasons.append("recent conversation with merchant")

    if not merchant or not category:
        score -= 15
        reasons.append("missing merchant or category")

    payload = trigger.get("payload") or {}
    if payload.get("delta_pct") is not None:
        reasons.append(f"delta {pct_to_text(payload.get('delta_pct'))}")
    if customer_id and customer is None:
        reasons.append(f"customer {customer_id} unavailable")

    return score, "; ".join(reasons)


def _has_matching_digest(trigger: dict[str, Any], category: dict[str, Any] | None) -> bool:
    if not category:
        return False
    payload = trigger.get("payload") or {}
    wanted = {
        payload.get("top_item_id"),
        payload.get("digest_item_id"),
        payload.get("alert_id"),
        payload.get("item_id"),
    }
    wanted.discard(None)
    if not wanted:
        return False
    return any(item.get("id") in wanted for item in category.get("digest", []) or [])


def _has_metric_movement(trigger: dict[str, Any], merchant: dict[str, Any] | None) -> bool:
    payload = trigger.get("payload") or {}
    if any(payload.get(key) is not None for key in ("metric", "delta_pct", "value_now", "occurrences_30d", "estimated_uplift_pct")):
        return True
    perf = (merchant or {}).get("performance", {}) or {}
    delta = perf.get("delta_7d", {}) or {}
    return any(value not in (None, 0, 0.0) for value in delta.values())


def _has_active_offer(merchant: dict[str, Any] | None) -> bool:
    for offer in (merchant or {}).get("offers", []) or []:
        if offer.get("status") == "active" and offer.get("title"):
            return True
    return False


def _signal_matches_kind(kind: str, merchant: dict[str, Any] | None) -> bool:
    signals = " ".join((merchant or {}).get("signals", []) or []).lower()
    if not signals:
        return False
    aliases = {
        "perf_dip": ["perf_dip", "dip", "below_peer"],
        "seasonal_perf_dip": ["seasonal_dip", "dip"],
        "renewal_due": ["renewal", "expiry"],
        "dormant_with_vera": ["dormant", "no_recent"],
        "gbp_unverified": ["unverified"],
        "curious_ask_due": ["high_engagement", "engaged"],
        "active_planning_intent": ["active_planning", "engaged"],
        "research_digest": ["cohort", "engaged"],
    }
    tokens = aliases.get(kind, [kind.replace("_", " ")])
    return any(token in signals for token in tokens)


def _recent_engagement(merchant: dict[str, Any] | None) -> bool:
    for turn in (merchant or {}).get("conversation_history", []) or []:
        engagement = str(turn.get("engagement") or "").lower()
        if "replied" in engagement or "intent" in engagement:
            return True
    return False


def _customer_can_receive(customer: dict[str, Any] | None) -> bool:
    if not customer:
        return False
    prefs = customer.get("preferences", {}) or {}
    consent = customer.get("consent", {}) or {}
    if prefs.get("reminder_opt_in") is False:
        return False
    scope = consent.get("scope")
    if isinstance(scope, list) and len(scope) == 0:
        return False
    return True

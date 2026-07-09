from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .utils import compact_ws, first_present, get_in, json_dumps, money_values, number_values, pct_to_text, truncate


@dataclass
class EvidencePack:
    ids: dict[str, str | None]
    merchant_display_name: str | None
    merchant_first_name: str | None
    category_slug: str | None
    city: str | None
    locality: str | None
    language_pref: str | None
    performance_facts: list[str] = field(default_factory=list)
    peer_facts: list[str] = field(default_factory=list)
    offer_facts: list[str] = field(default_factory=list)
    digest_facts: list[str] = field(default_factory=list)
    customer_facts: list[str] = field(default_factory=list)
    trigger_facts: list[str] = field(default_factory=list)
    voice_rules: dict[str, Any] = field(default_factory=dict)
    taboo_words: list[str] = field(default_factory=list)
    recommended_angle: str = "grounded_helpful_nudge"
    forbidden_claims: list[str] = field(default_factory=list)
    allowed_numbers: set[str] = field(default_factory=set)
    allowed_prices: set[str] = field(default_factory=set)
    allowed_sources: set[str] = field(default_factory=set)
    raw_trigger_kind: str | None = None
    selected_offer: str | None = None
    selected_digest: dict[str, Any] | None = None
    active_offers: list[str] = field(default_factory=list)
    slots: list[str] = field(default_factory=list)

    def to_prompt_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["allowed_numbers"] = sorted(self.allowed_numbers)
        data["allowed_prices"] = sorted(self.allowed_prices)
        data["allowed_sources"] = sorted(self.allowed_sources)
        return data

    def salutation(self) -> str:
        if self.category_slug == "dentists" and self.merchant_first_name:
            first = self.merchant_first_name
            return first if first.lower().startswith("dr") else f"Dr. {first}"
        return self.merchant_first_name or self.merchant_display_name or "there"


GLOBAL_FORBIDDEN = [
    "guaranteed",
    "100% safe",
    "miracle",
    "best in city",
    "viral guarantee",
    "guaranteed weight loss",
    "shred in 7 days",
    "packed house",
]


def extract_evidence(
    category: dict[str, Any] | None,
    merchant: dict[str, Any] | None,
    trigger: dict[str, Any] | None,
    customer: dict[str, Any] | None = None,
) -> EvidencePack:
    category = category or {}
    merchant = merchant or {}
    trigger = trigger or {}
    customer = customer or {}

    identity = merchant.get("identity", {}) or {}
    customer_identity = customer.get("identity", {}) or {}
    category_slug = first_present(
        category.get("slug"),
        merchant.get("category_slug"),
        get_in(trigger, ["payload", "category"]),
    )
    trigger_id = trigger.get("id")
    merchant_id = first_present(trigger.get("merchant_id"), get_in(trigger, ["payload", "merchant_id"]), merchant.get("merchant_id"))
    customer_id = first_present(trigger.get("customer_id"), get_in(trigger, ["payload", "customer_id"]), customer.get("customer_id"))

    voice = category.get("voice", {}) or {}
    taboo_words = list(dict.fromkeys((voice.get("vocab_taboo") or []) + (voice.get("taboos") or [])))
    language_pref = _language_pref(identity, customer_identity, bool(customer))

    pack = EvidencePack(
        ids={"merchant_id": merchant_id, "customer_id": customer_id, "trigger_id": trigger_id},
        merchant_display_name=identity.get("name"),
        merchant_first_name=identity.get("owner_first_name"),
        category_slug=category_slug,
        city=identity.get("city"),
        locality=identity.get("locality"),
        language_pref=language_pref,
        voice_rules={
            "tone": voice.get("tone"),
            "register": voice.get("register"),
            "code_mix": voice.get("code_mix"),
            "vocab_allowed": voice.get("vocab_allowed", [])[:14],
            "tone_examples": voice.get("tone_examples", [])[:3],
        },
        taboo_words=taboo_words,
        forbidden_claims=list(dict.fromkeys(taboo_words + GLOBAL_FORBIDDEN)),
        raw_trigger_kind=trigger.get("kind"),
    )

    pack.performance_facts = _performance_facts(merchant) + _aggregate_facts(merchant)
    pack.peer_facts = _peer_facts(category, merchant)
    pack.active_offers, pack.offer_facts = _offer_facts(merchant, category, trigger)
    pack.selected_offer = pack.active_offers[0] if pack.active_offers else None
    selected_digest_items = _select_digest_items(category, trigger)
    pack.selected_digest = selected_digest_items[0] if selected_digest_items else None
    pack.digest_facts = [_digest_to_fact(item) for item in selected_digest_items]
    pack.customer_facts = _customer_facts(customer)
    pack.trigger_facts, pack.slots = _trigger_facts(trigger)
    pack.recommended_angle = _recommended_angle(trigger, customer)

    _add_provenance(pack, category, merchant, trigger, customer)
    return pack


def _language_pref(identity: dict[str, Any], customer_identity: dict[str, Any], has_customer: bool) -> str:
    if has_customer:
        return customer_identity.get("language_pref") or "english"
    languages = [str(lang).lower() for lang in identity.get("languages", [])]
    if "hi" in languages:
        return "hi-en mix"
    if "en" in languages:
        return "english"
    return ", ".join(languages) if languages else "english"


def _performance_facts(merchant: dict[str, Any]) -> list[str]:
    perf = merchant.get("performance", {}) or {}
    if not perf:
        return []
    window = perf.get("window_days")
    prefix = f"{window}d" if window else "current"
    facts: list[str] = []
    for key in ("views", "calls", "directions", "leads"):
        if perf.get(key) is not None:
            facts.append(f"{prefix} {key}: {perf[key]}")
    if perf.get("ctr") is not None:
        facts.append(f"{prefix} CTR: {pct_to_text(perf.get('ctr'))}")
    delta = perf.get("delta_7d", {}) or {}
    for key, value in delta.items():
        pct = pct_to_text(value)
        if pct:
            facts.append(f"7d {key.replace('_pct', '')} change: {pct}")
    return facts[:9]


def _aggregate_facts(merchant: dict[str, Any]) -> list[str]:
    aggregate = merchant.get("customer_aggregate", {}) or {}
    facts: list[str] = []
    for key, value in aggregate.items():
        if value not in (None, "", [], {}):
            value_text = pct_to_text(value) if key.endswith("_pct") else value
            facts.append(f"customer_aggregate {key}: {value_text}")
    return facts[:8]


def _peer_facts(category: dict[str, Any], merchant: dict[str, Any]) -> list[str]:
    peer = category.get("peer_stats", {}) or {}
    if not peer:
        return []
    facts: list[str] = []
    scope = peer.get("scope")
    if scope:
        facts.append(f"peer scope: {scope}")
    merchant_ctr = get_in(merchant, ["performance", "ctr"])
    peer_ctr = peer.get("avg_ctr")
    if peer_ctr is not None:
        text = f"peer avg CTR: {pct_to_text(peer_ctr)}"
        if merchant_ctr is not None:
            direction = "below" if float(merchant_ctr) < float(peer_ctr) else "above"
            text += f"; merchant is {direction} this benchmark"
        facts.append(text)
    for key in ("avg_rating", "avg_review_count", "avg_reviews", "avg_views_30d", "avg_calls_30d", "retention_6mo_pct", "retention_3mo_pct", "monthly_churn_pct", "trial_to_paid_pct", "repeat_customer_pct"):
        if peer.get(key) is not None:
            value = pct_to_text(peer[key]) if key.endswith("_pct") else peer[key]
            facts.append(f"peer {key}: {value}")
    return facts[:8]


def _offer_facts(merchant: dict[str, Any], category: dict[str, Any], trigger: dict[str, Any]) -> tuple[list[str], list[str]]:
    active: list[str] = []
    facts: list[str] = []
    for offer in merchant.get("offers", []) or []:
        title = compact_ws(offer.get("title"))
        status = compact_ws(offer.get("status") or "unknown")
        if not title:
            continue
        if status == "active":
            active.append(title)
            facts.append(f"active offer: {title}")
        elif _trigger_may_need_inactive_offer(trigger):
            facts.append(f"{status} offer: {title}")
    if not facts:
        for offer in category.get("offer_catalog", [])[:3] or []:
            title = compact_ws(offer.get("title"))
            if title:
                facts.append(f"category offer pattern: {title}")
    return active[:5], facts[:7]


def _trigger_may_need_inactive_offer(trigger: dict[str, Any]) -> bool:
    kind = str(trigger.get("kind") or "")
    return any(part in kind for part in ("perf", "winback", "renewal", "dormant", "offer"))


def _select_digest_items(category: dict[str, Any], trigger: dict[str, Any]) -> list[dict[str, Any]]:
    digest = category.get("digest", []) or []
    payload = trigger.get("payload", {}) or {}
    wanted_ids = [
        payload.get("top_item_id"),
        payload.get("digest_item_id"),
        payload.get("alert_id"),
        payload.get("item_id"),
    ]
    wanted_ids = [item for item in wanted_ids if item]
    selected = [item for item in digest if item.get("id") in wanted_ids]
    if selected:
        return selected[:2]

    kind = str(trigger.get("kind") or "").lower()
    keywords: list[str] = []
    if "ipl" in kind:
        keywords = ["ipl", "match"]
    elif "compliance" in kind or "regulation" in kind:
        keywords = ["compliance", "circular", "audit", "dose", "gst", "schedule"]
    elif "supply" in kind or "recall" in kind:
        keywords = ["recall", "supply", "batch", "alert"]
    elif "research" in kind or "cde" in kind:
        keywords = ["research", "webinar", "cde", "jida", "icmr"]
    elif "season" in kind or "festival" in kind:
        keywords = ["season", "summer", "wedding", "festival", "diwali", "resolution"]
    elif "trend" in kind:
        keywords = ["trend", "search", "demand"]
    if keywords:
        matches = []
        for item in digest:
            haystack = " ".join(str(item.get(key, "")) for key in ("kind", "title", "summary", "actionable", "source")).lower()
            if any(keyword in haystack for keyword in keywords):
                matches.append(item)
        if matches:
            return matches[:2]
    return []


def _digest_to_fact(item: dict[str, Any]) -> str:
    parts = []
    for key in ("kind", "title", "source", "summary", "actionable", "date", "credits", "trial_n", "patient_segment"):
        if item.get(key) not in (None, "", [], {}):
            parts.append(f"{key}: {item[key]}")
    return truncate("; ".join(parts), 520)


def _customer_facts(customer: dict[str, Any]) -> list[str]:
    if not customer:
        return []
    identity = customer.get("identity", {}) or {}
    relationship = customer.get("relationship", {}) or {}
    prefs = customer.get("preferences", {}) or {}
    consent = customer.get("consent", {}) or {}
    facts: list[str] = []
    for label, value in [
        ("customer name", identity.get("name")),
        ("language", identity.get("language_pref")),
        ("age band", identity.get("age_band")),
        ("state", customer.get("state")),
        ("first visit", relationship.get("first_visit")),
        ("last visit", relationship.get("last_visit")),
        ("visits total", relationship.get("visits_total")),
        ("services", ", ".join(relationship.get("services_received", [])[:5]) if isinstance(relationship.get("services_received"), list) else relationship.get("services_received")),
        ("preferred slots", prefs.get("preferred_slots")),
        ("channel", prefs.get("channel")),
        ("consent scope", ", ".join(consent.get("scope", [])[:5]) if isinstance(consent.get("scope"), list) else consent.get("scope")),
    ]:
        if value not in (None, "", [], {}):
            facts.append(f"{label}: {value}")
    for key in ("wedding_date", "training_focus", "health_focus", "delivery_address", "favourite_dish", "office_nearby", "senior_citizen"):
        value = prefs.get(key) or identity.get(key) or relationship.get(key)
        if value not in (None, "", [], {}):
            facts.append(f"{key}: {value}")
    return facts[:12]


def _trigger_facts(trigger: dict[str, Any]) -> tuple[list[str], list[str]]:
    payload = trigger.get("payload", {}) or {}
    facts = [
        f"trigger kind: {trigger.get('kind')}",
        f"trigger source: {trigger.get('source')}",
        f"urgency: {trigger.get('urgency')}",
    ]
    if trigger.get("expires_at"):
        facts.append(f"expires at: {trigger.get('expires_at')}")
    slots: list[str] = []
    for key, value in payload.items():
        if key in {"available_slots", "next_session_options"} and isinstance(value, list):
            for slot in value:
                label = compact_ws(slot.get("label") if isinstance(slot, dict) else str(slot))
                if label:
                    slots.append(label)
            if slots:
                facts.append(f"{key}: {', '.join(slots)}")
            continue
        if key in {"placeholder"}:
            continue
        if value not in (None, "", [], {}):
            facts.append(f"{key}: {truncate(json_dumps(value), 220) if isinstance(value, (dict, list)) else value}")
    return facts[:14], slots[:4]


def _recommended_angle(trigger: dict[str, Any], customer: dict[str, Any]) -> str:
    kind = str(trigger.get("kind") or "").lower()
    if customer or trigger.get("scope") == "customer":
        if "refill" in kind:
            return "customer_refill_reminder"
        if "appointment" in kind:
            return "customer_appointment_reminder"
        if "trial" in kind or "wedding" in kind:
            return "customer_followup_to_booking"
        return "customer_relationship_nudge"
    if "research" in kind or "cde" in kind:
        return "knowledge_digest_with_source"
    if "compliance" in kind or "regulation" in kind or "supply" in kind:
        return "compliance_or_alert_with_action"
    if "perf_dip" in kind or "seasonal_perf_dip" in kind:
        return "performance_dip_reframe"
    if "perf_spike" in kind:
        return "capture_momentum"
    if "planning" in kind:
        return "turn_merchant_intent_into_artifact"
    if "curious" in kind:
        return "ask_one_specific_question"
    if "renewal" in kind or "winback" in kind:
        return "useful_retention_or_renewal_nudge"
    return "specific_why_now_nudge"


def _add_provenance(
    pack: EvidencePack,
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any],
) -> None:
    sources = set()
    for item in category.get("digest", []) or []:
        for key in ("source", "title", "id", "kind"):
            value = item.get(key)
            if value:
                sources.add(str(value))
    for key in ("regulatory_authorities", "professional_journals"):
        for item in category.get(key, []) or []:
            sources.add(str(item))
    for value in (trigger.get("source"), get_in(trigger, ["payload", "source"])):
        if value:
            sources.add(str(value))
    pack.allowed_sources = sources

    contexts = [category, merchant, trigger, customer]
    for context in contexts:
        text = json_dumps(context)
        pack.allowed_numbers.update(number_values(text))
        pack.allowed_prices.update(money_values(text))
        _collect_price_like_values(context, pack.allowed_prices)
    for offer in merchant.get("offers", []) or []:
        if offer.get("value") not in (None, ""):
            pack.allowed_prices.add(str(offer["value"]).replace(",", ""))
        pack.allowed_prices.update(money_values(offer.get("title")))
    for offer in category.get("offer_catalog", []) or []:
        if offer.get("value") not in (None, ""):
            pack.allowed_prices.add(str(offer["value"]).replace(",", ""))
        pack.allowed_prices.update(money_values(offer.get("title")))


def _collect_price_like_values(value: Any, prices: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if item not in (None, "", [], {}) and any(token in str(key).lower() for token in ("price", "amount", "value", "mrp", "fee", "cost")):
                for number in number_values(str(item)):
                    prices.add(number.replace(",", "").replace("%", ""))
            _collect_price_like_values(item, prices)
    elif isinstance(value, list):
        for item in value:
            _collect_price_like_values(item, prices)

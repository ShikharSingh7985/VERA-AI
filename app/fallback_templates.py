from __future__ import annotations

import re
from typing import Any

from .evidence import EvidencePack
from .utils import compact_ws, pct_to_text, truncate


def compose_fallback(pack: EvidencePack) -> dict[str, Any]:
    kind = (pack.raw_trigger_kind or "").lower()
    if pack.ids.get("customer_id"):
        result = _customer_message(pack, kind)
    else:
        result = _merchant_message(pack, kind)
    result["body"] = _finalize_body(result["body"])
    result.setdefault("template_params", _template_params(result["body"]))
    return result


def repair_with_rules(pack: EvidencePack, reason: str | None = None) -> dict[str, Any]:
    result = compose_fallback(pack)
    if reason:
        result["rationale"] = f"Rule fallback after validation issue: {reason}"
    return result


def _merchant_message(pack: EvidencePack, kind: str) -> dict[str, Any]:
    if kind in {"research_digest", "category_research_digest_release", "cde_opportunity"}:
        return _merchant_digest(pack, cde="cde" in kind)
    if kind in {"regulation_change", "compliance_alert"}:
        return _merchant_compliance(pack)
    if kind in {"supply_alert"}:
        return _merchant_supply_alert(pack)
    if kind in {"perf_dip", "seasonal_perf_dip"}:
        return _merchant_perf_dip(pack)
    if kind == "perf_spike":
        return _merchant_perf_spike(pack)
    if kind == "active_planning_intent":
        return _merchant_planning(pack)
    if kind == "curious_ask_due":
        return _merchant_curious_ask(pack)
    if kind == "renewal_due":
        return _merchant_renewal(pack)
    if kind in {"festival_upcoming", "category_seasonal", "ipl_match_today"}:
        return _merchant_seasonal(pack)
    if kind == "competitor_opened":
        return _merchant_competitor(pack)
    if kind == "review_theme_emerged":
        return _merchant_review_theme(pack)
    if kind == "milestone_reached":
        return _merchant_milestone(pack)
    if kind in {"gbp_unverified", "winback_eligible", "dormant_with_vera"}:
        return _merchant_account_nudge(pack)
    return _merchant_generic(pack)


def _customer_message(pack: EvidencePack, kind: str) -> dict[str, Any]:
    if kind in {"recall_due", "customer_lapsed_soft", "customer_lapsed_hard"}:
        return _customer_recall_or_winback(pack, kind)
    if kind == "chronic_refill_due":
        return _customer_refill(pack)
    if kind in {"appointment_tomorrow", "trial_followup"}:
        return _customer_appointment(pack)
    if kind == "wedding_package_followup":
        return _customer_wedding(pack)
    return _customer_generic(pack)


def _merchant_digest(pack: EvidencePack, cde: bool = False) -> dict[str, Any]:
    title = _digest_field(pack, "title") or _fact_after(pack.digest_facts, "title") or "a new category item"
    source = _digest_field(pack, "source") or _fact_after(pack.digest_facts, "source")
    summary = _digest_field(pack, "summary")
    actionable = _digest_field(pack, "actionable")
    hook = f"{source} has one item worth checking: {title}" if source else f"One category item is worth checking: {title}"
    merchant_signal = _merchant_signal_phrase(pack)
    body = _join(
        f"{pack.salutation()}, {hook}.",
        merchant_signal,
        truncate(summary, 170) if summary and not cde else None,
        truncate(actionable, 120) if actionable else None,
        "Want me to turn this into a short WhatsApp draft you can review?",
    )
    return _out(body, "open_ended", "vera_research_digest_v1", "Digest trigger with source-backed category evidence and a low-friction drafting CTA.")


def _merchant_compliance(pack: EvidencePack) -> dict[str, Any]:
    title = _digest_field(pack, "title") or _fact_after(pack.digest_facts, "title") or "a compliance update"
    source = _digest_field(pack, "source") or _fact_after(pack.digest_facts, "source")
    deadline = _fact_after(pack.trigger_facts, "deadline_iso") or _fact_after(pack.trigger_facts, "expires at")
    actionable = _digest_field(pack, "actionable")
    body = _join(
        f"{pack.salutation()}, compliance heads-up: {title}.",
        f"Source: {source}." if source else None,
        f"Deadline in context: {deadline}." if deadline else None,
        actionable,
        "Want me to draft the checklist and customer-safe wording?",
    )
    return _out(body, "open_ended", "vera_compliance_alert_v1", "Compliance trigger with cited source/deadline where present; asks for one concrete next step.")


def _merchant_supply_alert(pack: EvidencePack) -> dict[str, Any]:
    molecule = _fact_after(pack.trigger_facts, "molecule")
    batches = _fact_after(pack.trigger_facts, "affected_batches")
    manufacturer = _fact_after(pack.trigger_facts, "manufacturer")
    title = _digest_field(pack, "title")
    source = _digest_field(pack, "source")
    chronic_count = _fact_contains(pack.performance_facts + pack.peer_facts + pack.trigger_facts, "chronic_rx_count")
    body = _join(
        f"{pack.salutation()}, urgent pharmacy alert: {title or 'a supply alert is active'}.",
        f"Molecule: {molecule}." if molecule else None,
        f"Batches: {batches}." if batches else None,
        f"Manufacturer: {manufacturer}." if manufacturer else None,
        chronic_count,
        f"Source: {source}." if source else None,
        "Want me to draft the customer note and replacement-pickup workflow?",
    )
    return _out(body, "open_ended", "vera_supply_alert_v1", "High-urgency supply alert using only trigger batch/manufacturer evidence.")


def _merchant_perf_dip(pack: EvidencePack) -> dict[str, Any]:
    metric = _fact_after(pack.trigger_facts, "metric")
    delta = _fact_after(pack.trigger_facts, "delta_pct")
    window = _fact_after(pack.trigger_facts, "window")
    season = _fact_after(pack.trigger_facts, "season_note")
    delta_text = pct_to_text(delta) if delta else None
    peer = _first_with(pack.peer_facts, "peer avg")
    offer = pack.selected_offer
    body = _join(
        f"{pack.salutation()}, {metric or 'performance'} is down {delta_text or 'in the latest window'}{f' over {window}' if window else ''}.",
        f"This looks tied to {season}." if season else None,
        peer,
        f"Your active offer, {offer}, gives us something concrete to nudge with." if offer else None,
        "Reply YES and I'll prepare a short recovery message.",
    )
    return _out(body, "binary_yes", "vera_perf_dip_v1", "Performance dip selected because concrete movement and peer/action evidence were available.")


def _merchant_perf_spike(pack: EvidencePack) -> dict[str, Any]:
    metric = _fact_after(pack.trigger_facts, "metric")
    delta = pct_to_text(_fact_after(pack.trigger_facts, "delta_pct"))
    window = _fact_after(pack.trigger_facts, "window")
    driver = _fact_after(pack.trigger_facts, "likely_driver")
    offer = pack.selected_offer
    body = _join(
        f"{pack.salutation()}, {metric or 'performance'} is up {delta or 'this cycle'}{f' in {window}' if window else ''}.",
        f"Likely driver in context: {driver}." if driver else None,
        f"Before the momentum cools, we can push {offer}." if offer else None,
        "Want me to draft the quick follow-up post?",
    )
    return _out(body, "open_ended", "vera_perf_spike_v1", "Positive momentum trigger; message preserves the specific metric and turns it into next action.")


def _merchant_planning(pack: EvidencePack) -> dict[str, Any]:
    topic = _fact_after(pack.trigger_facts, "intent_topic")
    last = _fact_after(pack.trigger_facts, "merchant_last_message")
    offer = pack.selected_offer
    body = _join(
        f"{pack.salutation()}, picking up from your note{f' on {topic}' if topic else ''}.",
        f"You said: {last}." if last else None,
        f"I'll base the draft on your current offer: {offer}." if offer else None,
        "Reply YES and I'll prepare the ready-to-send version now.",
    )
    return _out(body, "binary_yes", "vera_planning_intent_v1", "Merchant already showed intent, so the bot moves directly to drafting instead of qualifying again.")


def _merchant_curious_ask(pack: EvidencePack) -> dict[str, Any]:
    name = pack.merchant_display_name or "your business"
    body = _join(
        f"Hi {pack.salutation()}, quick check for {name}: what service or item has been most asked-for this week?",
        "I'll turn your answer into a Google post and a short WhatsApp reply for customers.",
        "What should I draft around?",
    )
    return _out(body, "open_ended", "vera_curious_ask_v1", "Curious-ask trigger: one specific question plus an immediate artifact offer.")


def _merchant_renewal(pack: EvidencePack) -> dict[str, Any]:
    days = _fact_after(pack.trigger_facts, "days_remaining")
    plan = _fact_after(pack.trigger_facts, "plan")
    amount = _fact_after(pack.trigger_facts, "renewal_amount")
    perf = _first_with(pack.performance_facts, "calls") or _first_with(pack.performance_facts, "views")
    body = _join(
        f"{pack.salutation()}, your {plan or 'plan'} renewal is due{f' in {days} days' if days else ''}.",
        perf,
        f"Renewal amount in context: Rs {amount}." if amount else None,
        "Want the short renewal summary with what changed this month?",
    )
    return _out(body, "open_ended", "vera_renewal_v1", "Renewal trigger framed around current account evidence rather than a pushy payment ask.")


def _merchant_seasonal(pack: EvidencePack) -> dict[str, Any]:
    kind = (pack.raw_trigger_kind or "").lower()
    festival = _fact_after(pack.trigger_facts, "festival")
    days = _fact_after(pack.trigger_facts, "days_until")
    match = _fact_after(pack.trigger_facts, "match")
    venue = _fact_after(pack.trigger_facts, "venue")
    time = _fact_after(pack.trigger_facts, "match_time_iso")
    digest = _digest_field(pack, "summary")
    offer = pack.selected_offer
    if "ipl" in kind:
        body = _join(
            f"{pack.salutation()}, {match or 'match day'} is in context{f' at {venue}' if venue else ''}.",
            f"Match time: {time}." if time else None,
            truncate(digest, 160) if digest else None,
            f"Use the already-active offer: {offer}." if offer else None,
            "Want me to draft the match-day customer note?",
        )
    else:
        body = _join(
            f"{pack.salutation()}, {festival or 'this seasonal window'} is coming up{f' in {days} days' if days else ''}.",
            truncate(digest, 150) if digest else None,
            f"Your active offer is {offer}." if offer else None,
            "Want me to draft a timely WhatsApp nudge?",
        )
    return _out(body, "open_ended", "vera_seasonal_v1", "Seasonal trigger selected because timing and category/offer evidence can create a specific nudge.")


def _merchant_competitor(pack: EvidencePack) -> dict[str, Any]:
    competitor = _fact_after(pack.trigger_facts, "competitor_name")
    distance = _fact_after(pack.trigger_facts, "distance_km")
    their_offer = _fact_after(pack.trigger_facts, "their_offer")
    opened = _fact_after(pack.trigger_facts, "opened_date")
    body = _join(
        f"{pack.salutation()}, competitor signal: {competitor or 'a nearby competitor'} opened{f' {distance} km away' if distance else ''}.",
        f"Their offer in trigger: {their_offer}." if their_offer else None,
        f"Opened date: {opened}." if opened else None,
        f"Your active offer is {pack.selected_offer}." if pack.selected_offer else None,
        "Want me to draft a calm counter-message without discount shouting?",
    )
    return _out(body, "open_ended", "vera_competitor_v1", "Competitor is mentioned only because the trigger explicitly provided competitor details.")


def _merchant_review_theme(pack: EvidencePack) -> dict[str, Any]:
    theme = _fact_after(pack.trigger_facts, "theme")
    count = _fact_after(pack.trigger_facts, "occurrences_30d")
    quote = _fact_after(pack.trigger_facts, "common_quote")
    body = _join(
        f"{pack.salutation()}, review theme spotted: {theme or 'a repeated customer theme'}{f' appeared {count} times in 30d' if count else ''}.",
        f"Common quote: {quote}." if quote else None,
        "Want me to draft a reply and a small operations note for the team?",
    )
    return _out(body, "open_ended", "vera_review_theme_v1", "Review theme trigger with count/quote where provided and a useful next artifact.")


def _merchant_milestone(pack: EvidencePack) -> dict[str, Any]:
    metric = _fact_after(pack.trigger_facts, "metric")
    now = _fact_after(pack.trigger_facts, "value_now")
    milestone = _fact_after(pack.trigger_facts, "milestone_value")
    body = _join(
        f"{pack.salutation()}, you are close to a milestone: {metric or 'metric'} is at {now or 'the current value'}{f' toward {milestone}' if milestone else ''}.",
        "Want me to draft the short thank-you post for recent customers?",
    )
    return _out(body, "open_ended", "vera_milestone_v1", "Milestone trigger celebrates a concrete number and suggests a light customer-facing follow-up.")


def _merchant_account_nudge(pack: EvidencePack) -> dict[str, Any]:
    kind = (pack.raw_trigger_kind or "").lower()
    verified = _fact_after(pack.trigger_facts, "verified")
    uplift = pct_to_text(_fact_after(pack.trigger_facts, "estimated_uplift_pct"))
    days_expired = _fact_after(pack.trigger_facts, "days_since_expiry")
    days_dormant = _fact_after(pack.trigger_facts, "days_since_last_merchant_message")
    perf = _fact_after(pack.trigger_facts, "perf_dip_pct")
    if kind == "gbp_unverified":
        body = _join(
            f"{pack.salutation()}, your Google profile verification is marked {verified or 'not complete'} in context.",
            f"Estimated uplift in trigger: {uplift}." if uplift else None,
            "Want the exact verification checklist?",
        )
    elif kind == "winback_eligible":
        body = _join(
            f"{pack.salutation()}, your plan has been expired for {days_expired} days." if days_expired else f"{pack.salutation()}, winback context is active.",
            f"Performance dip in trigger: {pct_to_text(perf)}." if perf else None,
            "Want me to prepare a short recovery plan before you decide?",
        )
    else:
        body = _join(
            f"{pack.salutation()}, it has been {days_dormant} days since the last merchant reply." if days_dormant else f"{pack.salutation()}, quick account check.",
            "Want me to send one useful account summary instead of a promo nudge?",
        )
    return _out(body, "open_ended", "vera_account_nudge_v1", "Account-state trigger framed as useful operational help, not a generic promo.")


def _merchant_generic(pack: EvidencePack) -> dict[str, Any]:
    why = _first_with(pack.trigger_facts, "trigger kind") or "a fresh trigger is active"
    fact = _first_specific_fact(pack)
    body = _join(
        f"{pack.salutation()}, {why}.",
        fact,
        "Want me to draft the short next step?",
    )
    return _out(body, "open_ended", "vera_grounded_generic_v1", "Generic grounded fallback because the trigger kind was not recognized.")


def _customer_recall_or_winback(pack: EvidencePack, kind: str) -> dict[str, Any]:
    customer = _customer_name(pack)
    service = _fact_after(pack.trigger_facts, "service_due")
    last = _fact_after(pack.trigger_facts, "last_service_date") or _fact_after(pack.customer_facts, "last visit")
    due = _fact_after(pack.trigger_facts, "due_date")
    days = _fact_after(pack.trigger_facts, "days_since_last_visit")
    previous = _fact_after(pack.trigger_facts, "previous_focus")
    offer = pack.selected_offer
    slot_sentence = _slot_sentence(pack)
    if "lapsed" in kind:
        body = _join(
            f"Hi {customer}, {pack.merchant_display_name or 'your merchant'} here.",
            f"It has been {days} days since your last visit." if days else f"Last visit in context: {last}." if last else None,
            f"Your previous focus was {previous}." if previous else None,
            f"{offer} is active right now." if offer else None,
            "Reply YES if you want us to help you restart without pressure.",
        )
        return _out(body, "binary_yes", "merchant_lapsed_customer_v1", "Customer winback uses relationship state and avoids shame or ungrounded claims.")
    body = _join(
        f"Hi {customer}, {pack.merchant_display_name or 'your merchant'} here.",
        f"Your {service.replace('_', ' ') if service else 'recall'} is due." if service or due else None,
        f"Last service date in context: {last}." if last else None,
        f"Due date: {due}." if due else None,
        slot_sentence,
        f"{offer} is active." if offer else None,
        "Reply YES and we'll help you book.",
    )
    return _out(body, "booking_choice" if pack.slots else "binary_yes", "merchant_recall_reminder_v1", "Customer-scoped recall with customer name, due context, slots/offer only when provided.")


def _customer_refill(pack: EvidencePack) -> dict[str, Any]:
    customer = _customer_name(pack)
    molecules = _fact_after(pack.trigger_facts, "molecule_list")
    runout = _fact_after(pack.trigger_facts, "stock_runs_out_iso")
    saved = _fact_after(pack.trigger_facts, "delivery_address_saved")
    offers = "; ".join(pack.active_offers[:2])
    body = _join(
        f"Namaste {customer}, {pack.merchant_display_name or 'your pharmacy'} yahan.",
        f"Your monthly medicines in context: {molecules}." if molecules else None,
        f"Stock runs out: {runout}." if runout else None,
        f"Active pharmacy offers: {offers}." if offers else None,
        "Delivery address is saved." if str(saved).lower() == "true" else None,
        "Reply CONFIRM to dispatch, or tell us if any dosage changed.",
    )
    return _out(body, "binary_yes", "merchant_refill_reminder_v1", "Chronic refill reminder grounded in molecule list, runout date, and active offers only.")


def _customer_appointment(pack: EvidencePack) -> dict[str, Any]:
    customer = _customer_name(pack)
    trial = _fact_after(pack.trigger_facts, "trial_date")
    body = _join(
        f"Hi {customer}, {pack.merchant_display_name or 'your merchant'} here.",
        f"Following up on your trial from {trial}." if trial else "Your appointment reminder is active in our context.",
        _slot_sentence(pack),
        "Reply YES and we'll confirm it.",
    )
    return _out(body, "booking_choice" if pack.slots else "binary_yes", "merchant_appointment_followup_v1", "Appointment/trial follow-up with provided slot evidence only.")


def _customer_wedding(pack: EvidencePack) -> dict[str, Any]:
    customer = _customer_name(pack)
    wedding = _fact_after(pack.trigger_facts, "wedding_date") or _fact_after(pack.customer_facts, "wedding_date")
    days = _fact_after(pack.trigger_facts, "days_to_wedding")
    window = _fact_after(pack.trigger_facts, "next_step_window_open")
    trial = _fact_after(pack.trigger_facts, "trial_completed")
    body = _join(
        f"Hi {customer}, {pack.merchant_display_name or 'your salon'} here.",
        f"{days} days to your wedding" if days else f"Wedding date in context: {wedding}." if wedding else None,
        f"Trial completed: {trial}." if trial else None,
        f"Next step window: {window.replace('_', ' ')}." if window else None,
        "Reply YES and we'll share the short prep plan.",
    )
    return _out(body, "binary_yes", "merchant_bridal_followup_v1", "Bridal customer follow-up uses wedding/trial facts without inventing services or slots.")


def _customer_generic(pack: EvidencePack) -> dict[str, Any]:
    customer = _customer_name(pack)
    fact = _first_specific_fact(pack)
    body = _join(
        f"Hi {customer}, {pack.merchant_display_name or 'your merchant'} here.",
        fact,
        "Reply YES if you'd like us to help with the next step.",
    )
    return _out(body, "binary_yes", "merchant_customer_generic_v1", "Customer-scoped fallback uses only the available relationship and trigger facts.")


def _out(body: str, cta: str, template_name: str, rationale: str) -> dict[str, Any]:
    return {
        "body": body,
        "cta": cta,
        "template_name": template_name,
        "template_params": _template_params(body),
        "rationale": rationale,
    }


def _finalize_body(body: str) -> str:
    body = compact_ws(body)
    body = body.replace("..", ".")
    return truncate(body, 760)


def _join(*parts: str | None) -> str:
    clean: list[str] = []
    for part in parts:
        text = compact_ws(part)
        if not text:
            continue
        if text[-1] not in ".?!":
            text += "."
        clean.append(text)
    return " ".join(clean)


def _template_params(body: str) -> list[str]:
    text = compact_ws(body)
    params: list[str] = []
    while text and len(params) < 3:
        if len(text) <= 160:
            params.append(text)
            break
        cut = text.rfind(" ", 0, 160)
        if cut < 80:
            cut = 160
        params.append(text[:cut].strip())
        text = text[cut:].strip()
    return params


def _digest_field(pack: EvidencePack, key: str) -> str | None:
    if not pack.selected_digest:
        return None
    value = pack.selected_digest.get(key)
    if value in (None, "", [], {}):
        return None
    return compact_ws(str(value))


def _fact_after(facts: list[str], key: str) -> str | None:
    prefix = f"{key}:"
    for fact in facts:
        if fact.lower().startswith(prefix.lower()):
            return compact_ws(fact.split(":", 1)[1])
    return None


def _fact_contains(facts: list[str], key: str) -> str | None:
    key_l = key.lower()
    for fact in facts:
        if key_l in fact.lower():
            return fact
    return None


def _first_with(facts: list[str], text: str) -> str | None:
    text_l = text.lower()
    for fact in facts:
        if text_l in fact.lower():
            return fact
    return None


def _first_specific_fact(pack: EvidencePack) -> str | None:
    for group in (pack.trigger_facts, pack.performance_facts, pack.offer_facts, pack.digest_facts, pack.peer_facts, pack.customer_facts):
        for fact in group:
            if any(char.isdigit() for char in fact) or "source:" in fact.lower() or "active offer" in fact.lower():
                return fact
    return None


def _merchant_signal_phrase(pack: EvidencePack) -> str | None:
    high_risk = _fact_after(pack.performance_facts, "customer_aggregate high_risk_adult_count")
    if high_risk:
        return f"Your context shows {high_risk} high-risk adult customers."
    active_members = _fact_after(pack.performance_facts, "customer_aggregate total_active_members")
    if active_members:
        return f"Your context shows {active_members} active members."
    chronic = _fact_after(pack.performance_facts, "customer_aggregate chronic_rx_count")
    if chronic:
        return f"Your context shows {chronic} chronic-Rx customers."
    locality = pack.locality or pack.city
    if pack.category_slug and locality:
        return f"This fits your {locality} context."
    if pack.category_slug:
        return f"This fits your category context."
    return None


def _customer_name(pack: EvidencePack) -> str:
    name = _fact_after(pack.customer_facts, "customer name")
    if name and "walk-in" not in name.lower() and "no profile" not in name.lower():
        return name
    return "there"


def _slot_sentence(pack: EvidencePack) -> str | None:
    if not pack.slots:
        return None
    if len(pack.slots) == 1:
        return f"Available slot in context: {pack.slots[0]}."
    choices = " or ".join(pack.slots[:2])
    return f"Available slots in context: {choices}."

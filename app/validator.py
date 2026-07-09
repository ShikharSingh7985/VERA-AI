from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .evidence import EvidencePack
from .utils import money_values, normalize_text

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - used only if rapidfuzz is unavailable
    fuzz = None


SUSPICIOUS_CLAIMS = [
    "guaranteed",
    "miracle",
    "100% safe",
    "completely cure",
    "best in city",
    "viral guarantee",
    "guaranteed packed house",
    "guaranteed weight loss",
    "fastest results",
    "shred in 7 days",
]

SOURCE_KEYWORDS = [
    "jida",
    "dci",
    "ida",
    "cdsco",
    "dcgi",
    "fda india",
    "gst council",
    "icmr",
    "google trends",
    "zomato",
    "swiggy",
    "magicpin",
    "dental council",
    "l'oreal",
    "practo",
]


@dataclass
class ValidationResult:
    valid: bool
    reasons: list[str] = field(default_factory=list)


def validate_action(action: dict[str, Any], evidence_pack: EvidencePack, conversation_history: list[str] | None = None) -> ValidationResult:
    reasons: list[str] = []
    body = str(action.get("body") or "").strip()
    cta = str(action.get("cta") or "")
    if not body:
        reasons.append("empty body")
    if len(body) < 20:
        reasons.append("body too short")
    if len(body) > 900:
        reasons.append("body too long")
    if re.search(r"https?://|www\.", body, flags=re.I):
        reasons.append("url present")
    if repeated_message(body, conversation_history or []):
        reasons.append("repeated body in conversation")
    cta_count = count_ctas(body)
    allowed_cta_count = 2 if cta == "booking_choice" else 1
    if cta_count > allowed_cta_count:
        reasons.append("too many strong CTAs")
    taboo = contains_taboo(body, evidence_pack.taboo_words)
    if taboo:
        reasons.append(f"taboo word: {taboo}")
    fake_claim = contains_suspicious_fake_claims(body, evidence_pack)
    if fake_claim:
        reasons.append(f"suspicious claim: {fake_claim}")
    if not has_grounded_number_check(body, evidence_pack):
        reasons.append("ungrounded rupee price")
    source_issue = ungrounded_source_mention(body, evidence_pack)
    if source_issue:
        reasons.append(f"ungrounded source: {source_issue}")
    if cta not in {"open_ended", "binary_yes", "booking_choice", "none"}:
        reasons.append("invalid cta")
    return ValidationResult(valid=not reasons, reasons=reasons)


def count_ctas(body: str) -> int:
    normalized = normalize_text(body)
    patterns = [
        r"\bwant me to\b",
        r"\breply yes\b",
        r"\breply confirm\b",
        r"\bconfirm to\b",
        r"\bshould i\b",
        r"\bshall i\b",
        r"\bcan i\b",
        r"\bwould you like\b",
        r"\bwhat should i\b",
        r"\breply 1\b",
        r"\breply 2\b",
    ]
    count = 0
    for pattern in patterns:
        if re.search(pattern, normalized):
            count += 1
    question_marks = body.count("?")
    if question_marks and count == 0:
        count = question_marks
    return count


def contains_taboo(body: str, taboo_words: list[str]) -> str | None:
    normalized = normalize_text(body)
    for word in taboo_words:
        normalized_word = normalize_text(word)
        if normalized_word and normalized_word in normalized:
            return word
    return None


def contains_suspicious_fake_claims(body: str, evidence_pack: EvidencePack | None = None) -> str | None:
    normalized = normalize_text(body)
    for phrase in SUSPICIOUS_CLAIMS:
        if normalize_text(phrase) in normalized:
            allowed = False
            if evidence_pack:
                allowed_blob = normalize_text(" ".join(evidence_pack.allowed_sources | evidence_pack.allowed_numbers))
                allowed = normalize_text(phrase) in allowed_blob
            if not allowed:
                return phrase
    return None


def repeated_message(body: str, conversation_history: list[str]) -> bool:
    normalized = normalize_text(body)
    if not normalized:
        return False
    for existing in conversation_history:
        existing_norm = normalize_text(existing)
        if not existing_norm:
            continue
        if existing_norm == normalized:
            return True
        if fuzz is not None and fuzz.ratio(existing_norm, normalized) >= 96:
            return True
    return False


def has_grounded_number_check(body: str, evidence_pack: EvidencePack) -> bool:
    prices = money_values(body)
    if not prices:
        return True
    allowed = {str(value).replace(",", "").strip() for value in evidence_pack.allowed_prices}
    return all(price in allowed for price in prices)


def ungrounded_source_mention(body: str, evidence_pack: EvidencePack) -> str | None:
    normalized = normalize_text(body)
    allowed = normalize_text(" ".join(evidence_pack.allowed_sources))
    for keyword in SOURCE_KEYWORDS:
        key = normalize_text(keyword)
        if key in normalized and key not in allowed:
            return keyword
    return None


def strip_forbidden_phrases(body: str, evidence_pack: EvidencePack) -> str:
    cleaned = body
    for phrase in list(evidence_pack.taboo_words) + SUSPICIOUS_CLAIMS:
        if not phrase:
            continue
        cleaned = re.sub(re.escape(phrase), "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

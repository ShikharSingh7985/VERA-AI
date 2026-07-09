from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def stable_hash(*parts: Any, length: int = 10) -> str:
    raw = "|".join(str(part) for part in parts if part is not None)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]


def safe_slug(value: str | None, fallback: str = "x") -> str:
    text = normalize_text(value or "")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or fallback


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = text.replace("dont", "don't")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def compact_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def truncate(value: str | None, max_chars: int = 220) -> str:
    text = compact_ws(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def get_in(data: dict[str, Any] | None, path: Iterable[str], default: Any = None) -> Any:
    cur: Any = data or {}
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def money_values(text: str | None) -> set[str]:
    if not text:
        return set()
    values = set()
    for match in re.findall(r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*)", str(text), flags=re.I):
        values.add(match.replace(",", ""))
    for match in re.findall(r"@\s*(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*)", str(text), flags=re.I):
        values.add(match.replace(",", ""))
    return values


def number_values(text: str | None) -> set[str]:
    if not text:
        return set()
    values = set()
    for match in re.findall(r"(?<![a-zA-Z])[-+]?\d+(?:\.\d+)?%?", str(text)):
        values.add(match)
        values.add(match.replace("%", ""))
    return values


def pct_to_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) <= 1:
        number *= 100
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.0f}%"


def json_dumps(data: Any, max_chars: int | None = None) -> str:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    if max_chars and len(text) > max_chars:
        return text[: max_chars - 1] + "..."
    return text


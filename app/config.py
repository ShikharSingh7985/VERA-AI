from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from dotenv import load_dotenv

load_dotenv()


STARTED_AT = datetime.now(timezone.utc)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int, low: int | None = None, high: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError:
        value = default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _list_env(name: str) -> List[str]:
    raw = os.getenv(name, "")
    parts = raw.replace(";", ",").split(",")
    return [part.strip() for part in parts if part.strip()]


@dataclass(frozen=True)
class Settings:
    team_name: str
    team_members: list[str]
    contact_email: str
    bot_version: str
    gemini_model: str
    google_api_key: str | None
    use_llm: bool
    llm_timeout_seconds: int
    max_actions_per_tick: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            team_name=os.getenv("TEAM_NAME", "Vera Signal Engine"),
            team_members=_list_env("TEAM_MEMBERS") or ["Challenge Team"],
            contact_email=os.getenv("CONTACT_EMAIL", "team@example.com"),
            bot_version=os.getenv("BOT_VERSION", "1.0.0"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            google_api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
            use_llm=_bool_env("USE_LLM", True),
            llm_timeout_seconds=_int_env("LLM_TIMEOUT_SECONDS", 8, low=1, high=25),
            max_actions_per_tick=_int_env("MAX_ACTIONS_PER_TICK", 5, low=0, high=20),
        )


settings = Settings.from_env()


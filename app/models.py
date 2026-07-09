from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Scope = Literal["category", "merchant", "customer", "trigger"]
SendAs = Literal["vera", "merchant_on_behalf"]
CTA = Literal["open_ended", "binary_yes", "booking_choice", "none"]


class LooseModel(BaseModel):
    class Config:
        extra = "allow"


class ContextRequest(LooseModel):
    scope: Scope
    context_id: str = Field(min_length=1)
    version: int = Field(ge=0)
    payload: dict[str, Any]
    delivered_at: str | None = None


class TickRequest(LooseModel):
    now: str | None = None
    available_triggers: list[str] = Field(default_factory=list)


class ReplyRequest(LooseModel):
    conversation_id: str = Field(min_length=1)
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str = "merchant"
    message: str = ""
    received_at: str | None = None
    turn_number: int | None = None


class TickAction(LooseModel):
    conversation_id: str
    merchant_id: str
    customer_id: str | None = None
    send_as: SendAs
    trigger_id: str
    template_name: str
    template_params: list[str] = Field(default_factory=list)
    body: str
    cta: CTA
    suppression_key: str
    rationale: str


class ReplyResponse(LooseModel):
    action: Literal["send", "wait", "end"]
    body: str | None = None
    cta: CTA | None = None
    wait_seconds: int | None = None
    rationale: str


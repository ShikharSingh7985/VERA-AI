from __future__ import annotations

from .evidence import EvidencePack
from .utils import json_dumps


SYSTEM_PROMPT = """You are Vera, magicpin's merchant engagement assistant.
You write short WhatsApp-style business messages.
Your goal is decision quality, specificity, category fit, merchant fit, and engagement compulsion.

Rules:
- Use only the evidence provided.
- Never invent facts, sources, prices, dates, slots, metrics, offers, competitors, medical claims, or customer details.
- If evidence is thin, write a restrained message using only the available facts.
- Respect the category voice and taboo words.
- Mention why now.
- Use one clear CTA only.
- No URLs.
- No fake source citations.
- Return JSON only.
"""


def build_user_prompt(pack: EvidencePack, audience: str) -> str:
    schema = {
        "body": "short WhatsApp message",
        "cta": "open_ended | binary_yes | booking_choice | none",
        "template_name": "short_snake_case_name",
        "template_params": ["important body snippets only"],
        "rationale": "one sentence explaining why this trigger is worth sending",
    }
    instructions = {
        "audience": audience,
        "length_guidance": "Aim for 280-600 characters if the evidence supports it; shorter is OK when evidence is thin.",
        "style": [
            "strong first line",
            "one concrete fact early",
            "one clear final CTA",
            "merchant/customer language preference when available",
            "operator/peer tone, not promotional hype",
        ],
        "do_not_use": [
            "generic grow your business",
            "unguarded medical or safety claims",
            "new prices or discounts not in evidence",
            "competitor names unless evidence includes them",
            "more than one ask",
        ],
        "output_schema": schema,
    }
    return (
        "Compose the next Vera message from this evidence pack.\n\n"
        f"Instructions:\n{json_dumps(instructions)}\n\n"
        f"Evidence:\n{json_dumps(pack.to_prompt_dict(), max_chars=12000)}\n\n"
        "Return only valid JSON. Do not wrap it in markdown."
    )

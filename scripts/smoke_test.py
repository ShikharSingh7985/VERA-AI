from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx


BOT_URL = os.getenv("BOT_URL", "http://localhost:8080").rstrip("/")


CATEGORY = {
    "slug": "dentists",
    "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed", "miracle", "100% safe"]},
    "offer_catalog": [{"id": "den_001", "title": "Dental Cleaning @ Rs 299", "value": "299"}],
    "peer_stats": {"avg_ctr": 0.030, "avg_calls_30d": 12, "scope": "metro_solo_practices_2026"},
    "digest": [
        {
            "id": "d_2026W17_jida_fluoride",
            "kind": "research",
            "title": "3-month fluoride recall outperforms 6-month for high-risk adult caries",
            "source": "JIDA Oct 2026, p.14",
            "trial_n": 2100,
            "summary": "Indian trial shows 38% lower caries recurrence with 3-month vs 6-month recall in high-risk adults.",
            "actionable": "Reassess recall interval for adults flagged high-risk in charting",
        }
    ],
}


MERCHANT = {
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "category_slug": "dentists",
    "identity": {
        "name": "Dr. Meera's Dental Clinic",
        "city": "Delhi",
        "locality": "Lajpat Nagar",
        "languages": ["en", "hi"],
        "owner_first_name": "Meera",
    },
    "performance": {"window_days": 30, "views": 2410, "calls": 18, "ctr": 0.021, "delta_7d": {"calls_pct": -0.05}},
    "offers": [{"id": "o1", "title": "Dental Cleaning @ Rs 299", "status": "active"}],
    "customer_aggregate": {"high_risk_adult_count": 124, "lapsed_180d_plus": 78},
    "signals": ["ctr_below_peer_median", "high_risk_adult_cohort", "engaged_in_last_48h"],
    "conversation_history": [{"from": "merchant", "body": "Yes please", "engagement": "merchant_replied"}],
}


TRIGGER = {
    "id": "trg_001_research_digest_dentists",
    "scope": "merchant",
    "kind": "research_digest",
    "source": "external",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "customer_id": None,
    "payload": {"category": "dentists", "top_item_id": "d_2026W17_jida_fluoride"},
    "urgency": 3,
    "suppression_key": "research:dentists:2026-W17",
    "expires_at": "2099-05-03T00:00:00Z",
}


def post(client: httpx.Client, path: str, body: dict) -> dict:
    response = client.post(f"{BOT_URL}{path}", json=body, timeout=15)
    print(path, response.status_code)
    data = response.json()
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return data


def push_context(client: httpx.Client, scope: str, context_id: str, payload: dict) -> None:
    post(
        client,
        "/v1/context",
        {
            "scope": scope,
            "context_id": context_id,
            "version": 1,
            "payload": payload,
            "delivered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
    )


def main() -> None:
    with httpx.Client() as client:
        print("BOT_URL =", BOT_URL)
        print("healthz")
        print(client.get(f"{BOT_URL}/v1/healthz", timeout=5).json())
        print("metadata")
        print(client.get(f"{BOT_URL}/v1/metadata", timeout=5).json())
        post(client, "/v1/teardown", {})
        push_context(client, "category", "dentists", CATEGORY)
        push_context(client, "merchant", MERCHANT["merchant_id"], MERCHANT)
        push_context(client, "trigger", TRIGGER["id"], TRIGGER)
        tick = post(client, "/v1/tick", {"now": "2026-04-26T10:35:00Z", "available_triggers": [TRIGGER["id"]]})
        actions = tick.get("actions", [])
        if actions:
            conv_id = actions[0]["conversation_id"]
            post(
                client,
                "/v1/reply",
                {
                    "conversation_id": conv_id,
                    "merchant_id": MERCHANT["merchant_id"],
                    "customer_id": None,
                    "from_role": "merchant",
                    "message": "Yes please send it",
                    "received_at": "2026-04-26T10:40:00Z",
                    "turn_number": 2,
                },
            )


if __name__ == "__main__":
    main()


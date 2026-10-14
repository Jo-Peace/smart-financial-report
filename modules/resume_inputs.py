"""
Deterministic /1 inputs saved next to the daily report so the resume script
(scripts/maintenance/resume_daily_persist.py) can rebuild structured_data with
the same local facts and 驗收 results instead of letting Gemini guess them.

Schema 2 also stores the raw local payloads finalize_structured_data reads
(market_data / institutional_data / volume_data / value_ranking, plus
similar_sessions and the report fact check), so the resumed role_score_inputs, local_market_facts
and hero-card numbers match what /1 would have written.
"""

from __future__ import annotations

import json
import os

from modules.atomic_io import write_json_atomic
from modules.report_paths import daily_dir

RESUME_INPUTS_FILENAME = "resume_inputs.json"
SCHEMA_VERSION = 2


def resume_inputs_path(reports_dir: str, date_str: str) -> str:
    return os.path.join(daily_dir(reports_dir, date_str), RESUME_INPUTS_FILENAME)


def save_resume_inputs(
    reports_dir: str,
    date_str: str,
    *,
    local_facts=None,
    validation_results=None,
    validation_meta=None,
    editorial_frame=None,
    supply_chain_event_radar=None,
    market_data=None,
    institutional_data=None,
    volume_data=None,
    similar_sessions=None,
    fact_check=None,
    value_ranking=None,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": date_str,
        "local_facts": local_facts or {},
        "validation_results": validation_results or [],
        "validation_meta": validation_meta or {},
        "editorial_frame": editorial_frame,
        "supply_chain_event_radar": supply_chain_event_radar or [],
        "payloads": {
            # Failed analysis keys stay None (prompts say 「數據無法取得」).
            "market_data": {
                str(symbol): (dict(row) if isinstance(row, dict) else None)
                for symbol, row in (market_data or {}).items()
            },
            "institutional_data": dict(institutional_data or {}),
            "volume_data": [dict(row) for row in (volume_data or []) if isinstance(row, dict)],
            "similar_sessions": similar_sessions,
            "fact_check": fact_check,
            "value_ranking": value_ranking if isinstance(value_ranking, dict) else None,
        },
    }
    return write_json_atomic(resume_inputs_path(reports_dir, date_str), payload, default=str)


def saved_payloads(saved: dict | None) -> dict:
    """
    finalize_structured_data keyword inputs from a loaded resume_inputs.json:
    {market_data, institutional_data, volume_data, similar_sessions, fact_check,
    value_ranking}.
    Empty for schema 1 files (no payloads saved) or a missing file.
    """
    payloads = (saved or {}).get("payloads")
    if not isinstance(payloads, dict):
        return {}
    out = {}
    market = payloads.get("market_data")
    if isinstance(market, dict) and market:
        out["market_data"] = market
    institutional = payloads.get("institutional_data")
    if isinstance(institutional, dict) and institutional:
        out["institutional_data"] = institutional
    volume = payloads.get("volume_data")
    if isinstance(volume, list) and volume:
        out["volume_data"] = volume
    if isinstance(payloads.get("similar_sessions"), list):
        out["similar_sessions"] = payloads["similar_sessions"]
    if isinstance(payloads.get("fact_check"), dict):
        out["fact_check"] = payloads["fact_check"]
    if isinstance(payloads.get("value_ranking"), dict) and payloads["value_ranking"].get("stocks"):
        out["value_ranking"] = payloads["value_ranking"]
    return out


def load_resume_inputs(reports_dir: str, date_str: str) -> dict | None:
    path = resume_inputs_path(reports_dir, date_str)
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or str(payload.get("date") or "") != str(date_str):
        return None
    return payload

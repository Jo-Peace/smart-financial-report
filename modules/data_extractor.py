import json
import os
import re

from modules.gemini_client import default_model_name, generate_json
from modules.structured_facts import (
    format_ground_truth_block,
    merge_structured_with_local_facts,
)


def _loads_json_object(raw: str):
    """Parse Gemini JSON; tolerate trailing extra text / multiple objects."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty JSON response")
    # Strip markdown fences if model ignored mime type.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_err:
        # First complete object only (handles "Extra data: line N").
        decoder = json.JSONDecoder()
        try:
            obj, _end = decoder.raw_decode(text)
            return obj
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise first_err


# Narrative fields only — factual leaderboard fields are force-merged from local_facts.
NARRATIVE_SCHEMA = """
SCHEMA (narrative fields only — return ONLY these keys):
- "sectors": object. keys = sector names, values = arrays of stock names/statuses.
  * Differentiate CPO vs IPC / memory vs packaging when the report does.
- "ai_data_picks": array of 1-2 strings (e.g. ["華邦電 (外資大買)"]). If none: ["今日無安全推薦標的"].
- "prediction_targets": array (max 2) of objects ONLY from the 〔明日觀察焦點〕 section
  (not from 【今日深度押注】 or other sections):
  { "id": "股票代號", "name": "公司名", "direction": "多/空", "trigger": "理由摘要",
    "stop_loss_price": number|null, "stop_loss_desc": "停損條件" }
  If none: [].
- "price_volume_divergence": array of strings (量價/法人背離). If none: [].
- "conservative_strategy": brief string
- "aggressive_strategy": brief string

Do NOT invent or re-rank factual market fields (date, index_action, heavyweights_dumped,
safe_havens_bought, commodities, prev_day_picks_result). Those come from LOCAL MARKET GROUND TRUTH.
"""


def extract_structured_data(api_key, report_content, local_facts=None):
    """
    Extract narrative structured fields from the daily Markdown report via Gemini,
    then force-merge local market facts (TWSE/yfinance).
    """
    local_facts = local_facts or {}
    model_name = default_model_name()
    ground_truth = format_ground_truth_block(local_facts)

    # Keep extract prompt tight: ground truth for consistency + narrative schema only.
    prompt = f"""You are a precise financial report extractor.
Return ONLY a valid JSON object with the narrative schema keys below.
Never invent stocks, sectors, or numbers not present in the report.

{ground_truth}

{NARRATIVE_SCHEMA}

--- REPORT START ---
{report_content}
--- REPORT END ---
"""

    try:
        raw = generate_json(
            api_key,
            prompt,
            task_name="structured_data_extraction",
            model_name=model_name,
        )
        data = _loads_json_object(raw)
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        if not isinstance(data, dict):
            return {
                "error": "Failed to extract structured data.",
                "raw_error": f"unexpected type: {type(data).__name__}",
            }
        merged = merge_structured_with_local_facts(data, local_facts)
        # Local rule, not the model's: targets only from 〔觀察焦點〕, no mega-caps, max 2.
        from modules.prediction_targets import apply_prediction_target_filter

        return apply_prediction_target_filter(merged, report_content)
    except Exception as e:
        print(f"Error extracting structured data: {e}")
        return {
            "error": "Failed to extract structured data.",
            "raw_error": str(e),
        }


def extract_weekend_structured_data(api_key, english_report_content):
    """
    Extract weekend special meta-narrative points into structured JSON.
    """
    model_name = default_model_name()

    prompt = f"""You are a precise data extraction AI.
Read the following TRADITIONAL CHINESE financial report and extract key data into STRUCTURED JSON.

RULES:
1. Extract exactly what is mentioned.
2. Be descriptive but concise.
3. Return ONLY a valid JSON object.

SCHEMA:
- "date": YYYY-MM-DD
- "us_market_hook": 1-2 sentences on US market impact
- "geopolitical_impact": 1-2 sentences on geopolitics/macro
- "safe_havens": array of strings
- "taiwan_monday_defense": 1-2 sentences defensive posture
- "taiwan_monday_vulture": 1-2 sentences aggressive dip-buy posture

--- REPORT START ---
{english_report_content}
--- REPORT END ---
"""

    try:
        raw = generate_json(
            api_key,
            prompt,
            task_name="weekend_structured_data_extraction",
            model_name=model_name,
        )
        data = _loads_json_object(raw)
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            return {
                "error": "Failed to extract weekend structured data.",
                "raw_error": f"unexpected type: {type(data).__name__}",
            }
        return data
    except Exception as e:
        print(f"Error extracting weekend structured data: {e}")
        return {
            "error": "Failed to extract weekend structured data.",
            "raw_error": str(e),
        }

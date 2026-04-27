import os
import google.generativeai as genai
import datetime
import json

def extract_structured_data(api_key, report_content):
    """
    Extracts structured JSON data from the daily Markdown report to prevent 
    AI hallucinations (like mixing up sectors) in the next step.
    """
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    
    # We must enforce JSON output in the prompt
    model = genai.GenerativeModel(
        model_name,
        generation_config={"response_mime_type": "application/json"}
    )
    
    prompt = f"""
    You are a precise data extraction AI. Read the following financial report and extract the key data into a STRUCTURED JSON format.
    
    RULES:
    1. NEVER invent data, companies, or sectors. Only extract what is specifically mentioned.
    2. Be highly specific about WHICH stocks belong to WHICH sectors (e.g., separating Memory from CPO from IPC).
    3. Return ONLY a valid JSON object matching the schema below.
    
    SCHEMA EXPLANATION:
    - `"date"`: The date of the report (YYYY-MM-DD)
    - `"index_action"`: Very brief summary of the main index movement (e.g. "下跌 73.4點")
    - `"heavyweights_dumped"`: Array of strings. E.g. ["台積電 (-206.5億)", "0050 (-58.1億)"]
    - `"safe_havens_bought"`: Array of strings. E.g. ["群創 (+33.5億)"]
    - `"sectors"`: A dictionary where keys are sector names and values are arrays of stock names/statuses.
        * IMPORTANT: Differentiate CPO and IPC if the report states Advantech is IPC or industrial AI.
    - `"ai_data_picks"`: Array of strings representing the 1-2 stock picks deduced from data (e.g. ["華邦電 (外資大買)"]). If the report says no reliable picks, return ["今日無安全推薦標的"].
    - `"prediction_targets"`: [優化 B] Array of objects for TOMORROW's watch list. Extract from the 「明日觀察焦點」section.
        Each object: {{ "id": "股票代號(數字)", "name": "公司名稱", "direction": "多/空", "trigger": "推薦理由摘要", "stop_loss_price": 停損價格(數字，找不到則為null), "stop_loss_desc": "停損條件描述(e.g. 收盤跌破MA20)" }}
        If no targets: return [].
    - `"prev_day_picks_result"`: Array of strings. EXPLICITLY extract the results from the "昨日預測驗收" section. You MUST include specific numbers (like +6.3% or 3.17億股). E.g. ["華邦電 (+6.3%, 預測成立)", "台玻 (加碼27.2億, 預測成立)"]. If none, return [].
    - `"price_volume_divergence"`: Array of strings. Extract any price-volume or institutional-divergence phenomena mentioned (e.g. "股價噴漲76%但外資同步賣超12.3億"). If none, return [].
    - `"commodities"`: Any commodity movements (e.g. ["黃金漲X%", "白銀漲Y%"])
    - `"conservative_strategy"`: Brief summary of the conservative strategy recommendation
    - `"aggressive_strategy"`: Brief summary of the aggressive strategy recommendation
    
    --- START OF TODAY'S REPORT ---
    {report_content}
    --- END OF TODAY'S REPORT ---
    """
    
    try:
        response = model.generate_content(prompt)
        # Verify it's parsable JSON
        data = json.loads(response.text)
        if isinstance(data, list) and len(data) > 0:
            data = data[0]
        return data
    except Exception as e:
        print(f"Error extracting structured data: {e}")
        # Return a safe fallback structure
        return {
            "error": "Failed to extract structured data.",
            "raw_error": str(e)
        }

def extract_weekend_structured_data(api_key, english_report_content):
    """
    Extracts the key meta-narrative points from the English Weekend Special Report
    into a structured JSON for the Podcast generator.
    """
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    
    model = genai.GenerativeModel(
        model_name,
        generation_config={"response_mime_type": "application/json"}
    )
    
    prompt = f"""
    You are a precise data extraction AI. Read the following TRADITIONAL CHINESE financial report and extract the key data into a STRUCTURED JSON format.
    
    RULES:
    1. Extract exactly what is mentioned in the report.
    2. Be descriptive but concise in your summaries.
    3. Return ONLY a valid JSON object matching the schema below.
    
    SCHEMA EXPLANATION:
    - `"date"`: The date of the report (YYYY-MM-DD)
    - `"us_market_hook"`: 1-2 sentences summarizing the US stock market drop/bloodbath and tech damage.
    - `"geopolitical_impact"`: 1-2 sentences linking the market movement to the war/geopolitics.
    - `"safe_havens"`: Array of strings. e.g. ["黃金大漲至 X", "原油狂飆至 Y"]
    - `"taiwan_monday_defense"`: 1-2 sentences summarizing the defensive posture for Taiwan's open.
    - `"taiwan_monday_vulture"`: 1-2 sentences summarizing the aggressive 'vulture' dip-buying strategy.
    
    --- START OF TODAY'S WEEKEND CHINESE REPORT ---
    {english_report_content}
    --- END OF TODAY'S WEEKEND CHINESE REPORT ---
    """
    
    try:
        response = model.generate_content(prompt)
        data = json.loads(response.text)
        return data
    except Exception as e:
        print(f"Error extracting weekend structured data: {e}")
        return {
            "error": "Failed to extract weekend structured data.",
            "raw_error": str(e)
        }

import os
import google.generativeai as genai
import datetime

def generate_notebooklm_prompt(api_key, structured_data, date_str=None):
    """
    Generates a NotebookLM Podcast Prompt based on the EXTRACTED STRUCTURED DATA.
    
    Strategy: Provide the exact JSON structure to the AI and demand it uses
    THESE exact classifications for sectors and stocks.
    """
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    model = genai.GenerativeModel(model_name)
    
    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
    # Format the sectors specifically for the prompt
    sectors_formatted = ""
    for sector, stocks in structured_data.get("sectors", {}).items():
        sectors_formatted += f"  - [Sector: {sector}]: {', '.join(stocks)}\n"
    
    prompt = f"""You are an expert Podcast Script Writer. Your goal is to generate a PROMPT for Google NotebookLM to create a 2-host podcast.

--- TODAY'S VERIFIED DATA (DO NOT DEVIATE LONG OR INVENT) ---
Date: {structured_data.get('date', date_str)}
Index Action: {structured_data.get('index_action', 'N/A')}
Heavyweights Dumped: {', '.join(structured_data.get('heavyweights_dumped', []))}
Safe Havens Bought: {', '.join(structured_data.get('safe_havens_bought', []))}
Commodities: {', '.join(structured_data.get('commodities', []))}

SECTORS AND STOCKS (CRITICAL: DO NOT MIX THESE UP):
{sectors_formatted}

Conservative Strategy: {structured_data.get('conservative_strategy', 'N/A')}
Aggressive Strategy: {structured_data.get('aggressive_strategy', 'N/A')}
------------------------------------------------------------

**Your task**: Based STRICTLY on the JSON data above, generate a NotebookLM Audio Overview prompt. 

CRITICAL RULES FOR WRITING THIS PROMPT:
1. SECTOR ACCURACY IS PARAMOUNT. If a stock (like Advantech 研華) is listed under "Industrial Computer (IPC)", DO NOT call it a Silicon Photonics (CPO) stock. Keep them strictly separated as the data dictates.
2. Keep the structure to exactly 3 main segments + 1 ad-read. NotebookLM cuts content when there are too many segments.
3. For MUST-INCLUDE items, write them as EXAMPLE DIALOGUE LINES (e.g., "Host 2 says: '...'").
4. Bind commodity data (gold/oil/silver) INTO an existing segment's dialogue.

Now generate the prompt following this EXACT template, filling ALL placeholders with data above:

---

# NotebookLM Podcast Prompt: {date_str} 台股盤後分析

You are producing a daily financial podcast for Taiwan stock market investors. Your tone should be energetic, professional yet accessible, and engaging.

**Host Dynamics:**
- **Host 1 (The Anchor):** Experienced, data-driven, and likes to connect today's market events with past historical lessons.
- **Host 2 (The Color Commentator):** Curious, relatable to retail investors, asks smart questions, and translates complex financial jargon into simple concepts.

**Today's Focus & Episode Flow (CRITICAL — only 3 segments + ad):**

**1. The Hook & Institutional Data (0:00 - 1:30)**
- **CRITICAL OPENING SLOGAN**: Host 1 MUST start by saying: Welcome to **"AI 帶你看股市，只看數據，不看情緒"**
- Host 1 mentions the index action: "{structured_data.get('index_action', 'N/A')}"
- Host 1 calls out heavyweights being dumped: Mentioning specifically {', '.join(structured_data.get('heavyweights_dumped', []))}.
- Host 2 reacts with surprise and asks about the contrarian buy-ins into safe havens: {', '.join(structured_data.get('safe_havens_bought', []))}
- Host 1 explains the risk-off capital rotation logic.

**AD-READ (around 1:15):**
- Host 1 naturally says: "Quick interruption! If you find this daily AI financial analysis helpful, our system is currently in open beta for FREE. Check the link in the description to generate a custom report for your own stock portfolio! Remember, it's for reference only, not financial advice. Now, back to the market..."

**2. Technical Damage & Sector Rotation (1:30 - 3:30)**
- **MUST-INCLUDE Sector Analysis** — Host 1 MUST list the strong performing sectors based EXACTLY on this data:
{sectors_formatted}
- Host 1 MUST NOT mix up the sectors. Example dialogue: "While Memory stocks like X are rebounding, we are also seeing distinct strength in Industrial Computers like Y..."
- **MUST-INCLUDE Commodities in dialogue** — Host 2 MUST say something like: "And looking globally, commodities like {', '.join(structured_data.get('commodities', []))} are surging, confirming risk-off behavior."

**3. Strategy & Closing (3:30 - End)**
- **Conservative strategy**: Host 1 advises: {structured_data.get('conservative_strategy', 'N/A')}
- **Aggressive strategy**: Host 1 advises: {structured_data.get('aggressive_strategy', 'N/A')}
- **CRITICAL CLOSING SLOGAN**: Host 1 or 2 MUST end with: "And remember our rule: **AI 帶你看股市，只看數據，不看情緒**. See you next time!"

---

**ABSOLUTE RULES (non-negotiable):**
1. Every data point and stock name MUST match exactly.
2. DO NOT hallucinate sector associations. Stick to the list provided.
3. Preserve Chinese financial terms.
4. Write HOST DIALOGUE EXAMPLES for every MUST-INCLUDE item. NotebookLM needs example lines to follow.

Output ONLY the final NotebookLM prompt.
"""
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating NotebookLM prompt: {e}"


def generate_weekend_special_prompt(api_key, structured_data, date_str=None):
    """
    Generates a NotebookLM Podcast Prompt customized for the Weekend Special (US Market & War Impact).
    CRITICAL: Although the structural JSON is in English, the final Podcast Prompt instructs 
    the AI hosts to speak in Traditional Chinese for the Taiwan audience.
    """
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    model = genai.GenerativeModel(model_name)
    
    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
    prompt = f"""You are an expert Podcast Script Writer. Your goal is to generate a PROMPT for Google NotebookLM to create a 2-host weekend special podcast.

--- TODAY'S VERIFIED WEEKEND DATA (DO NOT DEVIATE) ---
Date: {structured_data.get('date', date_str)}
US Market Drop Snapshot: {structured_data.get('us_market_hook', 'N/A')}
Geopolitical & War Impact: {structured_data.get('geopolitical_impact', 'N/A')}
Commodities (Safe Havens): {', '.join(structured_data.get('safe_havens', []))}
Monday Taiwan Tactics - Defense: {structured_data.get('taiwan_monday_defense', 'N/A')}
Monday Taiwan Tactics - Vulture/Dip Buying: {structured_data.get('taiwan_monday_vulture', 'N/A')}
------------------------------------------------------------

**Your task**: Based STRICTLY on the JSON data above, generate a NotebookLM Audio Overview prompt. 

CRITICAL RULES FOR WRITING THIS PROMPT:
1. THE FINAL PODCAST SCRIPT INSTRUCTIONS MUST DEMAND TRADITIONAL CHINESE DIALOGUE.
2. Keep the structure to exactly 3 main segments + 1 ad-read. 
3. For MUST-INCLUDE items, write them as EXAMPLE DIALOGUE LINES (e.g., "Host 2 says: '...'").

Now generate the prompt following this EXACT template, filling ALL placeholders with data above (translated into lively Mandarin):

---

# NotebookLM Podcast Prompt: {date_str} 美股血洗與週末戰況特輯

You are producing a weekend special financial podcast for Taiwan stock market investors. Your tone should be serious yet engaging, focusing on survival and macro strategy.

**Host Dynamics:**
- **Host 1 (The Anchor):** Experienced macro strategist. Focuses on the US drop and global capital flows.
- **Host 2 (The Color Commentator):** Anxious retail investor representative, asking "What does this mean for Monday's open?"

**Episode Flow (CRITICAL — only 3 segments + ad):**

**1. The Hook & The Wall Street Bloodbath (0:00 - 1:30)**
- **CRITICAL OPENING SLOGAN**: Host 1 MUST start by saying: Welcome to **"AI 帶你看股市週末特輯：追蹤金錢猛獸"**
- Host 1 immediately addresses the US market drop: Translate [{structured_data.get('us_market_hook', 'N/A')}] into a dramatic Mandarin dialogue line.
- Host 2 reacts with fear/concern about tech stocks (NVDA, TSMC ADR).

**AD-READ (around 1:15):**
- Host 1 naturally says: "Quick interruption! If you are worried about your portfolio, our AI system is currently in open beta for FREE. Check the link in the description to generate a custom defensive report! Remember, it's for reference only. Now, back to the survival guide..."

**2. The Shadow of War & Safe Havens (1:30 - 3:30)**
- **MUST-INCLUDE Geopolitics** — Host 1 MUST explain the war/geopolitical narrative: Translate [{structured_data.get('geopolitical_impact', 'N/A')}] into a Mandarin dialogue line.
- **MUST-INCLUDE Commodities in dialogue** — Host 2 MUST say something like: "難怪避險資產全噴了！我看 {', '.join(structured_data.get('safe_havens', []))} 都在大漲..."

**3. Monday Survival Guide for Taiwan (3:30 - End)**
- Host 2 eagerly asks: "So what exactly should we do on Monday morning when the Taiwan market opens?"
- **Defensive Posture**: Host 1 advises: Translate [{structured_data.get('taiwan_monday_defense', 'N/A')}] into a direct strategy.
- **Vulture Strategy**: Host 1 advises: Translate [{structured_data.get('taiwan_monday_vulture', 'N/A')}] into specific indicators to watch.
- **CRITICAL CLOSING SLOGAN**: Host 1 or 2 MUST end with: "And remember our rule: **AI 帶你看股市，只看數據，不看情緒**. Stay safe this Monday!"

---

**ABSOLUTE RULES (non-negotiable):**
1. Every data point MUST match the provided data.
2. The example dialogue lines MUST BE IN TRADITIONAL CHINESE so NotebookLM generates Chinese audio.
3. Keep the geopolitical explanations objective.

Output ONLY the final NotebookLM prompt.
"""
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating NotebookLM prompt: {e}"

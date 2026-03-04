import os
import google.generativeai as genai
import datetime

def generate_notebooklm_prompt(api_key, report_content, date_str=None):
    """
    Generates a NotebookLM Podcast Prompt based on the ACTUAL daily report content.
    
    Strategy: Use DIALOGUE EXAMPLES instead of abstract instructions.
    NotebookLM follows "example lines" much more faithfully than "do X" commands.
    Keep segments to 3 + ad to minimize NotebookLM's self-editing.
    """
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
    model = genai.GenerativeModel(model_name)
    
    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    
    prompt = f"""You are an expert Podcast Script Writer. Read the following daily financial report, then generate a PROMPT for Google NotebookLM to create a 2-host podcast.

--- START OF TODAY'S REPORT ---
{report_content}
--- END OF TODAY'S REPORT ---

**Your task**: Based STRICTLY on the report above, generate a NotebookLM Audio Overview prompt. 

CRITICAL RULES FOR WRITING THIS PROMPT:
1. Keep the structure to exactly 3 main segments + 1 ad-read. NotebookLM cuts content when there are too many segments.
2. For MUST-INCLUDE items, write them as EXAMPLE DIALOGUE LINES (e.g., "Host 2 says: '...'"). NotebookLM follows example dialogue far more faithfully than abstract instructions.
3. Bind commodity data (gold/oil/silver) INTO an existing segment's dialogue, never as a standalone segment.
4. Name disaster zone stocks explicitly — don't just say "AI stocks."
5. MA5/MA20 analysis must appear as Host 1's specific spoken lines, not just "discuss MA."

Now generate the prompt following this EXACT template, filling ALL bracketed placeholders with real data from the report:

---

# NotebookLM Podcast Prompt: {date_str} 台股盤後分析

You are producing a daily financial podcast for Taiwan stock market investors. Your tone should be energetic, professional yet accessible, and engaging.

**Host Dynamics:**
- **Host 1 (The Anchor):** Experienced, data-driven, and likes to connect today's market events with past historical lessons.
- **Host 2 (The Color Commentator):** Curious, relatable to retail investors, asks smart questions, and translates complex financial jargon into simple concepts.

**Today's Focus & Episode Flow (CRITICAL — only 3 segments + ad):**

**1. The Hook & Institutional Data (0:00 - 1:30)**
- **CRITICAL OPENING SLOGAN**: Host 1 MUST start by saying: Welcome to **"AI 帶你看股市，只看數據，不看情緒"**
- [Extract the single most dramatic market number from today — e.g. index drop, historic volume. Write it as a Host 1 opening line.]
- [Extract the top 2-3 institutional sell-offs BY DOLLAR AMOUNT from the report. Write as Host 1 data callout.]
- Host 2 MUST react with surprise and ask about the contrarian buy-ins: [Extract 1-2 surprising institutional buys from the report, written as Host 2's dialogue line, e.g.: "Wait — foreign investors dumped [X] billion from TSMC but BOUGHT [Y] billion of [stock]? What's going on?"]
- Host 1 explains the risk-off capital rotation logic.

**AD-READ (around 1:15):**
- Host 1 naturally says: "Quick interruption! If you find this daily AI financial analysis helpful, our system is currently in open beta for FREE. Check the link in the description to generate a custom report for your own stock portfolio! Remember, it's for reference only, not financial advice. Now, back to the market..."

**2. Technical Damage & Disaster Zones (1:30 - 3:30)**
- **MUST-INCLUDE Data-Driven Technicals** — Host 1 MUST say something like: [Write a specific line analyzing the index and key stocks using whatever technical indicators are most prominent in today's report (e.g., MA5/月線, RSI, volume spikes, etc.). Extract the actual numbers and context from the report. Frame it so retail investors understand easily.]
- **MUST-INCLUDE Disaster Zones BY NAME** — Host 2 lists them: [Extract the specific disaster zone sectors AND individual stock names from the report. Write as Host 2's line, e.g.: "So today's damage report: [sector 1] got crushed with [stock names], [sector 2] saw [stock names] hit limit down..."]
- **MUST-INCLUDE Safe Havens** — [Extract safe haven stocks/sectors from the report]
- **MUST-INCLUDE Commodities in dialogue** — Host 2 MUST say something like: [Write a line binding commodity data into the safe haven discussion, e.g.: "And it's not just Taiwan — globally, gold surged [X]% and crude oil jumped [Y]%, confirming this is a full-blown risk-off day!"]

**3. Strategy & Closing (3:30 - End)**
- **Conservative strategy**: [Extract the conservative recommendation from the report, write as Host 1 advice]
- **Aggressive strategy**: [Extract the aggressive recommendation from the report, write as Host 1 advice]
- **CRITICAL CLOSING SLOGAN**: Host 1 or 2 MUST end with: "And remember our rule: **AI 帶你看股市，只看數據，不看情緒**. See you next time!"

---

**ABSOLUTE RULES (non-negotiable):**
1. Every data point, stock name, and technical indicator MUST come exactly from the report. NEVER invent data.
2. Do NOT mention stocks like Kinpo (金寶) unless they appear in the report's data.
3. Preserve Chinese financial terms (e.g., 三大法人, 重災區, 月線, 爆量, 超賣區, 避風港). Use the colloquial Chinese terms for technicals (e.g., "五日線" instead of "MA5", "月線" instead of "MA20") so it's easy to listen to.
4. When discussing technicals, base the analysis ON WHAT IS ACTUALLY HIGHLIGHTED in the report (could be RSI, moving averages, volume, etc.). Do not force indicators that aren't the main focus today.
5. Write HOST DIALOGUE EXAMPLES for every MUST-INCLUDE item. NotebookLM needs example lines to follow.

Output ONLY the final NotebookLM prompt. Fill in ALL bracketed placeholders with actual data.
"""
    
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating NotebookLM prompt: {e}"

import os
import json
from dotenv import load_dotenv
import sys

sys.path.append("/Users/shenghanchou/Desktop/stock/smart_financial_report")
from modules.notebooklm_generator import generate_notebooklm_prompt

load_dotenv("/Users/shenghanchou/Desktop/stock/smart_financial_report/.env")
API_KEY = os.getenv("GEMINI_API_KEY")

try:
    with open("/Users/shenghanchou/Desktop/stock/smart_financial_report/reports/structured_data_20260401.json", "r") as f:
        data = json.load(f)

    prompt = generate_notebooklm_prompt(API_KEY, data, "2026-04-01", "精簡版：今日只做簡單重點回顧，點出大盤小幅反彈與局勢大同小異，內容短短的就好，並預告明天會有重要的專題分析")

    with open("/Users/shenghanchou/Desktop/stock/smart_financial_report/reports/notebooklm_prompt_podcast_20260401.md", "w") as f:
        f.write(prompt)
    print("Regeneration Done!")
except Exception as e:
    print(f"Error: {e}")

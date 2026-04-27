import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel('gemini-2.5-flash')

input_path = "/Users/shenghanchou/Downloads/今日市場懸案：消失的百億資金.srt"
with open(input_path, "r", encoding="utf-8") as f:
    srt_content = f.read()

prompt = """
請將以下的 SRT 字幕檔轉換為繁體中文（zh-TW），並修正裡面的錯字、同音字錯誤與不通順的地方。
這是一份關於 2026-04-14 台股「數據大偵探」盤後分析與「消失的百億資金/大逃殺」的逐字稿。
請特別注意以下名詞的正確寫法與修正：
- 数据大侦探 -> 數據大偵探
- 只看数据不看情绪 -> 只看數據不看情緒
- 台机电/台积电 -> 台積電
- 南亚科 -> 南亞科
- 华邦电 -> 華邦電
- 华通 -> 華通
- 旺宏 -> 旺宏
- 台玻 -> 台玻
- 记录 -> 紀律
- 智障 -> 震盪 (或其他明顯語音辨識錯誤，請依上下文修正)
- 抓交替 -> 抓交替
- 精灵球 -> 精靈球

請確保數據邏輯與語氣維持原本主持人的口吻。
請保持 SRT 格式完全不變（包含數字序號與時間軸）。只輸出修正後的 SRT 內容，不要加上任何其他說明或 markdown 標籤（如 ```）。

SRT 內容如下：
""" + srt_content

print("Sending request to Gemini for SRT correction...")
response = model.generate_content(prompt)
corrected_content = response.text.replace('```srt', '').replace('```', '').strip()

output_path = input_path.replace(".srt", "_corrected.srt")
with open(output_path, "w", encoding="utf-8") as f:
    f.write(corrected_content)

print(f"Corrected SRT saved to {output_path}")

import os
import google.generativeai as genai
import datetime

class KnowledgeUpdater:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        # Using a fast and capable model for summarizing and extracting knowledge
        model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")
        self.model = genai.GenerativeModel(model_name)
        
        self.wiki_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge_wiki")
        os.makedirs(self.wiki_dir, exist_ok=True)
        
    def _read_existing_wiki(self):
        wiki_content = ""
        for filename in os.listdir(self.wiki_dir):
            if filename.endswith(".md"):
                filepath = os.path.join(self.wiki_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                        wiki_content += f"\n===【檔案：{filename}】===\n{content}\n"
                except Exception as e:
                    print(f"    [Knowledge Updater] 無法讀取 {filename}: {e}")
        return wiki_content

    def _write_file(self, filename, content):
        filepath = os.path.join(self.wiki_dir, filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"    [Knowledge Updater] 無法寫入 {filename}: {e}")

    def update_knowledge_base(self, todays_generated_report_content):
        """
        Takes today's generated markdown report, extracts key insights,
        and updates the knowledge wiki files.
        """
        print("\n🧠 [Knowledge Wiki] 啟動 AI 第二大腦反思與記憶機制...")
        
        existing_wiki = self._read_existing_wiki()
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        prompt = f"""
你是一個頂級的「AI 財經知識庫維護員」。
你的任務是讀取我們【今日剛剛完成的盤後分析報告】，並將報告中重要的「脈絡、發生事件、資金流動方向」，
**融合（覆寫或更新）**進我們現有的【知識庫 (Wiki)】中，讓明天的 AI 分析師能記得今天發生的事。

本日日期：{date_str}

=========================================
【今日完成的財經分析報告】
=========================================
{todays_generated_report_content}

=========================================
【我們目前的知識庫 Wiki (更新前)】
=========================================
{existing_wiki if existing_wiki.strip() else '(尚未建立任何知識，請開始初次建立)'}

=========================================
【任務要求】
=========================================
請根據「今日財經分析報告」的內容，為我產出三個更新後的 Markdown 檔案文字：
1. `capital_rotation.md`：記錄今日主要的「資金從哪裡流向哪裡」、「外資與內資的偏好」。保留有價值的歷史跡象，加入今日新趨勢。若無重大改變也請簡單提及今日盤勢確認。
2. `macro_events.md`：記錄大盤趨勢、總體經濟指標變化、地緣政治（如輝達財報、大選影響等），今日若有新事件也要加入，不用每天刪掉昨天的，而是像寫歷史年表般濃縮。
3. `trading_strategies.md`：記錄今日報告中提到的「驗收成功/失敗」的案例、以及「明天要觀察的個股與原因」。這能讓未來的 AI 記得我們的預測準確率。

---
【輸出格式限制】
你必須「只」輸出以下嚴格格式的文本，每一份檔案用三條短橫線和檔名分隔，不需要任何額外的開場白或自我介紹：

###FILE_START: capital_rotation.md
(這裡放更新後的 capital_rotation.md 內容，支援 Markdown)
###FILE_END

###FILE_START: macro_events.md
(這裡放更新後的 macro_events.md 內容，支援 Markdown)
###FILE_END

###FILE_START: trading_strategies.md
(這裡放更新後的 trading_strategies.md 內容，支援 Markdown)
###FILE_END

請確保沒有遺漏這三個檔案，並盡可能把知識「濃縮且精確」，這個知識庫是明天 AI 的「記憶體」。務必用繁體中文撰寫。
        """
        
        try:
            response = self.model.generate_content(prompt)
            output = response.text.strip()
            
            # Parsing the specific format
            files_updated = 0
            lines = output.splitlines()
            
            current_file = None
            current_content = []
            
            for line in lines:
                if line.startswith("###FILE_START:"):
                    current_file = line.replace("###FILE_START:", "").strip()
                    current_content = []
                elif line.startswith("###FILE_END") and current_file:
                    self._write_file(current_file, "\n".join(current_content).strip())
                    files_updated += 1
                    current_file = None
                elif current_file:
                    current_content.append(line)
                    
            if files_updated > 0:
                print(f"    ✅ 已成功將今日重點提煉並更新至 {files_updated} 份 Wiki 文件！(複利知識 +1)")
            else:
                print("    ⚠️ AI 未按照格式輸出，知識更新可能失敗。")
                
        except Exception as e:
            print(f"    ❌ 更新知識庫時發生錯誤: {e}")

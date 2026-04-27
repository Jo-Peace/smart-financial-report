import os
import json
import google.generativeai as genai
import datetime

class KnowledgeUpdater:
    """
    [優化 A] Append-Only 年表架構
    - 每日以結構化條目 append 至各 Wiki 檔案，不再覆寫
    - AI 只負責生成「今日新增條目」，不能修改舊條目
    - 每週五（或距上次壓縮>7天）自動壓縮為 weekly_digest.md
    - 防止 AI 格式失誤導致整份歷史記憶被清空
    """
    WIKI_FILES = ["capital_rotation.md", "macro_events.md", "trading_strategies.md"]
    DIGEST_FILE = "weekly_digest.md"
    DIGEST_STAMP_FILE = ".last_digest_date"

    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
        self.model = genai.GenerativeModel(model_name)
        self.wiki_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "knowledge_wiki"
        )
        os.makedirs(self.wiki_dir, exist_ok=True)

    # ─────────────────────────────────────────
    # 內部工具
    # ─────────────────────────────────────────
    def _path(self, filename):
        return os.path.join(self.wiki_dir, filename)

    def _read_file(self, filename, tail_lines=None):
        """讀取 Wiki 檔案內容；tail_lines 可限制只讀最後 N 行（給 AI 讀歷史參考用）。"""
        filepath = self._path(filename)
        if not os.path.exists(filepath):
            return ""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if tail_lines:
                lines = lines[-tail_lines:]
            return "".join(lines)
        except Exception as e:
            print(f"    [Wiki] 無法讀取 {filename}: {e}")
            return ""

    def _append_to_file(self, filename, new_entry_text):
        """將新條目安全地 append 至 Wiki 檔案底部，不覆寫任何既有內容。"""
        filepath = self._path(filename)
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write("\n" + new_entry_text.strip() + "\n")
        except Exception as e:
            print(f"    [Wiki] 無法 append 至 {filename}: {e}")

    def _write_file(self, filename, content):
        """完整覆寫（僅限於 weekly_digest 用途）。"""
        filepath = self._path(filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"    [Wiki] 無法寫入 {filename}: {e}")

    def _should_run_weekly_digest(self):
        """判斷是否需要執行週摘要壓縮（週五，或距上次超過 7 天）。"""
        stamp_path = self._path(self.DIGEST_STAMP_FILE)
        today = datetime.date.today()
        is_friday = today.weekday() == 4

        if os.path.exists(stamp_path):
            try:
                with open(stamp_path, "r") as f:
                    last = datetime.date.fromisoformat(f.read().strip())
                days_elapsed = (today - last).days
                return is_friday and days_elapsed >= 5
            except Exception:
                pass
        return is_friday

    def _save_digest_stamp(self):
        stamp_path = self._path(self.DIGEST_STAMP_FILE)
        with open(stamp_path, "w") as f:
            f.write(str(datetime.date.today()))

    def _read_full_wiki_for_digest(self):
        """讀取所有 Wiki 完整內容，供週壓縮使用。"""
        combined = ""
        for fn in self.WIKI_FILES:
            content = self._read_file(fn)
            if content.strip():
                combined += f"\n\n===【{fn}】===\n{content}"
        return combined

    # ─────────────────────────────────────────
    # 主要公開方法
    # ─────────────────────────────────────────
    def update_knowledge_base(self, todays_generated_report_content):
        """
        每日執行：
        1. AI 讀取今日報告 → 產出三份 Wiki 的「今日新增條目」(Append-Only)
        2. 週五觸發 weekly_digest 壓縮
        """
        print("\n🧠 [Knowledge Wiki] 啟動 AI 第二大腦反思與記憶機制（Append-Only 年表）...")

        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        weekday_str = ["一", "二", "三", "四", "五", "六", "日"][datetime.datetime.now().weekday()]

        # 讀最近 60 行作為「歷史背景」，避免 prompt 過長
        recent_rotation = self._read_file("capital_rotation.md", tail_lines=60)
        recent_macro = self._read_file("macro_events.md", tail_lines=60)
        recent_strategies = self._read_file("trading_strategies.md", tail_lines=60)

        prompt = f"""
你是一個頂級的「AI 財經知識庫年表維護員」。
你的任務：讀取【今日盤後分析報告】，為知識庫的三份 Wiki 文件各產出一段「今日新增條目」。

⚠️ 重要規則：
- 你只能產出「今日的新增條目」，格式為年表條目，以日期標題開頭
- 你不能重複或改寫舊條目的內容
- 如果今日與舊趨勢完全一致，仍需簡短確認，不得跳過
- 輸出格式嚴格按照下方規定

本日日期：{date_str}（週{weekday_str}）

=========================================
【今日盤後分析報告】
=========================================
{todays_generated_report_content[:4000]}

=========================================
【capital_rotation.md 近期歷史（供參考，勿重複）】
=========================================
{recent_rotation[-2000:] if recent_rotation else '(尚無歷史記錄)'}

=========================================
【macro_events.md 近期歷史（供參考，勿重複）】
=========================================
{recent_macro[-1500:] if recent_macro else '(尚無歷史記錄)'}

=========================================
【trading_strategies.md 近期歷史（供參考，勿重複）】
=========================================
{recent_strategies[-1500:] if recent_strategies else '(尚無歷史記錄)'}

=========================================
【輸出格式】嚴格遵守，不得有額外說明文字
=========================================

###FILE_START: capital_rotation.md
### {date_str}（週{weekday_str}）
- **資金撤出區**：(今日外資賣超標的 + 金額 + 原因)
- **資金流入區**：(今日外資買超標的 + 金額 + 原因)
- **法人策略觀察**：(外資/投信整體操作邏輯，1-2句)
###FILE_END

###FILE_START: macro_events.md
### {date_str}（週{weekday_str}）
- **大盤走勢**：(指數漲跌點數 + 1句定性)
- **宏觀指標**：(商品/匯率/利率等有異動的項目)
- **重大事件**：(地緣政治、財報、法說會等，無則寫「無重大新事件」)
###FILE_END

###FILE_START: trading_strategies.md
### {date_str}（週{weekday_str}）
- **預測驗收**：(命中✅/失準❌ + 原因分析)
- **明日觀察標的**：(推薦個股 + 理由 + 停損點)
- **風控提醒**：(1句，今日最重要的風控紀律)
###FILE_END

請立刻產出以上三份條目，務必使用繁體中文。
        """

        try:
            response = self.model.generate_content(prompt)
            output = response.text.strip()

            files_updated = 0
            lines = output.splitlines()
            current_file = None
            current_content = []

            for line in lines:
                if line.startswith("###FILE_START:"):
                    current_file = line.replace("###FILE_START:", "").strip()
                    current_content = []
                elif line.startswith("###FILE_END") and current_file:
                    # ✅ Append-Only：只新增，不覆寫
                    self._append_to_file(current_file, "\n".join(current_content))
                    files_updated += 1
                    current_file = None
                elif current_file:
                    current_content.append(line)

            if files_updated > 0:
                print(f"    ✅ 已成功 Append 今日重點至 {files_updated} 份 Wiki 年表！(複利知識 +1)")
            else:
                print("    ⚠️ AI 未按照格式輸出，知識更新可能失敗（舊資料安全，未被覆寫）。")

        except Exception as e:
            print(f"    ❌ 更新知識庫時發生錯誤（舊資料安全，未被覆寫）: {e}")

        # ── 週五觸發週摘要壓縮 ──
        if self._should_run_weekly_digest():
            self._run_weekly_digest(date_str)

    def _run_weekly_digest(self, date_str):
        """將完整 Wiki 年表壓縮為 weekly_digest.md，供 AI 快速讀取。"""
        print("    📅 [Weekly Digest] 週五觸發，正在壓縮本週知識為精華摘要...")
        full_wiki = self._read_full_wiki_for_digest()
        if not full_wiki.strip():
            print("    ⚠️ [Weekly Digest] Wiki 內容為空，跳過壓縮。")
            return

        prompt = f"""
你是一個財經知識庫的「週報壓縮員」。
請閱讀以下完整的 Wiki 年表（可能包含數週的條目），
提取出「最近一週（約5個交易日）內」最核心的市場洞察，壓縮成一份簡潔的 weekly_digest.md。

本次壓縮日期：{date_str}

{full_wiki}

---
請產出 weekly_digest.md 的完整內容，格式如下：

# 週摘要：{date_str} 當週精華

## 本週資金主軸
（本週外資最主要的買超/賣超方向，3-5條）

## 本週重大宏觀事件
（本週影響台股的總經事件，3-5條）

## 本週預測績效
（本週命中 X 次，失準 X 次，命中率 X%；各列一行說明）

## 下週持續追蹤標的
（未完成驗收的標的 + 理由）

---
*本摘要由 AI 自動壓縮，原始年表保留於各 Wiki 檔案中。*
        """
        try:
            response = self.model.generate_content(prompt)
            self._write_file(self.DIGEST_FILE, response.text.strip())
            self._save_digest_stamp()
            print(f"    ✅ [Weekly Digest] 週摘要已產出至 {self.DIGEST_FILE}")
        except Exception as e:
            print(f"    ❌ [Weekly Digest] 壓縮失敗: {e}")

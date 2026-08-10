import datetime
import os
import re

# Lazy Gemini import only when AI mode is used (avoids hard dependency on daily local path).


class KnowledgeUpdater:
    """
    Append-Only knowledge wiki updater.

    Default mode is local (no Gemini):
      KNOWLEDGE_WIKI_MODE=local|ai|off   (default: local)

    - local: build capital_rotation / macro_events / trading_strategies from structured_data
    - ai:    legacy Gemini append + Gemini weekly digest
    - off:   skip entirely

    Weekly digest (Friday rule) is local when mode=local, Gemini when mode=ai.
    """

    WIKI_FILES = ["capital_rotation.md", "macro_events.md", "trading_strategies.md"]
    DIGEST_FILE = "weekly_digest.md"
    DIGEST_STAMP_FILE = ".last_digest_date"

    def __init__(self, api_key=None):
        self.api_key = api_key
        from modules.gemini_client import default_model_name
        self.model_name = default_model_name()
        self.wiki_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "knowledge_wiki"
        )
        os.makedirs(self.wiki_dir, exist_ok=True)

    # ─────────────────────────────────────────
    # 內部工具
    # ─────────────────────────────────────────
    def _mode(self) -> str:
        raw = os.getenv("KNOWLEDGE_WIKI_MODE", "local").strip().lower()
        if raw in {"local", "ai", "off"}:
            return raw
        if raw in {"0", "false", "no", "none", "skip"}:
            return "off"
        if raw in {"gemini", "llm"}:
            return "ai"
        return "local"

    def _path(self, filename):
        return os.path.join(self.wiki_dir, filename)

    def _read_file(self, filename, tail_lines=None):
        """讀取 Wiki 檔案內容；tail_lines 可限制只讀最後 N 行。"""
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

    def _ensure_header(self, filename, title):
        path = self._path(filename)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return
        self._write_file(
            filename,
            f"# {title}\n\n"
            "此檔為 append-only 年表。預設由本機 structured_data 寫入；"
            "若需 AI 敘事條目請設 KNOWLEDGE_WIKI_MODE=ai。\n",
        )

    def _has_date_entry(self, filename, date_str) -> bool:
        content = self._read_file(filename)
        if not content:
            return False
        # Match "### 2026-07-08" or "## 2026-07-08"
        return bool(re.search(rf"^#{{2,3}}\s*{re.escape(date_str)}\b", content, re.MULTILINE))

    def _weekday_label(self, date_str: str) -> str:
        try:
            day = datetime.date.fromisoformat(str(date_str)[:10])
        except ValueError:
            day = datetime.date.today()
        return ["一", "二", "三", "四", "五", "六", "日"][day.weekday()]

    def _join_items(self, items, limit=5) -> str:
        cleaned = [str(x).strip() for x in (items or []) if str(x).strip()]
        if not cleaned:
            return "（無）"
        shown = cleaned[:limit]
        text = "；".join(shown)
        if len(cleaned) > limit:
            text += f" 等共 {len(cleaned)} 項"
        return text

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

    def _require_api_key(self):
        if not self.api_key:
            raise RuntimeError("KNOWLEDGE_WIKI_MODE=ai 需要 GEMINI_API_KEY")
        return self.api_key

    # ─────────────────────────────────────────
    # 本機條目
    # ─────────────────────────────────────────
    def build_local_entries(self, structured_data, date_str=None) -> dict[str, str]:
        """Build the three wiki entry bodies (without file wrappers)."""
        structured_data = structured_data or {}
        date_str = date_str or structured_data.get("date") or datetime.date.today().isoformat()
        weekday = self._weekday_label(date_str)
        header = f"### {date_str}（週{weekday}）"

        buys = structured_data.get("safe_havens_bought") or []
        sells = structured_data.get("heavyweights_dumped") or []
        commodities = structured_data.get("commodities") or []
        index_action = structured_data.get("index_action") or "資料不足"
        divergences = structured_data.get("price_volume_divergence") or []
        validation = structured_data.get("validation_results") or []
        prev_results = structured_data.get("prev_day_picks_result") or []
        targets = structured_data.get("prediction_targets") or []
        editorial = structured_data.get("editorial_frame") or {}
        conservative = str(structured_data.get("conservative_strategy") or "").strip()
        aggressive = str(structured_data.get("aggressive_strategy") or "").strip()

        # capital rotation
        strategy_bits = []
        if buys and sells:
            strategy_bits.append(
                f"資金由賣超端（{self._join_items(sells, 2)}）"
                f"轉向買超端（{self._join_items(buys, 2)}）"
            )
        frame_label = editorial.get("label") or editorial.get("frame")
        if frame_label:
            strategy_bits.append(f"編輯框架：{frame_label}")
        if not strategy_bits:
            strategy_bits.append("本機條目：依當日法人榜整理，無額外敘事。")
        capital = "\n".join(
            [
                header,
                f"- **賣超/轉弱證據**：{self._join_items(sells)}",
                f"- **買超/轉強證據**：{self._join_items(buys)}",
                f"- **法人策略觀察**：{'；'.join(strategy_bits)}",
            ]
        )

        # macro
        major = self._join_items(divergences, limit=2)
        if major == "（無）":
            reasons = editorial.get("reasons") or []
            if reasons:
                major = self._join_items(reasons, limit=2)
            else:
                major = "無重大新事件（本機條目）"
        macro = "\n".join(
            [
                header,
                f"- **大盤走勢**：{index_action}",
                f"- **宏觀指標**：{self._join_items(commodities) if commodities else '無商品資料'}",
                f"- **重大事件**：{major}",
            ]
        )

        # trading strategies
        if validation:
            val_lines = []
            for row in validation[:5]:
                hit = row.get("hit")
                mark = "✅" if hit is True else ("❌" if hit is False else "⚠️")
                pct = row.get("pct_change")
                pct_text = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "無行情"
                val_lines.append(
                    f"{mark} {row.get('id')} {row.get('name')}：{row.get('result', '')}（{pct_text}）"
                )
            validation_text = "；".join(val_lines)
        elif prev_results:
            validation_text = self._join_items(prev_results, limit=4)
        else:
            validation_text = "無前次 prediction_targets 可驗收"

        if targets:
            target_bits = []
            for t in targets[:3]:
                stop = t.get("stop_loss_price")
                stop_text = f"，停損 {stop}" if stop not in (None, "") else ""
                desc = t.get("stop_loss_desc") or t.get("trigger") or ""
                target_bits.append(
                    f"{t.get('id')} {t.get('name')}（{t.get('direction', '觀察')}；"
                    f"{desc}{stop_text}）"
                )
            targets_text = "；".join(target_bits)
        else:
            targets_text = "今日無正式 prediction_targets"

        if conservative:
            risk = conservative[:120] + ("…" if len(conservative) > 120 else "")
        elif aggressive:
            risk = f"攻擊面參考：{aggressive[:100]}{'…' if len(aggressive) > 100 else ''}"
        else:
            risk = "嚴格依停損執行；優先避開外資賣超且爆量轉弱標的。"

        strategies = "\n".join(
            [
                header,
                f"- **預測驗收**：{validation_text}",
                f"- **明日觀察標的**：{targets_text}",
                f"- **風控提醒**：{risk}",
            ]
        )

        return {
            "capital_rotation.md": capital,
            "macro_events.md": macro,
            "trading_strategies.md": strategies,
        }

    def _append_local_from_structured(self, structured_data, date_str) -> dict:
        if not structured_data or not isinstance(structured_data, dict):
            print("    ⚠️  [Wiki local] 無 structured_data，跳過本機 Wiki 更新。")
            return {"mode": "local", "files_updated": 0, "skipped": True}

        titles = {
            "capital_rotation.md": "資金輪動年表（capital_rotation）",
            "macro_events.md": "宏觀事件年表（macro_events）",
            "trading_strategies.md": "交易策略與驗收年表（trading_strategies）",
        }
        entries = self.build_local_entries(structured_data, date_str=date_str)
        files_updated = 0
        skipped_existing = 0
        for filename, body in entries.items():
            self._ensure_header(filename, titles[filename])
            if self._has_date_entry(filename, date_str):
                skipped_existing += 1
                continue
            self._append_to_file(filename, body)
            files_updated += 1

        if files_updated:
            print(f"    ✅ [Wiki local] 已 Append 本機條目至 {files_updated} 份 Wiki（不消耗 Gemini）")
        elif skipped_existing:
            print(f"    ⏭  [Wiki local] {date_str} 條目已存在，略過 {skipped_existing} 份")
        else:
            print("    ⚠️  [Wiki local] 未寫入任何檔案")

        return {
            "mode": "local",
            "files_updated": files_updated,
            "skipped_existing": skipped_existing,
            "date": date_str,
        }

    def _extract_recent_dated_blocks(self, filename, max_entries=6) -> list[tuple[str, str]]:
        """Parse trailing dated sections from a wiki file. Returns [(date, body), ...]."""
        content = self._read_file(filename)
        if not content.strip():
            return []
        pattern = re.compile(r"^###\s*(\d{4}-\d{2}-\d{2})[^\n]*\n(.*?)(?=^###\s*\d{4}-\d{2}-\d{2}|\Z)", re.M | re.S)
        matches = pattern.findall(content)
        if not matches:
            return []
        return [(d, body.strip()) for d, body in matches[-max_entries:]]

    def _run_weekly_digest_local(self, date_str):
        """Deterministic weekly digest from recent wiki entries (no Gemini)."""
        print("    📅 [Weekly Digest local] 正在以本機條目壓縮週摘要...")
        rotation = self._extract_recent_dated_blocks("capital_rotation.md", max_entries=6)
        macro = self._extract_recent_dated_blocks("macro_events.md", max_entries=6)
        strategies = self._extract_recent_dated_blocks("trading_strategies.md", max_entries=6)
        if not any([rotation, macro, strategies]):
            print("    ⚠️ [Weekly Digest local] 無可壓縮條目，跳過。")
            return

        lines = [
            f"# 週摘要：{date_str} 當週精華（本機）",
            "",
            "## 本週買賣證據鏈",
        ]
        if rotation:
            for d, body in rotation:
                first = next((ln.strip("- ").strip() for ln in body.splitlines() if ln.strip().startswith("-")), "")
                lines.append(f"- **{d}**：{first or '（見 capital_rotation 原文）'}")
        else:
            lines.append("- （無 capital_rotation 近期條目）")

        lines.extend(["", "## 本週重大宏觀事件"])
        if macro:
            for d, body in macro:
                for ln in body.splitlines():
                    if "重大事件" in ln or "大盤走勢" in ln:
                        lines.append(f"- **{d}**：{ln.strip('- ').strip()}")
                        break
                else:
                    lines.append(f"- **{d}**：（見 macro_events 原文）")
        else:
            lines.append("- （無 macro_events 近期條目）")

        lines.extend(["", "## 本週預測績效"])
        if strategies:
            for d, body in strategies:
                for ln in body.splitlines():
                    if "預測驗收" in ln:
                        lines.append(f"- **{d}**：{ln.strip('- ').strip()}")
                        break
        else:
            lines.append("- （無 trading_strategies 近期條目）")

        lines.extend(["", "## 下週持續追蹤標的"])
        if strategies:
            last_date, last_body = strategies[-1]
            for ln in last_body.splitlines():
                if "明日觀察" in ln or "觀察標的" in ln:
                    lines.append(f"- 自 {last_date}：{ln.strip('- ').strip()}")
                    break
            else:
                lines.append("- （見 trading_strategies 最新條目）")
        else:
            lines.append("- （無）")

        lines.extend(
            [
                "",
                "---",
                "*本摘要由本機條目自動壓縮（KNOWLEDGE_WIKI_MODE=local），原始年表保留於各 Wiki 檔案中。*",
            ]
        )
        self._write_file(self.DIGEST_FILE, "\n".join(lines) + "\n")
        self._save_digest_stamp()
        print(f"    ✅ [Weekly Digest local] 已寫入 {self.DIGEST_FILE}")

    # ─────────────────────────────────────────
    # 主要公開方法
    # ─────────────────────────────────────────
    def update_knowledge_base(self, todays_generated_report_content, structured_data=None, report_date=None):
        """
        每日執行：
        1. local 模式：以 structured_data 寫入三份 Wiki（不消耗 Gemini）
        2. ai 模式：Gemini 產出今日新增條目
        3. 週五觸發 weekly_digest
        """
        mode = self._mode()
        date_str = (
            report_date
            or (structured_data or {}).get("date")
            or datetime.datetime.now().strftime("%Y-%m-%d")
        )

        if mode == "off":
            print("\n🧠 [Knowledge Wiki] 已關閉（KNOWLEDGE_WIKI_MODE=off）")
            return {"mode": "off", "files_updated": 0}

        if mode == "local":
            print("\n🧠 [Knowledge Wiki] 本機模式：以 structured_data 寫入年表（不消耗 Gemini）...")
            result = self._append_local_from_structured(structured_data, date_str)
            if self._should_run_weekly_digest():
                self._run_weekly_digest_local(date_str)
            return result

        # ── ai mode (legacy) ──
        print("\n🧠 [Knowledge Wiki] AI 模式：啟動 Gemini 反思與記憶（Append-Only 年表）...")
        return self._update_with_ai(todays_generated_report_content, date_str)

    def _update_with_ai(self, todays_generated_report_content, date_str):
        from modules.gemini_client import generate_text

        weekday_str = self._weekday_label(date_str)
        api_key = self._require_api_key()

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
{(todays_generated_report_content or "")[:4000]}

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
- **賣超/轉弱證據**：(今日外資賣超標的 + 金額 + 成交量/技術佐證)
- **買超/轉強證據**：(今日外資買超標的 + 金額 + 成交量/技術佐證)
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

        files_updated = 0
        try:
            output = generate_text(
                api_key,
                prompt,
                task_name="knowledge_wiki_append",
                model_name=self.model_name,
            ).strip()

            lines = output.splitlines()
            current_file = None
            current_content = []

            for line in lines:
                if line.startswith("###FILE_START:"):
                    current_file = line.replace("###FILE_START:", "").strip()
                    current_content = []
                elif line.startswith("###FILE_END") and current_file:
                    if self._has_date_entry(current_file, date_str):
                        print(f"    ⏭  [Wiki ai] {current_file} 已有 {date_str}，略過")
                    else:
                        self._append_to_file(current_file, "\n".join(current_content))
                        files_updated += 1
                    current_file = None
                elif current_file:
                    current_content.append(line)

            if files_updated > 0:
                print(f"    ✅ 已成功 Append 今日重點至 {files_updated} 份 Wiki 年表！(複利知識 +1)")
            else:
                print("    ⚠️ AI 未按照格式輸出或條目已存在（舊資料安全，未被覆寫）。")

        except Exception as e:
            print(f"    ❌ 更新知識庫時發生錯誤（舊資料安全，未被覆寫）: {e}")

        if self._should_run_weekly_digest():
            self._run_weekly_digest(date_str)

        return {"mode": "ai", "files_updated": files_updated, "date": date_str}

    def _run_weekly_digest(self, date_str):
        """將完整 Wiki 年表壓縮為 weekly_digest.md（Gemini）。"""
        from modules.gemini_client import generate_text

        print("    📅 [Weekly Digest] 週五觸發，正在壓縮本週知識為精華摘要...")
        full_wiki = self._read_full_wiki_for_digest()
        if not full_wiki.strip():
            print("    ⚠️ [Weekly Digest] Wiki 內容為空，跳過壓縮。")
            return

        api_key = self._require_api_key()
        prompt = f"""
你是一個財經知識庫的「週報壓縮員」。
請閱讀以下完整的 Wiki 年表（可能包含數週的條目），
提取出「最近一週（約5個交易日）內」最核心的市場洞察，壓縮成一份簡潔的 weekly_digest.md。

本次壓縮日期：{date_str}

{full_wiki}

---
請產出 weekly_digest.md 的完整內容，格式如下：

# 週摘要：{date_str} 當週精華

## 本週買賣證據鏈
（本週外資最主要的買超/賣超方向、成交量佐證、驗收結果，3-5條）

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
            text = generate_text(
                api_key,
                prompt,
                task_name="knowledge_weekly_digest",
                model_name=self.model_name,
            ).strip()
            self._write_file(self.DIGEST_FILE, text)
            self._save_digest_stamp()
            print(f"    ✅ [Weekly Digest] 週摘要已產出至 {self.DIGEST_FILE}")
        except Exception as e:
            print(f"    ❌ [Weekly Digest] 壓縮失敗: {e}")

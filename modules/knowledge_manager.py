import datetime
import os
import re

# Lazy Gemini import only when AI mode is used (avoids hard dependency on daily local path).

WIKI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge_wiki")
DATED_HEADER_RE = re.compile(r"^#{2,3}\s*(\d{4}-\d{2}-\d{2})[^\n]*$", re.MULTILINE)
DIGEST_END_RE = re.compile(r"<!--\s*digest_end:\s*(\d{4}-\d{2}-\d{2})\s*-->")
DIGEST_LEGACY_RE = re.compile(r"^#\s*週摘要：\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE)

# Per-file prompt budgets for analyzer's wiki block (sum stays under the 3500 cap).
WIKI_PROMPT_BUDGETS = (
    ("weekly_digest.md", 850),
    ("capital_rotation.md", 850),
    ("macro_events.md", 750),
    ("trading_strategies.md", 800),
)
DIGEST_MAX_AGE_DAYS = 7


def _as_date(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


ANY_HEADER_RE = re.compile(r"^#{1,3}\s", re.MULTILINE)


def _dated_spans(content):
    """
    [(date, start, end), ...] for every ##/### YYYY-MM-DD section.

    A section ends at the next markdown header of any kind (level 1-3), so a hand-written
    non-dated section after an entry is never swallowed by replace/dedupe.
    """
    content = content or ""
    spans = []
    for match in DATED_HEADER_RE.finditer(content):
        nxt = ANY_HEADER_RE.search(content, match.end())
        spans.append((match.group(1), match.start(), nxt.start() if nxt else len(content)))
    return spans


def split_dated_blocks(content):
    """Split a wiki file into (preamble, [(date, block_text), ...]) by ##/### YYYY-MM-DD headers."""
    content = content or ""
    spans = _dated_spans(content)
    if not spans:
        return content, []
    preamble = content[: spans[0][1]]
    return preamble, [(d, content[start:end].strip()) for d, start, end in spans]


def upsert_dated_block(content, date_str, block, replace=False):
    """
    Return (new_content, action) with ``block`` stored under ``date_str``.

    Edits are span-based so the rest of the file is byte-for-byte unchanged:
    - existing date + replace=False → unchanged ("skipped")
    - existing date + replace=True  → first same-date section replaced, later duplicates
      removed ("replaced"; "unchanged" when the text is identical)
    - new date → inserted before the first later-dated section ("inserted"), else
      appended at the end ("appended")
    """
    content = content or ""
    block = (block or "").strip()
    date_str = str(date_str)
    spans = _dated_spans(content)
    same = [span for span in spans if span[0] == date_str]
    if same:
        if not replace:
            return content, "skipped"
        if len(same) == 1 and content[same[0][1]:same[0][2]].strip() == block:
            return content, "unchanged"
        out, cursor = [], 0
        for idx, (d, start, end) in enumerate(same):
            out.append(content[cursor:start])
            if idx == 0:
                out.append(block + ("\n\n" if end < len(content) else "\n"))
            cursor = end
        out.append(content[cursor:])
        return "".join(out), "replaced"
    later = [span for span in spans if span[0] > date_str]
    if later:
        pos = later[0][1]
        return content[:pos] + block + "\n\n" + content[pos:], "inserted"
    base = content.rstrip()
    return (base + "\n\n" if base else "") + block + "\n", "appended"


def dedupe_dated_blocks(content):
    """Keep only the last section for each date (historical AI-era duplicates). Returns (content, removed)."""
    content = content or ""
    spans = _dated_spans(content)
    last_index = {}
    for idx, (d, _, _) in enumerate(spans):
        last_index[d] = idx
    drop = [span for idx, span in enumerate(spans) if last_index[span[0]] != idx]
    if not drop:
        return content, 0
    out, cursor = [], 0
    for _, start, end in drop:
        out.append(content[cursor:start])
        cursor = end
    out.append(content[cursor:])
    return "".join(out), len(drop)


def sort_dated_blocks(content):
    """
    Re-order dated sections chronologically (stable for equal dates) without touching
    the preamble or non-dated sections. Returns (content, moved: bool).
    """
    content = content or ""
    spans = _dated_spans(content)
    if len(spans) < 2:
        return content, False
    order = sorted(range(len(spans)), key=lambda i: (spans[i][0], i))
    if order == list(range(len(spans))):
        return content, False
    # Only sections that sit back-to-back (separated by whitespace) are re-ordered as one run.
    runs, current = [], [0]
    for i in range(1, len(spans)):
        if content[spans[i - 1][2]:spans[i][1]].strip() == "":
            current.append(i)
        else:
            runs.append(current)
            current = [i]
    runs.append(current)
    out, cursor, moved = [], 0, False
    for run in runs:
        first, last = spans[run[0]][1], spans[run[-1]][2]
        texts = [content[spans[i][1]:spans[i][2]].strip() for i in run]
        ordered = sorted(range(len(run)), key=lambda k: (spans[run[k]][0], k))
        moved = moved or ordered != list(range(len(run)))
        out.append(content[cursor:first])
        out.append("\n\n".join(texts[k] for k in ordered) + ("\n\n" if last < len(content) else "\n"))
        cursor = last
    out.append(content[cursor:])
    return "".join(out), moved


def _clip_lines(text, budget):
    """Clip at a line boundary so an entry is never cut mid-sentence."""
    text = (text or "").strip()
    if len(text) <= budget:
        return text
    out, used = [], 0
    for line in text.splitlines():
        if used + len(line) + 1 > budget:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out).strip()


DIGEST_LINE_MAX_CHARS = 220


def _clip_digest_sections(text, budget):
    """
    Fit a weekly digest into ``budget`` without losing whole sections.

    A top-down cut used to keep only the header and the buy/sell lines, plus a dangling
    empty 「## 本週大盤與事件」. Now every ``## `` section keeps its header and as many
    lines as fit (round-robin, newest line first, shown in original order); sections
    that get no line are dropped, and the footer after ``---`` is never sent.
    """
    title, sections, current = [], [], None
    for line in (text or "").strip().splitlines():
        if line.strip() == "---":
            break  # footer
        if line.startswith("## "):
            current = [line.strip(), []]
            sections.append(current)
            continue
        if not line.strip():
            continue
        if current is None:
            title.append(line.strip())
            continue
        item = line.rstrip()
        if len(item) > DIGEST_LINE_MAX_CHARS:
            item = item[: DIGEST_LINE_MAX_CHARS - 1] + "…"
        current[1].append(item)
    sections = [(header, items) for header, items in sections if items]
    head = "\n".join(title)
    used = len(head)
    chosen = [[] for _ in sections]
    pointers = [len(items) - 1 for _, items in sections]
    progress = True
    while progress:
        progress = False
        for i, (header, items) in enumerate(sections):
            j = pointers[i]
            if j < 0:
                continue
            cost = len(items[j]) + 1 + (len(header) + 2 if not chosen[i] else 0)
            if used + cost > budget:
                pointers[i] = -1
                continue
            chosen[i].append(j)
            used += cost
            pointers[i] -= 1
            progress = True
    out = [head] if head else []
    for (header, items), picked in zip(sections, chosen):
        if picked:
            out.append("")
            out.append(header)
            out.extend(items[j] for j in sorted(picked))
    return "\n".join(out).strip()


def digest_end_date(digest_text):
    """Last trading date a weekly digest covers (new marker, else legacy header date)."""
    match = DIGEST_END_RE.search(digest_text or "") or DIGEST_LEGACY_RE.search(digest_text or "")
    return _as_date(match.group(1)) if match else None


def build_wiki_prompt_context(report_date=None, wiki_dir=None, max_chars=3500, budgets=WIKI_PROMPT_BUDGETS):
    """
    Analyzer's 「Second Brain」 block.

    - per-file budgets instead of one hard cut (trading_strategies is no longer dropped)
    - whole dated entries only, strictly older than ``report_date`` (no self-feedback on reruns)
    - weekly digest only when it ends within DIGEST_MAX_AGE_DAYS of the report date, labelled with its date
    - read-side narrative sanitize
    """
    from modules.narrative_sanitize import sanitize_narrative_text

    wiki_dir = wiki_dir or WIKI_DIR
    report_day = _as_date(report_date) or datetime.date.today()
    if not os.path.isdir(wiki_dir):
        return "（尚未累積任何歷史知識）"

    def _read(name):
        try:
            with open(os.path.join(wiki_dir, name), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    sections = []
    for filename, budget in budgets:
        content = _read(filename)
        if not content.strip():
            continue
        if filename == "weekly_digest.md":
            end = digest_end_date(content)
            if end is None or end >= report_day or (report_day - end).days > DIGEST_MAX_AGE_DAYS:
                continue
            body = _clip_digest_sections(DIGEST_END_RE.sub("", content), budget)
            sections.append(f"=== weekly_digest.md（截至 {end.isoformat()} 的週摘要）===\n{body}")
            continue
        _, blocks = split_dated_blocks(content)
        blocks = [(d, text) for d, text in blocks if (_as_date(d) or report_day) < report_day]
        chosen, used = [], 0
        for d, text in reversed(blocks):
            if used + len(text) + 2 > budget:
                if not chosen:
                    chosen.append((d, _clip_lines(text, budget)))
                break
            chosen.append((d, text))
            used += len(text) + 2
        if not chosen:
            continue
        chosen.reverse()
        label = f"最近 {len(chosen)} 筆（{chosen[0][0]}～{chosen[-1][0]}）"
        sections.append(f"=== {filename}（{label}）===\n" + "\n\n".join(t for _, t in chosen))

    wiki_str = sanitize_narrative_text("\n\n".join(sections))
    if len(wiki_str) > max_chars:
        wiki_str = _clip_lines(wiki_str, max_chars) + "\n...（已依條目截斷）"
    return wiki_str if wiki_str.strip() else "（尚未累積任何歷史知識）"


CHRONICLE_FILES = ("capital_rotation.md", "macro_events.md", "trading_strategies.md")
MEMORY_FILE = "local_market_memory.md"
LEGACY_REBUILD_MARKER = ".verifiable_rebuild_v1"
LEGACY_REBUILD_DAYS = 45


def rebuilt_entries_since(reports_dir, since, until=None):
    """
    {filename: {date: entry}} rebuilt from archived structured_data.json for
    ``since`` ≤ date < ``until`` with today's verifiable-fields-only builders (no Gemini).
    """
    import json

    from modules.local_knowledge_compounder import LocalKnowledgeCompounder
    from modules.report_paths import _date_from_structured_path, iter_structured_data_paths

    updater = KnowledgeUpdater(api_key=None, ensure_dir=False)
    compounder = LocalKnowledgeCompounder()
    out = {name: {} for name in CHRONICLE_FILES + (MEMORY_FILE,)}
    for path in iter_structured_data_paths(reports_dir):
        day = _date_from_structured_path(path)
        if not day or day < str(since) or (until and day >= str(until)):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for name, body in updater.build_local_entries(data, date_str=day).items():
            out[name][day] = body
        entry = compounder._build_memory_entry(data, day)
        if entry:
            out[MEMORY_FILE][day] = entry.strip()
    return out


def _iso_week_bounds(day):
    monday = day - datetime.timedelta(days=day.weekday())
    return monday, monday + datetime.timedelta(days=6)


def covered_digest_week(report_date):
    """
    Which ISO week a digest run on ``report_date`` should cover: (week_start, last_session).

    - no TWSE session left in the report's week after report_date (Friday, Thursday
      before a Friday holiday, or a weekend/holiday rerun) → this week, ending at its
      last session on or before report_date
    - otherwise → the previous week (catch-up when its last session had no run)
    Returns None when no session can be found.
    """
    from modules.twse_calendar import is_twse_session

    day = _as_date(report_date)
    if day is None:
        return None
    monday, _ = _iso_week_bounds(day)
    later_session = any(
        is_twse_session(monday + datetime.timedelta(days=i)) for i in range(day.weekday() + 1, 5)
    )
    if not later_session:
        this_week = [monday + datetime.timedelta(days=i) for i in range(min(day.weekday(), 4) + 1)]
        this_week = [d for d in this_week if is_twse_session(d)]
        if this_week:
            return monday, this_week[-1]
    prev_monday = monday - datetime.timedelta(days=7)
    sessions = [prev_monday + datetime.timedelta(days=i) for i in range(5)]
    sessions = [d for d in sessions if is_twse_session(d)]
    if not sessions:
        return None
    return prev_monday, sessions[-1]


class KnowledgeUpdater:
    """
    Append-Only knowledge wiki updater.

    Default mode is local (no Gemini):
      KNOWLEDGE_WIKI_MODE=local|ai|off   (default: local)

    - local: build capital_rotation / macro_events / trading_strategies from structured_data
    - ai:    legacy Gemini append + Gemini weekly digest
    - off:   skip entirely

    Weekly digest (last TWSE session of the report's week, with catch-up) is local
    when mode=local, Gemini when mode=ai.
    """

    WIKI_FILES = ["capital_rotation.md", "macro_events.md", "trading_strategies.md"]
    DIGEST_FILE = "weekly_digest.md"
    DIGEST_STAMP_FILE = ".last_digest_date"

    def __init__(self, api_key=None, ensure_dir=True):
        self.api_key = api_key
        from modules.gemini_client import default_model_name
        self.model_name = default_model_name()
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.wiki_dir = os.path.join(base_dir, "data", "knowledge_wiki")
        self.reports_dir = os.path.join(base_dir, "reports")
        self.backup_dir = os.path.join(base_dir, "data", "backup")
        if ensure_dir:
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

    def _upsert_entry(self, filename, date_str, body, replace=False) -> str:
        """Store one dated entry in date order; ``replace`` rewrites an existing same-date entry."""
        content = self._read_file(filename)
        new_content, action = upsert_dated_block(content, date_str, body, replace=replace)
        if action in {"skipped", "unchanged"}:
            return action
        if action == "appended" and not replace:
            self._append_to_file(filename, body)
        else:
            self._write_file(filename, new_content)
        return action

    def _write_file(self, filename, content):
        """完整覆寫（weekly_digest、同日條目取代、去重）；以 temp file + os.replace 原子寫入。"""
        from modules.atomic_io import atomic_write_text

        filepath = self._path(filename)
        try:
            atomic_write_text(filepath, content)
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
        from modules.narrative_sanitize import sanitize_narrative_text

        cleaned = [sanitize_narrative_text(str(x).strip()) for x in (items or []) if str(x).strip()]
        if not cleaned:
            return "（無）"
        shown = cleaned[:limit]
        text = "；".join(shown)
        if len(cleaned) > limit:
            text += f" 等共 {len(cleaned)} 項"
        return text

    def _read_digest_stamp(self):
        stamp_path = self._path(self.DIGEST_STAMP_FILE)
        try:
            with open(stamp_path, "r") as f:
                return _as_date(f.read().strip())
        except OSError:
            return None

    def _should_run_weekly_digest(self, date_str=None):
        """
        週摘要觸發（以報告日期判斷，不看主機日期）：
        報告日是該週最後一個交易日（週五或週五休市前的週四）→ 壓縮本週；
        否則若上一週的摘要沒做（stamp 早於上週最後交易日）→ 補做上一週。
        """
        covered = covered_digest_week(date_str or datetime.date.today().isoformat())
        if not covered:
            return False
        _, week_end = covered
        stamp = self._read_digest_stamp()
        return stamp is None or stamp < week_end

    def _save_digest_stamp(self, covered_end=None):
        from modules.atomic_io import atomic_write_text

        stamp_path = self._path(self.DIGEST_STAMP_FILE)
        atomic_write_text(stamp_path, str(covered_end or datetime.date.today()))

    def _read_full_wiki_for_digest(self, week_start=None, week_end=None):
        """讀取 Wiki 內容供週壓縮使用；給定涵蓋週時只取該週條目（不混入上一週）。"""
        combined = ""
        for fn in self.WIKI_FILES:
            if week_start and week_end:
                blocks = self._extract_week_blocks(fn, week_start, week_end)
                content = "\n\n".join(body for _, body in blocks)
            else:
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
        """
        Build the three wiki entry bodies (without file wrappers).

        Only locally verifiable fields are written (amounts, ranks, % moves, local
        validation, calendar). Gemini narrative (price_volume_divergence, triggers,
        strategies) stays in the day's structured_data.json and is NOT fed back into
        the analyzer through the wiki.
        """
        structured_data = structured_data or {}
        date_str = date_str or structured_data.get("date") or datetime.date.today().isoformat()
        weekday = self._weekday_label(date_str)
        header = f"### {date_str}（週{weekday}）"

        buys = structured_data.get("safe_havens_bought") or []
        sells = structured_data.get("heavyweights_dumped") or []
        commodities = structured_data.get("commodities") or []
        index_action = structured_data.get("index_action") or "資料不足"
        trust_conflicts = structured_data.get("trust_conflicts") or []
        validation = structured_data.get("validation_results") or []
        prev_results = structured_data.get("prev_day_picks_result") or []
        targets = structured_data.get("prediction_targets") or []
        editorial = structured_data.get("editorial_frame") or {}

        # capital rotation
        observation_bits = []
        if trust_conflicts:
            observation_bits.append(f"外資／投信對作：{self._join_items(trust_conflicts, 2)}")
        frame_label = editorial.get("label") or editorial.get("frame")
        if frame_label:
            observation_bits.append(f"編輯框架：{frame_label}")
        if not observation_bits:
            observation_bits.append("本機條目：依當日法人榜整理，無額外敘事。")
        capital = "\n".join(
            [
                header,
                f"- **賣超/轉弱證據**：{self._join_items(sells)}",
                f"- **買超/轉強證據**：{self._join_items(buys)}",
                f"- **法人策略觀察**：{'；'.join(observation_bits)}",
            ]
        )

        # macro: calendar facts since the previous session + top fresh radar headline
        # (no Gemini divergence text)
        events = []
        since = None
        try:
            from modules.twse_calendar import prev_session

            since = prev_session(datetime.date.fromisoformat(str(date_str)[:10])).isoformat()
        except Exception:
            since = None
        for row in structured_data.get("calendar_events") or []:
            if not isinstance(row, dict) or row.get("when") not in {"已發生", "今天"}:
                continue
            if row.get("estimated"):
                # An estimated window (e.g. the unconfirmed MSCI effective day) is
                # not a calendar fact: the wiki only records verifiable events.
                continue
            event_date = str(row.get("date") or "")[:10]
            if row.get("when") == "已發生" and since and event_date and not (since < event_date <= str(date_str)):
                # already recorded under an earlier session (an event dated on the previous
                # session was written that day as 「今天」)
                continue
            text = str(row.get("text") or "").split("。")[0].strip()
            if text:
                events.append(f"{event_date} {text}".strip())
        for row in (structured_data.get("supply_chain_event_radar") or [])[:1]:
            if not isinstance(row, dict) or row.get("freshness") == "stale":
                continue
            try:
                score = int(row.get("impact_score") or 0)
            except (TypeError, ValueError):
                score = 0
            headline = re.split(r"\s+[-|｜]\s+", str(row.get("event") or ""))[0].strip()
            if score >= 60 and headline:
                events.append(f"事件雷達：{headline[:50]}（分數 {score}）")
        major = self._join_items(events, limit=2) if events else "無本機可驗證的重大事件"
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

        stops = []
        if targets:
            target_bits = []
            for t in targets[:3]:
                stop = t.get("stop_loss_price")
                stop_text = f"，停損 {stop}" if stop not in (None, "") else ""
                if stop not in (None, ""):
                    stops.append(f"{t.get('name') or t.get('id')} {stop}")
                target_bits.append(f"{t.get('id')} {t.get('name')}（{t.get('direction', '觀察')}{stop_text}）")
            targets_text = "；".join(target_bits)
        else:
            targets_text = "今日無正式 prediction_targets"

        if stops:
            risk = f"依停損價執行：{'、'.join(stops)}。"
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

    def _append_local_from_structured(self, structured_data, date_str, replace_existing=False) -> dict:
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
            action = self._upsert_entry(filename, date_str, body, replace=replace_existing)
            if action in {"skipped", "unchanged"}:
                skipped_existing += 1
                continue
            files_updated += 1

        if files_updated:
            verb = "寫入／取代" if replace_existing else "Append"
            print(f"    ✅ [Wiki local] 已{verb}本機條目至 {files_updated} 份 Wiki（不消耗 Gemini）")
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

    def _extract_week_blocks(self, filename, week_start, week_end) -> list[tuple[str, str]]:
        """Dated sections inside [week_start, week_end] only (no spill-over from the prior week)."""
        start, end = week_start.isoformat(), week_end.isoformat()
        _, rows = split_dated_blocks(self._read_file(filename))
        latest = {}
        for d, body in rows:  # last entry per date wins
            if start <= d <= end:
                latest[d] = body
        return sorted(latest.items())

    @staticmethod
    def _bullet(body, label):
        for ln in body.splitlines():
            if label in ln:
                return ln.strip().lstrip("-").strip()
        return ""

    def _run_weekly_digest_local(self, date_str):
        """Deterministic weekly digest from the covered ISO week's wiki entries (no Gemini)."""
        covered = covered_digest_week(date_str)
        if not covered:
            print("    ⚠️ [Weekly Digest local] 無法判斷涵蓋週，跳過。")
            return
        week_start, week_end = covered
        print(f"    📅 [Weekly Digest local] 壓縮 {week_start}～{week_end} 週摘要...")
        rotation = self._extract_week_blocks("capital_rotation.md", week_start, week_end)
        macro = self._extract_week_blocks("macro_events.md", week_start, week_end)
        strategies = self._extract_week_blocks("trading_strategies.md", week_start, week_end)
        if not any([rotation, macro, strategies]):
            print("    ⚠️ [Weekly Digest local] 該週無可壓縮條目，跳過。")
            return
        dates = sorted({d for d, _ in rotation + macro + strategies})
        lines = [
            f"# 週摘要：{dates[0]}～{dates[-1]}（本機）",
            f"<!-- digest_end: {dates[-1]} -->",
            "",
            "## 本週買賣證據鏈",
        ]
        if rotation:
            for d, body in rotation:
                sell = self._bullet(body, "賣超/轉弱證據")
                buy = self._bullet(body, "買超/轉強證據")
                if sell:
                    lines.append(f"- **{d}** {sell}")
                if buy:
                    lines.append(f"- **{d}** {buy}")
                if not sell and not buy:
                    lines.append(f"- **{d}**：（見 capital_rotation 原文）")
        else:
            lines.append("- （無 capital_rotation 本週條目）")

        lines.extend(["", "## 本週大盤與事件"])
        if macro:
            for d, body in macro:
                index_line = self._bullet(body, "大盤走勢")
                event_line = self._bullet(body, "重大事件")
                bits = [b for b in (index_line, event_line) if b]
                lines.append(f"- **{d}**：{'；'.join(bits) if bits else '（見 macro_events 原文）'}")
        else:
            lines.append("- （無 macro_events 本週條目）")

        lines.extend(["", "## 本週預測績效"])
        if strategies:
            hits = misses = 0
            for d, body in strategies:
                line = self._bullet(body, "預測驗收")
                if line:
                    hits += line.count("✅")
                    misses += line.count("❌")
                    lines.append(f"- **{d}**：{line}")
            if hits + misses:
                # Counts only: hit-rate claims are owned by the horizon/validation modules.
                lines.append(f"- 本週合計：成立 {hits}、未成立 {misses}（隔日單日窗，僅供內部參考）")
        else:
            lines.append("- （無 trading_strategies 本週條目）")

        lines.extend(["", "## 下週持續追蹤標的"])
        if strategies:
            last_date, last_body = strategies[-1]
            line = self._bullet(last_body, "明日觀察標的") or self._bullet(last_body, "觀察標的")
            lines.append(f"- 自 {last_date}：{line}" if line else "- （見 trading_strategies 最新條目）")
        else:
            lines.append("- （無）")

        lines.extend(
            [
                "",
                "---",
                "*本摘要由本機條目自動壓縮（KNOWLEDGE_WIKI_MODE=local），只含可驗證欄位；原始年表保留於各 Wiki 檔案中。*",
            ]
        )
        self._write_file(self.DIGEST_FILE, "\n".join(lines) + "\n")
        self._save_digest_stamp(week_end)
        print(f"    ✅ [Weekly Digest local] 已寫入 {self.DIGEST_FILE}")

    def rebuild_legacy_entries_once(self, date_str, window_days=LEGACY_REBUILD_DAYS):
        """
        One-time cleanup (marker file): entries written before the verifiable-fields-only
        builders still carry Gemini narrative (「屬安靜吸籌」「低調掃貨背離」, trigger and
        cash-ratio advice) that build_wiki_prompt_context would keep sending to the analyzer.
        Re-writes the existing entries of the last ``window_days`` (before ``date_str``) from
        their archived structured_data — the same job as
        ``repair_learning_logs.py --apply --rebuild-since`` — with a daily backup first.
        Only existing dated entries are replaced; nothing is back-filled. Never raises.
        """
        from modules.atomic_io import backup_file_daily

        marker = self._path(LEGACY_REBUILD_MARKER)
        if os.path.exists(marker):
            return {"skipped": True}
        try:
            day = _as_date(date_str) or datetime.date.today()
            since = (day - datetime.timedelta(days=window_days)).isoformat()
            rebuilt = rebuilt_entries_since(self.reports_dir, since, until=day.isoformat())
            replaced = {}
            for name, entries in rebuilt.items():
                content = self._read_file(name)
                if not content.strip() or not entries:
                    continue
                existing = {d for d, _, _ in _dated_spans(content)}
                new, count = content, 0
                for entry_date, body in sorted(entries.items()):
                    if entry_date not in existing:
                        continue
                    new, action = upsert_dated_block(new, entry_date, body, replace=True)
                    count += action == "replaced"
                if new != content:
                    backup_file_daily(self._path(name), backup_dir=self.backup_dir)
                    self._write_file(name, new)
                    replaced[name] = count
            from modules.atomic_io import atomic_write_text

            atomic_write_text(marker, f"{datetime.date.today().isoformat()} since={since} replaced={replaced}\n")
            if replaced:
                total = sum(replaced.values())
                print(f"    🧹 [Wiki local] 一次性重建 {total} 筆舊條目（只留可驗證欄位；備份於 data/backup/）")
            return {"skipped": False, "replaced": replaced}
        except Exception as e:
            print(f"    ⚠️  [Wiki local] 舊條目重建失敗，略過（下次再試）：{e}")
            return {"skipped": False, "error": str(e)}

    # ─────────────────────────────────────────
    # 主要公開方法
    # ─────────────────────────────────────────
    def update_knowledge_base(self, todays_generated_report_content, structured_data=None, report_date=None, replace_existing=False):
        """
        每日執行：
        1. local 模式：以 structured_data 寫入三份 Wiki（不消耗 Gemini）
        2. ai 模式：Gemini 產出今日新增條目
        3. 該週最後一個交易日（或補做上週）觸發 weekly_digest

        replace_existing=True：重跑（regen/resume）時以修正後的 structured_data 取代同日條目。
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
            self.rebuild_legacy_entries_once(date_str)
            result = self._append_local_from_structured(structured_data, date_str, replace_existing=replace_existing)
            if self._should_run_weekly_digest(date_str):
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

        if self._should_run_weekly_digest(date_str):
            self._run_weekly_digest(date_str)

        return {"mode": "ai", "files_updated": files_updated, "date": date_str}

    def _run_weekly_digest(self, date_str):
        """將完整 Wiki 年表壓縮為 weekly_digest.md（Gemini）。"""
        from modules.gemini_client import generate_text

        covered = covered_digest_week(date_str)
        week_start, week_end = covered if covered else (None, None)
        label = f"{week_start}～{week_end}" if covered else str(date_str)
        print(f"    📅 [Weekly Digest] 壓縮 {label} 週摘要（Gemini）...")
        full_wiki = self._read_full_wiki_for_digest(week_start, week_end)
        if not full_wiki.strip():
            print("    ⚠️ [Weekly Digest] Wiki 內容為空，跳過壓縮。")
            return

        api_key = self._require_api_key()
        prompt = f"""
你是一個財經知識庫的「週報壓縮員」。
請閱讀以下 Wiki 年表（只含 {label} 這一週的條目），
只根據條目內的數字與日期，壓縮成一份簡潔的 weekly_digest.md；不得推論法人意圖（鎖貨、吸籌、布局）。

本次涵蓋：{label}

{full_wiki}

---
請產出 weekly_digest.md 的完整內容，格式如下：

# 週摘要：{label}

## 本週買賣證據鏈
（本週外資最主要的買超/賣超方向、成交量佐證、驗收結果，3-5條）

## 本週重大宏觀事件
（本週影響台股的總經事件，3-5條）

## 本週預測績效
（本週成立 X 次、未成立 X 次；各列一行說明，不要換算成命中率）

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
            if week_end:
                text = f"{text.rstrip()}\n<!-- digest_end: {week_end.isoformat()} -->\n"
            self._write_file(self.DIGEST_FILE, text)
            self._save_digest_stamp(week_end)
            print(f"    ✅ [Weekly Digest] 週摘要已產出至 {self.DIGEST_FILE}")
        except Exception as e:
            print(f"    ❌ [Weekly Digest] 壓縮失敗: {e}")

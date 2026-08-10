# Root 入口與工具整理

專案根目錄現在只保留**主流程**與**單一工具入口**，其餘腳本集中在 `scripts/`。

## 根目錄應看到的 Python 檔

| 檔案 | 用途 |
| --- | --- |
| `main.py` | 每日盤後主流程 /1 |
| `main_us.py` | 美國盤流程 |
| `main_weekly.py` | 週期性流程 |
| `weekend_special_main.py` | 週末特輯 |
| `cli.py` | **所有維護 / YouTube / legacy 工具的統一入口** |

其他像 `regen_prompt.py`、`regen_assets.py` 這類檔案**不應再出現在 root**。

## 工具怎麼跑

```bash
# 列出全部工具
python3 cli.py list

# 常用
python3 cli.py maintenance check-daily-workflow --date 2026-06-30
python3 cli.py youtube regen-assets --date 2026-06-30 --visual-theme "FIFA 32強"
python3 cli.py youtube check-youtube-package --date 2026-06-30
python3 cli.py maintenance migrate-reports
```

## 目錄分工

| 位置 | 角色 |
| --- | --- |
| `scripts/maintenance/` | 健康檢查、回填、reports 搬移 |
| `scripts/youtube/` | /2、YouTube 成效、提醒 |
| `scripts/legacy/` | 歷史一次性工具（不再擴充） |
| `scripts/tool_registry.py` | 工具清單（`cli.py` 讀這份） |
| `compat/` | 舊檔名 redirect；執行時會提示改用 `cli.py` |

## 舊檔名對照

| 以前 root 的檔名 | 現在請用 |
| --- | --- |
| `check_daily_workflow.py` | `python3 cli.py maintenance check-daily-workflow` |
| `regen_assets.py` | `python3 cli.py youtube regen-assets` |
| `check_youtube_package.py` | `python3 cli.py youtube check-youtube-package` |
| `regen_prompt.py` | `python3 cli.py legacy regen-prompt` |
| `backfill_analytics.py` | `python3 cli.py maintenance backfill-analytics` |
| `seed_knowledge_wiki.py` | `python3 cli.py maintenance seed-knowledge-wiki` |
| `review_reminder.py` | `python3 cli.py youtube review-reminder` |
| `update_youtube_from_channel.py` | `python3 cli.py youtube update-from-channel` |

完整清單：`python3 cli.py list`

## 維護原則

- 新工具：實作放 `scripts/`，並登記到 `scripts/tool_registry.py`
- 不要再把新腳本丟回 root
- `automation/` 排程請直接呼叫 `scripts/...` 或 `cli.py`（`automation/run_review_reminder.sh` 已是 canonical 路徑）
- 若外部文件仍寫舊 root 檔名，可暫用 `compat/xxx.py`，但請逐步改到 `cli.py`
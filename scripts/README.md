# Scripts

這裡放不屬於主流程的維護、檢查、回填與一次性工具腳本。

## 分類

- `maintenance/`：資料回填、健康檢查、知識庫灌入。
- `youtube/`：YouTube 回填、包裝檢查、觀看數記錄、提醒排程。
- `legacy/`：歷史遺留的一次性工具，保留相容性但不再優先擴充。
- 統一入口是根目錄的 `cli.py`；舊檔名 redirect 在 `../compat/`。

## 原則

- 主流程入口仍保留在專案根目錄：`main.py`、`main_us.py`、`main_weekly.py`、`weekend_special_main.py`
- 新增或修改工具腳本時，優先放進 `scripts/`
- 根目錄不放工具腳本；新工具登記到 `tool_registry.py` 並透過 `cli.py` 執行

## 常用檢查

- 每日 `/1` + `/2` 工作流健康檢查：
  `python3 cli.py maintenance check-daily-workflow --date YYYY-MM-DD`

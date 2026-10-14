# 架構說明

這個專案可以先拆成四層看：

## 1. 入口層

- `main.py`：每日盤後主流程
- `main_us.py`：美股盤後觀察日記（`cli.py special us-daily`，休市期間手動）
- `main_weekly.py`：美股週報文案（`cli.py special us-weekly`，手動）
- `weekend_special_main.py`：週末特輯（`cli.py special weekend`，手動）
  - 三支都輸出到 `reports/special_topics/YYYY-MM-DD_slug/`；台股日曆、ADR 溢價、本週台股數字由 `modules/special_context.py` 本機計算後注入
- `app/main_app.py`：Web 服務

## 2. 服務層

`modules/` 放的是可重用的正式邏輯：

- 資料抓取
- 分析
- 內容生成
- 知識庫更新
- 評分與驗收
- 影片素材規劃

## 3. 資料層

- `data/`：快取、知識庫、stock db
- `reports/`：正式輸出與歸檔

## 4. 規則層

- `project_skills/`：流程規則、角色定義、標題規則、skills registry

## 目前的重點

現在的重構方向是：

1. 先把產物和原始碼分離
2. 再把工作流檔案拆成 stage
3. 最後才做更細的資料夾重整

這樣可以先降低誤用風險，再逐步降低維護成本。

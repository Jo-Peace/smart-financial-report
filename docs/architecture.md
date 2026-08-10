# 架構說明

這個專案可以先拆成四層看：

## 1. 入口層

- `main.py`：每日盤後主流程
- `main_us.py`：美股 / 週末相關流程
- `main_weekly.py`：週期性彙總流程
- `weekend_special_main.py`：週末專題
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

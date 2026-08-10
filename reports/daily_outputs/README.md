# daily_outputs（已退役）

此目錄為**舊版日包過渡區**，正式產物已遷至 `../daily/YYYY-MM-DD/`。
殘留內容會由 `consolidate-residuals` 搬到 `../archive/legacy/daily_outputs/`。

## 角色

- 保留曾經搬過來的一整日輸出 bundle
- 方便回頭比對某一天的完整產物
- 適合做歷史審查，不適合當今天工作入口

## 使用方式

- 今天要找最新內容，先看 `../CURRENT.md`
- 今天的主線輸出，先以 `../CURRENT.md` 指向的檔案為準
- 需要回看某一天完整 bundle，再進對應日期資料夾

## 目錄規則

- 每個日期資料夾對應一天
- 日期內通常包含：
  - `daily_report_V21_YYYY-MM-DD.md`
  - `structured_data_YYYYMMDD.json`
  - `notebooklm_prompt_podcast_YYYYMMDD.md`
  - `notebooklm_source_YYYYMMDD.md`
  - 視流程可能還有 `youtube_package_YYYY-MM-DD.md` 與縮圖 prompt

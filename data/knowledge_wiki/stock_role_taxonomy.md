# 個股角色分類規則

此檔由本機規則產生，提供報告、專題與 YouTube package 判斷標題包裝方式。

## 方向指標股 (`direction_indicator`)
- **標題用途**：適合用來確認資金方向，不宜包裝成最大彈性飆股。

## 彈性攻擊股 (`elastic_attack`)
- **標題用途**：適合放進標題或封面回答「誰先動、誰彈性大」。

## 題材確認股 (`theme_confirmation`)
- **標題用途**：適合用來驗證族群邏輯，標題應搭配更有彈性的同族群標的。

## 資金載體 (`capital_vehicle`)
- **標題用途**：適合描述資金流向或 ETF 熱度，不應當成單一個股爆發故事。

## 防守收息股 (`defensive_income`)
- **標題用途**：適合做風控或防守配置，不宜包裝成攻擊主軸。

## 風險提醒股 (`risk_warning`)
- **標題用途**：適合放在風險提醒、法人撤退或高檔套現段落。

## 間接受惠基建股 (`indirect_infrastructure`)
- **標題用途**：適合放在專題延伸，不宜說成 AI 伺服器核心 BOM 個股。

## 量化分數（`market_role.scores`）

每日本機更新，供標題 / 封面 / NotebookLM 主角排序：

- **elasticity**：本機快照的實現波動率與對加權指數 beta（樣本不足才退回角色先驗；方向股/ETF 會被壓低）
- **foreign_buy_strength**：外資買超排名與金額；有當日成交值時再看外資淨額占成交值比重
- **volume_confirmation**：個股量增比與成交值名次（ETF 排除）；不在股數前 20 名＝量能未知，不是量縮
- **validation_credibility**：歷史 prediction 命中可信度
- **title_priority**：綜合分（誰該當標題/封面/主角卡；近幾日已當主角會遞減）

規則：方向指標股（如台積電）即使買超大，title_priority 仍應低於彈性攻擊股。
角色來源寫在 `market_role.role_source`：structural（權值／ETF 代號）、data:institutional_flows（近 10 個交易日多次進外資賣超前 5 → 風險提醒股）、rule（本機文字命中的關鍵字）或 default（未命中任何標記）。

# Analytics Summary

- Generated at: 2026-10-14 17:07
- Database: `data/analytics.db`
- Daily runs: 135
- Predictions: 185
- Evaluated results: 379 (due but unpriced → missing: 310)
- AI pick records: 227
- Editorial frames: 86
- YouTube asset plans: 85
- YouTube metric samples: 130

## Performance By Horizon

Horizon = N TWSE sessions after the prediction-day close; hit = excess return vs the same-window TWII move beyond a dead band (neutral and missing rows are excluded). Close-to-close, stop-loss checked on the close path; not a tradable fill. Baseline 50%; 95% Wilson CI on cluster-adjusted n. Raw Hit = direction sign only; compare with TWII Same-Dir (share of windows where the index itself moved the predicted way). Avg Return / Avg Excess are direction-adjusted (a short call on a falling stock is positive). Stop = stop-loss exits (always misses); Neutral = inside the dead band (not counted); Cond. = conditional entries in the denominator whose condition was not verified (no OHLC); Unjudged = priced but no same-window TWII close (not counted).

- 依本機回測（交易日窗、扣同窗大盤），各時間窗超額命中率的 95% 信賴區間都未高於 50% 基準，樣本不足以判定哪個時間窗較準；先並列 1D/3D/5D/20D，勿稱任一窗較準，也勿把命中率當成敗尺子。
- 參考：20D 目前超額命中率 22.4%（95% CI 12–37%，n=76），整段區間低於 50% 基準（目前樣本沒有贏過大盤）。

| Horizon | Samples | Eff. n | Excess Hit | 95% CI | Raw Hit | TWII Same-Dir | Avg Return | Avg Excess | Coverage | Stop / Neutral / Cond. / Unjudged | Read as |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1D | 138 | 114 | 47.8% | 39–57% | 43.5% | 56.1% | +0.59% | +0.29% | 85.8% | 14 / 19 / 8 / 4 | 樣本不足以判定 |
| 3D | 60 | 50 | 41.7% | 29–55% | 43.7% | 55.2% | +0.30% | -0.33% | 37.4% | 22 / 7 / 3 / 4 | 樣本不足以判定 |
| 5D | 61 | 55 | 39.3% | 28–53% | 37.9% | 58.5% | +1.84% | +1.05% | 37.1% | 29 / 4 / 3 / 1 | 樣本不足以判定 |
| 20D | 76 | 43 | 22.4% | 12–37% | 27.2% | 50.6% | +0.74% | -0.21% | 52.0% | 52 / 3 / 3 / 2 | 樣本不足以判定 |

## Performance By Direction
| Direction | Samples | Hit Rate | Avg Return | Avg Excess |
| --- | --- | --- | --- | --- |
| 多 | 325 | 38.8% | +0.92% | +0.25% |
| 空 | 10 | 60.0% | -1.52% | +1.35% |

## Performance By Signal Feature (5D / 20D)
Features observable at prediction time, joined on (prediction_date, ticker). Small cells are noise; read the CI. Not persisted yet (shown as 未記錄): 候選來源, 訊號類型.

| Horizon | Feature | Bucket | Samples | Excess Hit | 95% CI | Avg Excess |
| --- | --- | --- | --- | --- | --- | --- |
| 5D | MA20 | 收盤在MA20上 | 56 | 39.3% | 27–53% | +0.61% |
| 5D | MA20 | 收盤在MA20下 | 5 | 40.0% | 10–80% | +5.32% |
| 5D | 外資排名 | 外資買超4-10 | 2 | 50.0% | 9–91% | +3.02% |
| 5D | 外資排名 | 外資買超前3 | 56 | 41.1% | 29–55% | +1.37% |
| 5D | 外資排名 | 外資賣超榜 | 2 | 0.0% | 0–66% | -7.86% |
| 5D | 外資排名 | 未上外資榜 | 1 | 0.0% | 0–79% | -4.51% |
| 5D | 量增比 | 量增比<2 | 57 | 40.4% | 28–54% | +1.49% |
| 5D | 量增比 | 量增比≥2 | 4 | 25.0% | 5–70% | -4.25% |
| 20D | MA20 | 收盤在MA20上 | 66 | 21.2% | 11–36% | -0.67% |
| 20D | MA20 | 收盤在MA20下 | 10 | 30.0% | 9–65% | +2.62% |
| 20D | 外資排名 | 外資買超4-10 | 3 | 0.0% | 0–56% | -8.79% |
| 20D | 外資排名 | 外資買超前3 | 70 | 24.3% | 14–39% | +0.41% |
| 20D | 外資排名 | 外資賣超榜 | 2 | 0.0% | 0–66% | -5.02% |
| 20D | 外資排名 | 未上外資榜 | 1 | 0.0% | 0–79% | -4.51% |
| 20D | 量增比 | 量增比<2 | 67 | 22.4% | 12–38% | +0.36% |
| 20D | 量增比 | 量增比≥2 | 9 | 22.2% | 6–55% | -4.67% |

## Recent Prediction Results
| Pred Date | Ticker | Name | Dir | Horizon | Target | Exit Date | Status | Return | Index | Excess | Hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-13 | 1303 | 南亞 | 多 | 1D | 2026-10-14 | 2026-10-14 | ok | +9.40% | +0.59% | +8.82% | Y |
| 2026-10-13 | 2449 | 京元電子 | 多 | 1D | 2026-10-14 | 2026-10-14 | ok | -2.79% | +0.59% | -3.38% | N |
| 2026-10-08 | 3037 | 欣興 | 多 | 3D | 2026-10-14 | 2026-10-14 | ok | -6.69% | -1.39% | -5.30% | N |
| 2026-10-08 | 3231 | 緯創 | 多 | 3D | 2026-10-14 | 2026-10-14 | ok | -0.80% | -1.39% | +0.60% | – |
| 2026-10-06 | 2313 | 華通 | 多 | 5D | 2026-10-14 | 2026-10-14 | ok | -0.60% | -2.40% | +1.81% | Y |
| 2026-10-06 | 4958 | 臻鼎-KY | 多 | 5D | 2026-10-14 | 2026-10-14 | ok | -1.22% | -2.40% | +1.18% | Y |
| 2026-09-11 | 2368 | 金像電 | 多 | 20D | 2026-10-14 | 2026-10-14 | ok | +22.33% | +5.29% | +17.04% | Y |
| 2026-09-11 | 2609 | 陽明 | 多 | 20D | 2026-10-14 | 2026-10-14 | ok | +9.43% | +5.29% | +4.14% | Y |
| 2026-10-12 | 2603 | 長榮 | 多 | 1D | 2026-10-13 | 2026-10-13 | ok | +3.33% | -1.95% | +5.28% | Y |
| 2026-10-12 | 3443 | 創意 | 多 | 1D | 2026-10-13 | 2026-10-13 | ok | -0.06% | -1.95% | +1.89% | Y |
| 2026-10-08 | 3037 | 欣興 | 多 | 1D | 2026-10-12 | 2026-10-12 | ok | -1.18% | -0.02% | -1.16% | N |
| 2026-10-08 | 3231 | 緯創 | 多 | 1D | 2026-10-12 | 2026-10-12 | ok | +0.53% | -0.02% | +0.55% | Y |

## Recent Predictions
| Date | Ticker | Name | Dir | Trigger |
| --- | --- | --- | --- | --- |
| 2026-10-14 | 1303 | 南亞 | 多 | 外資買超 55.3 億居冠，且基本面毛利率連三季改善 |
| 2026-10-14 | 2327 | 國巨* | 多 | 外資買超第 3 名（36.6 億），股價轉強突破 MA20 |
| 2026-10-13 | 1303 | 南亞 | 多 | 外資買超 23.9 億，轉型為 AI 電子化學基礎族群後，籌碼穩定 |
| 2026-10-13 | 2449 | 京元電子 | 多 | 外資買超 17.2 億全台第三，大盤崩跌下唯一收紅的 AI 測試股 |
| 2026-10-12 | 2603 | 長榮 | 多 | 外資買超第2名且量增比2.94倍，爆量突破MA20轉強 |
| 2026-10-12 | 3443 | 創意 | 多 | 外資買超金額全台第1名，回檔時法人進行結構性鎖貨 |
| 2026-10-08 | 3037 | 欣興 | 多 | 外資逆勢收貨，看好ABF載板需求隨伺服器平台轉換回升 |
| 2026-10-08 | 3231 | 緯創 | 多 | 外資買超第2名且技術位站上MA20，成功避開成交量過熱區 |
| 2026-10-07 | 2344 | 華邦電 | 多 | 外資買超第2名且帶量站上MA20，反映記憶體報價回升邏輯 |
| 2026-10-07 | 4958 | 臻鼎-KY | 多 | 外資買超第1名且成交量尚未過熱，法人靜默吸籌 |
| 2026-10-06 | 2313 | 華通 | 多 | 外資買超第1名且帶量突破MA20，法人鎖貨跡象明顯 |
| 2026-10-06 | 4958 | 臻鼎-KY | 多 | 外資買超第3名，股價回測MA20支撐有成 |

## YouTube Title / Thumbnail Metrics

（每支影片取最新一次量測、排除週末特輯；顯示中位數）

| Title Var | Thumb Var | Videos | Median Views | Max Views | Avg CTR |
| --- | --- | --- | --- | --- | --- |
| B | N/A | 4 | 306 | 1100 | N/A |
| b | N/A | 1 | 217 | 217 | N/A |
| N/A | N/A | 32 | 198 | 2629 | N/A |
| manual | N/A | 42 | 112 | 1099 | N/A |
| A | N/A | 16 | 90 | 1111 | N/A |
| a | N/A | 17 | 76 | 1087 | N/A |

## Editorial Frame Mix
| Frame | Samples | Avg Confidence |
| --- | --- | --- |
| 重大事件日 | 41 | 76 |
| 驗收日 | 38 | 83 |
| 續航日 | 5 | 71 |
| 整理日 | 1 | 58 |
| 異常日 | 1 | 69 |

## Recent YouTube Measurements

（Quality：≥ 頻道中位數 142 × 2 = 285 views 才是 valid，可判 A/B；未達只作觀察；上架未滿 72 小時不判。n=28；中位數基準：與 /2、週報同一基準：近 30 支 channel_story、排除站外導流，累積觀看（僅上架 ≥72 小時；尚無足夠同齡快照））

| Date | Measured At | Views | Quality | Likes | Comments | CTR | Title | Thumb | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-13 | 2026-10-14 17:06:55 | 18 | young (<72h) | 2 | 0 | N/A | manual |  |  |
| 2026-10-12 | 2026-10-14 17:06:55 | 19 | young (<72h) | 1 | 0 | N/A | manual |  |  |
| 2026-10-08 | 2026-10-14 17:06:55 | 50 | observe | 1 | 0 | N/A | manual |  |  |
| 2026-10-07 | 2026-10-14 17:06:55 | 681 | valid (≥2× median) | 1 | 0 | N/A | manual |  |  |
| 2026-10-06 | 2026-10-14 17:06:55 | 125 | observe | 2 | 0 | N/A | manual |  |  |
| 2026-10-05 | 2026-10-14 17:06:55 | 122 | observe | 1 | 0 | N/A | manual |  |  |
| 2026-10-02 | 2026-10-14 17:06:55 | 1099 | valid (≥2× median) | 4 | 0 | N/A | manual |  |  |
| 2026-10-01 | 2026-10-14 17:06:55 | 61 | observe | 2 | 0 | N/A | manual |  |  |
| 2026-09-30 | 2026-10-14 17:06:55 | 327 | valid (≥2× median) | 2 | 0 | N/A |  |  |  |
| 2026-09-29 | 2026-10-14 17:06:55 | 579 | valid (≥2× median) | 2 | 0 | N/A |  |  |  |
| 2026-09-24 | 2026-10-14 17:06:55 | 53 | observe | 1 | 0 | N/A |  |  |  |
| 2026-09-23 | 2026-10-14 17:06:55 | 1059 | valid (≥2× median) | 2 | 0 | N/A | manual |  |  |

## Frequent AI Picks
| Count | Pick |
| --- | --- |
| 3 | 華邦電 (外資大買) |
| 2 | 今日無安全推薦標的 |
| 2 | 南亞科 (外資大買) |
| 2 | 台積電 (外資大買) |
| 2 | 台積電 (外資影子建倉) |
| 2 | 大立光 (外資大買) |
| 2 | 華通 (外資大買) |
| 1 | 1301 台塑 (低基期剛起漲) |
| 1 | 1717 長興 (外資點火爆量突破) |
| 1 | 2301 光寶科 (帶量突破 MA20) |
| 1 | 2303 聯電 (主升段確認且外資持續買超) |
| 1 | 2303 聯電 (外資買超冠軍且毛利改善) |

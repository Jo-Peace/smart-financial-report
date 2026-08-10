# YouTube Thumbnail Prompt｜2026-07-16 台積電法說

視覺主題：**護國神山 × 財經史詩**（原創神山，非特定電影/遊戲/宗教 IP）  
規則：神山是場景，**數字才是鉤子**；兩組構圖必須明顯不同，方便 A/B 測 CTR。

共通限制：

- 繁中大字清晰、高對比、小圖可讀
- 博士 + 韭菜（韭菜綠特徵固定）
- 台股配色：漲/強勢用紅，跌/壓力用綠
- 禁止：真實公司 Logo、國旗滿版土味海報、真實人物、可辨識動漫/遊戲 IP、亂碼英文

---

## A 組封面（數據衝擊型｜建議先測）

**測試假設：**「三創新高」大數字比神山氛圍更能停滑。

### 封面大字（生圖必須一模一樣）

| 層級 | 文字 |
|------|------|
| 主標第 1 行 | `台積電法說` |
| 主標第 2 行 | `三創新高！` |
| Badge | `EPS 27.25` / `毛利 67.7%` |
| 左上小日期 | `7/16 台股盤後` |

### 對應標題

```text
台積電EPS 27.25毛利率67.7%三創新高！Capex上修600億，大盤卻平盤？供應鏈誰先分化｜7/16 AI帶你看股市
```

### Image Prompt（A）

```text
Create a complete 16:9 YouTube finance thumbnail, ultra high contrast, high CTR, landscape.

Daily visual theme: 護國神山 (Guardian Sacred Mountain of Taiwan finance) — ORIGINAL mythic Taiwanese mountain metaphor only. Do NOT copy any movie, anime, game, temple brand, or franchise. The mountain is built from stacked semiconductor wafer layers, glowing circuit-veins, and holographic earnings panels on the summit.

Composition (must differ from B): low-angle hero shot looking UP at the sacred mountain peak. Peak is a giant transparent earnings monolith showing three glowing record numbers: EPS 27.25, Gross Margin 67.7%, Revenue 402億. Red (Taiwan bullish) energy beams shoot from the summit into the sky. Background Taiwan market ticker is almost flat/gray, while the mountain itself blazes strong red strength. Cinematic volumetric light, dark navy-gold atmosphere, epic but clean negative space for text.

Characters:
- "博士": Taiwanese male data strategist ~40, black round glasses, calm commander posture, pointing at the three record numbers on the monolith.
- "韭菜": Taiwanese male retail investor ~30, leek-leaf hair tuft or leek-green accessory clearly visible, shocked-excited face, holding a phone with charts. Stand lower on the mountain slope for scale contrast.

IMPORTANT exact Traditional Chinese text (must render cleanly, no gibberish):
- Huge two-line title occupying ~35% foreground:
  "台積電法說"
  "三創新高！"
- Two small badges near title: "EPS 27.25" and "毛利 67.7%"
- Small upper-left date label: "7/16 台股盤後"

Typography: bold white/yellow with thick black stroke, perfect Traditional Chinese, readable at phone size.
No company logos, no national-flag spam, no real person likeness, no extra random letters/numbers beyond specified text.
Ultra sharp, dramatic, YouTube finance thumbnail energy.
```

---

## B 組封面（好奇心缺口型｜48h 可換測）

**測試假設：**「Capex 誰受惠」問題感，對已知道法說利多的觀眾更有點擊理由。

### 封面大字（生圖必須一模一樣）

| 層級 | 文字 |
|------|------|
| 主標第 1 行 | `護國神山` |
| 主標第 2 行 | `誰真受惠？` |
| Badge | `Capex 600億` / `供應鏈分化` |
| 左上小日期 | `7/16 台股盤後` |

### 對應標題

```text
法說全年年增拚40%！Q3營收446億再衝高，為何華通欣興反而跌？利多出盡還是換股｜7/16 AI帶你看股市
```

### Image Prompt（B）

```text
Create a complete 16:9 YouTube finance thumbnail, ultra high contrast, high CTR, landscape.

Daily visual theme: 護國神山 — ORIGINAL Taiwan finance sacred mountain only. No franchise IP. Mountain of stacked wafers and golden capital-flow rivers.

Composition (must be TOTALLY different from A): TOP-DOWN / oblique map-like shot of the mountain. From the summit, three capital-flow rivers split downhill into different valleys labeled only by abstract icons (advanced process gear, packaging stack, materials crystal) — NOT real company logos. One main river is bright red and strong; two side rivers flicker mixed red/green, suggesting supply-chain divergence. A flat gray market plain surrounds the mountain. Mystery spotlight on the fork: which path benefits?

Characters:
- "博士": at the summit ridge, holding a tablet, presenting like a strategist, calm.
- "韭菜": mid-slope at the river fork, leek-green accents visible, confused and asking which path to take, phone in hand, exaggerated curiosity.

IMPORTANT exact Traditional Chinese text:
- Huge two-line title:
  "護國神山"
  "誰真受惠？"
- Two small badges: "Capex 600億" and "供應鏈分化"
- Small upper-left date: "7/16 台股盤後"

Typography: bold white/yellow with black stroke, perfect Traditional Chinese, no Simplified Chinese, no gibberish.
No company logos, no flag collage, no real people, no extra random text.
Different camera angle, character scale, and layout from version A. Ultra sharp, suspenseful, high CTR.
```

---

## A/B 測試操作

| 步驟 | 做法 |
|------|------|
| 1 | 先上 **A**（三創新高）+ 標題 A |
| 2 | 記錄 24h CTR / 曝光 / 前 30 秒留存 |
| 3 | 48h 後若 views ≥ 900 且 CTR 偏低，換 **B** 封面（可保留標題 A，或連標題 B 一起換） |
| 4 | views &lt; 900 只觀察，不判勝負 |

### 對照變因（方便你事後寫 experiment log）

| 組 | 主鉤 | 視覺重點 |
|----|------|----------|
| A | 結果數字（三創新高） | 仰角神山 + 財報巨碑 |
| B | 未解問題（誰真受惠） | 俯角分流 + 供應鏈分化 |

---

## 生圖備註

- 若中文糊掉：同一 prompt 重產，或 Canva 覆蓋精確大字  
- 神山務必「晶圓層疊 + 全息數據」，避免變成普通風景照  
- 兩張不要同一構圖只改字；否則 A/B 測不出差異  

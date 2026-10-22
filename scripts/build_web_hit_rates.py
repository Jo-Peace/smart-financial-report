"""Build data/web_hit_rates.json for the public web app.

Only public channel prediction stats are exported (ticker -> name, hits, total,
last date, market role). No holdings, trades, prompts or keys.

    python3 scripts/build_web_hit_rates.py [--src data/stock_db.json]
"""
import argparse
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(stock_db: dict) -> dict:
    out = {}
    for ticker, entry in sorted(stock_db.items()):
        tracking = (entry or {}).get("tracking") or {}
        history = [h for h in tracking.get("history") or [] if isinstance(h, dict)]
        counted = [h for h in history if h.get("counted", True) and ("✅" in str(h.get("result")) or "❌" in str(h.get("result")) or "hit" in h)]
        total = len(counted)
        if not total:
            continue
        hits = sum(1 for h in counted if h.get("hit") is True or (h.get("hit") is None and "✅" in str(h.get("result"))))
        last = max((str(h.get("date") or "")[:10] for h in history), default="") or tracking.get("last_recommended")
        role = (entry.get("market_role") or {})
        out[str(ticker)] = {
            "name": entry.get("name") or "",
            "hits": hits,
            "total": total,
            "last_date": last,
            "role": role.get("base_role") or "",
            "role_label": role.get("label") or "",
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "data", "stock_db.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "web_hit_rates.json"))
    args = ap.parse_args()
    with open(args.src, encoding="utf-8") as f:
        summary = build(json.load(f))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(f"wrote {len(summary)} tickers -> {args.out}")


if __name__ == "__main__":
    main()

"""
src/news.py — Team news fetcher and xG adjustment engine.

Modes
-----
--fetch   Query Anthropic web-search for injury/squad news for today's
          fixtures.  Writes data/news/<fixture_id>.json for each match.

--apply   Read every data/news/<fixture_id>.json that has non-trivial
          multipliers and re-run the Poisson model on those matches,
          overwriting outputs/predictions.json in-place.

Usage
-----
  python src/news.py --fetch   # morning job, step 2
  python src/news.py --apply   # morning job, step 4  (also called by lineup.py)
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT     = Path(__file__).parent.parent
DATA     = ROOT / "data"
NEWS_DIR = DATA / "news"
OUTPUTS  = ROOT / "outputs"

NEWS_DIR.mkdir(exist_ok=True)

# ── Import Poisson helpers from predict.py (avoids duplicating the model) ────
sys.path.insert(0, str(ROOT / "src"))
from predict import (
    score_matrix,
    wdl_from_matrix,
    top_scorelines,
    blend,
    odds_to_probs,
    MARKET_WEIGHT,
    MODEL_WEIGHT,
)

# Clamp multipliers to a sensible range so a bad LLM response can't break things
MULT_MIN, MULT_MAX = 0.75, 1.05


# ── Anthropic web search ──────────────────────────────────────────────────────

def _fetch_news_for_match(client, match: dict) -> dict:
    """
    Ask Claude (with web search) for squad news affecting today's match.
    Returns a dict ready to be saved as data/news/<id>.json.
    """
    home, away = match["home"], match["away"]
    fid        = match["id"]

    prompt = (
        f"Search for the latest FIFA World Cup 2026 pre-match news about "
        f"{home} and {away}. Focus on:\n"
        f"- Confirmed injury or suspension absences\n"
        f"- Key player doubts or late fitness tests\n"
        f"- Rotation hints from the manager\n\n"
        f"Respond with ONLY a JSON object — no prose, no markdown fences:\n"
        f'{{\n'
        f'  "home_multiplier": 1.0,\n'
        f'  "away_multiplier": 1.0,\n'
        f'  "notes": "1-2 sentence injury/squad summary for both teams",\n'
        f'  "home_scorers": ["Player Name (role/reason)", "Player Name (role/reason)", "Player Name (role/reason)"],\n'
        f'  "away_scorers": ["Player Name (role/reason)", "Player Name (role/reason)", "Player Name (role/reason)"]\n'
        f'}}\n\n'
        f"home_multiplier / away_multiplier: between {MULT_MIN:.2f} and {MULT_MAX:.2f} "
        f"based on absences ({MULT_MAX:.2f} if full strength, {MULT_MIN:.2f} if key striker/playmaker missing). "
        f"home_scorers / away_scorers: top 3 most likely goalscorers for each team, "
        f"with a brief parenthetical (e.g. 'leading striker', 'set-piece threat', 'on penalties')."
    )

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"    API error for {home} vs {away}: {exc}")
        return _neutral(fid, home, away, "api_error")

    # Extract text from the final non-tool-use block
    text = "".join(
        block.text for block in resp.content if hasattr(block, "text")
    )

    # Extract outermost JSON object (handles nested arrays)
    start = text.find('{')
    end   = text.rfind('}')
    if start < 0 or end <= start:
        print(f"    No JSON found in response for {home} vs {away}")
        return _neutral(fid, home, away, "no_json")

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return _neutral(fid, home, away, "bad_json")

    hm = float(data.get("home_multiplier", 1.0))
    am = float(data.get("away_multiplier", 1.0))

    def clean_scorers(raw):
        if isinstance(raw, list):
            return [str(s)[:80] for s in raw[:3]]
        return []

    return {
        "fixture_id":       fid,
        "home":             home,
        "away":             away,
        "home_multiplier":  round(max(MULT_MIN, min(MULT_MAX, hm)), 3),
        "away_multiplier":  round(max(MULT_MIN, min(MULT_MAX, am)), 3),
        "notes":            str(data.get("notes", ""))[:500],
        "home_scorers":     clean_scorers(data.get("home_scorers", [])),
        "away_scorers":     clean_scorers(data.get("away_scorers", [])),
        "source":           "anthropic_web_search",
        "fetched_at":       datetime.now(timezone.utc).isoformat(),
    }


def _neutral(fid, home, away, source):
    return {
        "fixture_id":       fid,
        "home":             home,
        "away":             away,
        "home_multiplier":  1.0,
        "away_multiplier":  1.0,
        "notes":            "",
        "home_scorers":     [],
        "away_scorers":     [],
        "source":           source,
        "fetched_at":       datetime.now(timezone.utc).isoformat(),
    }


def cmd_fetch():
    """--fetch: search web and write data/news/<id>.json for today's matches."""
    sys.stdout.reconfigure(encoding="utf-8")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set — skipping news fetch")
        return

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    with open(DATA / "fixtures.json", encoding="utf-8") as f:
        fixtures_data = json.load(f)

    today = date.today().isoformat()
    todays = [
        m for m in fixtures_data["matches"]
        if m.get("date") == today and m.get("home") and m.get("away")
    ]

    if not todays:
        print(f"No fixtures today ({today}) — no news to fetch")
        return

    print(f"Fetching news for {len(todays)} match(es) on {today}:")

    for match in todays:
        home, away = match["home"], match["away"]
        print(f"  {home} vs {away} ...", end=" ", flush=True)

        result = _fetch_news_for_match(client, match)
        out    = NEWS_DIR / f"{match['id']}.json"

        with open(out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        hm = result["home_multiplier"]
        am = result["away_multiplier"]
        flag = " ⚠" if hm < 0.97 or am < 0.97 else ""
        print(f"home×{hm:.2f}  away×{am:.2f}{flag}")

    print("✓ News fetch complete")


# ── Adjustment application ────────────────────────────────────────────────────

def cmd_apply():
    """--apply: re-run Poisson on any prediction with a non-trivial multiplier."""
    preds_path = OUTPUTS / "predictions.json"
    if not preds_path.exists():
        sys.exit("outputs/predictions.json not found — run predict.py first")

    with open(preds_path, encoding="utf-8") as f:
        preds_data = json.load(f)

    changed = 0

    for p in preds_data["predictions"]:
        news_file = NEWS_DIR / f"{p['id']}.json"
        if not news_file.exists():
            continue

        with open(news_file, encoding="utf-8") as f:
            adj = json.load(f)

        hm = float(adj.get("home_multiplier", 1.0))
        am = float(adj.get("away_multiplier", 1.0))

        # Always copy scorer/notes data so the dashboard tab can display it
        p["team_news"] = {
            "notes":        adj.get("notes", ""),
            "home_scorers": adj.get("home_scorers", []),
            "away_scorers": adj.get("away_scorers", []),
            "fetched_at":   adj.get("fetched_at", ""),
        }

        # Only re-run Poisson when multipliers meaningfully differ from 1.0
        if abs(hm - 1.0) < 0.001 and abs(am - 1.0) < 0.001:
            continue

        # Skip TBD knockout matches (no xG to adjust)
        if p.get("xg_home") is None or p.get("xg_away") is None:
            continue

        xg_h = p["xg_home"] * hm
        xg_a = p["xg_away"] * am

        mat       = score_matrix(xg_h, xg_a)
        model_wdl = wdl_from_matrix(mat)

        # Re-blend with market odds if we have them (same 60/40 as predict.py)
        mkt_entry = None
        if p.get("market_win") and p.get("market_draw") and p.get("market_loss"):
            mkt_entry = {
                "home_win": 1.0 / p["market_win"],
                "draw":     1.0 / p["market_draw"],
                "away_win": 1.0 / p["market_loss"],
            }
        market_wdl = odds_to_probs(mkt_entry)
        final_wdl  = blend(model_wdl, market_wdl)

        p["xg_home"]   = round(xg_h, 3)
        p["xg_away"]   = round(xg_a, 3)
        p["win_prob"]  = round(final_wdl[0], 4)
        p["draw_prob"] = round(final_wdl[1], 4)
        p["loss_prob"] = round(final_wdl[2], 4)
        p["model_win"]  = round(model_wdl[0], 4)
        p["model_draw"] = round(model_wdl[1], 4)
        p["model_loss"] = round(model_wdl[2], 4)
        p["top_scorelines"] = top_scorelines(mat)
        p["score_matrix"]   = [[round(v, 5) for v in row] for row in mat]
        p["news_adjustment"] = {
            "home": hm,
            "away": am,
            "notes": adj.get("notes", ""),
        }

        changed += 1
        print(f"  Applied news adj to {p.get('home')} vs {p.get('away')}: "
              f"xG ×{hm:.2f}/×{am:.2f}")

    with open(preds_path, "w", encoding="utf-8") as f:
        json.dump(preds_data, f, indent=2, ensure_ascii=False)

    if changed:
        print(f"✓ News adjustments applied to {changed} match(es)")
    else:
        print("✓ No adjustments needed (all multipliers are 1.0)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Team news fetcher / adjustment engine")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fetch", action="store_true",
                       help="Fetch team news via Anthropic web search")
    group.add_argument("--apply", action="store_true",
                       help="Apply data/news/ multipliers to predictions.json")
    args = parser.parse_args()

    if args.fetch:
        cmd_fetch()
    else:
        cmd_apply()


if __name__ == "__main__":
    main()

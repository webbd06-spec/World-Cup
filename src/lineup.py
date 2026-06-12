"""
src/lineup.py — Pre-kickoff lineup fetcher and adjustment writer.

Modes
-----
--check   Scan fixtures.json for any match with kickoff UTC within the next
          90 minutes.  Writes GitHub Actions step outputs:
            has_match=true|false
            match_ids=GS_L1 GS_A2   (space-separated, only when has_match=true)
          Exits 0 in all cases (missing/no-match is not an error).

--update <id> [id ...]
          For each fixture ID:
            1. Fetch the confirmed lineup from football-data.org (/v4/matches/<api_id>)
            2. Derive xG multipliers from missing players
            3. Write/update data/news/<id>.json
          Then calls news.py --apply to propagate changes into predictions.json.

Usage
-----
  python src/lineup.py --check
  python src/lineup.py --update GS_L1
  python src/lineup.py --update GS_L1 GS_A2
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT     = Path(__file__).parent.parent
DATA     = ROOT / "data"
NEWS_DIR = DATA / "news"
OUTPUTS  = ROOT / "outputs"

NEWS_DIR.mkdir(exist_ok=True)

FD_BASE = "https://api.football-data.org/v4"


# ── football-data.org helper ──────────────────────────────────────────────────

def _fd_get(path: str) -> dict:
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    headers = {
        "X-Auth-Token": api_key,
        "Accept":       "application/json",
    }
    req = urllib.request.Request(FD_BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# ── fixture loader ────────────────────────────────────────────────────────────

def _load_fixtures() -> list:
    with open(DATA / "fixtures.json", encoding="utf-8") as f:
        return json.load(f)["matches"]


def _fixture_by_id(fid: str) -> dict | None:
    return next((m for m in _load_fixtures() if m["id"] == fid), None)


# ── --check ───────────────────────────────────────────────────────────────────

def cmd_check():
    """
    Find fixtures kicking off within the next 90 minutes (UTC).
    Writes to GITHUB_OUTPUT if running in Actions, otherwise prints.
    """
    fixtures = _load_fixtures()
    now      = datetime.now(timezone.utc)
    cutoff   = now + timedelta(minutes=90)

    upcoming = []
    for m in fixtures:
        if not m.get("home") or not m.get("away"):
            continue  # TBD knockout
        date_str = m.get("date", "")
        ko_str   = m.get("kickoff_utc", "")
        if not date_str or not ko_str:
            continue
        try:
            kickoff = datetime.fromisoformat(f"{date_str}T{ko_str}:00+00:00")
        except ValueError:
            continue
        if now <= kickoff <= cutoff:
            upcoming.append(m["id"])

    _set_output("has_match", "true" if upcoming else "false")
    if upcoming:
        _set_output("match_ids", " ".join(upcoming))
        print(f"Upcoming kickoffs: {', '.join(upcoming)}")
    else:
        print(f"No matches within 90 minutes of {now.strftime('%H:%M UTC')}")


# ── --update ──────────────────────────────────────────────────────────────────

# xG penalty per confirmed absent outfield starter (very rough heuristic).
# A full implementation would weight by player Elo / market value.
_MISSING_PENALTY = 0.06   # 6 % reduction per absent starter

def cmd_update(fixture_ids: list[str]):
    """
    Fetch lineups for each fixture, write data/news/<id>.json,
    then invoke news.py --apply to push changes into predictions.json.
    """
    for fid in fixture_ids:
        fixture = _fixture_by_id(fid)
        if not fixture:
            print(f"  {fid}: not found in fixtures.json — skipping")
            continue

        home, away = fixture["home"], fixture["away"]
        print(f"\nLineup check: {home} vs {away} ({fid})")

        api_id = fixture.get("api_id")
        if not api_id:
            print(f"  No api_id for {fid} — cannot fetch lineup")
            _write_neutral(fid, home, away, "no_api_id")
            continue

        home_missing, away_missing = _fetch_missing_starters(api_id)
        hm = round(max(0.80, 1.0 - home_missing * _MISSING_PENALTY), 3)
        am = round(max(0.80, 1.0 - away_missing * _MISSING_PENALTY), 3)

        _write_news(fid, home, away, hm, am, home_missing, away_missing)
        print(f"  {home}: {home_missing} absent → ×{hm:.3f}")
        print(f"  {away}: {away_missing} absent → ×{am:.3f}")

    # Re-apply all adjustments (including any from morning news fetch)
    print("\nApplying adjustments to predictions.json …")
    result = subprocess.run(
        [sys.executable, str(ROOT / "src" / "news.py"), "--apply"],
        check=False,
    )
    if result.returncode != 0:
        print("  WARNING: news.py --apply exited with error")


def _fetch_missing_starters(api_id: int) -> tuple[int, int]:
    """
    Return (home_missing, away_missing) based on the lineup from football-data.org.
    A lineup is considered confirmed when both teams have 11 starters listed.
    Falls back to (0, 0) when lineups are not yet available.
    """
    try:
        data    = _fd_get(f"/matches/{api_id}")
        lineups = data.get("lineups", [])

        if len(lineups) < 2:
            print("  Lineups not yet published by football-data.org")
            return 0, 0

        home_xi = len(lineups[0].get("startingXI", []))
        away_xi = len(lineups[1].get("startingXI", []))
        print(f"  Starting XIs: home {home_xi}/11, away {away_xi}/11")

        home_missing = max(0, 11 - home_xi)
        away_missing = max(0, 11 - away_xi)
        return home_missing, away_missing

    except Exception as exc:
        print(f"  Could not fetch lineup from football-data.org: {exc}")
        return 0, 0


def _write_news(fid, home, away, hm, am, home_missing, away_missing):
    """Merge lineup adjustment into data/news/<fid>.json."""
    news_file = NEWS_DIR / f"{fid}.json"

    # Preserve notes from the morning news-fetch if they exist
    existing = {}
    if news_file.exists():
        try:
            with open(news_file, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.update({
        "fixture_id":             fid,
        "home":                   home,
        "away":                   away,
        "home_multiplier":        hm,
        "away_multiplier":        am,
        "lineup_home_absent":     home_missing,
        "lineup_away_absent":     away_missing,
        "source":                 "lineup_fetch",
        "fetched_at":             datetime.now(timezone.utc).isoformat(),
    })

    with open(news_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def _write_neutral(fid, home, away, source):
    news_file = NEWS_DIR / f"{fid}.json"
    with open(news_file, "w", encoding="utf-8") as f:
        json.dump({
            "fixture_id":      fid,
            "home":            home,
            "away":            away,
            "home_multiplier": 1.0,
            "away_multiplier": 1.0,
            "notes":           "",
            "source":          source,
            "fetched_at":      datetime.now(timezone.utc).isoformat(),
        }, f, indent=2, ensure_ascii=False)


# ── GitHub Actions output helper ──────────────────────────────────────────────

def _set_output(name: str, value: str):
    """Write a step output for GitHub Actions; print locally."""
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")
    else:
        print(f"OUTPUT {name}={value}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Pre-kickoff lineup adjuster")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="Check for kickoffs in the next 90 minutes")
    group.add_argument("--update", nargs="+", metavar="FIXTURE_ID",
                       help="Fetch lineups for given fixture IDs and update predictions")
    args = parser.parse_args()

    if args.check:
        cmd_check()
    else:
        cmd_update(args.update)


if __name__ == "__main__":
    main()

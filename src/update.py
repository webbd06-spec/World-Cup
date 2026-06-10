"""
Live data updater — run before predict.py each day.

Pipeline (in order):
  1. fetch_fixtures   — full fixture list from football-data.org
  2. fetch_venues     — venue data from Wikipedia (via src/venues.py)
  3. fetch_elo        — current Elo ratings from eloratings.net
  4. fetch_odds       — head-to-head odds from the-odds-api.com
  5. validate_data    — sanity-checks fixtures before predictions run;
                        aborts the process on any failure

Never call predict.py on data that has not passed validation.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

ROOT    = Path(__file__).parent.parent
DATA    = ROOT / "data"
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(exist_ok=True)

ODDS_API_KEY         = os.getenv("ODDS_API_KEY", "")
FOOTBALL_DATA_KEY    = os.getenv("FOOTBALL_DATA_API_KEY", "")
FOOTBALL_DATA_WC_ID  = 2000
FD_BASE              = "https://api.football-data.org/v4"

EXPECTED_GROUPS = list("ABCDEFGHIJKL")
EXPECTED_TEAMS_PER_GROUP = 4

# football-data.org team names that differ from our canonical names
_TEAM_NORM = {
    "United States":         "USA",
    "Korea Republic":        "South Korea",
    "IR Iran":               "Iran",
    "Côte d'Ivoire":         "Ivory Coast",
    "Cote d'Ivoire":         "Ivory Coast",
    "DR Congo":              "DR Congo",
    "Congo DR":              "DR Congo",
    "Curaçao":               "Curacao",
    "Cape Verde Islands":    "Cape Verde",
    "Czech Republic":        "Czechia",
    "Bosnia and Herzegovina":"Bosnia-Herzegovina",
}

_STAGE_MAP = {
    "GROUP_STAGE":   "group",
    "LAST_32":       "round_of_32",
    "LAST_16":       "round_of_16",
    "QUARTER_FINALS":"quarterfinal",
    "SEMI_FINALS":   "semifinal",
    "THIRD_PLACE":   "third_place",
    "FINAL":         "final",
}

_STATUS_MAP = {
    "TIMED":       "scheduled",
    "SCHEDULED":   "scheduled",
    "IN_PLAY":     "in_play",
    "PAUSED":      "in_play",
    "FINISHED":    "finished",
    "POSTPONED":   "postponed",
    "SUSPENDED":   "suspended",
    "CANCELLED":   "cancelled",
}


def _norm_team(name):
    if name is None:
        return None
    return _TEAM_NORM.get(name, name)


# ---------------------------------------------------------------------------
# Step 1 — Fixtures (football-data.org)
# ---------------------------------------------------------------------------

def _make_id(stage, group, seq):
    """Generate a stable human-readable fixture ID."""
    prefixes = {
        "group":        "GS",
        "round_of_32":  "R32",
        "round_of_16":  "R16",
        "quarterfinal": "QF",
        "semifinal":    "SF",
        "third_place":  "TP",
        "final":        "F",
    }
    pfx = prefixes.get(stage, stage.upper())
    if stage == "group" and group:
        return f"{pfx}_{group}{seq}"
    return f"{pfx}_{seq}"


def fetch_fixtures():
    """
    Fetch all 104 WC 2026 fixtures from football-data.org and write
    data/fixtures.json.  Venue fields are left null here — venues.main()
    fills them in the next step.

    Returns True on success, False on failure.
    """
    if not FOOTBALL_DATA_KEY:
        print("  ERROR: FOOTBALL_DATA_API_KEY not set — cannot fetch fixtures")
        return False

    print("  Fetching from football-data.org…")
    try:
        resp = requests.get(
            f"{FD_BASE}/competitions/{FOOTBALL_DATA_WC_ID}/matches",
            headers={"X-Auth-Token": FOOTBALL_DATA_KEY},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  ERROR: fixtures fetch failed — {exc}")
        return False

    raw_matches = resp.json().get("matches", [])
    if not raw_matches:
        print("  ERROR: API returned 0 matches")
        return False

    # Sort so IDs are assigned in chronological order within each bucket
    raw_matches.sort(key=lambda m: (m["stage"], m.get("group") or "", m["utcDate"]))

    # Counter per (stage, group) for sequential IDs
    seq_counters = {}
    matches = []

    for m in raw_matches:
        stage  = _STAGE_MAP.get(m["stage"], m["stage"].lower())
        group  = (m.get("group") or "").replace("GROUP_", "") or None
        status = _STATUS_MAP.get(m["status"], m["status"].lower())

        bucket = (stage, group or "")
        seq_counters[bucket] = seq_counters.get(bucket, 0) + 1
        seq = seq_counters[bucket]

        home_raw = (m["homeTeam"] or {}).get("name")
        away_raw = (m["awayTeam"] or {}).get("name")
        home = _norm_team(home_raw)
        away = _norm_team(away_raw)

        # Parse UTC date and kickoff time
        utc_dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        date_str    = utc_dt.strftime("%Y-%m-%d")
        kickoff_str = utc_dt.strftime("%H:%M")

        score_ft = (m.get("score") or {}).get("fullTime") or {}
        result = None
        if score_ft.get("home") is not None and score_ft.get("away") is not None:
            result = {"home": score_ft["home"], "away": score_ft["away"]}

        matches.append({
            "id":          _make_id(stage, group, seq),
            "api_id":      m["id"],
            "stage":       stage,
            "group":       group,
            "matchday":    m.get("matchday"),
            "home":        home,
            "away":        away,
            "date":        date_str,
            "kickoff_utc": kickoff_str,
            "venue":       None,
            "city":        None,
            "result":      result,
            "status":      status,
        })

    fixture_data = {
        "_note":    "Generated by src/update.py — do not edit manually",
        "_fetched": datetime.now(timezone.utc).isoformat(),
        "matches":  matches,
    }

    out_path = DATA / "fixtures.json"
    with open(out_path, "w") as f:
        json.dump(fixture_data, f, indent=2)

    print(f"  ✓ {len(matches)} fixtures written → {out_path.name}")
    return True


# ---------------------------------------------------------------------------
# Step 2 — Venues (Wikipedia wikitext)
# ---------------------------------------------------------------------------

def fetch_venues():
    """Delegate to src/venues.py to populate venue data in fixtures.json."""
    sys.path.insert(0, str(Path(__file__).parent))
    import venues as _venues
    matched, unmatched = _venues.main()
    return matched


# ---------------------------------------------------------------------------
# Step 3 — Elo ratings (eloratings.net)
# ---------------------------------------------------------------------------

ELO_URL = "https://www.eloratings.net/World"

_ELO_NAME_MAP = {
    "United States": "USA",
    "Korea Republic": "South Korea",
    "IR Iran": "Iran",
    "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast",
    "DR Congo": "DR Congo",
    "Congo DR": "DR Congo",
    "Curaçao": "Curacao",
    "Cape Verde Islands": "Cape Verde",
    "Czech Republic": "Czechia",
    "Bosnia and Herzegovina": "Bosnia-Herzegovina",
}


def fetch_elo_ratings():
    """Scrape current Elo ratings from eloratings.net."""
    print("  Fetching from eloratings.net…")
    try:
        resp = requests.get(ELO_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  WARNING: could not fetch Elo ratings — {exc}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    ratings = {}

    for script in soup.find_all("script"):
        text = script.string or ""
        match = re.search(r'"teams"\s*:\s*(\[.*?\])', text, re.DOTALL)
        if match:
            try:
                teams_data = json.loads(match.group(1))
                for t in teams_data:
                    raw = t.get("name", "")
                    name = _ELO_NAME_MAP.get(raw, raw)
                    if name:
                        ratings[name] = int(t.get("rating", 0))
                print(f"  ✓ Parsed {len(ratings)} Elo ratings from JSON")
                return ratings
            except (json.JSONDecodeError, ValueError):
                pass

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) >= 3:
            try:
                raw  = cells[1].get_text(strip=True)
                name = _ELO_NAME_MAP.get(raw, raw)
                ratings[name] = int(cells[2].get_text(strip=True).replace(",", ""))
            except (ValueError, IndexError):
                continue

    if ratings:
        print(f"  ✓ Parsed {len(ratings)} Elo ratings from table")
    else:
        print("  WARNING: No Elo ratings found (site may require JS)")
    return ratings


def update_elo_in_teams(elo_ratings):
    if not elo_ratings:
        return
    teams_path = DATA / "teams.json"
    with open(teams_path) as f:
        teams_data = json.load(f)
    updated = 0
    for name, elo in elo_ratings.items():
        if name in teams_data["teams"] and elo > 0:
            teams_data["teams"][name]["elo"] = elo
            updated += 1
    with open(teams_path, "w") as f:
        json.dump(teams_data, f, indent=2)
    print(f"  ✓ Updated {updated} Elo ratings in teams.json")


# ---------------------------------------------------------------------------
# Step 4 — Betting odds (the-odds-api.com)
# ---------------------------------------------------------------------------

ODDS_API_BASE  = "https://api.the-odds-api.com/v4"
SPORT          = "soccer_fifa_world_cup"
REGIONS        = "us"
MARKETS        = "h2h"
UK_MARKETS     = "h2h,totals"   # btts not available for soccer_fifa_world_cup
# Display names for known UK bookmakers (key → label)
UK_BM_NAMES = {
    "paddypower":   "Paddy Power",
    "skybet":       "Sky Bet",
    "betfair_ex_uk":"Betfair",
    "coral":        "Coral",
    "ladbrokes_uk": "Ladbrokes",
    "williamhill":  "William Hill",
    "bet365":       "bet365",
    "betfred_uk":   "Betfred",
    "boylesports":  "BoyleSports",
    "unibet_uk":    "Unibet",
    "betvictor":    "BetVictor",
    "betway":       "Betway",
}


def fetch_odds():
    """Fetch head-to-head odds and map them to fixture IDs."""
    if not ODDS_API_KEY:
        print("  WARNING: ODDS_API_KEY not set — skipping market odds")
        _write_empty_odds()
        return {}

    print("  Fetching from the-odds-api.com…")
    try:
        resp = requests.get(
            f"{ODDS_API_BASE}/sports/{SPORT}/odds",
            params={"apiKey": ODDS_API_KEY, "regions": REGIONS,
                    "markets": MARKETS, "oddsFormat": "decimal"},
            timeout=15,
        )
        resp.raise_for_status()
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"  API requests remaining: {remaining}")
    except requests.RequestException as exc:
        print(f"  WARNING: could not fetch odds — {exc}")
        _write_empty_odds()
        return {}

    events = resp.json()
    odds_raw = {}

    for event in events:
        home_team = event.get("home_team", "")
        away_team = event.get("away_team", "")
        best = {"home_win": None, "draw": None, "away_win": None, "_margin": 999}
        for bm in event.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "h2h":
                    continue
                oc = {o["name"]: o["price"] for o in mkt.get("outcomes", [])}
                hw, dr, aw = oc.get(home_team), oc.get("Draw"), oc.get(away_team)
                if hw and dr and aw:
                    margin = (1/hw + 1/dr + 1/aw) - 1
                    if margin < best["_margin"]:
                        best = {"home_win": hw, "draw": dr, "away_win": aw, "_margin": margin}
        if best["home_win"]:
            del best["_margin"]
            odds_raw[f"{home_team} vs {away_team}"] = best

    # Map to fixture IDs
    fixtures_path = DATA / "fixtures.json"
    with open(fixtures_path) as f:
        fixtures = json.load(f)
    lookup = {(m["home"], m["away"]): m["id"] for m in fixtures["matches"]}

    odds_mapped = {}
    for key, odd in odds_raw.items():
        for (home, away), fid in lookup.items():
            if home and away and home.lower() in key.lower() and away.lower() in key.lower():
                odds_mapped[fid] = odd
                break

    out_path = OUTPUTS / "live_odds.json"
    with open(out_path, "w") as f:
        json.dump(odds_mapped, f, indent=2)

    print(f"  ✓ Saved odds for {len(odds_mapped)} fixtures → {out_path.name}")
    return odds_mapped


def _write_empty_odds():
    out_path = OUTPUTS / "live_odds.json"
    with open(out_path, "w") as f:
        json.dump({}, f)


# ---------------------------------------------------------------------------
# Step 4b — UK bookmaker odds (dashboard detail view)
# ---------------------------------------------------------------------------

def _find_fixture_id(api_home, api_away, fix_lookup):
    """Match The Odds API team names to our fixture IDs."""
    h = _norm_team(api_home).lower()
    a = _norm_team(api_away).lower()
    if (h, a) in fix_lookup:
        return fix_lookup[(h, a)]
    # Fuzzy: substring containment (handles "United States" ↔ "USA" after norm)
    for (fh, fa), fid in fix_lookup.items():
        if (fh in h or h in fh) and (fa in a or a in fa):
            return fid
    return None


def fetch_uk_bookmaker_odds():
    """
    Fetch per-bookmaker odds (h2h, over/under 2.5, BTTS) for UK bookmakers
    and save to outputs/live_odds_uk.json for the dashboard.
    """
    if not ODDS_API_KEY:
        print("  WARNING: ODDS_API_KEY not set — skipping UK bookmaker odds")
        return

    print("  Fetching UK bookmaker odds from the-odds-api.com…")
    try:
        resp = requests.get(
            f"{ODDS_API_BASE}/sports/{SPORT}/odds",
            params={
                "apiKey":     ODDS_API_KEY,
                "regions":    "uk",
                "markets":    UK_MARKETS,
                "oddsFormat": "decimal",
            },
            timeout=15,
        )
        resp.raise_for_status()
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"  API requests remaining: {remaining}")
    except requests.RequestException as exc:
        print(f"  WARNING: could not fetch UK odds — {exc}")
        return

    # Build (home_lower, away_lower) → fixture_id lookup
    fixtures_path = DATA / "fixtures.json"
    with open(fixtures_path) as f:
        fixtures = json.load(f)
    fix_lookup = {
        (m["home"].lower(), m["away"].lower()): m["id"]
        for m in fixtures["matches"]
        if m.get("home") and m.get("away")
    }

    events = resp.json()
    out = {"_fetched": datetime.now(timezone.utc).isoformat(), "matches": {}}

    for event in events:
        api_home = event.get("home_team", "")
        api_away = event.get("away_team", "")
        fid = _find_fixture_id(api_home, api_away, fix_lookup)
        if not fid:
            continue

        match_odds = {"h2h": {}, "totals": {}}

        for bm in event.get("bookmakers", []):
            bm_key = bm.get("key", "")
            # Only include bookmakers we have display names for
            if bm_key not in UK_BM_NAMES:
                continue
            for mkt in bm.get("markets", []):
                mkt_key  = mkt.get("key", "")
                outcomes = mkt.get("outcomes", [])

                if mkt_key == "h2h":
                    oc = {o["name"]: o["price"] for o in outcomes}
                    hw, dr, aw = oc.get(api_home), oc.get("Draw"), oc.get(api_away)
                    if hw and dr and aw:
                        match_odds["h2h"][bm_key] = {
                            "home_win": hw, "draw": dr, "away_win": aw,
                        }

                elif mkt_key == "totals":
                    for o in outcomes:
                        if abs(o.get("point", 0) - 2.5) < 0.01:
                            k = "over_2_5" if o["name"] == "Over" else "under_2_5"
                            match_odds["totals"].setdefault(bm_key, {})[k] = o["price"]

        if any(match_odds[k] for k in match_odds):
            out["matches"][fid] = match_odds

    out_path = OUTPUTS / "live_odds_uk.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)

    print(f"  ✓ Saved UK odds for {len(out['matches'])} fixtures → {out_path.name}")


# ---------------------------------------------------------------------------
# Step 5 — Validation
# ---------------------------------------------------------------------------

def validate_data():
    """
    Validate fixtures.json before predictions run.

    Checks:
      a) All 12 groups (A–L) contain exactly 4 distinct teams
      b) England has exactly 3 group-stage fixtures
      c) Each of England's fixtures has a confirmed venue (not null/TBC)
      d) Each of England's fixtures has a well-formed YYYY-MM-DD date

    Raises SystemExit(1) on any failure so the pipeline hard-stops.
    """
    fixtures_path = DATA / "fixtures.json"
    with open(fixtures_path) as f:
        data = json.load(f)

    matches = data["matches"]
    errors = []

    # ── (a) Group membership ──────────────────────────────────────────────────
    group_teams = {}
    for m in matches:
        if m.get("stage") != "group":
            continue
        grp = m.get("group")
        if not grp:
            continue
        group_teams.setdefault(grp, set())
        if m.get("home"):
            group_teams[grp].add(m["home"])
        if m.get("away"):
            group_teams[grp].add(m["away"])

    for grp in EXPECTED_GROUPS:
        teams = group_teams.get(grp, set())
        if len(teams) != EXPECTED_TEAMS_PER_GROUP:
            errors.append(
                f"Group {grp}: expected {EXPECTED_TEAMS_PER_GROUP} teams, "
                f"got {len(teams)} → {sorted(teams)}"
            )

    # ── (b) England fixture count ─────────────────────────────────────────────
    england_fixtures = [
        m for m in matches
        if m.get("stage") == "group"
        and "England" in (m.get("home") or "", m.get("away") or "")
    ]

    if len(england_fixtures) != 3:
        errors.append(
            f"England: expected 3 group fixtures, found {len(england_fixtures)}"
        )

    # ── (c) England venues confirmed ─────────────────────────────────────────
    for m in england_fixtures:
        venue = m.get("venue")
        if not venue or venue == "TBC":
            opp = m.get("away") if m.get("home") == "England" else m.get("home")
            errors.append(
                f"England vs {opp} ({m.get('date')}): venue is '{venue}' — not confirmed"
            )

    # ── (d) England fixture dates ─────────────────────────────────────────────
    date_re = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    for m in england_fixtures:
        d = m.get("date", "")
        if not date_re.match(d):
            opp = m.get("away") if m.get("home") == "England" else m.get("home")
            errors.append(
                f"England vs {opp}: invalid date '{d}' (expected YYYY-MM-DD)"
            )

    # ── Report ────────────────────────────────────────────────────────────────
    if errors:
        print("\n" + "=" * 60)
        print("VALIDATION FAILED — aborting before predictions run")
        print("=" * 60)
        for err in errors:
            print(f"  ✗ {err}")
        print("=" * 60 + "\n")
        sys.exit(1)

    # Summary of what was validated
    group_summary = "  ".join(
        f"{g}:{len(group_teams.get(g,set()))}" for g in EXPECTED_GROUPS
    )
    print(f"  ✓ All 12 groups confirmed — {group_summary}")
    print(f"  ✓ England has 3 group fixtures with confirmed venues:")
    for m in england_fixtures:
        opp = m.get("away") if m.get("home") == "England" else m.get("home")
        print(f"      {m['date']}  England vs {opp}  →  {m['venue']}, {m.get('city')}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("=== Step 1: Fixtures (football-data.org) ===")
    ok = fetch_fixtures()
    if not ok:
        print("ABORT: Could not fetch fixtures.")
        sys.exit(1)

    print("\n=== Step 2: Venues (Wikipedia) ===")
    fetch_venues()

    print("\n=== Step 3: Elo ratings (eloratings.net) ===")
    elo = fetch_elo_ratings()
    update_elo_in_teams(elo)

    print("\n=== Step 4: Betting odds (the-odds-api.com) ===")
    fetch_odds()                   # best-odds blend → live_odds.json (for predict.py)
    fetch_uk_bookmaker_odds()      # per-bookmaker detail → live_odds_uk.json (for dashboard)

    print("\n=== Step 5: Validation ===")
    validate_data()

    print("\n✓ update.py complete — safe to run predict.py")


if __name__ == "__main__":
    main()

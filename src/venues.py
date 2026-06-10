"""
src/venues.py — Populate venue data in fixtures.json from Wikipedia wikitext.

Strategy:
  1. For each group page (A–L), fetch wikitext via the Wikipedia API
     (action=parse&prop=wikitext — no HTML scraping, no BeautifulSoup required)
  2. Extract every {{#invoke:football box|main ...}} block using a
     balanced-brace parser (plain regex misses nested {{ }})
  3. Parse |date=, |team1=, |team2=, |stadium= from each block
  4. Match records to fixtures.json by (ISO date, normalised team names)
  5. Write confirmed venues back to fixtures.json
  6. Add any newly discovered venue names to data/venues.json with
     reasonable default factors (so predict.py never sees a key error)

Rules:
  - No venue is hardcoded — if Wikipedia doesn't confirm it, write "TBC"
  - Group-stage TBD / knockout-stage matches are always "TBC"
"""

import json
import re
import sys
import time
import urllib.request
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Optional

ROOT     = Path(__file__).parent.parent
FIXTURES = ROOT / "data" / "fixtures.json"
VENUES   = ROOT / "data" / "venues.json"

GROUPS      = list("ABCDEFGHIJKL")
WIKI_API    = ("https://en.wikipedia.org/w/api.php"
               "?action=parse&page=2026_FIFA_World_Cup_Group_{group}"
               "&prop=wikitext&format=json")
HEADERS     = {"User-Agent": "WC2026Predictor/1.0 (educational project)"}
CRAWL_DELAY = 3.0   # seconds between requests — Wikipedia rate-limits aggressive clients
MAX_RETRIES = 4


# ── Country-code → canonical team name ───────────────────────────────────────

FIFA_CODE = {
    "MEX": "Mexico",       "KOR": "South Korea",   "CZE": "Czechia",
    "RSA": "South Africa", "CAN": "Canada",         "SUI": "Switzerland",
    "QAT": "Qatar",        "BIH": "Bosnia-Herzegovina",
    "BRA": "Brazil",       "MAR": "Morocco",        "SCO": "Scotland",
    "HAI": "Haiti",        "USA": "USA",             "TUR": "Turkey",
    "PAR": "Paraguay",     "AUS": "Australia",       "GER": "Germany",
    "CIV": "Ivory Coast",  "ECU": "Ecuador",         "CUW": "Curacao",
    "NED": "Netherlands",  "JPN": "Japan",           "SWE": "Sweden",
    "TUN": "Tunisia",      "BEL": "Belgium",         "EGY": "Egypt",
    "IRN": "Iran",         "NZL": "New Zealand",     "ESP": "Spain",
    "KSA": "Saudi Arabia", "URU": "Uruguay",         "CPV": "Cape Verde",
    "FRA": "France",       "SEN": "Senegal",         "NOR": "Norway",
    "IRQ": "Iraq",         "ARG": "Argentina",       "ALG": "Algeria",
    "AUT": "Austria",      "JOR": "Jordan",          "POR": "Portugal",
    "COL": "Colombia",     "UZB": "Uzbekistan",      "COD": "DR Congo",
    "ENG": "England",      "CRO": "Croatia",         "GHA": "Ghana",
    "PAN": "Panama",
}

# Extra normalisation for display names that may appear in wikitext
TEAM_NORM = {
    "Czech Republic":                   "Czechia",
    "United States":                    "USA",
    "Congo DR":                         "DR Congo",
    "Democratic Republic of the Congo": "DR Congo",
    "D.R. Congo":                       "DR Congo",
    "Curaçao":                          "Curacao",
    "Cape Verde Islands":               "Cape Verde",
    "Korea Republic":                   "South Korea",
    "Côte d'Ivoire":                    "Ivory Coast",
    "Bosnia and Herzegovina":           "Bosnia-Herzegovina",
}


def _norm_team(raw: str) -> str:
    raw = raw.strip()
    return TEAM_NORM.get(raw, raw)


# ── Wikitext helpers ──────────────────────────────────────────────────────────

def _extract_templates(text: str, marker: str) -> list:
    """
    Return every top-level wikitext template block that starts with `marker`.
    Uses brace-depth counting so nested {{ }} don't terminate the match early.
    """
    results = []
    search_from = 0
    while True:
        start = text.find(marker, search_from)
        if start == -1:
            break
        depth = 0
        i = start
        while i < len(text) - 1:
            if text[i:i+2] == "{{":
                depth += 1
                i += 2
            elif text[i:i+2] == "}}":
                depth -= 1
                if depth == 0:
                    results.append(text[start : i + 2])
                    search_from = i + 2
                    break
                i += 2
            else:
                i += 1
        else:
            break  # hit end of string without closing braces
    return results


def _wikilink(raw: str) -> str:
    """
    Resolve a wikilink to its display text.
      [[City]]                    → "City"
      [[Guadalupe, Nuevo León|Guadalupe]]  → "Guadalupe"
      plain text (no brackets)   → raw.strip()
    """
    m = re.match(r'\[\[(?:[^|\]]+\|)?([^\]]+)\]\]', raw.strip())
    return m.group(1).strip() if m else raw.strip()


def _parse_stadium(raw: str) -> tuple:
    """
    Parse a |stadium= value such as:
      [[Estadio Azteca]], [[Mexico City]]
      [[AT&T Stadium]], [[Arlington, Texas|Arlington]]
      [[Mercedes-Benz Stadium]], [[Atlanta]]

    Returns (venue_name, city) as plain strings, or ("TBC", None).
    """
    raw = raw.strip()
    if not raw:
        return "TBC", None

    # Split at the comma between the two wikilinks
    # Pattern: [[...]], [[...]]
    m = re.match(r'(\[\[[^\]]+\]\])\s*,\s*(\[\[[^\]]+\]\])(.*)', raw)
    if m:
        venue = _wikilink(m.group(1))
        city  = _wikilink(m.group(2))
        return venue, city

    # Fallback: single wikilink or plain text
    m2 = re.match(r'\[\[([^\]]+)\]\]', raw)
    if m2:
        parts = m2.group(1).split("|")
        return parts[-1].strip(), None

    return raw, None


def _parse_date(raw: str) -> Optional[str]:
    """
    Parse |date={{Start date|2026|M|D}} → "2026-MM-DD".
    """
    m = re.search(r'Start date\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})', raw)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def _parse_team_code(raw: str) -> Optional[str]:
    """
    Parse {{#invoke:flag|fb-rt|CODE}} or {{#invoke:flag|fb|CODE}} → canonical name.
    Falls back to display text normalisation for any plain text.
    """
    m = re.search(r'#invoke:flag\s*\|fb(?:-rt)?\|([A-Z]+)', raw)
    if m:
        code = m.group(1)
        return FIFA_CODE.get(code, code)  # return code itself if unmapped
    # Plain text team name
    text = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', raw).strip()
    return _norm_team(text) if text else None


def _parse_block(block: str) -> Optional[dict]:
    """
    Extract date, home, away, venue, city from a single football box block.
    Returns None if any required field is missing.
    """
    date  = _parse_date(block)
    if not date:
        return None

    # |team1= and |team2= lines
    m1 = re.search(r'\|team1\s*=\s*(.+?)(?=\n|\|team|\|score|\|goals|\|stadium|\|time)', block, re.DOTALL)
    m2 = re.search(r'\|team2\s*=\s*(.+?)(?=\n|\|score|\|goals|\|stadium|\|time)', block, re.DOTALL)
    if not m1 or not m2:
        return None

    home = _parse_team_code(m1.group(1))
    away = _parse_team_code(m2.group(1))
    if not home or not away:
        return None

    # |stadium= line
    ms = re.search(r'\|stadium\s*=\s*(.+?)(?=\n|\|attendance|\|referee|\|report)', block, re.DOTALL)
    if not ms:
        return None

    venue, city = _parse_stadium(ms.group(1))

    return {"date": date, "home": home, "away": away, "venue": venue, "city": city}


# ── Wikipedia fetch ───────────────────────────────────────────────────────────

def fetch_group(group: str) -> list:
    """
    Fetch the Wikipedia wikitext for one group page and return a list of
    match records: [{date, home, away, venue, city}, ...]
    Retries up to MAX_RETRIES times on 429 / transient errors.
    """
    url = WIKI_API.format(group=group)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read())
            wikitext = data["parse"]["wikitext"]["*"]
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < MAX_RETRIES:
                wait = 15 * attempt   # 15s, 30s, 45s — give Wikipedia real breathing room
                print(f"  429 rate-limit — waiting {wait}s before retry {attempt+1}...", flush=True)
                time.sleep(wait)
            else:
                print(f"  WARNING: could not fetch Group {group} — {exc}")
                return []
        except Exception as exc:
            print(f"  WARNING: could not fetch Group {group} — {exc}")
            return []

    blocks  = _extract_templates(wikitext, "{{#invoke:football box")
    records = []
    for block in blocks:
        rec = _parse_block(block)
        if rec:
            records.append(rec)
        else:
            print(f"  WARNING: Group {group} — could not parse a block")

    return records


# ── Default venue metadata for newly discovered venues ───────────────────────

_VENUE_DEFAULTS = {
    "Mercedes-Benz Stadium": {
        "city": "Atlanta, GA", "country": "USA", "capacity": 71000,
        "altitude_m": 294, "climate": "humid_subtropical",
        "surface": "artificial", "altitude_factor": 1.0, "heat_factor": 0.96,
    },
    "Toronto Stadium": {
        "city": "Toronto, ON", "country": "Canada", "capacity": 45000,
        "altitude_m": 76, "climate": "continental",
        "surface": "artificial", "altitude_factor": 1.0, "heat_factor": 1.0,
    },
    "Lumen Field": {
        "city": "Seattle, WA", "country": "USA", "capacity": 69000,
        "altitude_m": 5, "climate": "oceanic",
        "surface": "artificial", "altitude_factor": 1.0, "heat_factor": 1.0,
    },
}

def _country_from_city(city: Optional[str]) -> str:
    """Rough heuristic: Mexico/Canada/else USA."""
    if not city:
        return "USA"
    c = city.lower()
    if any(x in c for x in ("mexico", "guadalajara", "monterrey", "guadalupe", "zapopan")):
        return "Mexico"
    if any(x in c for x in ("toronto", "vancouver")):
        return "Canada"
    return "USA"


def _default_metadata(venue_name: str, city: Optional[str]) -> dict:
    if venue_name in _VENUE_DEFAULTS:
        return _VENUE_DEFAULTS[venue_name]
    return {
        "city": city or "TBC",
        "country": _country_from_city(city),
        "capacity": None,
        "altitude_m": None,
        "climate": "unknown",
        "surface": "unknown",
        "altitude_factor": 1.0,
        "heat_factor": 1.0,
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(FIXTURES) as f:
        fixture_data = json.load(f)
    with open(VENUES) as f:
        venue_data = json.load(f)

    fixtures = fixture_data["matches"]
    venue_meta = venue_data["venues"]

    # Clear all existing venue fields so no stale data survives
    for fx in fixtures:
        fx["venue"] = None
        fx["city"]  = None

    # Build (date, home, away) → list index for group-stage matches
    lookup = {}  # type: dict
    for i, fx in enumerate(fixtures):
        if fx.get("stage") != "group":
            continue
        lookup[(fx["date"], fx.get("home"), fx.get("away"))] = i
        lookup[(fx["date"], fx.get("away"), fx.get("home"))] = i  # reverse fallback

    # Fetch venue data from Wikipedia for all 12 groups
    print("Fetching venue data from Wikipedia (wikitext API)...")
    all_records = []
    for group in GROUPS:
        print(f"  Group {group} ...", end=" ", flush=True)
        recs = fetch_group(group)
        all_records.extend(recs)
        print(f"{len(recs)} matches parsed")
        time.sleep(CRAWL_DELAY)

    # Apply records to fixtures.
    # Wikipedia dates are local; fixtures.json dates are UTC.
    # Evening kick-offs (e.g. 8 PM UTC-6 = 2 AM UTC next day) shift date +1.
    # Probe both the exact date and date+1 before giving up.
    def _next_day(iso):
        d = _date.fromisoformat(iso)
        return (d + timedelta(days=1)).isoformat()

    matched = 0
    unmatched = []
    new_venues = []

    for rec in all_records:
        key  = (rec["date"],            rec["home"], rec["away"])
        key1 = (_next_day(rec["date"]), rec["home"], rec["away"])
        idx  = lookup.get(key)
        if idx is None:
            idx = lookup.get(key1)
        if idx is None:
            unmatched.append(rec)
            continue
        fixtures[idx]["venue"] = rec["venue"]
        fixtures[idx]["city"]  = rec["city"]
        matched += 1

        # Track venues not yet in venues.json
        if rec["venue"] and rec["venue"] != "TBC" and rec["venue"] not in venue_meta:
            new_venues.append(rec)

    # Anything still without a venue → TBC
    tbc = 0
    for fx in fixtures:
        if not fx.get("venue"):
            fx["venue"] = "TBC"
            fx["city"]  = None
            tbc += 1

    # Add newly discovered venues to venues.json
    for rec in new_venues:
        v = rec["venue"]
        if v not in venue_meta:
            venue_meta[v] = _default_metadata(v, rec.get("city"))
            print(f"  + Added new venue to venues.json: {v!r}")

    # Write both files
    with open(FIXTURES, "w") as f:
        json.dump(fixture_data, f, indent=2)
    with open(VENUES, "w") as f:
        json.dump(venue_data, f, indent=2)

    print(f"\nSummary")
    print(f"  Wikipedia records parsed : {len(all_records)}")
    print(f"  Matched to fixtures      : {matched}")
    print(f"  Set to TBC               : {tbc}")
    if unmatched:
        print(f"  Unmatched Wikipedia records ({len(unmatched)}) — check team name normalisation:")
        for u in unmatched:
            print(f"    {u['date']}  {u['home']!r:24} vs {u['away']!r:24}  →  {u['venue']}")
    print(f"\n✓ {FIXTURES.name} and {VENUES.name} updated")
    return matched, unmatched


if __name__ == "__main__":
    main()

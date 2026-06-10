# World Cup 2026 Match Predictor

## Project Overview
Poisson-based match prediction model for FIFA World Cup 2026, blended with UK
bookmaker odds.  Dashboard served via GitHub Pages; pipeline automated via
GitHub Actions.

---

## Build Status (as of 2026-06-11)

**Commits on main:**
- `7e19e5b` Initial commit
- `4aae2af` Full CI/CD pipeline, dashboard upgrades, news/lineup scripts
- `8073d3f` Move dashboard → docs/ for GitHub Pages
- `108dc65` Fix: survive football-data.org API failures without aborting ← HEAD

**What is working:**
- `src/update.py` runs end-to-end: fixtures (football-data.org) → venues
  (Wikipedia) → Elo (eloratings.net) → odds (the-odds-api.com)
- `src/predict.py` produces 104 predictions (72 group + 32 knockout TBD)
- `src/venues.py` parses Wikipedia wikitext with balanced-brace extraction and
  UTC/local ±1-day date fallback; all England venues confirmed
- `outputs/predictions.json` and `outputs/live_odds_uk.json` generated locally
- `docs/index.html` dashboard fully functional at `http://localhost:8080/docs/`
- GitHub Actions workflow live at `.github/workflows/matchday.yml`
- Repo pushed to `github.com/webbd06-spec/World-Cup`

**What still needs doing (outstanding items):**
1. **GitHub Pages not yet enabled** — go to repo Settings → Pages → Source:
   Deploy from a branch → Branch: main → Folder: /docs → Save.
   Public URL will be `https://webbd06-spec.github.io/World-Cup/`
2. **`FOOTBALL_DATA_API_KEY` secret not set in GitHub Actions** — this is the
   root cause of the recent 400 failures. Add it at repo Settings → Secrets
   and variables → Actions → New repository secret.
3. **Secrets to add in GitHub Actions** (all five required):
   - `FOOTBALL_DATA_API_KEY` — fc038b0fe93c4677b8f1f76b71de5397
   - `ODDS_API_KEY` — in .env locally
   - `ANTHROPIC_API_KEY` — needed for news.py --fetch and lineup.py
   - `TELEGRAM_BOT_TOKEN` — optional; continue-on-error so it won't block
   - `TELEGRAM_CHAT_ID` — optional
4. **`src/news.py --fetch`** uses Anthropic web_search_20250305 tool — requires
   the ANTHROPIC_API_KEY secret and a Claude model that supports built-in web
   search (claude-sonnet-4-6 does). Not tested end-to-end in CI yet.
5. **`src/lineup.py --update`** calls news.py --apply internally via subprocess
   — the combined flow (check → update → apply) has not been exercised in CI.
6. **docs/live_odds_uk.json is stale** — the file in docs/ is a one-time seed
   copy; it will be refreshed automatically by the morning Action once secrets
   are set.
7. **Accuracy tab shows empty state** — no matches have been played yet
   (tournament starts today). Will auto-populate as results flow in via
   update.py fetching match statuses.
8. **Standings Qual% is pre-tournament** — all groups at 0/6 played, all
   probabilities from prior simulation only. Will update as results land.
9. **data/news/ is empty** — the news/ directory exists (.gitkeep) but no
   per-match adjustment files exist yet. news.py --fetch writes them daily.
10. **Push requires PAT with `workflow` scope** — the remote URL needs a token
    with both Contents:write and Workflows:write when pushing workflow changes.
    Strip the token from the remote after each push:
    `git remote set-url origin https://github.com/webbd06-spec/World-Cup.git`

---

## Manual Run

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in API keys
python src/update.py          # fetch fixtures, venues, Elo, odds
python src/predict.py         # run model → outputs/predictions.json
python src/news.py --fetch    # optional: fetch squad news (needs ANTHROPIC_API_KEY)
python src/news.py --apply    # optional: apply news multipliers to predictions
# serve dashboard:
python3 -m http.server 8080
# open http://localhost:8080/docs/
```

## GitHub Actions Pipeline

Two jobs in `.github/workflows/matchday.yml`:

**Job 1 — morning-prediction** (daily 11:00 UTC = 07:00 ET):
1. `python src/update.py` — fixtures, venues, Elo, odds
2. `python src/news.py --fetch` — Anthropic web-search for squad news
3. `python src/predict.py` — Poisson model → outputs/predictions.json
4. `python src/news.py --apply` — apply injury/news xG multipliers
5. Copy outputs/predictions.json + live_odds_uk.json → docs/
6. `python src/notify.py` — Telegram ping (continue-on-error)
7. git commit + push

**Job 2 — pre-kickoff-update** (every 15 min, 14:00–06:00 UTC, matchdays only):
1. Date gate: skip if outside 2026-06-11 → 2026-07-19
2. `python src/lineup.py --check` — find kickoffs within 90 min
3. If match found: `python src/lineup.py --update <ids>` → fetch confirmed
   lineups from football-data.org, write data/news/<id>.json, re-apply
4. Copy updated predictions.json → docs/
5. git commit + push

Job routing: each cron trigger fires both jobs; `if: github.event.schedule ==`
conditions ensure only the intended job runs.  `workflow_dispatch` input
`job: morning | pre-kickoff` for manual runs.

---

## Environment Variables

Create `.env` (never commit):
```
FOOTBALL_DATA_API_KEY=your_key_here
ODDS_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
TELEGRAM_BOT_TOKEN=optional
TELEGRAM_CHAT_ID=optional
```

---

## File Inventory

```
.github/
  workflows/
    matchday.yml        Two-job GitHub Actions pipeline

data/
  fixtures.json         104 matches — dates, venues, group, api_id, status, result
  teams.json            48 teams — Elo, attack/defence multipliers
  venues.json           19 venues — altitude_m, heat_factor, climate, country
  news/
    .gitkeep            Placeholder; daily news.py writes <fixture_id>.json here

docs/                   GitHub Pages publish root (/docs)
  index.html            Single-file dashboard (all CSS + JS inline)
  predictions.json      Seeded copy; refreshed by morning Action
  live_odds_uk.json     Seeded copy; refreshed by morning Action

outputs/
  predictions.json      Model output — 104 predictions incl. 32 knockout TBD
  live_odds.json        Best-odds blend for predict.py (gitignored)
  live_odds_uk.json     Per-bookmaker breakdown for dashboard (gitignored)

src/
  predict.py            Poisson model + 60/40 market blend → predictions.json
  update.py             5-step pipeline: fixtures → venues → Elo → odds → validate
  venues.py             Wikipedia wikitext parser (balanced-brace extraction)
  news.py               --fetch: Anthropic web-search for squad news
                        --apply: re-run Poisson with xG multipliers from data/news/
  lineup.py             --check: find kickoffs within 90 min, set GH Actions outputs
                        --update: fetch lineups, write news/<id>.json, call news --apply
  notify.py             Telegram notification stub

.env                    Local secrets (gitignored)
.env.example            Template for new contributors
.gitignore              Excludes .env, outputs/live_odds*.json, __pycache__
requirements.txt        requests, bs4, python-dotenv, lxml, anthropic>=0.40.0
README.md               Public-facing quickstart
CLAUDE.md               This file — internal build state for Claude Code sessions
```

---

## Model Architecture

- **xG base**: `attack × opp_defence × 1.35` (LEAGUE_AVG)
  - `defence < 1.0` = strong defence (opponents score *less*). Never use `1/defence`.
- **Adjustments**: altitude_factor, heat_factor, rest_factor, stage_multiplier,
  crowd_factor (hosts USA/Mexico/Canada get +5%)
- **Blend**: 60% market odds implied probability + 40% Poisson model
- **News layer**: xG multiplied by home_multiplier/away_multiplier from
  data/news/<id>.json before final blend (applied by news.py --apply)
- **Output fields per prediction**: win_prob, draw_prob, loss_prob, xg_home,
  xg_away, model_win/draw/loss, market_win/draw/loss, top_scorelines (6),
  score_matrix (9×9), source (blend|model_only|tbd), news_adjustment

## Dashboard Features (docs/index.html)

**Views** (top nav):
- **Fixtures**: match cards filtered by group (All/A–L) and date strip
  (Jun 11 → Jul 19; knockout dates shown with dashed border)
- **Standings**: 12 group tables — P/W/D/L/GF/GA/GD/Pts + Qual% via 5 000-run
  Monte Carlo simulation using predicted win/draw/loss probabilities
- **Accuracy**: predicted vs actual, running accuracy %, P&L at £10 per value
  bet (≥5% model edge). Shows empty state until first results land.

**Match cards**:
- Flag emoji + team names + predicted scoreline
- Kickoff: `local · ET · BST` (omits duplicate when user is in ET or UK)
- Venue/city (📍) — hidden when TBC
- xG dominance bar (home blue / away red)
- WDL stacked bar (green/grey/red)
- Adjustment tags: Altitude / Heat / Host crowd / Knockout
- Confidence dots (High 3 / Medium 2 / Low 1)
- England games: blue left border + subtle blue gradient

**Expandable tabs per card**:
- Scorelines: top 6 Poisson scorelines with probability bars
- Bookmaker odds: per-bookmaker table (UK tier), best odds highlighted green,
  Over/Under 2.5 section; falls back to blended market line if no live odds
- Value bets: model edge vs best odds; Strong (≥8%) / Slight (5–8%) / Fair /
  Avoid traffic-light system; £10 P&L disclaimer

**Knockout TBD cards**: show stage label + kickoff time + venue (if confirmed);
"TBD vs TBD" placeholder — no xG/WDL bars, no tabs.

## Known Quirks / Gotchas

- **football-data.org returns 400 for missing/invalid API key** (not 401/403).
  update.py handles this gracefully and falls back to cached fixtures.json.
- **Wikipedia rate-limits aggressively**: CRAWL_DELAY=3.0s, MAX_RETRIES=4,
  retry waits 15s/30s/45s. Groups K and L are most likely to 429.
- **UTC vs local date mismatch**: Wikipedia records local match dates;
  football-data.org stores UTC. Evening matches in UTC-6 venues shift to +1 day.
  venues.py probes both `date` and `date+1` before marking unmatched.
- **Anthropic web_search_20250305 tool**: available on claude-sonnet-4-6 and
  later. news.py gracefully skips if ANTHROPIC_API_KEY is unset.
- **GitHub Pages requires /docs or root** — free accounts cannot use an
  arbitrary folder. dashboard/ was moved to docs/ for this reason.
- **[skip ci] in bot commits** — prevents triggering the push-based CI
  (irrelevant here since workflow triggers are schedule/dispatch only, but
  good practice).
- **Venue preservation**: fetch_fixtures() now carries forward existing
  venue/city data from fixtures.json when rewriting it, so a successful API
  refresh never clears Wikipedia-confirmed venues.

## Adding / Updating a Team Rating

Edit `data/teams.json`:
```json
"England": {
  "elo": 2020,
  "attack": 1.38,
  "defence": 0.78
}
```
`attack > 1.0` = above-average scorer. `defence < 1.0` = strong (opponents
score less). Both are multipliers relative to LEAGUE_AVG (1.35 goals/match).

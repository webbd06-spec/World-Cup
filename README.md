# World Cup 2026 Match Predictor

Poisson-based match predictor for the 2026 FIFA World Cup, blended with betting market odds.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your API keys
python src/update.py        # fetch live Elo + odds
python src/predict.py       # run model → outputs/predictions.json
open dashboard/index.html   # view dashboard
```

## Architecture

```
attack_rating × (1 / opp_defence) × 1.35 = base xG
     × altitude_factor × heat_factor
     × rest_factor × stage_multiplier × crowd_factor
```

Then blend: **60% market odds + 40% Poisson model**

## Data sources

| Source | What | Cost |
|--------|------|------|
| [eloratings.net](https://eloratings.net) | Elo ratings | Free |
| [the-odds-api.com](https://the-odds-api.com) | Betting odds | Free tier (500 req/month) |
| [football-data.org](https://football-data.org) | Fixtures & results | Free tier |

## Environment variables

Create `.env` (never commit this file):

```
ODDS_API_KEY=your_key_here
FOOTBALL_DATA_API_KEY=your_key_here
TELEGRAM_BOT_TOKEN=optional
TELEGRAM_CHAT_ID=optional
```

## Automated runs

GitHub Actions runs the full pipeline daily at **11:00 UTC (7 AM ET)**.

Add your API keys as repository secrets: Settings → Secrets and variables → Actions.

## Repo layout

```
data/
  fixtures.json     all 104 matches (2026 draw)
  teams.json        Elo + attack/defence ratings
  venues.json       altitude, climate per stadium
src/
  predict.py        Poisson model + blending
  update.py         live data fetcher
  notify.py         Telegram notification stub
outputs/
  predictions.json  generated daily
dashboard/
  index.html        single-file dashboard
.github/workflows/
  matchday.yml      daily cron
```

# World Cup 2026 Match Predictor

## Project Overview
Poisson-based match prediction model for FIFA World Cup 2026, blended with betting market odds.

## Workflow

### Daily Run (automated via GitHub Actions at 11:00 UTC)
1. `python src/update.py` — fetches latest Elo ratings and market odds, updates data files
2. `python src/predict.py` — runs model, writes `outputs/predictions.json`
3. Dashboard at `dashboard/index.html` reads `outputs/predictions.json` directly

### Manual Run
```bash
pip install -r requirements.txt
cp .env.example .env  # add your ODDS_API_KEY
python src/update.py
python src/predict.py
# open dashboard/index.html in browser
```

## Environment Variables
Create a `.env` file (never commit this):
```
ODDS_API_KEY=your_key_here
FOOTBALL_DATA_API_KEY=your_key_here  # optional, free tier
```

## Model Architecture
- **Base**: Poisson distribution on expected goals (xG)
- **xG formula**: `attack_rating × opponent_defence_rating × league_average`
- **Adjustments**: rest days, altitude, heat, stage stakes, crowd factor
- **Calibration**: 60% market odds / 40% Poisson model blend
- **Output**: W/D/L probabilities + scoreline probability matrix

## Data Files
- `data/fixtures.json` — all 104 matches with dates, venues, group info
- `data/teams.json` — Elo ratings, attack/defence strength per team
- `data/venues.json` — altitude, climate, city per venue
- `outputs/predictions.json` — generated daily, consumed by dashboard

## Key Files
- `src/predict.py` — core model logic
- `src/update.py` — live data fetcher
- `src/notify.py` — Telegram notification stub
- `dashboard/index.html` — single-file dashboard

## Adding a New Team Rating
Edit `data/teams.json` and update `elo`, `attack`, and `defence` fields.
Attack/defence are multipliers relative to league average (1.0 = average).

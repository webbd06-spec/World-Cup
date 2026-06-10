"""
Poisson-based World Cup 2026 match predictor.

Model pipeline:
1. Compute expected goals (xG) per team using attack/defence ratings
2. Apply situational adjustments (rest, altitude, heat, stage, crowd)
3. Build Poisson score probability matrix
4. Blend with betting market odds (60% market / 40% model)
5. Write outputs/predictions.json
"""

import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path
from itertools import product

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
OUTPUTS.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_data():
    fixtures = load_json(DATA / "fixtures.json")
    teams    = load_json(DATA / "teams.json")
    venues   = load_json(DATA / "venues.json")

    # Try to load live odds if available
    odds_path = OUTPUTS / "live_odds.json"
    odds = load_json(odds_path) if odds_path.exists() else {}

    return fixtures, teams, venues, odds


# ---------------------------------------------------------------------------
# xG calculation
# ---------------------------------------------------------------------------

LEAGUE_AVG = 1.35  # expected goals per team per match

def compute_xg(attack, opp_defence, venue_factors=None):
    """
    Base xG = attack × opp_defence × league_avg, adjusted for venue.

    Convention (same as Dixon-Coles):
      attack  > 1.0  → above-average scorer
      defence < 1.0  → strong defence (opponents score less than average)
      So: λ = attack_team × defence_opponent × μ
    """
    base = attack * opp_defence * LEAGUE_AVG
    if venue_factors:
        base *= venue_factors.get("altitude_factor", 1.0)
        base *= venue_factors.get("heat_factor", 1.0)
    return max(base, 0.05)


def stage_multiplier(stage):
    """Teams elevate performance as stakes rise."""
    return {
        "group":        1.00,
        "round_of_32":  1.02,
        "round_of_16":  1.04,
        "quarterfinal": 1.06,
        "semifinal":    1.08,
        "third_place":  1.03,
        "final":        1.10,
    }.get(stage, 1.0)


def rest_factor(rest_days):
    """Fatigue/freshness based on days since last match."""
    if rest_days is None:
        return 1.0
    if rest_days <= 2:
        return 0.93
    if rest_days == 3:
        return 0.97
    if rest_days == 4:
        return 1.00
    if rest_days >= 5:
        return 1.02
    return 1.0


def crowd_factor(is_home_nation, venue_country, team_country):
    """Crowd advantage for host nation or regional support."""
    if is_home_nation:
        return 1.05
    if venue_country == team_country:
        return 1.03
    return 1.0


def apply_adjustments(xg, rest_days=None, is_home=False, venue_country="USA", team_country=None, stage="group"):
    """Apply all situational multipliers to base xG."""
    xg *= rest_factor(rest_days)
    xg *= stage_multiplier(stage)
    xg *= crowd_factor(is_home, venue_country, team_country or "")
    return max(xg, 0.05)


# ---------------------------------------------------------------------------
# Poisson probability matrix
# ---------------------------------------------------------------------------

MAX_GOALS = 8

def poisson_pmf(lam, k):
    """P(X = k) for Poisson distribution."""
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def score_matrix(xg_home, xg_away):
    """Return (MAX_GOALS+1)×(MAX_GOALS+1) matrix of P(score = h:a)."""
    matrix = []
    for h in range(MAX_GOALS + 1):
        row = []
        for a in range(MAX_GOALS + 1):
            row.append(poisson_pmf(xg_home, h) * poisson_pmf(xg_away, a))
        matrix.append(row)
    return matrix


def wdl_from_matrix(matrix):
    """Derive W/D/L probabilities from score matrix."""
    win = draw = loss = 0.0
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            p = matrix[h][a]
            if h > a:
                win += p
            elif h == a:
                draw += p
            else:
                loss += p
    total = win + draw + loss
    return win / total, draw / total, loss / total


def top_scorelines(matrix, n=6):
    """Return the n most likely exact scorelines."""
    scores = []
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            scores.append((h, a, matrix[h][a]))
    scores.sort(key=lambda x: -x[2])
    return [{"home": h, "away": a, "prob": round(p, 4)} for h, a, p in scores[:n]]


# ---------------------------------------------------------------------------
# Market odds blending
# ---------------------------------------------------------------------------

def odds_to_probs(odds_entry):
    """Convert decimal odds to implied probabilities (normalised)."""
    if not odds_entry:
        return None
    try:
        raw_w = 1.0 / odds_entry["home_win"]
        raw_d = 1.0 / odds_entry["draw"]
        raw_l = 1.0 / odds_entry["away_win"]
        total = raw_w + raw_d + raw_l
        return raw_w / total, raw_d / total, raw_l / total
    except (KeyError, ZeroDivisionError, TypeError):
        return None


MARKET_WEIGHT = 0.60
MODEL_WEIGHT  = 0.40

def blend(model_wdl, market_wdl):
    """60/40 blend of market and model probabilities."""
    if market_wdl is None:
        return model_wdl
    blended = tuple(
        MARKET_WEIGHT * m + MODEL_WEIGHT * p
        for m, p in zip(market_wdl, model_wdl)
    )
    total = sum(blended)
    return tuple(b / total for b in blended)


# ---------------------------------------------------------------------------
# Expected goals for a single match
# ---------------------------------------------------------------------------

def get_team_ratings(team_name, teams_data):
    """Look up team ratings, falling back to league-average defaults."""
    teams = teams_data["teams"]
    if team_name in teams:
        t = teams[team_name]
        return t["attack"], t["defence"], t.get("elo", 1700)
    # Unknown team — use average
    return 1.0, 1.0, 1700


def predict_match(match, teams_data, venues_data, odds_data):
    """
    Return a prediction dict for a single match.
    For TBD knockout matches, returns a skeleton.
    """
    home_name = match["home"]
    away_name = match["away"]

    if not home_name or not away_name or "TBD" in (home_name or "") or "TBD" in (away_name or ""):
        return {
            "id":     match["id"],
            "stage":  match["stage"],
            "home":   home_name,
            "away":   away_name,
            "date":   match["date"],
            "venue":  match.get("venue"),
            "status": "tbd",
            "win_prob":  None,
            "draw_prob": None,
            "loss_prob": None,
            "xg_home":   None,
            "xg_away":   None,
            "top_scorelines": [],
            "score_matrix": [],
            "source": "tbd",
        }

    venue_name = match.get("venue", "")
    venue_info = venues_data["venues"].get(venue_name, {})
    venue_country = venue_info.get("country", "USA")

    home_attack, home_defence, _ = get_team_ratings(home_name, teams_data)
    away_attack, away_defence, _ = get_team_ratings(away_name, teams_data)

    # Base xG before adjustments
    xg_home_base = compute_xg(home_attack, away_defence, venue_info)
    xg_away_base = compute_xg(away_attack, home_defence, venue_info)

    stage = match.get("stage", "group")
    # Home/neutral: WC matches are mostly neutral, but host nations get crowd boost
    home_is_host = home_name in ("USA", "Mexico", "Canada")
    away_is_host = away_name in ("USA", "Mexico", "Canada")

    xg_home = apply_adjustments(
        xg_home_base,
        rest_days=match.get("rest_days_home"),
        is_home=home_is_host,
        venue_country=venue_country,
        team_country=home_name,
        stage=stage,
    )
    xg_away = apply_adjustments(
        xg_away_base,
        rest_days=match.get("rest_days_away"),
        is_home=away_is_host,
        venue_country=venue_country,
        team_country=away_name,
        stage=stage,
    )

    matrix = score_matrix(xg_home, xg_away)
    model_wdl = wdl_from_matrix(matrix)

    # Market blend
    market_raw = odds_data.get(match["id"])
    market_wdl = odds_to_probs(market_raw)
    final_wdl  = blend(model_wdl, market_wdl)

    return {
        "id":     match["id"],
        "stage":  stage,
        "group":  match.get("group"),
        "matchday": match.get("matchday"),
        "home":   home_name,
        "away":   away_name,
        "date":   match["date"],
        "kickoff_utc": match.get("kickoff_utc"),
        "venue":  venue_name,
        "city":   match.get("city"),
        "status": match.get("status", "scheduled"),
        "result": match.get("result"),

        "xg_home": round(xg_home, 3),
        "xg_away": round(xg_away, 3),

        "win_prob":  round(final_wdl[0], 4),
        "draw_prob": round(final_wdl[1], 4),
        "loss_prob": round(final_wdl[2], 4),

        "model_win":  round(model_wdl[0], 4),
        "model_draw": round(model_wdl[1], 4),
        "model_loss": round(model_wdl[2], 4),

        "market_win":  round(market_wdl[0], 4) if market_wdl else None,
        "market_draw": round(market_wdl[1], 4) if market_wdl else None,
        "market_loss": round(market_wdl[2], 4) if market_wdl else None,

        "top_scorelines": top_scorelines(matrix),
        "score_matrix": [[round(p, 5) for p in row] for row in matrix],

        "source": "blend" if market_wdl else "model_only",
        "blend_weights": {"market": MARKET_WEIGHT, "model": MODEL_WEIGHT} if market_wdl else None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(match_filter=None):
    fixtures, teams, venues, odds = load_data()

    today = date.today().isoformat()
    predictions = []

    for match in fixtures["matches"]:
        # Optional: only predict a specific match ID or today's matches
        if match_filter and match["id"] != match_filter:
            if match_filter != "today":
                continue
            if match.get("date") != today:
                continue

        pred = predict_match(match, teams, venues, odds)
        predictions.append(pred)

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "date": today,
        "model_version": "1.0.0",
        "blend_weights": {"market": MARKET_WEIGHT, "model": MODEL_WEIGHT},
        "predictions": predictions,
    }

    out_path = OUTPUTS / "predictions.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"✓ Wrote {len(predictions)} predictions → {out_path}")

    # Print England's first game for quick review
    for p in predictions:
        if p["home"] == "England" or p["away"] == "England":
            if p.get("matchday") == 1:
                _print_prediction(p)
                break

    return output


def _print_prediction(p):
    print("\n" + "=" * 55)
    print(f"  {p['home']} vs {p['away']}")
    print(f"  {p['date']}  |  {p['venue']}")
    print(f"  Stage: {p['stage']}  |  Group: {p.get('group', '—')}")
    print("-" * 55)
    print(f"  xG:  {p['home']} {p['xg_home']:.2f}  |  {p['away']} {p['xg_away']:.2f}")
    print(f"  Win:  {p['win_prob']*100:.1f}%  "
          f"Draw: {p['draw_prob']*100:.1f}%  "
          f"Loss: {p['loss_prob']*100:.1f}%")
    print(f"  Source: {p['source']}")
    print("\n  Top scorelines:")
    for s in p["top_scorelines"]:
        print(f"    {p['home']} {s['home']}–{s['away']} {p['away']}   {s['prob']*100:.1f}%")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    filter_arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(match_filter=filter_arg)

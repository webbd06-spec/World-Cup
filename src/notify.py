"""
Telegram notifications for WC 2026 predictions.

Usage:
    python src/notify.py                    # morning: all today's fixtures
    python src/notify.py --lineup GS_A1    # pre-kickoff: lineup update
    python src/notify.py --lineup GS_A1 GS_B2  # multiple matches
"""

import json
import os
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

try:
    import requests
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

ROOT    = Path(__file__).parent.parent
OUTPUTS = ROOT / "outputs"
DATA    = ROOT / "data"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
DASHBOARD = "https://webbd06-spec.github.io/World-Cup"

FLAGS = {
    "Afghanistan": "🇦🇫", "Albania": "🇦🇱", "Algeria": "🇩🇿",
    "Angola": "🇦🇴", "Argentina": "🇦🇷", "Australia": "🇦🇺",
    "Austria": "🇦🇹", "Belgium": "🇧🇪", "Bolivia": "🇧🇴",
    "Bosnia and Herzegovina": "🇧🇦", "Bosnia & Herzegovina": "🇧🇦",
    "Brazil": "🇧🇷", "Cameroon": "🇨🇲", "Canada": "🇨🇦",
    "Chile": "🇨🇱", "China PR": "🇨🇳", "China": "🇨🇳",
    "Colombia": "🇨🇴", "Congo DR": "🇨🇩", "DR Congo": "🇨🇩",
    "Costa Rica": "🇨🇷", "Croatia": "🇭🇷", "Cuba": "🇨🇺",
    "Czech Republic": "🇨🇿", "Czechia": "🇨🇿",
    "Denmark": "🇩🇰", "Ecuador": "🇪🇨", "Egypt": "🇪🇬",
    "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Fiji": "🇫🇯", "France": "🇫🇷",
    "Germany": "🇩🇪", "Ghana": "🇬🇭", "Greece": "🇬🇷",
    "Guatemala": "🇬🇹", "Haiti": "🇭🇹", "Honduras": "🇭🇳",
    "Hungary": "🇭🇺", "Indonesia": "🇮🇩", "Iran": "🇮🇷",
    "Iraq": "🇮🇶", "Israel": "🇮🇱", "Italy": "🇮🇹",
    "Ivory Coast": "🇨🇮", "Côte d'Ivoire": "🇨🇮",
    "Jamaica": "🇯🇲", "Japan": "🇯🇵", "Jordan": "🇯🇴",
    "Kenya": "🇰🇪", "Korea Republic": "🇰🇷", "South Korea": "🇰🇷",
    "Mali": "🇲🇱", "Mexico": "🇲🇽", "Morocco": "🇲🇦",
    "Netherlands": "🇳🇱", "New Caledonia": "🇳🇨", "New Zealand": "🇳🇿",
    "Nigeria": "🇳🇬", "Norway": "🇳🇴", "Panama": "🇵🇦",
    "Paraguay": "🇵🇾", "Peru": "🇵🇪", "Poland": "🇵🇱",
    "Portugal": "🇵🇹", "Qatar": "🇶🇦", "Romania": "🇷🇴",
    "Saudi Arabia": "🇸🇦", "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Senegal": "🇸🇳",
    "Serbia": "🇷🇸", "Slovakia": "🇸🇰", "Slovenia": "🇸🇮",
    "Solomon Islands": "🇸🇧", "South Africa": "🇿🇦",
    "Spain": "🇪🇸", "Switzerland": "🇨🇭", "Tanzania": "🇹🇿",
    "Trinidad and Tobago": "🇹🇹", "Tunisia": "🇹🇳",
    "Turkey": "🇹🇷", "Türkiye": "🇹🇷", "Uganda": "🇺🇬",
    "Ukraine": "🇺🇦", "United States": "🇺🇸", "USA": "🇺🇸",
    "Uruguay": "🇺🇾", "Uzbekistan": "🇺🇿", "Venezuela": "🇻🇪",
    "Wales": "🏴󠁧󠁢󠁷󠁬󠁳󠁿", "Zambia": "🇿🇲", "Zimbabwe": "🇿🇼",
}

BOOKIES = {
    "betfair_ex_uk": "Betfair",
    "betfred_uk":    "Betfred",
    "betvictor":     "BetVictor",
    "betway":        "Betway",
    "boylesports":   "BoyleSports",
    "coral":         "Coral",
    "ladbrokes_uk":  "Ladbrokes",
    "paddypower":    "Paddy Power",
    "skybet":        "Sky Bet",
    "unibet_uk":     "Unibet",
    "williamhill":   "William Hill",
}


def flag(team: str) -> str:
    return FLAGS.get(team, "🏳")


def bookie_name(key: str) -> str:
    return BOOKIES.get(key, key)


def send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error {result.get('error_code')}: "
            f"{result.get('description')}"
        )
    return True


def send_chunked(blocks: list[str], header: str = "", footer: str = "") -> None:
    """Send blocks as one message if they fit, otherwise split per block."""
    full = header + "\n\n".join(blocks) + footer
    if len(full) <= 4096:
        send(full)
        return
    if header:
        send(header.strip())
    for i, block in enumerate(blocks):
        chunk = block + (footer if i == len(blocks) - 1 else "")
        send(chunk.strip())


def ko_times(date_str: str, ko_utc: str) -> str:
    if not ko_utc or ko_utc == "00:00":
        return ""
    dt = datetime.strptime(f"{date_str}T{ko_utc}:00+00:00", "%Y-%m-%dT%H:%M:%S%z")
    bst = dt.astimezone(ZoneInfo("Europe/London")).strftime("%H:%M")
    et  = dt.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M")
    return f"{bst} BST · {et} ET"


def countdown_str(date_str: str, ko_utc: str) -> str:
    if not ko_utc or ko_utc == "00:00":
        return ""
    ko_dt = datetime.strptime(f"{date_str}T{ko_utc}:00+00:00", "%Y-%m-%dT%H:%M:%S%z")
    now   = datetime.now(timezone.utc)
    mins  = int((ko_dt - now).total_seconds() / 60)
    bst   = ko_dt.astimezone(ZoneInfo("Europe/London")).strftime("%H:%M BST")
    if mins > 0:
        return f"⏱ Kickoff in *{mins} min* ({bst})"
    return f"⏱ Kicked off at {bst}"


def best_odds_for(match_odds: dict, outcome: str) -> tuple[float, str]:
    """Return (best_decimal_odds, bookmaker_key) for the given outcome key."""
    best_price, best_book = 0.0, ""
    for book, prices in match_odds.get("h2h", {}).items():
        p = prices.get(outcome, 0)
        if p > best_price:
            best_price, best_book = p, book
    return best_price, best_book


def value_bet_lines(p: dict, match_odds: Optional[dict], threshold: float = 0.08) -> list[str]:
    if not match_odds:
        return []
    checks = [
        ("home_win", p.get("win_prob",  0), p.get("home", "Home")),
        ("draw",     p.get("draw_prob", 0), "Draw"),
        ("away_win", p.get("loss_prob", 0), p.get("away", "Away")),
    ]
    lines = []
    for key, model_prob, label in checks:
        price, book = best_odds_for(match_odds, key)
        if not price:
            continue
        edge = model_prob - (1 / price)
        if edge >= threshold:
            icon = "🔥" if edge >= 0.12 else "✅"
            lines.append(
                f"{icon} *{label}* @ {price} ({bookie_name(book)}) — edge +{edge*100:.0f}%"
            )
    return lines


# ── Morning ───────────────────────────────────────────────────────────────────

def format_match_block(p: dict, match_odds: Optional[dict]) -> str:
    hf  = flag(p["home"])
    af  = flag(p["away"])
    ko  = ko_times(p["date"], p.get("kickoff_utc", ""))
    top = p.get("top_scorelines", [])

    lines = [f"*{hf} {p['home']} vs {af} {p['away']}*"]

    if ko:
        lines.append(f"⏰ {ko}")
    if p.get("venue") and p.get("city"):
        lines.append(f"📍 {p['venue']}, {p['city']}")

    pred = f"{top[0]['home']}–{top[0]['away']}" if top else "–"
    lines.append(
        f"📊 Predicted: {pred}  "
        f"xG {p.get('xg_home', 0):.2f}–{p.get('xg_away', 0):.2f}"
    )

    w = p.get("win_prob",  0) * 100
    d = p.get("draw_prob", 0) * 100
    l = p.get("loss_prob", 0) * 100
    lines.append(f"Win {w:.0f}%  ·  Draw {d:.0f}%  ·  Loss {l:.0f}%")

    if top:
        sl = "  ".join(
            f"{s['home']}–{s['away']} ({s['prob']*100:.0f}%)" for s in top[:3]
        )
        lines.append(f"Scores: {sl}")

    vb = value_bet_lines(p, match_odds)
    if vb:
        lines.extend(vb)

    return "\n".join(lines)


def _kickoff_dt(p: dict) -> Optional[datetime]:
    """Return UTC kickoff datetime for a prediction, or None."""
    date_str = p.get("date", "")
    ko_str   = p.get("kickoff_utc", "")
    if not date_str or not ko_str or ko_str == "00:00":
        return None
    try:
        return datetime.strptime(f"{date_str}T{ko_str}:00+00:00", "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def _outcome(home: int, away: int) -> str:
    return "W" if home > away else "D" if home == away else "L"


def format_result_block(results: list[dict], pred_map: dict) -> str:
    """One-line summary per finished match: score + whether prediction was right."""
    lines = []
    for r in results:
        hf   = flag(r["home"])
        af   = flag(r["away"])
        hs, aws = r["home_score"], r["away_score"]
        actual_outcome = _outcome(hs, aws)

        pred = pred_map.get(r["id"])
        top  = (pred.get("top_scorelines") or []) if pred else []
        if top:
            ps, pa = top[0]["home"], top[0]["away"]
            pred_outcome = _outcome(ps, pa)
            pred_str  = f"{ps}–{pa}"
            correct   = "✅" if pred_outcome == actual_outcome else "❌"
            lines.append(f"{correct} {hf} *{r['home']} {hs}–{aws} {r['away']}* {af}  _(pred {pred_str})_")
        else:
            lines.append(f"🏁 {hf} *{r['home']} {hs}–{aws} {r['away']}* {af}")

    return "\n".join(lines)


def morning(predictions: list[dict], all_odds: dict, results: list[dict]) -> None:
    now   = datetime.now(timezone.utc)
    today = date.today().isoformat()
    pred_map = {p["id"]: p for p in predictions}

    # ── Recent results (since yesterday morning) ──────────────────────────────
    # Show any result from the last 2 UTC days — catches overnight games
    # (e.g. 01:00 UTC today) that belong to the previous matchday session.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    recent_results = [r for r in results if r["date"] >= yesterday]
    recent_results.sort(key=lambda r: r["date"])

    # ── Today's upcoming fixtures (+ overnight games within 20 h) ─────────────
    cutoff      = now + timedelta(hours=20)
    result_ids  = {r["id"] for r in results}
    upcoming = [
        p for p in predictions
        if p.get("home") and p.get("away")
        and p.get("status") not in ("tbd", "finished", "in_progress")
        and p["id"] not in result_ids
        and now <= (_kickoff_dt(p) or datetime.min.replace(tzinfo=timezone.utc)) <= cutoff
    ]
    upcoming.sort(key=lambda p: _kickoff_dt(p) or datetime.max.replace(tzinfo=timezone.utc))

    if not recent_results and not upcoming:
        print("No results or upcoming matches — nothing to send.")
        return

    today_fmt = datetime.strptime(today, "%Y-%m-%d").strftime("%-d %b %Y")
    parts = []

    # Results section
    if recent_results:
        dates = sorted({r["date"] for r in recent_results})
        date_label = " & ".join(
            datetime.strptime(d, "%Y-%m-%d").strftime("%-d %b") for d in dates
        )
        parts.append(
            f"📋 *Results — {date_label}*\n"
            + format_result_block(recent_results, pred_map)
        )

    # Predictions section
    if upcoming:
        parts.append(
            f"⚽ *Predictions — {today_fmt}*\n"
            + "\n\n".join(format_match_block(p, all_odds.get(p["id"])) for p in upcoming)
        )

    footer = f"\n\n🔗 {DASHBOARD}"
    full   = "\n\n".join(parts) + footer

    if len(full) <= 4096:
        send(full)
    else:
        for part in parts:
            send(part)
        send(f"🔗 {DASHBOARD}")

    print(f"Morning notification sent — {len(recent_results)} result(s), {len(upcoming)} prediction(s).")


# ── Pre-kickoff lineup update ─────────────────────────────────────────────────

def lineup_update(fixture_ids: list[str], predictions: list[dict], all_odds: dict) -> None:
    pred_map = {p["id"]: p for p in predictions}

    for fid in fixture_ids:
        p = pred_map.get(fid)
        if not p or not p.get("home"):
            print(f"No prediction found for {fid} — skipping.")
            continue

        hf = flag(p["home"])
        af = flag(p["away"])

        lines = [f"📋 *Lineups confirmed — {hf} {p['home']} vs {af} {p['away']}*"]

        cd = countdown_str(p["date"], p.get("kickoff_utc", ""))
        if cd:
            lines.append(cd)

        # Lineup adjustment notes from data/news/<id>.json
        news_path = DATA / "news" / f"{fid}.json"
        if news_path.exists():
            with open(news_path) as f:
                news = json.load(f)
            hm = news.get("home_multiplier", 1.0)
            am = news.get("away_multiplier", 1.0)
            adj_parts = []
            if abs(hm - 1.0) >= 0.01:
                direction = "↓" if hm < 1.0 else "↑"
                adj_parts.append(f"{p['home']} xG {direction} ×{hm:.2f}")
            if abs(am - 1.0) >= 0.01:
                direction = "↓" if am < 1.0 else "↑"
                adj_parts.append(f"{p['away']} xG {direction} ×{am:.2f}")
            if adj_parts:
                lines.append("📋 " + "  ·  ".join(adj_parts))
            notes = news.get("notes", "").strip()
            if notes:
                lines.append(f"_{notes}_")

        lines.append("")
        w = p.get("win_prob",  0) * 100
        d = p.get("draw_prob", 0) * 100
        l = p.get("loss_prob", 0) * 100
        lines.append(f"Updated: Win {w:.0f}%  ·  Draw {d:.0f}%  ·  Loss {l:.0f}%")

        vb = value_bet_lines(p, all_odds.get(fid))
        if vb:
            lines.extend(vb)
        else:
            lines.append("No value bets at current odds.")

        lines.append(f"\n🔗 {DASHBOARD}")
        send("\n".join(lines))
        print(f"Pre-kickoff notification sent for {fid}.")


# ── Kickoff reminder (60–75 min before kickoff) ───────────────────────────────

def kickoff_reminder(predictions: list[dict], all_odds: dict) -> None:
    """
    Send a reminder for any match kicking off in 55–75 minutes.
    Called every 15 min; the 20-min window ensures exactly one notification
    fires per match even with GitHub cron jitter.
    """
    now          = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=55)
    window_end   = now + timedelta(minutes=75)

    upcoming = [
        p for p in predictions
        if p.get("home") and p.get("away")
        and p.get("status") not in ("finished", "tbd", "in_progress")
        and window_start <= (_kickoff_dt(p) or datetime.min.replace(tzinfo=timezone.utc)) <= window_end
    ]

    if not upcoming:
        print("No kickoffs in the 55–75 min window.")
        return

    for p in upcoming:
        hf  = flag(p["home"])
        af  = flag(p["away"])
        ko  = _kickoff_dt(p)
        bst = ko.astimezone(ZoneInfo("Europe/London")).strftime("%H:%M BST") if ko else ""
        et  = ko.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M ET") if ko else ""

        top = p.get("top_scorelines", [])
        pred = f"{top[0]['home']}–{top[0]['away']}" if top else "–"
        w = p.get("win_prob",  0) * 100
        d = p.get("draw_prob", 0) * 100
        l = p.get("loss_prob", 0) * 100

        lines = [
            f"⚽ *{hf} {p['home']} vs {af} {p['away']}* — 1 hour to go!",
            f"⏰ {bst} · {et}",
        ]
        if p.get("venue") and p.get("city"):
            lines.append(f"📍 {p['venue']}, {p['city']}")
        lines.append(
            f"📊 Predicted: *{pred}*  xG {p.get('xg_home',0):.2f}–{p.get('xg_away',0):.2f}"
        )
        lines.append(f"Win {w:.0f}%  ·  Draw {d:.0f}%  ·  Loss {l:.0f}%")

        vb = value_bet_lines(p, all_odds.get(p["id"]))
        if vb:
            lines.extend(vb)

        news_path = DATA / "news" / f"{p['id']}.json"
        if news_path.exists() and news_path.stat().st_size > 0:
            with open(news_path) as f:
                news = json.load(f)
            notes = news.get("notes", "").strip()
            if notes:
                lines.append(f"\n_{notes}_")

        lines.append(f"\n🔗 {DASHBOARD}")
        send("\n".join(lines))
        print(f"Kickoff reminder sent for {p['id']} ({p['home']} vs {p['away']}).")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping.")
        return

    pred_path = OUTPUTS / "predictions.json"
    odds_path = OUTPUTS / "live_odds_uk.json"

    if not pred_path.exists():
        print("No predictions.json — run predict.py first.")
        sys.exit(1)

    with open(pred_path) as f:
        pred_data = json.load(f)
    predictions = pred_data.get("predictions", [])

    all_odds = {}
    if odds_path.exists():
        with open(odds_path) as f:
            raw = json.load(f)
        all_odds = raw.get("matches", raw)

    results = []
    results_path = DATA / "results.json"
    if results_path.exists():
        with open(results_path) as f:
            results = json.load(f).get("results", [])

    if len(sys.argv) >= 3 and sys.argv[1] == "--lineup":
        lineup_update(sys.argv[2:], predictions, all_odds)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--remind":
        kickoff_reminder(predictions, all_odds)
    else:
        morning(predictions, all_odds, results)


if __name__ == "__main__":
    main()

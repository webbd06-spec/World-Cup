"""
Telegram notification stub.

Sends today's top predictions to a Telegram chat.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Usage:
    python src/notify.py                   # send today's predictions
    python src/notify.py GS_D1            # send specific match
"""

import json
import os
import sys
from pathlib import Path
from datetime import date

try:
    import requests
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

ROOT    = Path(__file__).parent.parent
OUTPUTS = ROOT / "outputs"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def send_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env)")
        print("--- Message preview ---")
        print(text)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=10)
    resp.raise_for_status()
    return True


def format_prediction(p):
    home = p["home"]
    away = p["away"]
    win  = p["win_prob"]  * 100
    draw = p["draw_prob"] * 100
    loss = p["loss_prob"] * 100

    bar_win  = "█" * round(win  / 10)
    bar_draw = "░" * round(draw / 10)
    bar_loss = "▒" * round(loss / 10)

    top = p["top_scorelines"][:3]
    scorelines = "  ".join(f"{s['home']}–{s['away']} ({s['prob']*100:.0f}%)" for s in top)

    src = "📊 model+market" if p.get("source") == "blend" else "🤖 model only"

    return (
        f"*{home} vs {away}*\n"
        f"📅 {p['date']}  🏟 {p.get('city', '')}\n"
        f"W {win:.0f}% {bar_win}|{bar_draw} {draw:.0f}% D  {loss:.0f}% L {bar_loss}\n"
        f"Top scores: {scorelines}\n"
        f"xG: {home} {p['xg_home']}  {away} {p['xg_away']}  ({src})"
    )


def main():
    pred_path = OUTPUTS / "predictions.json"
    if not pred_path.exists():
        print("No predictions.json found — run predict.py first")
        sys.exit(1)

    with open(pred_path) as f:
        data = json.load(f)

    match_filter = sys.argv[1] if len(sys.argv) > 1 else None
    today = date.today().isoformat()

    messages = []
    for p in data["predictions"]:
        if p.get("status") == "tbd":
            continue
        if match_filter:
            if p["id"] != match_filter:
                continue
        else:
            if p.get("date") != today:
                continue
        messages.append(format_prediction(p))

    if not messages:
        print(f"No {'match ' + match_filter if match_filter else 'matches today'} found in predictions")
        return

    header = f"⚽ *World Cup 2026 Predictions* — {today}\n\n"
    body = "\n\n---\n\n".join(messages)
    full_msg = header + body

    if len(full_msg) > 4096:
        # Split into chunks
        chunks = [header]
        for msg in messages:
            if len(chunks[-1]) + len(msg) > 4000:
                chunks.append("")
            chunks[-1] += msg + "\n\n---\n\n"
        for chunk in chunks:
            send_message(chunk.strip())
    else:
        send_message(full_msg)

    print(f"Sent {len(messages)} prediction(s)")


if __name__ == "__main__":
    main()

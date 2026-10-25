import asyncio
import html
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from engine import LEAGUES, EdgePicksEngine

TOKEN = os.environ.get("BOT_TOKEN")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

DISCLAIMER = "\n<i>Statistical estimates, not guarantees. Bet responsibly.</i>"


def pct(x):
    return f"{x * 100:.0f}%"


def fmt_football(m, p):
    head = f"⚔️ <b>{html.escape(m['home'])} vs {html.escape(m['away'])}</b> ({m['time']})\n"
    if not p.get("success"):
        return head + f"⚠️ {p['error']}\n\n"
    best, best_p = p["top_scores"][0]
    alt = ", ".join(f"{s} ({pct(sp)})" for s, sp in p["top_scores"][1:])
    c, k = p["corners"], p["cards"]
    return (
        head
        + f"🎯 Likely score: <b>{best}</b> ({pct(best_p)}) | also {alt}\n"
        + f"📊 1X2: H {pct(p['p_home'])} | D {pct(p['p_draw'])} | A {pct(p['p_away'])}\n"
        + f"⚽ Exp. goals {p['xg'][0]:.1f}-{p['xg'][1]:.1f} | O1.5 {pct(p['over'][1.5])} | "
        + f"O2.5 {pct(p['over'][2.5])} | O3.5 {pct(p['over'][3.5])} | BTTS {pct(p['btts'])}\n"
        + f"🚩 Corners: ~{c['mu']:.1f} → <b>{c['side']} {c['line']}</b> ({pct(c['prob'])})\n"
        + f"🟨 Cards: ~{k['mu']:.1f} → <b>{k['side']} {k['line']}</b> ({pct(k['prob'])})\n"
        + f"🔎 Data confidence: {p['confidence']}\n\n"
    )


def fmt_us(m, emoji):
    sp = m["spread"]
    fav = m["home"] if sp < 0 else m["away"]
    return (
        f"{emoji} <b>{html.escape(m['home'])} vs {html.escape(m['away'])}</b> ({m['time']})\n"
        f"🎯 Market-implied score: {m['home_pts']:.0f} - {m['away_pts']:.0f} | "
        f"Total: {m['total']} | Favourite: {html.escape(fav)} ({-abs(sp)})\n\n"
    )


async def send_blocks(update: Update, header: str, blocks):
    text = header
    for b in blocks:
        if len(text) + len(b) > 3800:
            await update.message.reply_text(text, parse_mode="HTML")
            text = ""
        text += b
    await update.message.reply_text(text + DISCLAIMER, parse_mode="HTML")


def get_hours(context: ContextTypes.DEFAULT_TYPE, default=24):
    try:
        return max(1, min(int(context.args[0]), 72))
    except (IndexError, ValueError):
        return default


async def run_football(update: Update, context: ContextTypes.DEFAULT_TYPE, keys):
    if not ODDS_API_KEY:
        await update.message.reply_text("❌ ODDS_API_KEY is not set on the server.")
        return
    hours = get_hours(context)
    await update.message.reply_text("⏳ Crunching the numbers...")
    blocks = []
    try:
        for key in keys:
            events = await asyncio.to_thread(
                EdgePicksEngine.get_events, ODDS_API_KEY, LEAGUES[key][0], hours)
            if not events:
                continue
            blocks.append(f"🏆 <b>{LEAGUES[key][2]}</b>\n\n")
            for m in events:
                pred = await asyncio.to_thread(
                    EdgePicksEngine.predict_football, key, m["home"], m["away"])
                blocks.append(fmt_football(m, pred))
    except Exception as e:
        print(f"football error: {e}")
        await update.message.reply_text(f"⚠️ Could not fetch matches: {e}")
        return
    if not blocks:
        await update.message.reply_text(f"No matches in the next {hours}h. Try /today 48")
        return
    await send_blocks(update, f"⚽ <b>EdgePicks - next {hours}h</b>\n\n", blocks)


def league_cmd(key):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await run_football(update, context, [key])
    return handler


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_football(update, context, list(LEAGUES))


async def run_us(update: Update, context: ContextTypes.DEFAULT_TYPE, sport_key, name, emoji):
    if not ODDS_API_KEY:
        await update.message.reply_text("❌ ODDS_API_KEY is not set on the server.")
        return
    hours = get_hours(context, 36)
    try:
        games = await asyncio.to_thread(
            EdgePicksEngine.get_market_lines, ODDS_API_KEY, sport_key, hours)
    except Exception as e:
        print(f"{name} error: {e}")
        await update.message.reply_text(f"⚠️ Could not fetch {name} lines: {e}")
        return
    if not games:
        await update.message.reply_text(f"No {name} games with lines in the next {hours}h.")
        return
    header = f"{emoji} <b>{name} - bookmaker consensus</b>\n\n"
    await send_blocks(update, header, [fmt_us(g, emoji) for g in games])


async def nba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_us(update, context, "basketball_nba", "NBA", "🏀")


async def nfl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_us(update, context, "americanfootball_nfl", "NFL", "🏈")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>EdgePicks Bot</b>\n\n"
        "⚽ /today - all top-5 leagues (next 24h)\n"
        "/epl /laliga /bundesliga /seriea /ligue1 - one league\n"
        "🏀 /nba   🏈 /nfl\n\n"
        "Add hours to widen the window, e.g. <code>/today 48</code> (max 72).",
        parse_mode="HTML",
    )


def run_bot():
    if not TOKEN:
        print("❌ Error: BOT_TOKEN is not set")
        return
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("today", today))
    for key in LEAGUES:
        app.add_handler(CommandHandler(key, league_cmd(key)))
    app.add_handler(CommandHandler("nba", nba))
    app.add_handler(CommandHandler("nfl", nfl))
    print("🤖 EdgePicks Bot running with polling...")
    app.run_polling()

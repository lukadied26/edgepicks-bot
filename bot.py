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


SEP = "──────────────"


def pick_result(m, p):
    home, away = html.escape(m["home"]), html.escape(m["away"])
    h, d, a = p["p_home"], p["p_draw"], p["p_away"]
    best = max(h, d, a)
    if best >= 0.5:
        if best == h:
            label = f"{home} to win"
        elif best == a:
            label = f"{away} to win"
        else:
            label = "Draw"
        return f"Result: {label} ({pct(best)})"
    options = {
        f"{home} or Draw": h + d,
        f"{away} or Draw": a + d,
        f"{home} or {away}": h + a,
    }
    label, prob = max(options.items(), key=lambda kv: kv[1])
    return f"Double chance: {label} ({pct(prob)})"


def fmt_football(m, p):
    head = (
        f"⚔️ <b>{html.escape(m['home'])} vs {html.escape(m['away'])}</b>\n"
        f"🕒 {m['time']}\n\n"
    )
    if not p.get("success"):
        return head + f"⚠️ {p['error']}\n\n{SEP}\n\n"

    o25 = p["over"][2.5]
    goals, goals_p = ("Over 2.5", o25) if o25 >= 0.5 else ("Under 2.5", 1 - o25)
    btts, btts_p = ("Yes", p["btts"]) if p["btts"] >= 0.5 else ("No", 1 - p["btts"])
    best, best_p = p["top_scores"][0]
    others = ", ".join(f"{s} ({pct(sp)})" for s, sp in p["top_scores"][1:])
    c, k = p["corners"], p["cards"]

    return (
        head
        + "📊 <b>Analysis</b>\n"
        + f"• 1X2: H {pct(p['p_home'])} | D {pct(p['p_draw'])} | A {pct(p['p_away'])}\n"
        + f"• Exp. goals: {p['xg'][0]:.1f} - {p['xg'][1]:.1f}\n"
        + f"• Over 1.5: {pct(p['over'][1.5])} | 2.5: {pct(o25)} | 3.5: {pct(p['over'][3.5])}\n"
        + f"• BTTS: {pct(p['btts'])}\n"
        + f"• Other scores: {others}\n"
        + f"• Corners ~{c['mu']:.1f} | Cards ~{k['mu']:.1f}\n\n"
        + "🔥 <b>Most likely outcomes</b>\n"
        + f"• FT score: <b>{best}</b> ({pct(best_p)})\n"
        + f"• {pick_result(m, p)}\n"
        + f"• Goals: <b>{goals}</b> ({pct(goals_p)})\n"
        + f"• BTTS: <b>{btts}</b> ({pct(btts_p)})\n"
        + f"• Corners: <b>{c['side']} {c['line']}</b> ({pct(c['prob'])})\n"
        + f"• Cards: <b>{k['side']} {k['line']}</b> ({pct(k['prob'])})\n\n"
        + f"🔎 Data confidence: {p['confidence']}\n\n"
        + f"{SEP}\n\n"
    )


def fmt_us(m, emoji):
    sp = m["spread"]
    fav = m["home"] if sp < 0 else m["away"]
    return (
        f"{emoji} <b>{html.escape(m['home'])} vs {html.escape(m['away'])}</b>\n"
        f"🕒 {m['time']}\n\n"
        f"• Projected score: <b>{m['home_pts']:.0f} - {m['away_pts']:.0f}</b>\n"
        f"• Total: {m['total']}\n"
        f"• Favourite: {html.escape(fav)} ({-abs(sp)})\n\n"
        f"{SEP}\n\n"
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


async def run_football(update: Update, context: ContextTypes.DEFAULT_TYPE, keys, hours=None):
    if not ODDS_API_KEY:
        await update.message.reply_text("❌ ODDS_API_KEY is not set on the server.")
        return
    hours = hours or get_hours(context)
    await update.message.reply_text("⏳ Crunching the numbers...")
    blocks = []
    try:
        for key in keys:
            events = await asyncio.to_thread(
                EdgePicksEngine.get_events, ODDS_API_KEY, LEAGUES[key][0], hours)
            if not events:
                continue
            header = f"🏆 <b>{LEAGUES[key][2]}</b>\n\n"
            for m in events:
                pred = await asyncio.to_thread(
                    EdgePicksEngine.predict_football, key, m["home"], m["away"])
                blocks.append(header + fmt_football(m, pred))
                header = ""
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


def window_cmd(hours):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await run_football(update, context, list(LEAGUES), hours)
    return handler


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
        "/today48 - next 48h   /today72 - next 72h\n"
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
    app.add_handler(CommandHandler("today48", window_cmd(48)))
    app.add_handler(CommandHandler("today72", window_cmd(72)))
    for key in LEAGUES:
        app.add_handler(CommandHandler(key, league_cmd(key)))
    app.add_handler(CommandHandler("nba", nba))
    app.add_handler(CommandHandler("nfl", nfl))
    print("🤖 EdgePicks Bot running with polling...")
    app.run_polling()

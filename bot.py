import asyncio
import html
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from engine import COMPETITIONS, CUPS, TOP5, EdgePicksEngine

TOKEN = os.environ.get("BOT_TOKEN")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

DISCLAIMER = "\n<i>Statistical estimates, not guarantees. Bet responsibly.</i>"
SEP = "──────────────"


def pct(x):
    return f"{x * 100:.0f}%"


def esc(s):
    return html.escape(str(s))


def pick_result(m, p):
    home, away = esc(m["home"]), esc(m["away"])
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
    if " or Draw" not in label:
        return f"No draw: {label} ({pct(prob)})"
    return f"Double chance: {label} ({pct(prob)})"


def news_block(m, p, news, flags):
    lines = []
    many = False
    if news and news.get("injuries_known"):
        for side in ("home", "away"):
            players = news[side]
            if len(players) >= 3:
                many = True
            shown = ", ".join(esc(x) for x in players[:3])
            if len(players) > 3:
                shown += f" +{len(players) - 3} more"
            lines.append(f"• {esc(m[side])}: {shown or 'none reported'}")
    if p.get("referee"):
        r = p["referee"]
        lines.append(f"• Referee: {esc(r['name'])} (avg {r['avg']:.1f} cards/match)")
    for f in list(p.get("rest_flags", [])) + list(flags):
        lines.append(f"⚠️ {esc(f)}")
    if not lines:
        return "", many
    return "📰 <b>Team news &amp; schedule</b>\n" + "\n".join(lines) + "\n\n", many


def fmt_match(item):
    m, p, news, flags = item["event"], item["pred"], item["news"], item["flags"]
    head = f"⚔️ <b>{esc(m['home'])} vs {esc(m['away'])}</b>\n🕒 {m['time']}\n\n"
    if not p.get("success"):
        return head + f"⚠️ {esc(p['error'])}\n\n{SEP}\n\n"

    o25 = p["over"][2.5]
    goals, goals_p = ("Over 2.5", o25) if o25 >= 0.5 else ("Under 2.5", 1 - o25)
    btts, btts_p = ("Yes", p["btts"]) if p["btts"] >= 0.5 else ("No", 1 - p["btts"])
    best, best_p = p["top_scores"][0]
    others = ", ".join(f"{s} ({pct(sp)})" for s, sp in p["top_scores"][1:])
    c, k = p.get("corners"), p.get("cards")

    analysis = (
        "📊 <b>Analysis</b>\n"
        f"• 1X2: H {pct(p['p_home'])} | D {pct(p['p_draw'])} | A {pct(p['p_away'])}\n"
        f"• Exp. goals: {p['xg'][0]:.1f} - {p['xg'][1]:.1f}\n"
        f"• Over 1.5: {pct(p['over'][1.5])} | 2.5: {pct(o25)} | 3.5: {pct(p['over'][3.5])}\n"
        f"• BTTS: {pct(p['btts'])}\n"
        f"• Other scores: {others}\n"
    )
    if c and k:
        analysis += f"• Corners ~{c['mu']:.1f} | Cards ~{k['mu']:.1f}\n"

    outcomes = (
        "\n🔥 <b>Most likely outcomes</b>\n"
        f"• FT score: <b>{best}</b> ({pct(best_p)})\n"
        f"• {pick_result(m, p)}\n"
        f"• Goals: <b>{goals}</b> ({pct(goals_p)})\n"
        f"• BTTS: <b>{btts}</b> ({pct(btts_p)})\n"
    )
    if c and k:
        outcomes += (
            f"• Corners: <b>{c['side']} {c['line']}</b> ({pct(c['prob'])})\n"
            f"• Cards: <b>{k['side']} {k['line']}</b> ({pct(k['prob'])})\n"
        )

    notes, many = news_block(m, p, news, flags)
    conf = p["confidence"] + (" | several injuries listed, treat with caution" if many else "")
    return head + analysis + outcomes + "\n" + notes + f"🔎 {esc(conf)}\n\n{SEP}\n\n"


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


async def run_comps(update: Update, context: ContextTypes.DEFAULT_TYPE, comp_ids, hours=None):
    if not ODDS_API_KEY:
        await update.message.reply_text("❌ ODDS_API_KEY is not set on the server.")
        return
    hours = hours or get_hours(context)
    await update.message.reply_text("⏳ Crunching the numbers...")
    blocks, errors = [], []
    for cid in comp_ids:
        try:
            items = await asyncio.to_thread(
                EdgePicksEngine.analyze_competition, ODDS_API_KEY, API_FOOTBALL_KEY, cid, hours)
        except Exception as e:
            print(f"{cid} error: {e}")
            errors.append(str(e))
            continue
        if not items:
            continue
        header = f"🏆 <b>{COMPETITIONS[cid]['name']}</b>\n\n"
        for it in items:
            blocks.append(header + fmt_match(it))
            header = ""
    if not blocks:
        if errors:
            await update.message.reply_text(f"⚠️ Could not fetch matches: {errors[0]}")
        else:
            await update.message.reply_text(f"No matches in the next {hours}h. Try /today48")
        return
    await send_blocks(update, f"⚽ <b>EdgePicks - next {hours}h</b>\n\n", blocks)


def comp_cmd(cid):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await run_comps(update, context, [cid])
    return handler


def window_cmd(hours):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await run_comps(update, context, TOP5 + CUPS, hours)
    return handler


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await run_comps(update, context, TOP5 + CUPS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>EdgePicks Bot</b>\n\n"
        "⚽ /today - top 5 leagues + UCL, UEL, UECL (next 24h)\n"
        "/today48 - next 48h   /today72 - next 72h\n"
        "/epl /laliga /bundesliga /seriea /ligue1\n"
        "/ucl /uel /uecl\n"
        "/mls /saudi /internationals\n\n"
        "Add hours to any command, e.g. <code>/laliga 48</code> (max 72).",
        parse_mode="HTML",
    )


def run_bot():
    if not TOKEN:
        print("❌ Error: BOT_TOKEN is not set")
        return
    app = ApplicationBuilder().token(TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("today48", window_cmd(48)))
    app.add_handler(CommandHandler("today72", window_cmd(72)))
    for cid in COMPETITIONS:
        app.add_handler(CommandHandler(cid, comp_cmd(cid)))
    print("🤖 EdgePicks Bot running with polling...")
    app.run_polling()

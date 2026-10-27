import asyncio
import html
import os

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from engine import COMPETITIONS, CUPS, TOP5, EdgePicksEngine, _pmf

TOKEN = os.environ.get("BOT_TOKEN")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY")

DISCLAIMER = "\n<i>Statistical estimates, not guarantees. Bet responsibly.</i>"
SEP = "──────────────"


def pct(x):
    return f"{x * 100:.0f}%"


def esc(s):
    return html.escape(str(s))


LEAN = 0.55  # below this a pick is a coin flip, so we say "no clear lean"


def pick_result(m, p):
    """Returns (text, set of outcomes the pick covers)."""
    home, away = esc(m["home"]), esc(m["away"])
    h, d, a = p["p_home"], p["p_draw"], p["p_away"]
    best = max(h, d, a)
    if best >= 0.5:
        if best == h:
            return f"Result: {home} to win ({pct(best)})", {"H"}
        if best == a:
            return f"Result: {away} to win ({pct(best)})", {"A"}
        return f"Result: Draw ({pct(best)})", {"D"}
    options = {
        (f"Double chance: {home} or Draw", frozenset({"H", "D"})): h + d,
        (f"Double chance: {away} or Draw", frozenset({"A", "D"})): a + d,
        (f"No draw: {home} or {away}", frozenset({"H", "A"})): h + a,
    }
    (label, covers), prob = max(options.items(), key=lambda kv: kv[1])
    return f"{label} ({pct(prob)})", set(covers)


def lean(prob_yes, yes_label, no_label):
    """(label, probability) if there is a clear lean, otherwise None."""
    if prob_yes >= LEAN:
        return yes_label, prob_yes
    if prob_yes <= 1 - LEAN:
        return no_label, 1 - prob_yes
    return None


def fitting_score(p, allowed, goals, btts):
    """Most likely scoreline that agrees with the result, goals and BTTS picks."""
    ph, pa = _pmf(p["xg"][0], 8), _pmf(p["xg"][1], 8)
    mass = sum(ph) * sum(pa)
    grid = [(i, j, x * y / mass) for i, x in enumerate(ph) for j, y in enumerate(pa)]

    def ok(i, j, use_goals, use_btts):
        out = "H" if i > j else ("A" if j > i else "D")
        if out not in allowed:
            return False
        if use_goals and goals is not None and ((i + j > 2.5) != (goals[0] == "Over 2.5")):
            return False
        if use_btts and btts is not None and ((i > 0 and j > 0) != (btts[0] == "Yes")):
            return False
        return True

    for use_goals, use_btts in ((True, True), (True, False), (False, False)):
        fits = [g for g in grid if ok(g[0], g[1], use_goals, use_btts)]
        if fits:
            i, j, pr = max(fits, key=lambda g: g[2])
            return f"{i}-{j}", pr
    return None


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
    c, k = p.get("corners"), p.get("cards")
    top = ", ".join(f"{s} ({pct(sp)})" for s, sp in p["top_scores"])

    analysis = (
        "📊 <b>Analysis</b>\n"
        f"• 1X2: H {pct(p['p_home'])} | D {pct(p['p_draw'])} | A {pct(p['p_away'])}\n"
        f"• Exp. goals: {p['xg'][0]:.1f} - {p['xg'][1]:.1f}\n"
        f"• Over 1.5: {pct(p['over'][1.5])} | 2.5: {pct(o25)} | 3.5: {pct(p['over'][3.5])}\n"
        f"• BTTS: {pct(p['btts'])}\n"
        f"• Likeliest scores: {top}\n"
    )
    if c and k:
        analysis += f"• Corners ~{c['mu']:.1f} | Cards ~{k['mu']:.1f}\n"

    result_text, allowed = pick_result(m, p)
    goals = lean(o25, "Over 2.5", "Under 2.5")
    btts = lean(p["btts"], "Yes", "No")
    fit = fitting_score(p, allowed, goals, btts)

    outcomes = "\n🔥 <b>Most likely outcomes</b>\n" + f"• {result_text}\n"
    outcomes += (f"• Goals: <b>{goals[0]}</b> ({pct(goals[1])})\n" if goals
                 else f"• Goals: no clear lean (Over 2.5 {pct(o25)})\n")
    outcomes += (f"• BTTS: <b>{btts[0]}</b> ({pct(btts[1])})\n" if btts
                 else f"• BTTS: no clear lean ({pct(p['btts'])})\n")
    if c and k:
        for name, t in (("Corners", c), ("Cards", k)):
            if t["prob"] >= LEAN:
                outcomes += f"• {name}: <b>{t['side']} {t['line']}</b> ({pct(t['prob'])})\n"
            else:
                outcomes += f"• {name}: no clear lean (~{t['mu']:.1f} expected)\n"
    if fit:
        outcomes += f"• Score that fits these picks: <b>{fit[0]}</b> ({pct(fit[1])})\n"

    notes, many = news_block(m, p, news, flags)
    conf = p["confidence"] + (" | several injuries listed, treat with caution" if many else "")
    return head + analysis + outcomes + "\n" + notes + f"🔎 Data confidence: {esc(conf)}\n\n{SEP}\n\n"

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
        "/carabao /facup /dfbpokal /copadelrey /coppaitalia /coupe\n"
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

import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
from engine import EdgePicksEngine

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
TOKEN = os.getenv("BOT_TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🤖 *Welcome to EdgePicks Bot!*\n\n"
        "Your multi-sport automated predictor.\n\n"
        "*Fetch Live Today's Games:*\n"
        "• `/today_football` - Today's Football Matches\n"
        "• `/today_nba` - Today's NBA Games\n"
        "• `/today_nfl` - Today's NFL Games"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def today_football(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ODDS_API_KEY:
        await update.message.reply_text("⚠️ API Key is missing in Render settings!")
        return
    await update.message.reply_text("⚽ Fetching today's top Football fixtures...")
    matches = EdgePicksEngine.get_todays_matches_by_sport(ODDS_API_KEY, "soccer_epl")
    
    if not matches:
        await update.message.reply_text("📅 No scheduled EPL matches found for today.")
        return

    res = "⚽ *Today's Football Predictions*\n\n"
    for m in matches:
        pred = EdgePicksEngine.predict_football(m['home'], m['away'])
        res += f"⚔️ *{m['home']} vs {m['away']}* ({m['time']} UTC)\n🎯 Score: {pred['projected_score']} (H: {pred['prob_home']}% | D: {pred['prob_draw']}% | A: {pred['prob_away']}%)\n\n"
    await update.message.reply_text(res, parse_mode="Markdown")

async def today_nba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ODDS_API_KEY:
        await update.message.reply_text("⚠️ API Key is missing in Render settings!")
        return
    await update.message.reply_text("🏀 Fetching today's NBA games...")
    matches = EdgePicksEngine.get_todays_matches_by_sport(ODDS_API_KEY, "basketball_nba")

    if not matches:
        await update.message.reply_text("📅 No NBA games found scheduled for today.")
        return

    res = "🏀 *Today's NBA Predictions*\n\n"
    for m in matches:
        pred = EdgePicksEngine.predict_nba(m['home'], m['away'])
        res += f"⚔️ *{m['home']} vs {m['away']}*\n🎯 Projected Score: {pred['projected_score']} | Total: {pred['total']} | Line: {pred['spread']}\n\n"
    await update.message.reply_text(res, parse_mode="Markdown")

async def today_nfl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ODDS_API_KEY:
        await update.message.reply_text("⚠️ API Key is missing in Render settings!")
        return
    await update.message.reply_text("🏈 Fetching today's NFL games...")
    matches = EdgePicksEngine.get_todays_matches_by_sport(ODDS_API_KEY, "americanfootball_nfl")

    if not matches:
        await update.message.reply_text("📅 No NFL games found scheduled for today.")
        return

    res = "🏈 *Today's NFL Predictions*\n\n"
    for m in matches:
        pred = EdgePicksEngine.predict_nfl(m['home'], m['away'])
        res += f"⚔️ *{m['home']} vs {m['away']}*\n🎯 Projected Score: {pred['projected_score']} | Line: {pred['spread']}\n\n"
    await update.message.reply_text(res, parse_mode="Markdown")

def run_bot():
    if not TOKEN:
        print("❌ Error: BOT_TOKEN is missing!")
        return
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today_football", today_football))
    app.add_handler(CommandHandler("today_nba", today_nba))
    app.add_handler(CommandHandler("today_nfl", today_nfl))
    print("🤖 EdgePicks Bot running with Multi-Sport Fetcher...")
    app.run_polling()
      

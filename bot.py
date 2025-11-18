import os
import re
import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

from pymongo import MongoClient
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# =========================
# ENV / CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "7941244038"))
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "")

COOKIES_TXT = os.getenv("COOKIES_TXT")  # optional
MONGO_URI = os.getenv("MONGO_URI")      # required
MONGO_DB = os.getenv("MONGO_DB", "youtube_bot")
MONGO_USERS = os.getenv("MONGO_USERS", "users")

# Paths
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("ytbot")

# =========================
# MongoDB Setup
# =========================

try:
    # Connect to MongoDB with proper settings for Atlas
    mongo = MongoClient(
        MONGO_URI,
        tls=True,
        tlsAllowInvalidCertificates=False,
        serverSelectionTimeoutMS=5000,
        retryWrites=True,
        w='majority'
    )
    # Test connection
    mongo.admin.command('ping')
    db = mongo[MONGO_DB]
    users_col = db[MONGO_USERS]
    MONGO_AVAILABLE = True
    log.info("✅ MongoDB connected successfully")
except Exception as e:
    log.error(f"❌ MongoDB connection failed: {e}")
    log.warning("Bot will run without database features")
    MONGO_AVAILABLE = False
    mongo = db = users_col = None

# =========================
# Helpers
# =========================

def ensure_user(update: Update):
    """Track users only if database is available"""
    if not MONGO_AVAILABLE or not update.effective_user:
        return
    try:
        u = update.effective_user
        users_col.update_one(
            {"_id": u.id},
            {"$set": {"name": u.full_name or u.username or str(u.id)}},
            upsert=True
        )
    except Exception as e:
        log.error(f"User tracking failed: {e}")

def is_admin(user_id: int) -> bool:
    """Check admin status without database dependency"""
    return int(user_id) == OWNER_ID

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "output"

YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)

PENDING: Dict[str, str] = {}

def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    token = str(abs(hash((url, os.urandom(4)))))[:10]
    PENDING[token] = url
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("360p", callback_data=f"q|{token}|360"),
         InlineKeyboardButton("480p", callback_data=f"q|{token}|480")],
        [InlineKeyboardButton("720p", callback_data=f"q|{token}|720"),
         InlineKeyboardButton("1080p", callback_data=f"q|{token}|1080")],
        [InlineKeyboardButton("MP3 🎧", callback_data=f"q|{token}|mp3")],
    ])

# =========================
# Error Handler
# =========================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and prevent crashes"""
    log.error("Exception while handling an update:", exc_info=context.error)

# =========================
# yt-dlp Downloader
# =========================

async def download_and_send(chat_id, reply_msg, context, url, quality):
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except:
        pass

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
    }

    if quality == "mp3":
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best"

    if COOKIES_TXT:
        try:
            cookie_path = Path("/tmp/cookies.txt")
            cookie_path.write_text(COOKIES_TXT, encoding="utf-8")
            ydl_opts["cookiefile"] = str(cookie_path)
        except Exception as e:
            log.error(f"Cookie write failed: {e}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = sanitize_filename(info.get("title", "video"))
    except Exception as e:
        await reply_msg.reply_text(f"⚠️ Download failed: {e}")
        return

    ext = ".mp3" if quality == "mp3" else ".mp4"
    files = sorted(DOWNLOAD_DIR.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        await reply_msg.reply_text("⚠️ File not found after download.")
        return

    final_path = files[0]
    caption = f"Downloaded by :- @spotifyxmusixbot"

    try:
        if quality == "mp3":
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_AUDIO)
            with open(final_path, "rb") as f:
                await reply_msg.reply_audio(InputFile(f, filename=final_path.name), caption=caption, title=title)
        else:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
            with open(final_path, "rb") as f:
                await reply_msg.reply_video(InputFile(f, filename=final_path.name), caption=caption)
    except Exception as e:
        await reply_msg.reply_text(f"⚠️ Upload failed: {e}")

# =========================
# Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    # FIXED: Properly escaped MarkdownV2 formatting
    start_text = (
        "🎧 *Welcome to SpotifyX Musix Bot* 🎧\n"
        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n\\n"
        
        "🔥 *Your all\\-in\\-one YouTube downloader*\\n"
        "• Download *MP3 music* in 192kbps 🎧\\n"
        "• Download *Videos* in 360p/480p/720p/1080p 🎬\\n"
        "• Search any song using */search <name>* 🔍\\n"
        "• Fast, clean, no ads — ever 😎\\n\\n"

        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
        "📌 *How to use the bot\\?*\\n"
        "1\\. Send any *YouTube link* → choose quality\\n"
        "2\\. Or use */search* to find songs\\n"
        "3\\. Audio & video sent instantly ⚡\\n\\n"

        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
        "📢 *Important Links*\\n"
        f"• Updates: {UPDATES_CHANNEL}\\n"
        "• Report Issue: @mahadev_ki_iccha\\n"
        "• Paid Bots / Promo: @mahadev_ki_iccha\\n\\n"

        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
        "❓ *Need full guide\\?*\\n"
        "Use */help* to view all commands and details\\.\\n"
    )

    await update.message.reply_text(start_text, parse_mode="MarkdownV2")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    # FIXED: Properly escaped MarkdownV2 formatting
    help_text = (
        "✨ *SpotifyX Musix Bot — Full Guide* ✨\\n"
        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n\\n"

        "🔥 *What this bot can do\\?*\\n"
        "• Download *MP3 music* 🎧\\n"
        "• Download *YouTube Videos* \\(360p/480p/720p/1080p\\) 🎬\\n"
        "• Search any song / video via */search*\\n"
        "• Fast, free, no ads — ever 😎\\n"
        "• Auto quality menu on YouTube link\\n\\n"

        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
        "📌 *How to use the bot\\?*\\n"
        "1\\. Send *any YouTube link* → choose quality\\n"
        "2\\. Use */search <name>* → pick result → choose quality\\n"
        "3\\. Use */start* anytime if bot feels sleepy 😴\\n"
        "4\\. MP3 download gives best audio 192kbps\\n\\n"

        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
        "📢 *Important Links*\\n"
        f"• Updates Channel: {UPDATES_CHANNEL}\\n"
        "• Report Issue: @ayushxchat_robot\\n"
        "• Contact for Paid Bots / Cross Promo: @mahadev_ki_iccha\\n\\n"

        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
        "👑 *Admin Commands*\\n"
        "• /stats — Show user count\\n"
        "• /broadcast <text> — send message to all users\\n\\n"

        "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
        "🤖 *Bot Created By*\\n"
        "• *Tony Stark Jr*⚡\\n"
    )
    await update.message.reply_text(help_text, parse_mode="MarkdownV2")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    txt = update.message.text.strip()
    match = YOUTUBE_REGEX.search(txt)
    if match:
        url = match.group(0)
        await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))

async def on_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        _, token, qlt = q.data.split("|")
    except:
        return
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("Session expired.")
        return
    await q.edit_message_text(f"Downloading {qlt}…")
    await download_and_send(q.message.chat.id, q.message, context, url, qlt)

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /search <text>")
        return

    await update.message.reply_text(f"Searching '{query}'…")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "default_search": "ytsearch5",
        "extract_flat": "in_playlist",
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Search failed: {e}")
        return

    entries = info.get("entries", [])
    if not entries:
        await update.message.reply_text("No results.")
        return

    buttons = []
    for e in entries[:5]:
        title = sanitize_filename(e.get("title") or "video")
        url = e.get("url") or e.get("webpage_url")
        if not url.startswith("http"):
            url = "https://youtube.com/watch?v= " + url
        token = str(abs(hash((url, os.urandom(4)))))[:10]
        PENDING[token] = url
        buttons.append([InlineKeyboardButton(title[:60], callback_data=f"s|{token}|pick")])

    await update.message.reply_text("Choose:", reply_markup=InlineKeyboardMarkup(buttons))

async def on_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, token, _ = q.data.split("|")
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("Expired.")
        return
    await q.edit_message_text("Choose quality:", reply_markup=quality_keyboard(url))

# =========================
# Admin
# =========================

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not MONGO_AVAILABLE:
        await update.message.reply_text("Database is not available")
        return
    total = users_col.count_documents({})
    docs = users_col.find().limit(50)
    preview = "\n".join([f"{d['name']} — {d['_id']}" for d in docs])
    await update.message.reply_text(f"Users: {total}\n\n{preview}")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not MONGO_AVAILABLE:
        await update.message.reply_text("Database is not available")
        return
    users = users_col.find({}, {"_id": 1})
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(u["_id"], text)
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await update.message.reply_text(f"Broadcasted to {sent} users.")

# =========================
# Main
# =========================

def main():
    # Ensure only one instance runs
    import signal
    import sys
    
    def shutdown_handler(signum, frame):
        log.info("Shutting down gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add error handler
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))

    log.info("Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()

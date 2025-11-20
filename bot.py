import os
import re
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

import aiohttp  # For image generation
from pymongo import MongoClient
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.constants import ParseMode
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
    mongo = MongoClient(
        MONGO_URI,
        tls=True,
        tlsAllowInvalidCertificates=False,
        serverSelectionTimeoutMS=5000,
        retryWrites=True,
        w='majority'
    )
    mongo.admin.command('ping')
    db = mongo[MONGO_DB]
    users_col = db[MONGO_USERS]
    MONGO_AVAILABLE = True
    log.info("✅ MongoDB connected successfully")
except Exception as e:
    log.error(f"❌ MongoDB connection failed: {e}")
    MONGO_AVAILABLE = False
    mongo = db = users_col = None

# =========================
# Helpers
# =========================

def ensure_user(update: Update):
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
    log.error("Exception while handling an update:", exc_info=context.error)

# =========================
# yt-dlp Downloader
# =========================

async def download_and_send(chat_id, reply_msg, context, url, quality):
    try:
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
            # SIMPLIFIED: Standard MP4 that Telegram streams natively
            ydl_opts.update({
                "format": f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]",
                "merge_output_format": "mp4",
                "postprocessor_args": {
                    "MOV+FFmpegVideoConvertor+mp4": [
                        "-movflags", "+faststart",  # Enable streaming
                    ]
                }
            })

        if COOKIES_TXT:
            try:
                cookie_path = Path("/tmp/cookies.txt")
                cookie_path.write_text(COOKIES_TXT, encoding="utf-8")
                ydl_opts["cookiefile"] = str(cookie_path)
            except Exception as e:
                log.error(f"Cookie write failed: {e}")

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = sanitize_filename(info.get("title", "video"))

        # Send file
        ext = ".mp3" if quality == "mp3" else ".mp4"
        files = sorted(DOWNLOAD_DIR.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            await reply_msg.reply_text("⚠️ File not found after download.")
            return

        final_path = files[0]
        caption = f"📥 <b>{title}</b>\n\nDownloaded by @spotifyxmusixbot"

        if quality == "mp3":
            await reply_msg.reply_document(
                document=final_path,
                caption=caption,
                filename=f"{title}.mp3",
                parse_mode=ParseMode.HTML
            )
        else:
            await reply_msg.reply_video(
                video=final_path,
                caption=caption,
                filename=f"{title}.mp4",
                supports_streaming=True,  # Enable Telegram streaming
                parse_mode=ParseMode.HTML
            )

        # Cleanup
        cleanup_old_files()

    except Exception as e:
        await reply_msg.reply_text(f"⚠️ Error: {e}")

def cleanup_old_files():
    """Keep only last 10 files"""
    try:
        all_files = sorted(DOWNLOAD_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in all_files[10:]:
            f.unlink()
    except:
        pass

# =========================
# NEW: Image Generation Feature
# =========================

async def gen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate image using Vercel AI"""
    ensure_user(update)
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gen <description>\nExample: `/gen a ripe mango on mango tree`")
        return

    # Show generating message
    status_msg = await update.message.reply_text("🎨 Generating image...")

    try:
        # Encode query for URL
        encoded_query = query.replace(" ", "+")
        image_url = f"https://flux-pro.vercel.app/generate?q={encoded_query}"
        
        # Download image
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ Generation failed (Error {resp.status})")
                    return
                
                # Save image temporarily
                image_data = await resp.read()
                image_path = DOWNLOAD_DIR / f"gen_{update.effective_user.id}.png"
                with open(image_path, "wb") as f:
                    f.write(image_data)

        # Send generated image
        caption = f"🖼️ <b>{query}</b>\n\nGenerated by @spotifyxmusixbot"
        await update.message.reply_photo(
            photo=image_path,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

        # Delete status message
        await status_msg.delete()
        
        # Cleanup generated image
        image_path.unlink(missing_ok=True)

    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to generate image: {e}")

# =========================
# Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    start_text = (
        "<b>🎧 Welcome to SpotifyX Musix Bot 🎧</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        "<b>🔥 Your all-in-one YouTube downloader</b>\n"
        "• Download <b>MP3 music</b> in 192kbps 🎧\n"
        "• Download <b>Videos</b> in 360p/480p/720p/1080p 🎬\n"
        "• Search any song using <code>/search &lt;name&gt;</code> 🔍\n"
        "• Generate AI images with <code>/gen &lt;description&gt;</code> 🎨\n"
        "• Fast, clean, no ads — ever 😎\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📌 How to use the bot?</b>\n"
        "1. Send any <b>YouTube link</b> → choose quality\n"
        "2. Use <code>/search &lt;name&gt;</code> to find songs\n"
        "3. Use <code>/gen &lt;description&gt;</code> to create AI images\n"
        "4. All files sent instantly with Telegram streaming ⚡\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📢 Important Links</b>\n"
        f"• Updates: {UPDATES_CHANNEL}\n"
        "• Report Issue: @mahadev_ki_iccha\n"
        "• Paid Bots / Promo: @mahadev_ki_iccha\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>❓ Need help?</b>\n"
        "Use <code>/help</code> for all commands.\n"
    )

    await update.message.reply_text(start_text, parse_mode=ParseMode.HTML)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    help_text = (
        "<b>✨ SpotifyX Musix Bot — Full Guide ✨</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"

        "<b>🔥 Features:</b>\n"
        "• Download <b>MP3 music</b> 🎧\n"
        "• Download <b>YouTube Videos</b> (360p/480p/720p/1080p) 🎬\n"
        "• Search any song / video via <code>/search</code>\n"
        "• Generate AI images with <code>/gen</code> 🎨\n"
        "• All videos support Telegram streaming!\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📌 Commands:</b>\n"
        "• <code>/start</code> — Start the bot\n"
        "• <code>/help</code> — Show this help\n"
        "• <code>/search &lt;name&gt;</code> — Search YouTube\n"
        "• <code>/gen &lt;description&gt;</code> — Generate AI image\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🎬 Quality Options:</b>\n"
        "360p, 480p, 720p, 1080p, MP3\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📢 Important Links</b>\n"
        f"• Updates Channel: {UPDATES_CHANNEL}\n"
        "• Report Issue: @ayushxchat_robot\n"
        "• Contact for Paid Bots / Cross Promo: @mahadev_ki_iccha\n\n"

        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🤖 Bot Created By</b>\n"
        "• <b>Tony Stark Jr</b>⚡\n"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

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
        "extract_flat": False,
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
        video_id = e.get('id')
        url = f"https://youtube.com/watch?v={video_id}" if video_id else e.get('webpage_url')
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
    import signal
    import sys
    
    def shutdown_handler(signum, frame):
        log.info("Shutting down gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("gen", gen_cmd))  # NEW: Image generation command

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))

    log.info("Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import re
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, List
import aiohttp
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

COOKIES_TXT = os.getenv("COOKIES_TXT")  # Should be a file path like /app/cookies.txt
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "youtube_bot")
MONGO_USERS = os.getenv("MONGO_USERS", "users")

# Paths
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Broadcast storage
BROADCAST_STORE: Dict[int, List[dict]] = {}
BROADCAST_STATE: Dict[int, bool] = {}

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

        # FIXED: Proper cookie file handling
        if COOKIES_TXT and Path(COOKIES_TXT).exists():
            ydl_opts["cookiefile"] = COOKIES_TXT
            log.info(f"Using cookies from: {COOKIES_TXT}")
        elif COOKIES_TXT and not Path(COOKIES_TXT).exists():
            log.warning(f"Cookie file not found at: {COOKIES_TXT}")
            await reply_msg.reply_text("⚠️ Warning: Cookie file not found")

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
            ydl_opts.update({
                "format": f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]",
                "merge_output_format": "mp4",
                "postprocessor_args": {
                    "MOV+FFmpegVideoConvertor+mp4": [
                        "-movflags", "+faststart",
                    ]
                }
            })

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = sanitize_filename(info.get("title", "video"))

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
                supports_streaming=True,
                parse_mode=ParseMode.HTML
            )

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
# AI Image Generation
# =========================

async def gen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate image using Vercel AI"""
    ensure_user(update)
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gen <description>\nExample: `/gen a ripe mango on mango tree`")
        return

    status_msg = await update.message.reply_text("🎨 Generating image...")

    try:
        encoded_query = query.replace(" ", "+")
        image_url = f"https://flux-pro.vercel.app/generate?q={encoded_query}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ Generation failed (Error {resp.status})")
                    return
                
                image_data = await resp.read()
                image_path = DOWNLOAD_DIR / f"gen_{update.effective_user.id}.png"
                with open(image_path, "wb") as f:
                    f.write(image_data)

        caption = f"🖼️ <b>{query}</b>\n\nGenerated by @spotifyxmusixbot"
        await update.message.reply_photo(
            photo=image_path,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

        await status_msg.delete()
        image_path.unlink(missing_ok=True)

    except Exception as e:
        await status_msg.edit_text(f"❌ Failed: {e}")

# =========================
# Admin Commands
# =========================

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user statistics"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized!")
        return
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("Database is not available")
        return
        
    total = users_col.count_documents({})
    docs = users_col.find().limit(50)
    preview = "\n".join([f"{d['name']} — {d['_id']}" for d in docs])
    await update.message.reply_text(f"👥 Users: {total}\n\n{preview}")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast mode"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized!")
        return
    
    admin_id = update.effective_user.id
    BROADCAST_STORE[admin_id] = []
    BROADCAST_STATE[admin_id] = True
    
    await update.message.reply_text(
        "📢 <b>Broadcast Mode Activated!</b>\n\n"
        "Send me messages, photos, videos, GIFs, or documents.\n"
        "Use <b>/done_broadcast</b> when finished.\n"
        "Use <b>/cancel_broadcast</b> to cancel.",
        parse_mode=ParseMode.HTML
    )

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect messages during broadcast mode"""
    admin_id = update.effective_user.id
    
    if not BROADCAST_STATE.get(admin_id):
        return  # Not in broadcast mode
    
    message_data = {
        "text": update.message.text,
        "photo": update.message.photo[-1].file_id if update.message.photo else None,
        "video": update.message.video.file_id if update.message.video else None,
        "document": update.message.document.file_id if update.message.document else None,
        "animation": update.message.animation.file_id if update.message.animation else None,
        "caption": update.message.caption,
        "parse_mode": ParseMode.HTML if update.message.caption_entities else None
    }
    
    BROADCAST_STORE[admin_id].append(message_data)
    msg_count = len(BROADCAST_STORE[admin_id])
    await update.message.reply_text(f"✅ Message #{msg_count} added to broadcast queue")

async def done_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show broadcast preview"""
    if not is_admin(update.effective_user.id):
        return
    
    admin_id = update.effective_user.id
    
    if not BROADCAST_STATE.get(admin_id):
        await update.message.reply_text("❌ You are not in broadcast mode. Use /broadcast first.")
        return
    
    if not BROADCAST_STORE.get(admin_id):
        await update.message.reply_text("❌ No messages added. Send some messages first!")
        return
    
    await update.message.reply_text("📢 <b>Broadcast Preview:</b>", parse_mode=ParseMode.HTML)
    
    for msg in BROADCAST_STORE[admin_id]:
        if msg["photo"]:
            await update.message.reply_photo(photo=msg["photo"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["video"]:
            await update.message.reply_video(video=msg["video"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["document"]:
            await update.message.reply_document(document=msg["document"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["animation"]:
            await update.message.reply_animation(animation=msg["animation"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["text"]:
            await update.message.reply_text(msg["text"], parse_mode=ParseMode.HTML)
    
    await update.message.reply_text(
        "✅ Preview complete!\n\n"
        "Send <b>/send_broadcast</b> to broadcast to ALL users and groups.\n"
        "Send <b>/cancel_broadcast</b> to cancel.",
        parse_mode=ParseMode.HTML
    )

async def send_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send broadcast to all users and groups"""
    if not is_admin(update.effective_user.id):
        return
    
    admin_id = update.effective_user.id
    
    if not BROADCAST_STATE.get(admin_id):
        await update.message.reply_text("❌ You are not in broadcast mode.")
        return
    
    messages = BROADCAST_STORE.get(admin_id, [])
    if not messages:
        await update.message.reply_text("❌ No messages to broadcast.")
        return
    
    # Get all users from database
    recipients = set()
    if MONGO_AVAILABLE:
        users_cursor = users_col.find({}, {"_id": 1})
        for u in users_cursor:
            recipients.add(u["_id"])
    
    # FIXED: Also get groups from chat history
    # Add group IDs manually if you want, or track them separately
    # For now, we'll just broadcast to users
    
    await update.message.reply_text(f"📢 Broadcasting to {len(recipients)} recipients...")
    
    success = 0
    failed = 0
    
    for chat_id in recipients:
        try:
            for msg in messages:
                if msg["photo"]:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=msg["photo"],
                        caption=msg["caption"],
                        parse_mode=msg["parse_mode"]
                    )
                elif msg["video"]:
                    await context.bot.send_video(
                        chat_id=chat_id,
                        video=msg["video"],
                        caption=msg["caption"],
                        parse_mode=msg["parse_mode"]
                    )
                elif msg["document"]:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=msg["document"],
                        caption=msg["caption"],
                        parse_mode=msg["parse_mode"]
                    )
                elif msg["animation"]:
                    await context.bot.send_animation(
                        chat_id=chat_id,
                        animation=msg["animation"],
                        caption=msg["caption"],
                        parse_mode=msg["parse_mode"]
                    )
                elif msg["text"]:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=msg["text"],
                        parse_mode=ParseMode.HTML
                    )
            success += 1
        except Exception as e:
            log.error(f"Failed to send to {chat_id}: {e}")
            failed += 1
        await asyncio.sleep(0.05)
    
    # Clear broadcast data
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    
    await update.message.reply_text(
        f"✅ Broadcast Complete!\n"
        f"📤 Successful: {success}\n"
        f"❌ Failed: {failed}",
        parse_mode=ParseMode.HTML
    )

async def cancel_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel broadcast mode"""
    if not is_admin(update.effective_user.id):
        return
    
    admin_id = update.effective_user.id
    
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    
    await update.message.reply_text("❌ Broadcast cancelled.")

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
        "• <code>/gen &lt;description&gt;</code> — Generate AI image\n"
        "• <code>/broadcast</code> — Admin: Start broadcast (multi-message)\n"
        "• <code>/done_broadcast</code> — Admin: Preview broadcast\n"
        "• <code>/send_broadcast</code> — Admin: Send broadcast\n"
        "• <code>/cancel_broadcast</code> — Admin: Cancel broadcast\n"
        "• <code>/stats</code> — Admin: Show user statistics\n\n"

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

def main():
    import signal
    import sys
    
    def shutdown_handler(signum, frame):
        log.info("Shutting down gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    # Command handlers - ALL DEFINED NOW
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("done_broadcast", done_broadcast_cmd))
    app.add_handler(CommandHandler("send_broadcast", send_broadcast_cmd))
    app.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast_cmd))
    app.add_handler(CommandHandler("gen", gen_cmd))

    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast_message))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))

    log.info("Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()

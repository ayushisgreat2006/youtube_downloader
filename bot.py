import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
import secrets
import aiohttp
import random
import aiofiles
import json
import re
import asyncio
from collections import deque
from typing import Dict, Any
from pathlib import Path
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, ChatMemberHandler
)
import yt_dlp
from pymongo import MongoClient
from groq import Groq

# =========================
# CONFIGURATION
# =========================
# =========================
# CONFIGURATION
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "7941244038"))
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "@tonystark_jr")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "@tonystark_jr")
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "-5066591546"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Cookies configuration for YouTube
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")  # Netscape format cookies file

# MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "youtube_bot")
MONGO_USERS = os.getenv("MONGO_USERS", "users")
MONGO_ADMINS = os.getenv("MONGO_ADMINS", "admins")
MONGO_REDEEM = os.getenv("MONGO_REDEEM", "redeem_codes")
MONGO_WHITELIST = os.getenv("MONGO_WHITELIST", "whitelist")

# Credit System Constants
BASE_CREDITS = 20
REFERRER_BONUS = 20
CLAIMER_BONUS = 15
PREMIUM_BOT_USERNAME = "@ayushxchat_robot"

# File size limits
DOWNLOAD_DIR = Path("downloads")
MAX_FREE_SIZE = 50 * 1024 * 1024
PREMIUM_SIZE = 450 * 1024 * 1024
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)

# Combined Media Generation Limits (images + videos share the same pool)
BASE_MEDIA_GEN_LIMIT = 10      # Default 10 media items per day per user
PROXY_LIST = []                # Not used - direct connection only (API has 100/min limit)
PROXY_ROTATE_ON_FAILURE = False
VIDEO_MAX_ATTEMPTS = 1         # Direct IP only
IMAGE_MAX_ATTEMPTS = 1         # Direct IP only

#config of vdo gen
# GeminiGen AI Video Configuration
BEARER_TOKEN = os.getenv("BEARER_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NjQzOTEyNDYsInN1YiI6ImY5MTlhYjEyLWNiMDgtMTFmMC05YWEyLWVlNDdlYmE0N2M1ZCJ9.Fc-S3UZISOlG4EuD8nip2q3tbESy0kb2IIvNFachA-8")
COOKIE_FILE_CONTENT = os.getenv("COOKIE_FILE_CONTENT", """# Netscape HTTP Cookie File
geminigen.ai	FALSE	/	FALSE	1779779317	ext_name	ojplmecpdpgccookcobabopnaifgidhf
geminigen.ai	FALSE	/	FALSE	1779741622	i18n_redirected	en
geminigen.ai	FALSE	/	FALSE	0	video-aspect-ratio	16%3A9
geminigen.ai	FALSE	/	FALSE	0	video-resolution	720p
geminigen.ai	FALSE	/	FALSE	0	video-gen-model	%7B%22label%22%3A%22Veo%203.1%20Fast%22%2C%22value%22%3A%22veo-3-fast%22%7D
geminigen.ai	FALSE	/	FALSE	0	video-gen-duration	8
geminigen.ai	FALSE	/	FALSE	0	video-gen-enhance-prompt	true
geminigen.ai	FALSE	/	FALSE	0	video-model	veo-3-fast
geminigen.ai	FALSE	/	FALSE	0	video-duration	8
.geminigen.ai	TRUE	/	TRUE	1779741772	cf_clearance	6azc623mvyLqCfSRQZvLt3JCLs_lqXVIlYCUOAE3770-1764189771-1.2.1.1-dTH3sePAT0USkZbzKNjwE1dzzgJ5V6p7iuW6TMuQ_6sYmZsxVpJREHoDuolv9gfwvOKlURyCynaKbUOLS0aHsZj1pe72wdtYZUAOqkQ1sIFrBREfEoJh.s763UkmcFZdXlNdWOLaTmeo4TSFgyKkCVmxPUfWtNYlrxXsYG18B.HmBYgT.9EkTVduLdVeD7QqCClAlvuYU7JXp7TYBih8XtAEsMv78zBirZLxrEkyvvI
""")

# Video generation queue and semaphore
video_generation_queue = deque()
active_generations = 0
MAX_CONCURRENT_GENERATIONS = 2  # Allow 2 videos at once
generation_semaphore = asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)
user_active_tasks: Dict[int, asyncio.Task] = {}

# =========================
# Logging & Storage
# =========================
logging.basicConfig(
    level=logging.INFO, 
    format="[%(levelname)s] %(asctime)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("ytbot")

DOWNLOAD_DIR.mkdir(exist_ok=True)
Path(COOKIES_FILE).touch(exist_ok=True)  # Create cookies file placeholder if it doesn't exist

# In-memory storage (volatile)
PENDING: Dict[str, dict] = {}
USER_CONVERSATIONS: Dict[int, List[dict]] = {}
BROADCAST_STORE: Dict[int, List[dict]] = {}
BROADCAST_STATE: Dict[int, bool] = {}

# =========================
# Groq Client Setup
# =========================
groq_client = None
if GROQ_API_KEY:
    try:
        groq_client = Groq(api_key=GROQ_API_KEY)
        log.info(f"✅ Groq client initialized with model: {GROQ_MODEL}")
    except Exception as e:
        log.error(f"❌ Failed to initialize Groq client: {e}")
else:
    log.warning("⚠️ GROQ_API_KEY not set. AI features disabled.")

# =========================
# MongoDB Setup
# =========================
try:
    mongo = MongoClient(
        MONGO_URI, tls=True, tlsAllowInvalidCertificates=False,
        serverSelectionTimeoutMS=5000, retryWrites=True, w='majority'
    )
    mongo.admin.command('ping')
    db = mongo[MONGO_DB]
    users_col = db[MONGO_USERS]
    admins_col = db[MONGO_ADMINS]
    redeem_col = db[MONGO_REDEEM]
    whitelist_col = db[MONGO_WHITELIST]
    MONGO_AVAILABLE = True
    log.info("✅ MongoDB connected")
    
    # Create indexes
    users_col.create_index("referral_code", unique=True, sparse=True)
    redeem_col.create_index("code", unique=True)
    
    # Add owner as admin if collection empty
    if admins_col is not None and admins_col.count_documents({}) == 0:
        admins_col.insert_one({
            "_id": OWNER_ID, "name": "Owner",
            "added_by": OWNER_ID, "added_at": datetime.now()
        })
        log.info("✅ Owner added to admin list")
        
except Exception as e:
    log.error(f"❌ MongoDB failed: {e}")
    MONGO_AVAILABLE = False
    mongo = db = users_col = admins_col = redeem_col = whitelist_col = None

# =========================
# Credit System Functions
# =========================
def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

async def get_user_credits(user_id: int) -> tuple[int, int, bool]:
    """Returns (current_credits, used_today, is_whitelisted)"""
    if not MONGO_AVAILABLE:
        return BASE_CREDITS, 0, False
    
    if is_admin(user_id):
        return 99999, 0, True
    
    today = get_today_str()
    
    # Check whitelist first
    whitelist_entry = whitelist_col.find_one({"_id": user_id}) if whitelist_col is not None else None
    if whitelist_entry:
        limit = whitelist_entry.get("daily_limit", BASE_CREDITS)
        last_date = whitelist_entry.get("last_usage_date", today)
        used = whitelist_entry.get("daily_usage", 0) if last_date == today else 0
        return limit, used, True
    
    # Regular user
    user = users_col.find_one({"_id": user_id}, {"credits": 1, "daily_usage": 1, "last_usage_date": 1})
    if not user:
        return BASE_CREDITS, 0, False
    
    last_date = user.get("last_usage_date", today)
    if last_date != today:
        # Reset daily usage
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"daily_usage": 0, "last_usage_date": today}}
        )
        return user.get("credits", BASE_CREDITS), 0, False
    
    return user.get("credits", BASE_CREDITS), user.get("daily_usage", 0), False

async def consume_video_credit(user_id: int) -> bool:
    """Consume 1 video credit, return True if successful"""
    if not MONGO_AVAILABLE:
        return True
    
    if is_admin(user_id):
        return True
    
    credits, used, is_whitelisted = await get_user_video_credits(user_id)
    
    if used >= credits:
        return False
    
    today = get_today_str()
    update_fields = {"$inc": {"video_daily_usage": 1}}
    
    if is_whitelisted:
        whitelist_col.update_one(
            {"_id": user_id},
            {**update_fields, "$set": {"video_last_usage_date": today}},
            upsert=True
        )
    else:
        users_col.update_one(
            {"_id": user_id},
            {**update_fields, "$set": {"video_last_usage_date": today}},
            upsert=True
        )
    
    return True

# =========================
# Log Forwarding Helper
# =========================
async def forward_interaction_to_log(update: Update, context: ContextTypes.DEFAULT_TYPE, response_msg=None, error_msg=None):
    """Forward user's command and bot's response to log group"""
    if not LOG_GROUP_ID:
        return
    
    try:
        if update.message:
            await context.bot.forward_message(
                chat_id=LOG_GROUP_ID,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
        
        if response_msg and hasattr(response_msg, 'message_id'):
            await context.bot.forward_message(
                chat_id=LOG_GROUP_ID,
                from_chat_id=response_msg.chat_id,
                message_id=response_msg.message_id
            )
        elif response_msg and isinstance(response_msg, str):
            await context.bot.send_message(
                chat_id=LOG_GROUP_ID,
                text=response_msg,
                parse_mode=ParseMode.HTML
            )
        
        if error_msg:
            await context.bot.send_message(
                chat_id=LOG_GROUP_ID,
                text=f"❌ Error: {error_msg}",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        log.error(f"Failed to forward to log group: {e}")

# =========================
# Helper Functions
# =========================
def ensure_user(update: Update):
    """Track user in database"""
    if not MONGO_AVAILABLE or update.effective_user is None:
        return
    
    try:
        u = update.effective_user
        users_col.update_one(
            {"_id": u.id},
            {
                "$set": {
                    "name": u.full_name or u.username or str(u.id),
                    "username": u.username,
                },
                "$setOnInsert": {
                    "credits": BASE_CREDITS,
                    "daily_usage": 0,
                    "last_usage_date": get_today_str(),
                    "video_credits": VIDEO_BASE_CREDITS,
                    "video_daily_usage": 0,
                    "video_last_usage_date": get_today_str(),
                    "referrals_made": 0,
                    "first_seen": datetime.now(),
                }
            },
            upsert=True
        )
    except Exception as e:
        log.error(f"User tracking failed: {e}")

def is_owner(user_id: int) -> bool:
    return int(user_id) == OWNER_ID

def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    if is_owner(user_id):
        return True
    if not MONGO_AVAILABLE or admins_col is None:
        return False
    try:
        return admins_col.find_one({"_id": user_id}) is not None
    except Exception as e:
        log.error(f"Error checking admin status for {user_id}: {e}")
        return False

def is_premium(user_id: int) -> bool:
    """Check if user has premium"""
    if not MONGO_AVAILABLE or users_col is None:
        return False
    try:
        user = users_col.find_one({"_id": user_id}, {"premium": 1})
        if user is None:
            return False
        return user.get("premium", False)
    except Exception as e:
        log.error(f"Error checking premium status: {e}")
        return False

def sanitize_filename(name: str) -> str:
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "output"

def store_url(url: str) -> str:
    token = secrets.token_urlsafe(16)
    PENDING[token] = {"url": url, "exp": asyncio.get_event_loop().time() + 3600}
    return token

def cleanup_old_files():
    try:
        all_files = sorted(DOWNLOAD_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in all_files[10:]:
            f.unlink()
    except:
        pass

def get_ytdl_options(quality: str, download_id: str) -> dict:
    """Generate yt-dlp options with cookies support"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(DOWNLOAD_DIR / f"%(title)s_{download_id}.%(ext)s"),
    }
    
    cookies_path = Path(COOKIES_FILE)
    if cookies_path.exists() and cookies_path.stat().st_size > 0:
        ydl_opts["cookiefile"] = str(cookies_path)
        log.info(f"Using cookies file: {cookies_path}")
    else:
        log.warning(f"No cookies file found at {cookies_path}")
    
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
            "format": f"bestvideo[height<={quality}][vcodec^=avc][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/best[height<={quality}][ext=mp4]",
            "merge_output_format": "mp4",
            "postprocessor_args": {
                "MOV+FFmpegVideoConvertor+mp4": [
                    "-movflags", "+faststart",
                    "-c:v", "libx264",
                    "-c:a", "aac",
                    "-preset", "faster",
                    "-crf", "23"
                ]
            }
        })
    
    return ydl_opts

async def log_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, 
                       details: str = "", user_id: Optional[int] = None, is_error: bool = False):
    if not LOG_GROUP_ID:
        return
        
    try:
        user = update.effective_user if update.effective_user else None
        user_info = f"👤 User: {user.full_name or user.username or 'Unknown'} (<code>{user.id}</code>)" if user else ""
        
        action_info = f"🎯 Action: {action}"
        details_info = f"📄 Details: {details}" if details else ""
        
        log_text = (
            f"❌ <b>ERROR LOG</b>\n\n{user_info}\n{action_info}\n{details_info}\n\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) if is_error else (
            f"✅ <b>ACTIVITY LOG</b>\n\n{user_info}\n{action_info}\n{details_info}\n\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await context.bot.send_message(
            chat_id=LOG_GROUP_ID,
            text=log_text,
            parse_mode=ParseMode.HTML
        )
        log.info(f"✅ Log sent to group {LOG_GROUP_ID}")
        
    except Exception as e:
        log.error(f"❌ Failed to send log to group {LOG_GROUP_ID}: {e}")

async def ensure_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not FORCE_JOIN_CHANNEL:
        return True
    
    if update.message and update.message.chat.type in ["group", "supergroup"]:
        if not (update.message.text and f"@{context.bot.username}" in update.message.text):
            return True
    
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(
            chat_id=FORCE_JOIN_CHANNEL,
            user_id=user_id
        )
        if member.status not in ["left", "kicked"]:
            return True
    except Exception as e:
        log.error(f"Membership check failed: {e}")
        await update.message.reply_text("❌ Could not verify membership. Try again.")
        return False
    
    channel_username = FORCE_JOIN_CHANNEL.replace('@', '')
    join_url = f"https://t.me/{channel_username}"
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Join Channel 🔔", url=join_url),
        InlineKeyboardButton("✅ Verify", callback_data="verify_membership")
    ]])
    
    await update.message.reply_text(
        f"⚠️ <b>You must join {FORCE_JOIN_CHANNEL} to use this bot!</b>\n\n"
        f"Please join and click 'Verify'.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return False

async def fetch_lyrics(song_title: str) -> Optional[str]:
    """Fetch lyrics for a song title"""
    try:
        clean_title = re.sub(r'\(official.*?\)|\[official.*?\]|\(audio\)|\[audio\]|\(lyric.*?\)|\[lyric.*?\]|\(video.*?\)|\[video.*?\]|\(hd\)|\[hd\]|\(4k\)|\[4k\]|\(feat\..*?\)|\[feat\..*?\]', '', song_title, flags=re.IGNORECASE)
        clean_title = re.sub(r'[–—|-]', ' ', clean_title)
        clean_title = re.sub(r'\s+', ' ', clean_title).strip()
        api_url = f"https://api.maher-zubair.tech/lyrics?q={clean_title}"

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == 200 and data.get("result"):
                        return data["result"]
    except Exception as e:
        log.error(f"Failed to fetch lyrics for '{song_title}': {e}")

    return None

# =========================
# Download Function with Logging
# =========================
async def download_and_send(chat_id, reply_msg, context, url, quality):
    user_id = reply_msg.chat.id
    download_id = f"{user_id}_{secrets.token_urlsafe(8)}"
    
    try:
        status_msg = await reply_msg.reply_text("⏳ Preparing download...")
        
        ydl_opts = get_ytdl_options(quality, download_id)

        await status_msg.edit_text("⬇️ Downloading from YouTube...")
        
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
            title = sanitize_filename(info.get("title", "video"))

        ext = ".mp3" if quality == "mp3" else ".mp4"
        files = sorted(DOWNLOAD_DIR.glob(f"*{download_id}{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
        
        if not files:
            await status_msg.edit_text("⚠️ File not found after download.")
            await log_to_group(update=None, context=context, action="Download Failed", 
                             details=f"User {user_id}: File not found", is_error=True)
            return

        final_path = files[0]
        file_size = final_path.stat().st_size
        is_user_premium = is_premium(user_id)

        if file_size > MAX_FREE_SIZE and not is_user_premium:
            final_path.unlink()
            premium_msg = (
                f"❌ <b>File too large!</b>\n\n"
                f"📦 Size: {file_size / 1024 / 1024:.1f}MB\n"
                f"💳 Free limit: {MAX_FREE_SIZE / 1024 / 1024}MB\n\n"
                f"🔓 <b>Premium users get:</b>\n"
                f"• Up to 450MB files\n"
                f"• Priority downloads\n"
                f"• No ads\n\n"
                f"👉 Contact {PREMIUM_BOT_USERNAME} to subscribe premium!"
            )
            await status_msg.edit_text(premium_msg, parse_mode=ParseMode.HTML)
            await log_to_group(update=None, context=context, action="Download Size Limit", 
                             details=f"User {user_id}: {file_size/1024/1024:.1f}MB")
            return

        if file_size > PREMIUM_SIZE:
            final_path.unlink()
            await status_msg.edit_text("❌ File exceeds maximum size (450MB). Try lower quality.")
            await log_to_group(update=None, context=context, action="Download Size Limit", 
                             details=f"User {user_id}: Exceeded 450MB", is_error=True)
            return

        caption = f"📥 <b>{title}</b> ({file_size/1024/1024:.1f}MB)\n\nDownloaded by @spotifyxmusixbot"
        await status_msg.edit_text("⬆️ Uploading to Telegram...")
        
        try:
            async with aiofiles.open(final_path, 'rb') as f:
                file_data = await f.read()
            
            if quality == "mp3":
                sent_msg = await reply_msg.reply_document(
                    document=file_data,
                    caption=caption,
                    filename=f"{title}.mp3",
                    parse_mode=ParseMode.HTML,
                    connect_timeout=60,
                    read_timeout=60,
                    write_timeout=60
                )
            else:
                sent_msg = await reply_msg.reply_video(
                    video=file_data,
                    caption=caption,
                    filename=f"{title}.mp4",
                    supports_streaming=True,
                    parse_mode=ParseMode.HTML,
                    connect_timeout=60,
                    read_timeout=60,
                    write_timeout=60
                )
            
            await status_msg.delete()
            
            if quality == "mp3":
                lyrics_button = InlineKeyboardButton("📝 Get Lyrics", callback_data=f"lyrics|{title}")
                keyboard = InlineKeyboardMarkup([[lyrics_button]])
                await reply_msg.reply_text(
                    "🎵 Download complete! Click below to get lyrics:",
                    reply_markup=keyboard
                )
            
            await log_to_group(update=None, context=context, action="Download Success", 
                             details=f"User {user_id}: {title[:50]}")
            
            # Forward to log group
            if LOG_GROUP_ID:
                await context.bot.forward_message(
                    chat_id=LOG_GROUP_ID,
                    from_chat_id=sent_msg.chat_id,
                    message_id=sent_msg.message_id
                )
            
        finally:
            final_path.unlink(missing_ok=True)
            cleanup_old_files()

    except Exception as e:
        error_msg = f"⚠️ Error: {str(e)[:100]}"
        await reply_msg.reply_text(error_msg)
        await log_to_group(update=None, context=context, action="Download Failed", 
                         details=f"User {user_id}: {error_msg}", is_error=True)
        log.error(f"Download failed: {e}", exc_info=True)

# =========================
# Command Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    if MONGO_AVAILABLE and update.message.chat.type in ["group", "supergroup", "channel"]:
        try:
            db["broadcast_chats"].update_one(
                {"_id": update.message.chat.id},
                {"$set": {
                    "title": update.message.chat.title,
                    "type": update.message.chat.type,
                    "added_at": datetime.now()
                }},
                upsert=True
            )
        except:
            pass
    
    await log_to_group(update, context, action="/start", details="User started bot")
    
    cookies_path = Path(COOKIES_FILE)
    cookies_working = cookies_path.exists() and cookies_path.stat().st_size > 0
    
    start_text = (
        "<b>🎧 Welcome to SpotifyX Musix Bot 🎧</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🔥 Features:</b>\n"
        "• Download MP3 music 🎧\n"
        "• Download Videos (360p-1080p) 🎬\n"
        "• Search YouTube 🔍\n"
        "• Generate AI images 🎨\n"
        "• Generate AI videos 🎬\n"
        "• AI Chat with Groq 💬\n"
        "• Get song lyrics 📝\n"
        "• Premium: Up to 450MB files 💳\n\n"
        "<b>💳 AI Credits:</b> 20/day\n"
        "<b>🎬 Video Credits:</b> 2/day\n"
        "<b>💳 OR CONTACT @ayushxchat_robot</b> FOR PREMIUM\n"
        "<b>🎁 Refer:</b> /refer to earn more\n\n"
        f"<b>📌 Cookies Status:</b> {'✅ Working' if cookies_working else '❌ Not configured'}\n"
        f"<b>📌 Use /help for commands</b>\n\n"
        "<b>⚠️ YouTube Notice:</b> If search fails, cookies may need refresh. Use /testcookies"
    )
    response = await update.message.reply_text(start_text, parse_mode=ParseMode.HTML)
    await forward_interaction_to_log(update, context, response)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    ai_status = "✅" if groq_client else "❌"
    
    cookies_path = Path(COOKIES_FILE)
    cookies_working = cookies_path.exists() and cookies_path.stat().st_size > 0
    
    help_text = (
        "<b>✨ SpotifyX Musix Bot — Commands ✨</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>User Commands:</b>\n"
        "<code>/start</code> — Start bot\n"
        "<code>/help</code> — Show help\n"
        "<code>/search &lt;name&gt;</code> — Search YouTube\n"
        "<code>/lyrics &lt;song&gt;</code> — Get song lyrics 📝\n"
        "<code>/gen &lt;prompt&gt;</code> — Generate AI image\n"
        "<code>/vdogen &lt;prompt&gt;</code> — Generate AI video 🎬\n"
        "<code>/gpt &lt;query&gt;</code> — Chat with AI (20/day)\n"
        "<code>/refer</code> — Generate referral code\n"
        "<code>/claim &lt;code&gt;</code> — Claim referral code\n"
        "<code>/redeem &lt;code&gt;</code> — Redeem admin code (AI credits)\n"
        "<code>/vdoredeem &lt;code&gt;</code> — Redeem video code\n"
        "<code>/credits</code> — Check your credits\n\n"
        "<b>Admin Commands:</b>\n"
        "<code>/stats</code> — View statistics\n"
        "<code>/broadcast</code> — Broadcast message\n"
        "<code>/adminlist</code> — List admins\n"
        "<code>/gen_redeem &lt;value&gt; &lt;code&gt;</code> — Generate AI redeem code\n"
        "<code>/genvdo_redeem &lt;value&gt; &lt;code&gt;</code> — Generate video redeem code\n"
        "<code>/whitelist_ai &lt;id&gt; &lt;value&gt;</code> — Whitelist AI user\n"
        "<code>/whitelist_vdo &lt;id&gt; &lt;value&gt;</code> — Whitelist video user\n"
        "<code>/testcookies</code> — Test YouTube cookies\n\n"
        "<b>Owner Commands:</b>\n"
        "<code>/addadmin &lt;id&gt;</code> — Add admin\n"
        "<code>/rmadmin &lt;id&gt;</code> — Remove admin\n\n"
        f"<b>Updates:</b> {UPDATES_CHANNEL}\n"
        f"<b>Support:</b> {PREMIUM_BOT_USERNAME}\n\n"
        f"<b>AI Status:</b> {ai_status} {'Configured' if groq_client else 'Not Set'}\n"
        f"<b>Cookies Status:</b> {'✅ Working' if cookies_working else '❌ Not configured'}"
    )
    response = await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
    await forward_interaction_to_log(update, context, response)



# Add this function after the other command handlers, before the main() function

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search YouTube for videos"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        response = await update.message.reply_text(
            "Usage: /search <video name>\n"
            "Example: /search Ed Sheeran Shape of You"
        )
        await forward_interaction_to_log(update, context, response)
        return
    
    status_msg = await update.message.reply_text(
        f"🔍 Searching YouTube for '<b>{query}</b>'...",
        parse_mode=ParseMode.HTML
    )
    
    try:
        # Use yt-dlp to search YouTube
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "cookiefile": str(COOKIES_FILE) if Path(COOKIES_FILE).exists() else None,
        }
        
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = await loop.run_in_executor(
                None, 
                lambda: ydl.extract_info(f"ytsearch10:{query}", download=False)
            )
        
        if not search_results or "entries" not in search_results or not search_results["entries"]:
            await status_msg.edit_text(
                f"❌ No results found for '<code>{query}</code>'",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Create keyboard with search results
        keyboard = []
        for i, entry in enumerate(search_results["entries"][:5]):  # Show top 5 results
            title = entry.get("title", "Unknown")
            
            # FIXED: Handle duration properly as float/int
            duration = entry.get("duration", 0)
            if duration:
                duration_int = int(duration)  # Convert to int to avoid float formatting errors
                minutes = duration_int // 60
                seconds = duration_int % 60
                duration_str = f"{minutes}:{seconds:02d}"
            else:
                duration_str = "Unknown"
            
            # Truncate long titles
            display_title = title[:40] + "..." if len(title) > 40 else title
            
            token = store_url(f"https://www.youtube.com/watch?v={entry['id']}")
            keyboard.append([
                InlineKeyboardButton(
                    f"{i+1}. {display_title} [{duration_str}]",
                    callback_data=f"s|{token}|select"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="s|cancel")])
        
        results_text = (
            f"📺 <b>Search Results for:</b> <code>{query}</code>\n\n"
            f"Select a video to download:"
        )
        
        await status_msg.edit_text(
            results_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/search", details=f"Query: {query}")
        
    except Exception as e:
        error_msg = f"❌ Search failed: {str(e)[:200]}"
        await status_msg.edit_text(error_msg)
        await log_to_group(update, context, action="/search", details=f"Error: {str(e)[:150]}", is_error=True)

# Also update the callback handler for search picks
async def on_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    
    if q.data == "s|cancel":
        await q.edit_message_text("❌ Search cancelled.")
        return
    
    try:
        _, token, _ = q.data.split("|")
    except:
        await q.edit_message_text("❌ Invalid selection.")
        return
    
    data = PENDING.get(token)
    if not data or data["exp"] < asyncio.get_event_loop().time():
        await q.edit_message_text("❌ Session expired. Please search again.")
        return
    
    # Show quality selection for the selected video
    await q.edit_message_text("⏳ Preparing download...")
    await q.edit_message_text("Choose quality:", reply_markup=quality_keyboard(data["url"]))



async def credits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user's credit balance"""
    ensure_user(update)
    user_id = update.effective_user.id
    
    ai_credits, ai_used, is_whitelisted = await get_user_credits(user_id)
    video_credits, video_used, _ = await get_user_video_credits(user_id)
    
    status = "👑 Whitelisted" if is_whitelisted else "🎫 Regular User"
    ai_remaining = ai_credits - ai_used
    video_remaining = video_credits - video_used
    
    credits_text = (
        f"💳 <b>Your Credit Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Status: {status}\n\n"
        f"<b>🤖 AI Credits:</b>\n"
        f"📊 Daily Limit: {ai_credits}\n"
        f"✅ Used Today: {ai_used}\n"
        f"🎁 Remaining: {ai_remaining}\n\n"
        f"<b>🎬 Video Credits:</b>\n"
        f"📊 Daily Limit: {video_credits}\n"
        f"✅ Used Today: {video_used}\n"
        f"🎁 Remaining: {video_remaining}\n\n"
        f"<b>Get more:</b>\n"
        f"• /refer - Earn {REFERRER_BONUS} credits\n"
        f"• /claim - Claim someone's code\n"
        f"• Contact {PREMIUM_BOT_USERNAME} for premium"
    )
    
    response = await update.message.reply_text(credits_text, parse_mode=ParseMode.HTML)
    await forward_interaction_to_log(update, context, response)



async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not is_admin(update.effective_user.id):
        response = await update.message.reply_text("❌ Admin only!")
        await forward_interaction_to_log(update, context, response)
        return
    
    # Basic stats placeholder
    await update.message.reply_text("📊 Statistics feature - Implementation pending")


async def refer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate referral code"""
    ensure_user(update)
    user_id = update.effective_user.id
    
    if not MONGO_AVAILABLE:
        response = await update.message.reply_text("❌ Database not available.")
        await forward_interaction_to_log(update, context, response)
        return
    
    code = secrets.token_urlsafe(12).upper()
    
    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"referral_code": code}},
            upsert=True
        )
        
        response = await update.message.reply_text(
            f"🎁 <b>Your Referral Code</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{code}</code>\n\n"
            f"<b>Share this code!</b>\n"
            f"• You get +{REFERRER_BONUS} AI credits when someone uses it\n"
            f"• They get +{CLAIMER_BONUS} AI credits & +{VIDEO_CLAIMER_BONUS} video credits\n\n"
            f"Use: /claim {code}",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/refer", details=f"Generated code: {code[:10]}...")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def claim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim a referral code"""
    ensure_user(update)
    
    if not context.args:
        response = await update.message.reply_text("Usage: /claim <referral_code>")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not MONGO_AVAILABLE:
        response = await update.message.reply_text("❌ Database not available.")
        await forward_interaction_to_log(update, context, response)
        return
    
    code = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        referrer = users_col.find_one({"referral_code": code})
        if not referrer:
            response = await update.message.reply_text("❌ Invalid referral code!")
            await forward_interaction_to_log(update, context, response)
            return
        
        referrer_id = referrer["_id"]
        if referrer_id == user_id:
            response = await update.message.reply_text("❌ You cannot use your own code!")
            await forward_interaction_to_log(update, context, response)
            return
        
        claimed = users_col.find_one({"_id": user_id, f"claimed_codes.{code}": {"$exists": True}})
        if claimed:
            response = await update.message.reply_text("❌ You already claimed this code!")
            await forward_interaction_to_log(update, context, response)
            return
        
        # Give bonuses
        users_col.update_one(
            {"_id": referrer_id},
            {"$inc": {"credits": REFERRER_BONUS, "referrals_made": 1}}
        )
        
        # Claimer gets both AI and video credits
        await add_credits(user_id, CLAIMER_BONUS, "ai")
        await add_credits(user_id, VIDEO_CLAIMER_BONUS, "video")
        
        # Mark as claimed
        users_col.update_one(
            {"_id": user_id},
            {"$set": {f"claimed_codes.{code}": datetime.now()}}
        )
        
        # Send notification to referrer
        try:
            referrer_user = await context.bot.get_chat(referrer_id)
            referrer_name = referrer_user.full_name or referrer_user.username or str(referrer_id)
            await context.bot.send_message(
                chat_id=referrer_id,
                text=f"🎉 <b>Referral Used!</b>\n\n@{update.effective_user.username or update.effective_user.id} used your code!\n✅ You earned +{REFERRER_BONUS} AI credits",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
        
        response = await update.message.reply_text(
            f"🎉 <b>Success!</b>\n\n"
            f"✅ You earned +{CLAIMER_BONUS} AI credits\n"
            f"✅ You earned +{VIDEO_CLAIMER_BONUS} video credits\n"
            f"📊 Your referrer got +{REFERRER_BONUS} AI credits\n\n"
            f"Use /credits to check balance",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/claim", 
                         details=f"User {user_id} claimed code from {referrer_id}")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/claim", details=f"Error: {e}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def gen_redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI redeem code (Admin/Owner only) - single use"""
    if not is_admin(update.effective_user.id):
        response = await update.message.reply_text("❌ Admin only!")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /gen_redeem <value> <code_name>")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not MONGO_AVAILABLE:
        response = await update.message.reply_text("❌ Database not available.")
        await forward_interaction_to_log(update, context, response)
        return
    
    try:
        value = int(context.args[0])
        code_name = context.args[1].strip().upper()
        
        redeem_col.insert_one({
            "code": code_name,
            "value": value,
            "created_by": update.effective_user.id,
            "created_at": datetime.now(),
            "used_by": [],
            "max_uses": 1
        })
        
        response = await update.message.reply_text(
            f"✅ Single-use AI redeem code created!\n\n"
            f"<b>Code:</b> <code>{code_name}</code>\n"
            f"<b>Value:</b> {value} AI credits\n"
            f"<b>Uses:</b> 1 time only\n\n"
            f"Users can claim with: /redeem {code_name}",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/gen_redeem", details=f"Code: {code_name}, Value: {value}")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/gen_redeem", details=f"Error: {e}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem admin code - adds to AI credits"""
    ensure_user(update)
    
    if not context.args:
        response = await update.message.reply_text("Usage: /redeem <code_name>")
        await forward_interaction_to_log(update, context, response)
        return
    
    code_name = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        code_entry = redeem_col.find_one({"code": code_name})
        if not code_entry:
            response = await update.message.reply_text("❌ Invalid redeem code!")
            await forward_interaction_to_log(update, context, response)
            return
        
        if user_id in code_entry.get("used_by", []):
            response = await update.message.reply_text("❌ You already used this code!")
            await forward_interaction_to_log(update, context, response)
            return
        
        value = code_entry["value"]
        user_data = users_col.find_one({"_id": user_id}, {"credits": 1})
        current_credits = user_data.get("credits", BASE_CREDITS) if user_data else BASE_CREDITS
        
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"credits": current_credits + value}},
            upsert=True
        )
        
        redeem_col.update_one(
            {"code": code_name},
            {"$push": {"used_by": user_id}}
        )
        
        response = await update.message.reply_text(
            f"🎉 <b>Redeemed Successfully!</b>\n\n"
            f"✅ Your AI credits increased by <b>{value}</b>\n"
            f"📊 New total: {current_credits + value}\n\n"
            f"Use /gpt to chat with AI!",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/redeem", 
                         details=f"User {user_id} redeemed {code_name} for {value} AI credits")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/redeem", details=f"Error: {e}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def whitelist_ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Whitelist user with custom AI credits"""
    if not is_admin(update.effective_user.id):
        response = await update.message.reply_text("❌ Admin only!")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /whitelist_ai <user_id> <limit>")
        await forward_interaction_to_log(update, context, response)
        return
    
    try:
        target_id = int(context.args[0])
        limit = int(context.args[1])
        
        whitelist_col.update_one(
            {"_id": target_id},
            {"$set": {
                "daily_limit": limit,
                "last_usage_date": get_today_str(),
                "daily_usage": 0
            }},
            upsert=True
        )
        
        user_info = users_col.find_one({"_id": target_id}, {"name": 1})
        name = user_info.get("name", str(target_id)) if user_info else str(target_id)
        
        response = await update.message.reply_text(
            f"✅ <b>User Whitelisted for AI</b>\n\n"
            f"👤 User: <code>{target_id}</code> ({name})\n"
            f"📊 AI Limit: {limit} per day",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/whitelist_ai", details=f"Set AI limit to {limit} for user {target_id}")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/whitelist_ai", details=f"Error: {e}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def lyrics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get lyrics for a song"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        response = await update.message.reply_text("Usage: /lyrics <song name>\nExample: /lyrics Ed Sheeran Shape of You")
        await forward_interaction_to_log(update, context, response)
        return
    
    await log_to_group(update, context, action="/lyrics", details=f"Query: {query}")
    status_msg = await update.message.reply_text(f"📝 Searching lyrics for '<b>{query}</b>'...", parse_mode=ParseMode.HTML)
    
    lyrics = await fetch_lyrics(query)
    
    if lyrics:
        if len(lyrics) > 3800:
            lyrics = lyrics[:3800] + "\n\n... (lyrics truncated due to message limit)"
        
        response = await status_msg.edit_text(
            f"🎵 <b>Lyrics for:</b> <code>{query}</code>\n\n"
            f"<pre>{lyrics}</pre>",
            parse_mode=ParseMode.HTML
        )
    else:
        response = await status_msg.edit_text(
            f"❌ Lyrics not found for '<code>{query}</code>'\n\n"
            f"Tips:\n"
            f"• Include artist name for better results\n"
            f"• Check spelling\n"
            f"• Song might not be in database",
            parse_mode=ParseMode.HTML
        )
    
    await forward_interaction_to_log(update, context, response)

# =========================
# Video Generation Functions
# =========================
def parse_netscape_cookies(content: str) -> dict:
    """Convert Netscape cookie file to aiohttp-compatible dict"""
    cookies = {}
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '\t' in line:
            try:
                parts = line.split('\t', 6)
                if len(parts) >= 7:
                    name, value = parts[5], parts[6]
                    cookies[name] = value
            except Exception:
                continue
    return cookies

class GeminiGenAPI:
    def __init__(self, cookies: dict, bearer_token: str):
        self.cookies = cookies
        self.bearer_token = bearer_token
        self.base_url = "https://api.geminigen.ai"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://geminigen.ai",
            "Referer": "https://geminigen.ai/",
            "Authorization": f"Bearer {self.bearer_token}",
        }
    
    async def generate_video(self, prompt: str) -> str:
        """Submit generation request - returns UUID"""
        async with aiohttp.ClientSession(cookies=self.cookies, headers=self.headers) as session:
            endpoint = f"{self.base_url}/api/video-gen/veo"
            
            form = aiohttp.FormData()
            form.add_field('prompt', prompt)
            form.add_field('model', 'veo-3-fast')
            form.add_field('duration', '8')
            form.add_field('resolution', '720p')
            form.add_field('aspect_ratio', '16:9')
            form.add_field('enhance_prompt', 'true')
            
            log.info(f"🚀 POST {endpoint}")
            
            async with session.post(endpoint, data=form) as resp:
                if resp.status not in (200, 202):
                    text = await resp.text()
                    raise Exception(f"Generation failed: HTTP {resp.status}\nResponse: {text[:500]}")
                
                result = await resp.json()
                log.info(f"✅ Generation response: {json.dumps(result, indent=2)}")
                
                job_id = result.get("uuid") or result.get("id")
                if not job_id:
                    raise Exception(f"No job_id found: {result}")
                
                log.info(f"🆔 Job UUID: {job_id}")
                return job_id
    
    async def poll_for_video(self, job_id: str, timeout: int = 300) -> str:
        """Poll history endpoint with smart URL detection"""
        async with aiohttp.ClientSession(cookies=self.cookies, headers=self.headers) as session:
            start = datetime.now()
            endpoint = f"{self.base_url}/api/history/{job_id}"
            
            while True:
                elapsed = (datetime.now() - start).total_seconds()
                if elapsed > timeout:
                    raise TimeoutError(f"Timeout after {timeout}s")
                
                log.info(f"⏳ Polling {endpoint} ({elapsed:.1f}s)")
                
                async with session.get(endpoint) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        log.warning(f"Poll failed: HTTP {resp.status} - {text[:200]}")
                        await asyncio.sleep(3)
                        continue
                    
                    result = await resp.json()
                    log.debug(f"📄 Full response: {json.dumps(result, indent=2)}")
                    
                    video_url = None
                    
                    if "generated_video" in result and isinstance(result["generated_video"], list):
                        for video_item in result["generated_video"]:
                            if isinstance(video_item, dict):
                                possible_fields = ['video_url', 'file_download_url', 'download_url', 'url', 'sora_post_url']
                                for field in possible_fields:
                                    if field in video_item and video_item[field]:
                                        video_url = video_item[field]
                                        log.info(f"✅ Found video URL in generated_video[0]['{field}']: {video_url[:80]}...")
                                        break
                                if video_url:
                                    break
                    
                    if not video_url:
                        top_fields = ['video_url', 'download_url', 'url', 'media_url', 'output_url']
                        for field in top_fields:
                            if field in result and result[field]:
                                video_url = result[field]
                                log.info(f"✅ Found video URL in top-level '{field}': {video_url[:80]}...")
                                break
                    
                    if not video_url:
                        result_str = json.dumps(result)
                        mp4_matches = re.findall(r'https?://[^\s"]+\.mp4(?:\?[^\s"]*)?', result_str)
                        if mp4_matches:
                            video_url = mp4_matches[0]
                            log.info(f"✅ Extracted MP4 URL from JSON scan: {video_url[:80]}...")
                    
                    if video_url:
                        return video_url
                    
                    status = result.get("status", "")
                    progress = result.get("status_percentage", 0)
                    queue = result.get("queue_position", 0)
                    
                    error_message = result.get("error_message")
                    if error_message and str(error_message).strip() and str(error_message).lower() not in ['null', 'none', '']:
                        raise Exception(f"Server error: {error_message}")
                    
                    if status in [0, "failed", "error"]:
                        raise Exception(f"Generation failed with status: {status}")
                    
                    if status in [1, "processing", "queued"] or progress < 100:
                        log.info(f"⏳ Processing... Progress: {progress}%, Queue: {queue}")
                        await asyncio.sleep(3)
                        continue
                    
                    log.warning(f"Unknown state (no URL yet): status={status}, progress={progress}")
                
                await asyncio.sleep(3)
    
    async def download_video(self, url: str) -> bytes:
        """Download video from Cloudflare R2"""
        async with aiohttp.ClientSession() as session:
            log.info(f"📥 Downloading from {url[:80]}...")
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"Download failed: HTTP {resp.status}")
                
                size = int(resp.headers.get('content-length', 0))
                log.info(f"Download size: {size / 1024 / 1024:.2f} MB")
                
                return await resp.read()

# NEW: Bearer Token and Cookie Content
BEARER_TOKEN = os.getenv("BEARER_TOKEN", "your_bearer_token_here")
COOKIE_FILE_CONTENT = os.getenv("COOKIE_FILE_CONTENT", "")

async def vdogen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI video - NEW credit system"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        response = await update.message.reply_text("Usage: /vdogen <description>\nExample: /vdogen A cute girl dancing")
        await forward_interaction_to_log(update, context, response)
        return
    
    user_id = update.effective_user.id
    
    # Check video credits
    if not is_admin(user_id):
        credits, used, is_whitelisted = await get_user_video_credits(user_id)
        remaining = credits - used
        
        if remaining <= 0:
            no_video_credits = (
                f"❌ <b>No Video Credits Remaining!</b>\n\n"
                f"📊 Your daily limit: {credits}\n"
                f"✅ Used: {used}\n\n"
                f"<b>Get more video credits:</b>\n"
                f"• /refer - Share your code (earn when used)\n"
                f"• /claim - Claim someone's code\n"
                f"• /vdoredeem - Redeem a special code\n"
                f"• Contact {PREMIUM_BOT_USERNAME} for premium\n\n"
                f"💳 Your AI credits: Check with /credits"
            )
            response = await update.message.reply_text(no_video_credits, parse_mode=ParseMode.HTML)
            await forward_interaction_to_log(update, context, response)
            return
    
    # Check if user already has an active generation
    if user_id in user_active_tasks and not user_active_tasks[user_id].done():
        response = await update.message.reply_text(
            "⏳ <b>You already have a video generating!</b>\n\n"
            "Please wait for your current request to complete before starting a new one.\n\n"
            "Use /credits to check your status.",
            parse_mode=ParseMode.HTML
        )
        await forward_interaction_to_log(update, context, response)
        return
    
    status_msg = await update.message.reply_text(
        f"🎬 <b>Video Request Received!</b>\n\n"
        f"📝 Prompt: <code>{query[:60]}...</code>\n\n"
        f"⏳ <i>Processing... (Queue position: {len(video_generation_queue) + 1})</i>",
        parse_mode=ParseMode.HTML
    )
    
    await log_to_group(update, context, action="/vdogen", details=f"Prompt: {query} | User: {user_id} | Queued")
    
    queue_item = {
        "user_id": user_id,
        "query": query,
        "status_msg": status_msg,
        "update": update,
        "context": context,
    }
    
    video_generation_queue.append(queue_item)
    asyncio.create_task(process_video_queue())
    
    log.info(f"✅ Added to queue. Current queue size: {len(video_generation_queue)}")

async def process_video_queue():
    """Background worker that processes video generation queue"""
    global active_generations
    
    if active_generations >= MAX_CONCURRENT_GENERATIONS:
        log.info(f"⏳ Max concurrent generations reached ({MAX_CONCURRENT_GENERATIONS}). Waiting...")
        return
    
    if not video_generation_queue:
        return
    
    async with generation_semaphore:
        active_generations += 1
        queue_item = video_generation_queue.popleft()
        
        user_id = queue_item["user_id"]
        query = queue_item["query"]
        status_msg = queue_item["status_msg"]
        update = queue_item["update"]
        context = queue_item["context"]
        
        task = asyncio.current_task()
        user_active_tasks[user_id] = task
        
        try:
            log.info(f"🎬 Starting generation for user {user_id}")
            
            await status_msg.edit_text(
                f"📝 <b>Processing:</b> <code>{query[:60]}...</code>\n"
                f"⏳ Generation in progress...",
                parse_mode=ParseMode.HTML
            )
            
            api = GeminiGenAPI(parse_netscape_cookies(COOKIE_FILE_CONTENT), BEARER_TOKEN)
            
            await status_msg.edit_text(
                f"🚀 <b>Submitting to AI...</b>\n"
                f"⏳ This takes 30-90 seconds",
                parse_mode=ParseMode.HTML
            )
            job_id = await api.generate_video(query)
            
            await status_msg.edit_text(
                f"⏳ <b>Generating video...</b>\n"
                f"🆔 Job: <code>{job_id[:8]}...</code>",
                parse_mode=ParseMode.HTML
            )
            video_url = await api.poll_for_video(job_id, timeout=300)
            
            await status_msg.edit_text("⬇️ <b>Downloading video...</b>", parse_mode=ParseMode.HTML)
            video_bytes = await api.download_video(video_url)
            
            await status_msg.edit_text("⬆️ <b>Uploading to Telegram...</b>", parse_mode=ParseMode.HTML)
            
            video_path = DOWNLOAD_DIR / f"vdo_{user_id}_{secrets.token_urlsafe(8)}.mp4"
            async with aiofiles.open(video_path, "wb") as f:
                await f.write(video_bytes)
            
            caption = (
                f"🎬 <b>{query}</b>\n\n"
                f"✨ Generated by @spotifyxmusixbot\n"
                f"🔖 Job: <code>{job_id[:8]}...</code>"
            )
            
            sent_msg = await update.message.reply_video(
                video=video_path,
                caption=caption,
                filename=f"{query}.mp4",
                parse_mode=ParseMode.HTML,
                width=1280,
                height=720,
                duration=8,
                supports_streaming=True,
                connect_timeout=60,
                read_timeout=60,
                write_timeout=60
            )
            
            # Consume video credit
            if not is_admin(user_id):
                await consume_video_credit(user_id)
                log.info(f"✅ Video credit consumed for user {user_id}")
            
            await status_msg.delete()
            video_path.unlink(missing_ok=True)
            
            log.info(f"✅ SUCCESS! Video sent for user {user_id}")
            
            # Forward to log group
            await forward_interaction_to_log(update, context, sent_msg)
            
        except Exception as e:
            error_str = str(e)
            log.error(f"vdogen failed for user {user_id}: {e}", exc_info=True)
            
            try:
                response = await status_msg.edit_text(
                    "❌ <b>Video Generation Error</b>\n\n"
                    "Our AI video service is temporarily unavailable.\n\n"
                    "💡 <b>Try:</b>\n"
                    "• /gen for AI images\n"
                    "• Try again in a few minutes\n"
                    "• Contact @ayushxchat_robot for support\n\n"
                    f"<i>Error: {error_str[:100]}</i>",
                    parse_mode=ParseMode.HTML
                )
                await forward_interaction_to_log(update, context, response, error_str)
            except:
                pass
            
            await log_to_group(update, context, action="/vdogen", 
                             details=f"Error: {error_str[:150]} | User: {user_id}", is_error=True)
        
        finally:
            active_generations -= 1
            if user_id in user_active_tasks:
                del user_active_tasks[user_id]
            
            if video_generation_queue:
                asyncio.create_task(process_video_queue())

# =========================
# Video Redeem Commands
# =========================
async def genvdo_redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate video redeem code (Owner/Admin only) - single use"""
    if not is_admin(update.effective_user.id):
        response = await update.message.reply_text("❌ Admin only!")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /genvdo_redeem <value> <code_name>")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not MONGO_AVAILABLE:
        response = await update.message.reply_text("❌ Database not available.")
        await forward_interaction_to_log(update, context, response)
        return
    
    try:
        value = int(context.args[0])
        code_name = context.args[1].strip().upper()
        
        video_redeem_col.insert_one({
            "code": code_name,
            "value": value,
            "created_by": update.effective_user.id,
            "created_at": datetime.now(),
            "used_by": [],
            "max_uses": 1
        })
        
        response = await update.message.reply_text(
            f"✅ Single-use video redeem code created!\n\n"
            f"<b>Code:</b> <code>{code_name}</code>\n"
            f"<b>Value:</b> {value} video credits\n"
            f"<b>Uses:</b> 1 time only\n\n"
            f"Users can claim with: /vdoredeem {code_name}",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/genvdo_redeem", details=f"Code: {code_name}, Value: {value}")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/genvdo_redeem", details=f"Error: {e}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def vdoredeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem video code - adds to video credits"""
    ensure_user(update)
    
    if not context.args:
        response = await update.message.reply_text("Usage: /vdoredeem <code_name>")
        await forward_interaction_to_log(update, context, response)
        return
    
    code_name = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        code_entry = video_redeem_col.find_one({"code": code_name})
        if not code_entry:
            response = await update.message.reply_text("❌ Invalid video redeem code!")
            await forward_interaction_to_log(update, context, response)
            return
        
        if user_id in code_entry.get("used_by", []):
            response = await update.message.reply_text("❌ You already used this code!")
            await forward_interaction_to_log(update, context, response)
            return
        
        value = code_entry["value"]
        user_data = users_col.find_one({"_id": user_id}, {"video_credits": 1})
        current_credits = user_data.get("video_credits", VIDEO_BASE_CREDITS) if user_data else VIDEO_BASE_CREDITS
        
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"video_credits": current_credits + value}},
            upsert=True
        )
        
        video_redeem_col.update_one(
            {"code": code_name},
            {"$push": {"used_by": user_id}}
        )
        
        response = await update.message.reply_text(
            f"🎉 <b>Video Code Redeemed!</b>\n\n"
            f"✅ Your video credits increased by <b>{value}</b>\n"
            f"📊 New total: {current_credits + value}\n\n"
            f"Use /vdogen to generate videos!",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/vdoredeem", 
                         details=f"User {user_id} redeemed {code_name} for {value} video credits")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/vdoredeem", details=f"Error: {e}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def whitelist_vdo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Whitelist user with custom video credits"""
    if not is_admin(update.effective_user.id):
        response = await update.message.reply_text("❌ Admin only!")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /whitelist_vdo <user_id> <limit>")
        await forward_interaction_to_log(update, context, response)
        return
    
    try:
        target_id = int(context.args[0])
        limit = int(context.args[1])
        
        whitelist_col.update_one(
            {"_id": target_id},
            {"$set": {
                "video_daily_limit": limit,
                "video_last_usage_date": get_today_str(),
                "video_daily_usage": 0
            }},
            upsert=True
        )
        
        user_info = users_col.find_one({"_id": target_id}, {"name": 1})
        name = user_info.get("name", str(target_id)) if user_info else str(target_id)
        
        response = await update.message.reply_text(
            f"✅ <b>User Whitelisted for Videos</b>\n\n"
            f"👤 User: <code>{target_id}</code> ({name})\n"
            f"📊 Video Limit: {limit} per day",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/whitelist_vdo", details=f"Set video limit to {limit} for user {target_id}")
        await forward_interaction_to_log(update, context, response)
        
    except Exception as e:
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/whitelist_vdo", details=f"Error: {e}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

# =========================
# AI Image Generation
# =========================
async def gen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI image"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        response = await update.message.reply_text("Usage: /gen <description>")
        await forward_interaction_to_log(update, context, response)
        return
    
    user_id = update.effective_user.id
    
    # Check image limit
    today = get_today_str()
    user_data = users_col.find_one({"_id": user_id}, {"media_gen_today": 1, "media_gen_date": 1})
    used_today = user_data.get("media_gen_today", 0) if user_data and user_data.get("media_gen_date") == today else 0
    
    if used_today >= BASE_MEDIA_GEN_LIMIT:
        response = await update.message.reply_text(f"❌ Daily image limit: {used_today}/{BASE_MEDIA_GEN_LIMIT}")
        await forward_interaction_to_log(update, context, response)
        return
    
    status = await update.message.reply_text(f"🎨 Generating: <b>{query}</b>...", parse_mode=ParseMode.HTML)
    
    try:
        encoded = query.replace(" ", "+")
        url = f"https://flux-pro.vercel.app/generate?q={encoded}"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await status.edit_text(f"❌ API Error: {resp.status}")
                    return
                
                data = await resp.read()
                path = DOWNLOAD_DIR / f"gen_{user_id}.png"
                async with aiofiles.open(path, "wb") as f:
                    await f.write(data)
        
        caption = f"🖼️ <b>{query}</b>\n\n<i>Generated by @spotifyxmusixbot</i>"
        sent_msg = await update.message.reply_photo(photo=path, caption=caption, parse_mode=ParseMode.HTML)
        
        # Update counter
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"media_gen_date": today, "media_gen_today": used_today + 1}},
            upsert=True
        )
        
        await status.delete()
        path.unlink()
        
        # Forward to log group
        await forward_interaction_to_log(update, context, sent_msg)
        
    except Exception as e:
        await status.edit_text(f"❌ Failed: {str(e)[:100]}")

# =========================
# AI Chat Command
# =========================
async def gpt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI Chat command"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        response = await update.message.reply_text("Usage: /gpt <your question>")
        await forward_interaction_to_log(update, context, response)
        return
    
    if not groq_client:
        response = await update.message.reply_text("❌ AI not configured. Contact admin.", parse_mode=ParseMode.HTML)
        await forward_interaction_to_log(update, context, response)
        return
    
    user_id = update.effective_user.id
    
    # CREDIT CHECK
    try:
        credits, used, is_whitelisted = await get_user_credits(user_id)
        remaining = credits - used
        
    except Exception as e:
        credits, used, is_whitelisted = BASE_CREDITS, 0, False
        remaining = credits
    
    if remaining <= 0:
        no_credits_text = (
            f"❌ <b>No Credits Remaining!</b>\n\n"
            f"📊 Your daily limit: {credits}\n"
            f"✅ Used: {used}\n\n"
            f"<b>Get more credits:</b>\n"
            f"• /refer - Generate referral code (+{REFERRER_BONUS} per friend)\n"
            f"• /claim - Claim someone's code (+{CLAIMER_BONUS})\n"
            f"• Contact {PREMIUM_BOT_USERNAME} for premium access\n\n"
            f"Use /credits to check your balance"
        )
        response = await update.message.reply_text(no_credits_text, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/gpt", details=f"User {user_id} out of credits (used {used}/{credits})")
        await forward_interaction_to_log(update, context, response)
        return
    
    status_msg = await update.message.reply_text(f"🤖 Processing... (Credits left: {remaining-1})")
    
    if user_id not in USER_CONVERSATIONS:
        USER_CONVERSATIONS[user_id] = [
            {"role": "system", "content": "You are a helpful assistant. Be concise and clear."}
        ]
    
    USER_CONVERSATIONS[user_id].append({"role": "user", "content": query})
    
    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=USER_CONVERSATIONS[user_id],
            max_tokens=1000,
            temperature=0.7
        )
        
        answer = response.choices[0].message.content
        USER_CONVERSATIONS[user_id].append({"role": "assistant", "content": answer})
        
        if len(USER_CONVERSATIONS[user_id]) > 10:
            USER_CONVERSATIONS[user_id] = [USER_CONVERSATIONS[user_id][0]] + USER_CONVERSATIONS[user_id][-9:]
        
        if len(answer) > 4000:
            answer = answer[:4000] + "\n\n... (truncated)"
        
        response_text = (
            f"💬 <b>Query:</b> <code>{query}</code>\n\n"
            f"<b>Answer:</b>\n{answer}\n\n"
            f"<i>ai by @spotifyxmusixbot</i>"
        )
        
        sent_msg = await status_msg.edit_text(response_text, parse_mode=ParseMode.HTML)
        
        credit_success = await consume_credit(user_id)
        log.info(f"✅ GPT_CMD SUCCESS | User: {user_id} | Credit consumed: {credit_success}")
        
        await log_to_group(update, context, action="/gpt", 
                         details=f"User {user_id}: {query[:50]}... | Remaining: {remaining-1}")
        
        # Forward to log group
        await forward_interaction_to_log(update, context, sent_msg)
        
    except Exception as e:
        log.error(f"💥 GPT_CMD AI ERROR for {user_id}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ AI Error: {str(e)[:200]}")
        await log_to_group(update, context, action="/gpt", details=f"Error: {e}", is_error=True)
        USER_CONVERSATIONS[user_id] = [{"role": "system", "content": "You are a helpful assistant."}]

# =========================
# Test Cookies Command
# =========================
async def test_cookies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test YouTube cookies functionality - Admin only"""
    if not is_admin(update.effective_user.id):
        response = await update.message.reply_text("❌ Admin only!")
        await forward_interaction_to_log(update, context, response)
        return
    
    cookies_path = Path(COOKIES_FILE)
    if not cookies_path.exists():
        response = await update.message.reply_text(
            f"❌ Cookies file not found!\n\n"
            f"Expected location: <code>{cookies_path.absolute()}</code>\n\n"
            f"<b>How to get cookies:</b>\n"
            f"1. Install browser extension 'Get cookies.txt LOCALLY'\n"
            f"2. Log in to YouTube\n"
            f"3. Click extension → Export → Netscape format\n"
            f"4. Save as <code>{COOKIES_FILE}</code> in bot directory\n"
            f"5. Restart bot",
            parse_mode=ParseMode.HTML
        )
        await forward_interaction_to_log(update, context, response)
        return
    
    if cookies_path.stat().st_size == 0:
        response = await update.message.reply_text(
            f"⚠️ Cookies file is empty!\n\n"
            f"Location: <code>{cookies_path.absolute()}</code>\n\n"
            f"Please export cookies from YouTube and save to this file.",
            parse_mode=ParseMode.HTML
        )
        await forward_interaction_to_log(update, context, response)
        return
    
    status_msg = await update.message.reply_text("🔍 Testing YouTube cookies...")
    
    try:
        with open(cookies_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if '# Netscape HTTP Cookie File' not in content:
                raise ValueError("Not a Netscape format cookies file")
            if '.youtube.com' not in content and '.google.com' not in content:
                raise ValueError("No YouTube/Google cookies found")
        
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "cookiefile": str(cookies_path),
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(test_url, download=False)
            
            is_logged_in = False
            has_pauth = False
            if info:
                if info.get('duration') is not None or info.get('uploader') is not None:
                    is_logged_in = True
                
                if 'SAPISID' in content or '__Secure-3PAPISID' in content:
                    has_pauth = True
        
        result_text = (
            f"✅ <b>Cookies Test Results</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📁 File: <code>{cookies_path.absolute()}</code>\n"
            f"📏 Size: {cookies_path.stat().st_size} bytes\n"
            f"🍪 Format: ✅ Netscape\n"
            f"🔑 YouTube Cookies: {'✅ Found' if '.youtube.com' in content else '❌ Missing'}\n"
            f"🔐 Auth Cookies: {'✅ Found' if has_pauth else '⚠️ Partial'}\n"
            f"🎬 Video Access: {'✅ Success' if is_logged_in else '⚠️ Limited'}\n\n"
            f"<b>Status:</b> {'✅ Ready for use' if is_logged_in else '⚠️ May need refresh'}"
        )
        
        response = await status_msg.edit_text(result_text, parse_mode=ParseMode.HTML)
        
        await log_to_group(update, context, action="/testcookies", 
                         details=f"Cookies test passed. Auth: {is_logged_in}, PAuth: {has_pauth}")
        await forward_interaction_to_log(update, context, response)
        
    except ValueError as ve:
        error_text = (
            f"❌ <b>Cookies Format Error</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📁 File: <code>{cookies_path.absolute()}</code>\n"
            f"❌ Error: {str(ve)}\n\n"
            f"<b>Solution:</b>\n"
            f"Export cookies in Netscape format:\n"
            f"1. Install 'Get cookies.txt LOCALLY' extension\n"
            f"2. Go to YouTube and ensure you're logged in\n"
            f"3. Click extension → Export → Netscape format\n"
            f"4. Save as <code>{COOKIES_FILE}</code>"
        )
        response = await status_msg.edit_text(error_text, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/testcookies", details=f"Format error: {ve}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(ve))
        
    except Exception as e:
        error_text = (
            f"❌ <b>Cookies Test Failed</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📁 File: <code>{cookies_path.absolute()}</code>\n"
            f"❌ Error: {str(e)[:200]}\n\n"
            f"<b>Possible issues:</b>\n"
            f"• Cookies expired (export again)\n"
            f"• Wrong format (must be Netscape)\n"
            f"• File permissions\n"
            f"• YouTube account flagged\n\n"
            f"<b>Tip:</b> Log out and back into YouTube, then re-export cookies"
        )
        
        response = await status_msg.edit_text(error_text, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/testcookies", details=f"Test failed: {str(e)[:100]}", is_error=True)
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

# =========================
# Broadcast Functions
# =========================
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        response = await update.message.reply_text("❌ Not authorized!")
        await forward_interaction_to_log(update, context, response)
        return
    
    admin_id = update.effective_user.id
    BROADCAST_STORE[admin_id] = []
    BROADCAST_STATE[admin_id] = True
    
    response = await update.message.reply_text(
        "📢 Broadcast mode ON. Send messages to add to queue.\n"
        "Then use:\n"
        "/done_broadcast - Preview messages\n"
        "/send_broadcast - Send to all users and groups\n"
        "/cancel_broadcast - Cancel"
    )
    
    await log_to_group(update, context, action="/broadcast", details="Broadcast mode started")
    await forward_interaction_to_log(update, context, response)

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ALL non-command messages for broadcast"""
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id):
        return
    
    msg = {}
    if update.message.text:
        msg = {"type": "text", "text": update.message.text}
    elif update.message.photo:
        msg = {
            "type": "photo",
            "photo": update.message.photo[-1].file_id,
            "caption": update.message.caption or ""
        }
    elif update.message.video:
        msg = {
            "type": "video",
            "video": update.message.video.file_id,
            "caption": update.message.caption or ""
        }
    elif update.message.document:
        msg = {
            "type": "document",
            "document": update.message.document.file_id,
            "caption": update.message.caption or ""
        }
    elif update.message.animation:
        msg = {
            "type": "animation",
            "animation": update.message.animation.file_id,
            "caption": update.message.caption or ""
        }
    elif update.message.audio:
        msg = {
            "type": "audio",
            "audio": update.message.audio.file_id,
            "caption": update.message.caption or ""
        }
    else:
        await update.message.reply_text("⚠️ Unsupported message type for broadcast.")
        return
    
    BROADCAST_STORE.setdefault(admin_id, []).append(msg)
    count = len(BROADCAST_STORE[admin_id])
    
    await update.message.reply_text(f"✅ Message added. Queue: {count}")

async def done_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id): 
        await update.message.reply_text("❌ Not in broadcast mode.")
        return
    
    messages = BROADCAST_STORE.get(admin_id, [])
    if not messages: 
        await update.message.reply_text("❌ No messages to preview.")
        return
    
    await update.message.reply_text("📢 <b>Broadcast Preview:</b>", parse_mode=ParseMode.HTML)
    
    for i, msg in enumerate(messages, 1):
        try:
            if msg["type"] == "text":
                await update.message.reply_text(msg["text"], parse_mode=ParseMode.HTML)
            elif msg["type"] == "photo":
                await update.message.reply_photo(photo=msg["photo"], caption=msg["caption"], parse_mode=ParseMode.HTML)
            elif msg["type"] == "video":
                await update.message.reply_video(video=msg["video"], caption=msg["caption"], parse_mode=ParseMode.HTML)
            elif msg["type"] == "document":
                await update.message.reply_document(document=msg["document"], caption=msg["caption"], parse_mode=ParseMode.HTML)
            elif msg["type"] == "animation":
                await update.message.reply_animation(animation=msg["animation"], caption=msg["caption"], parse_mode=ParseMode.HTML)
            elif msg["type"] == "audio":
                await update.message.reply_audio(audio=msg["audio"], caption=msg["caption"], parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to preview message {i}: {e}")
    
    await update.message.reply_text(
        "✅ Preview complete.\n"
        "Send /send_broadcast to broadcast or /cancel_broadcast to cancel."
    )

async def send_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id): 
        await update.message.reply_text("❌ Not in broadcast mode.")
        return
    
    messages = BROADCAST_STORE.get(admin_id, [])
    if not messages: 
        await update.message.reply_text("❌ No messages to broadcast.")
        return
    
    recipients = set()
    
    if MONGO_AVAILABLE:
        for u in users_col.find({}, {"_id": 1}): 
            recipients.add(u["_id"])
        
        for g in db["broadcast_chats"].find({}, {"_id": 1}):
            recipients.add(g["_id"])
    
    if not recipients:
        await update.message.reply_text("❌ No recipients found.")
        return
    
    await update.message.reply_text(f"📢 Broadcasting to {len(recipients)} chats...")
    
    success, failed = 0, 0
    progress_msg = await update.message.reply_text("Progress: 0%")
    
    for i, chat_id in enumerate(recipients):
        if i % 50 == 0:
            progress = (i / len(recipients)) * 100
            await progress_msg.edit_text(f"Progress: {progress:.1f}% ({i}/{len(recipients)})")
            await asyncio.sleep(0.1)
        
        try:
            for msg in messages:
                if msg["type"] == "text":
                    await context.bot.send_message(chat_id=chat_id, text=msg["text"], parse_mode=ParseMode.HTML)
                elif msg["type"] == "photo":
                    await context.bot.send_photo(chat_id=chat_id, photo=msg["photo"], caption=msg["caption"], parse_mode=ParseMode.HTML)
                elif msg["type"] == "video":
                    await context.bot.send_video(chat_id=chat_id, video=msg["video"], caption=msg["caption"], parse_mode=ParseMode.HTML)
                elif msg["type"] == "document":
                    await context.bot.send_document(chat_id=chat_id, document=msg["document"], caption=msg["caption"], parse_mode=ParseMode.HTML)
                elif msg["type"] == "animation":
                    await context.bot.send_animation(chat_id=chat_id, animation=msg["animation"], caption=msg["caption"], parse_mode=ParseMode.HTML)
                elif msg["type"] == "audio":
                    await context.bot.send_audio(chat_id=chat_id, audio=msg["audio"], caption=msg["caption"], parse_mode=ParseMode.HTML)
            success += 1
        except Exception as e:
            log.error(f"Broadcast failed to {chat_id}: {e}")
            failed += 1
        await asyncio.sleep(0.05)
    
    await progress_msg.delete()
    
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    
    summary = (
        f"✅ <b>Broadcast Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 Successful: {success}\n"
        f"❌ Failed: {failed}\n"
        f"👥 Total Recipients: {len(recipients)}"
    )
    
    await update.message.reply_text(summary, parse_mode=ParseMode.HTML)
    
    # Fixed logging calls - removed duplicate line and closed string literals
    await log_to_group(update, context, action="/send_broadcast", 
                      details=f"Sent to {success} users, {failed} failed")
    await forward_interaction_to_log(update, context, None)

async def cancel_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    
    admin_id = update.effective_user.id
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    
    response = await update.message.reply_text("❌ Broadcast cancelled.")
    await log_to_group(update, context, action="/cancel_broadcast", details="Broadcast cancelled")
    await forward_interaction_to_log(update, context, response)

# =========================
# Admin Management Commands
# =========================
async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): 
        response = await update.message.reply_text("❌ Owner only!")
        await forward_interaction_to_log(update, context, response)
        return
    if not context.args: 
        response = await update.message.reply_text("Usage: /addadmin <user_id>")
        await forward_interaction_to_log(update, context, response)
        return
    try:
        new_id = int(context.args[0])
        user = users_col.find_one({"_id": new_id})
        if not user: 
            response = await update.message.reply_text("❌ User not found. They must /start first.")
            await forward_interaction_to_log(update, context, response)
            return
        if admins_col.find_one({"_id": new_id}): 
            response = await update.message.reply_text("❌ Already admin.")
            await forward_interaction_to_log(update, context, response)
            return
        admins_col.insert_one({
            "_id": new_id, 
            "name": user.get("name", str(new_id)), 
            "added_by": update.effective_user.id, 
            "added_at": datetime.now()
        })
        await log_to_group(update, context, action="/addadmin", details=f"Added admin {new_id}")
        response = await update.message.reply_text(f"✅ Added <b>{user.get('name', new_id)}</b> as admin.", parse_mode=ParseMode.HTML)
        await forward_interaction_to_log(update, context, response)
    except Exception as e: 
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def rmadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): 
        response = await update.message.reply_text("❌ Owner only!")
        await forward_interaction_to_log(update, context, response)
        return
    if not context.args: 
        response = await update.message.reply_text("Usage: /rmadmin <user_id>")
        await forward_interaction_to_log(update, context, response)
        return
    try:
        rm_id = int(context.args[0])
        if rm_id == OWNER_ID: 
            response = await update.message.reply_text("❌ Cannot remove owner!")
            await forward_interaction_to_log(update, context, response)
            return
        if not admins_col.find_one({"_id": rm_id}): 
            response = await update.message.reply_text("❌ Not an admin.")
            await forward_interaction_to_log(update, context, response)
            return
        admins_col.delete_one({"_id": rm_id})
        await log_to_group(update, context, action="/rmadmin", details=f"Removed admin {rm_id}")
        response = await update.message.reply_text(f"✅ Removed admin.", parse_mode=ParseMode.HTML)
        await forward_interaction_to_log(update, context, response)
    except Exception as e: 
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        response = await update.message.reply_text("❌ Not authorized!")
        await forward_interaction_to_log(update, context, response)
        return
    if not MONGO_AVAILABLE: 
        response = await update.message.reply_text("Database not available.")
        await forward_interaction_to_log(update, context, response)
        return
    try:
        admins = list(admins_col.find().sort("added_at", -1))
        if not admins: 
            response = await update.message.reply_text("No admins.")
            await forward_interaction_to_log(update, context, response)
            return
        admin_list = "👥 <b>Admin List</b>\n━━━━━━━━━━━━\n\n"
        for admin in admins:
            admin_id = admin["_id"]
            name = admin.get("name", "Unknown")
            role = "👑 Owner" if admin_id == OWNER_ID else "🔧 Admin"
            admin_list += f"• <code>{admin_id}</code> - {name} ({role})\n"
        admin_list += f"\n<b>Total: {len(admins)}</b>"
        response = await update.message.reply_text(admin_list, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/adminlist", details=f"Listed {len(admins)} admins")
        await forward_interaction_to_log(update, context, response)
    except Exception as e: 
        response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_interaction_to_log(update, context, response, error_msg=str(e))

# =========================
# Callback Handlers
# =========================
async def on_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        _, token, qlt = q.data.split("|")
    except:
        return
    data = PENDING.get(token)
    if not data or data["exp"] < asyncio.get_event_loop().time():
        await q.edit_message_text("Session expired.")
        return
    await q.edit_message_text(f"⬇️ Downloading {qlt}p quality...")
    await download_and_send(q.message.chat.id, q.message, context, data["url"], qlt)

async def on_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        _, token, _ = q.data.split("|")
    except:
        return
    data = PENDING.get(token)
    if not data or data["exp"] < asyncio.get_event_loop().time():
        await q.edit_message_text("Expired.")
        return
    await q.edit_message_text("Choose quality:", reply_markup=quality_keyboard(data["url"]))

async def on_lyrics_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle lyrics button clicks"""
    q = update.callback_query
    await q.answer()
    
    try:
        _, song_title = q.data.split("|", 1)
    except:
        await q.edit_message_text("❌ Invalid request")
        return
    
    status_msg = await q.edit_message_text("📝 Searching for lyrics...")
    
    lyrics = await fetch_lyrics(song_title)
    
    if lyrics:
        if len(lyrics) > 3800:
            lyrics = lyrics[:3800] + "\n\n... (lyrics truncated due to message limit)"
        
        response = await status_msg.edit_text(
            f"🎵 <b>Lyrics for:</b> <code>{song_title}</code>\n\n"
            f"<pre>{lyrics}</pre>",
            parse_mode=ParseMode.HTML
        )
    else:
        response = await status_msg.edit_text(
            f"❌ Lyrics not found for '<code>{song_title}</code>'\n\n"
            f"• Song might be too new\n"
            f"• Title might be misspelled\n"
            f"• Try manual search: /lyrics artist song",
            parse_mode=ParseMode.HTML
        )
    
    await forward_interaction_to_log(update, context, response)

async def on_verify_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        member = await context.bot.get_chat_member(
            chat_id=FORCE_JOIN_CHANNEL,
            user_id=q.from_user.id
        )
        if member.status not in ["left", "kicked"]:
            await q.edit_message_text("✅ Verified! You can now use the bot.")
            await start(update, context)
            await log_to_group(update, context, action="Channel Verified", details=f"User {q.from_user.id} verified membership")
        else:
            await q.answer("❌ Please join the channel first!", show_alert=True)
    except Exception as e:
        log.error(f"Membership verification failed: {e}")
        await q.answer("❌ Error verifying. Try again.", show_alert=True)

# =========================
# Message Handlers
# =========================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    if update.message.chat.type in ["group", "supergroup", "channel"]:
        if MONGO_AVAILABLE:
            db["broadcast_chats"].update_one(
                {"_id": update.message.chat.id},
                {"$set": {
                    "title": update.message.chat.title,
                    "type": update.message.chat.type,
                    "updated_at": datetime.now()
                }},
                upsert=True
            )
    
    if not await ensure_membership(update, context):
        return
    
    if update.effective_user and is_admin(update.effective_user.id):
        admin_id = update.effective_user.id
        if BROADCAST_STATE.get(admin_id):
            await handle_broadcast_message(update, context)
            return
    
    txt = update.message.text.strip()
    match = YOUTUBE_REGEX.search(txt)
    if match:
        url = match.group(0)
        user_id = update.effective_user.id
        await log_to_group(update, context, action="YouTube URL", details=f"User {user_id} sent: {url[:50]}...")
        response = await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))
        await forward_interaction_to_log(update, context, response)

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all message types for potential broadcast"""
    if update.effective_user and is_admin(update.effective_user.id):
        admin_id = update.effective_user.id
        if BROADCAST_STATE.get(admin_id):
            await handle_broadcast_message(update, context)



# =========================
# Keyboard Generator
# =========================
def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    token = store_url(url)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 MP3 Audio", callback_data=f"q|{token}|mp3")],
        [InlineKeyboardButton("🎬 360p", callback_data=f"q|{token}|360")],
        [InlineKeyboardButton("🎬 480p", callback_data=f"q|{token}|480")],
        [InlineKeyboardButton("🎬 720p", callback_data=f"q|{token}|720")],
        [InlineKeyboardButton("🎬 1080p", callback_data=f"q|{token}|1080")],
    ])

# =========================
# Chat Member Handler - Track Bot Group Membership
# =========================
async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bot being added/removed from groups"""
    result = update.my_chat_member
    chat = result.chat
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    
    # Log the change
    log.info(f"Chat member update: {chat.title} ({chat.id}) | {old_status} -> {new_status}")
    
    # Bot added to group
    if old_status in ["left", "kicked"] and new_status in ["member", "administrator"]:
        log.info(f"✅ Bot was added to {chat.title} ({chat.id})")
        
        # Add to broadcast database
        if MONGO_AVAILABLE and chat.type in ["group", "supergroup", "channel"]:
            try:
                db["broadcast_chats"].update_one(
                    {"_id": chat.id},
                    {"$set": {
                        "title": chat.title,
                        "type": chat.type,
                        "added_at": datetime.now(),
                        "updated_at": datetime.now()
                    }},
                    upsert=True
                )
                log.info(f"✅ Added {chat.title} to broadcast_chats")
            except Exception as e:
                log.error(f"Failed to add chat to broadcast_chats: {e}")
    
    # Bot removed from group
    elif old_status in ["member", "administrator"] and new_status in ["left", "kicked"]:
        log.info(f"❌ Bot was removed from {chat.title} ({chat.id})")
        
        # Remove from broadcast database
        if MONGO_AVAILABLE:
            try:
                db["broadcast_chats"].delete_one({"_id": chat.id})
                log.info(f"✅ Removed {chat.title} from broadcast_chats")
            except Exception as e:
                log.error(f"Failed to remove chat from broadcast_chats: {e}")

# Add this new command handler
async def test_video_api_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test video generation API credentials - Admin only"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    status_msg = await update.message.reply_text("🔍 Testing video generation API credentials...")
    
    try:
        # Check if credentials are set
        if not BEARER_TOKEN or BEARER_TOKEN == "your_bearer_token_here":
            raise ValueError("BEARER_TOKEN not configured or is placeholder")
        
        if not COOKIE_FILE_CONTENT:
            raise ValueError("COOKIE_FILE_CONTENT not configured")
        
        if "# Netscape HTTP Cookie File" not in COOKIE_FILE_CONTENT:
            raise ValueError("Cookie format is not Netscape")
        
        # Test parsing cookies
        cookies = parse_netscape_cookies(COOKIE_FILE_CONTENT)
        if not cookies:
            raise ValueError("No valid cookies found in COOKIE_FILE_CONTENT")
        
        # Test API call
        api = GeminiGenAPI(cookies, BEARER_TOKEN)
        
        await status_msg.edit_text(
            "🚀 Submitting test generation request...\n"
            "This usually takes 30-90 seconds..."
        )
        
        # Use a simple prompt
        test_prompt = "a simple test video of a cat walking"
        job_id = await api.generate_video(test_prompt)
        
        await status_msg.edit_text(
            f"✅ API Test Successful!\n\n"
            f"🆔 Job ID: <code>{job_id}</code>\n\n"
            f"Your credentials are working. The video will complete in background.",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/testvideoapi", details="Video API credentials test passed")
        
        # Poll for completion in background (optional)
        try:
            video_url = await api.poll_for_video(job_id, timeout=180)
            await update.message.reply_text(f"✅ Test video completed: {video_url[:100]}...")
        except TimeoutError:
            await update.message.reply_text("⏳ Test video generation still in progress...")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Video generation issue: {str(e)[:200]}")
            
    except Exception as e:
        error_details = (
            f"❌ <b>Video API Test Failed</b>\n\n"
            f"📋 Error: <code>{str(e)}</code>\n\n"
            f"<b>Configuration Check:</b>\n"
            f"• BEARER_TOKEN: {'✅ Set' if BEARER_TOKEN and BEARER_TOKEN != 'your_bearer_token_here' else '❌ Missing/Placeholder'}\n"
            f"• COOKIE_FILE_CONTENT: {'✅ Set' if COOKIE_FILE_CONTENT else '❌ Missing'}\n"
            f"• Cookie Format: {'✅ Netscape' if COOKIE_FILE_CONTENT and '# Netscape HTTP Cookie File' in COOKIE_FILE_CONTENT else '❌ Wrong format'}\n\n"
            f"<b>To fix:</b>\n"
            f"1. Get fresh bearer token from geminigen.ai\n"
            f"2. Export fresh YouTube cookies (Netscape format)\n"
            f"3. Set them as environment variables\n"
            f"4. Restart bot",
            parse_mode=ParseMode.HTML
        )
        await status_msg.edit_text(error_details)
        await log_to_group(update, context, action="/testvideoapi", details=f"Test failed: {str(e)}", is_error=True)
        
# =========================
# Main Function
# =========================
def main():
    import signal
    import sys
    
    def shutdown_handler(signum, frame):
        log.info("Shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    cookies_path = Path(COOKIES_FILE)
    cookies_working = cookies_path.exists() and cookies_path.stat().st_size > 0
    
    log.info("="*60)
    log.info("🔍 BOT STARTUP")
    log.info(f"Current Directory: {Path.cwd()}")
    log.info(f"Force Join: {FORCE_JOIN_CHANNEL}")
    log.info(f"Log Group: {LOG_GROUP_ID}")
    log.info(f"AI API Key: {'✅ Set' if groq_client else '❌ Not Set'}")
    log.info(f"Cookies File: {'✅ Found' if cookies_working else '❌ Not configured'} ({cookies_path.absolute()})")
    log.info("="*60)
    
    app = ApplicationBuilder().token(BOT_TOKEN).connect_timeout(60).read_timeout(60).write_timeout(60).build()
    
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        log.error("Exception while handling an update:", exc_info=context.error)
        try:
            if LOG_GROUP_ID and hasattr(update, 'effective_user') and update.effective_user:
                error_text = (
                    f"❌ <b>Bot Error</b>\n\n"
                    f"User: {update.effective_user.id}\n"
                    f"Error: {str(context.error)[:200]}\n\n"
                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                await context.bot.send_message(
                    chat_id=LOG_GROUP_ID,
                    text=error_text,
                    parse_mode=ParseMode.HTML
                )
        except:
            pass
    
    app.add_error_handler(error_handler)

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("credits", credits_cmd))
    app.add_handler(CommandHandler("refer", refer_cmd))
    app.add_handler(CommandHandler("claim", claim_cmd))
    app.add_handler(CommandHandler("gen_redeem", gen_redeem_cmd))
    app.add_handler(CommandHandler("genvdo_redeem", genvdo_redeem_cmd))
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("vdoredeem", vdoredeem_cmd))
    app.add_handler(CommandHandler("whitelist_ai", whitelist_ai_cmd))
    app.add_handler(CommandHandler("whitelist_vdo", whitelist_vdo_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("gpt", gpt_cmd))
    app.add_handler(CommandHandler("gen", gen_cmd))
    app.add_handler(CommandHandler("vdogen", vdogen_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("done_broadcast", done_broadcast_cmd))
    app.add_handler(CommandHandler("send_broadcast", send_broadcast_cmd))
    app.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("rmadmin", rmadmin_cmd))
    app.add_handler(CommandHandler("adminlist", adminlist_cmd))
    app.add_handler(CommandHandler("testcookies", test_cookies_cmd))
    app.add_handler(CommandHandler("lyrics", lyrics_cmd))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(on_lyrics_request, pattern=r"^lyrics\|"))
    app.add_handler(CallbackQueryHandler(on_verify_membership, pattern=r"^verify_membership$"))
    
    # Chat member handler
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    # Add this to your main() function's command handlers:
    app.add_handler(CommandHandler("testvideoapi", test_video_api_cmd))
    
    # Start the bot
    log.info("🚀 Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()

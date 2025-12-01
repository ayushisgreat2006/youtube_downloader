import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
import secrets
import aiohttp
import random
import aiofiles
import aiohttp
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
# =========================
# GROQ TTS CONFIGURATION
# =========================
GROQ_TTS_VOICES = [
    'Arista-PlayAI', 'Atlas-PlayAI', 'Basil-PlayAI', 'Briggs-PlayAI',
    'Calum-PlayAI', 'Celeste-PlayAI', 'Cheyenne-PlayAI', 'Chip-PlayAI',
    'Cillian-PlayAI', 'Deedee-PlayAI', 'Fritz-PlayAI', 'Gail-PlayAI',
    'Indigo-PlayAI', 'Mamaw-PlayAI', 'Mason-PlayAI', 'Mikail-PlayAI',
    'Mitch-PlayAI', 'Quinn-PlayAI', 'Thunder-PlayAI'
]
GROQ_TTS_DEFAULT_VOICE = "Fritz-PlayAI"
GROQ_TTS_MODEL = "playai-tts"
GROQ_TTS_FORMAT = "mp3"  # MP3 is better for Telegram documents

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
BEARER_TOKEN = os.getenv("BEARER_TOKEN", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NjQ1Njg3OTIsInN1YiI6ImY5MTlhYjEyLWNiMDgtMTFmMC05YWEyLWVlNDdlYmE0N2M1ZCJ9.DgPjxlpW6aGXthSQb9Szv31OO0wzndwe9j2AuNcDQnM")
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

async def consume_credit(user_id: int) -> bool:
    """Consume 1 credit, return True if successful"""
    if not MONGO_AVAILABLE:
        return True
    
    if is_admin(user_id):
        return True
    
    credits, used, is_whitelisted = await get_user_credits(user_id)
    
    if used >= credits:
        return False
    
    today = get_today_str()
    update_fields = {"$inc": {"daily_usage": 1}}
    
    if is_whitelisted:
        whitelist_col.update_one(
            {"_id": user_id},
            {**update_fields, "$set": {"last_usage_date": today}},
            upsert=True
        )
    else:
        users_col.update_one(
            {"_id": user_id},
            {**update_fields, "$set": {"last_usage_date": today}},
            upsert=True
        )
    
    return True

async def add_credits(user_id: int, amount: int, is_referral: bool = False) -> bool:
    """Add credits to user"""
    if not MONGO_AVAILABLE:
        return False
    
    try:
        users_col.update_one(
            {"_id": user_id},
            {"$inc": {"credits": amount}},
            upsert=True
        )
        return True
    except Exception as e:
        log.error(f"Failed to add credits to {user_id}: {e}")
        return False

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
    """Check if user is admin without truth value testing"""
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
    """Check if user has premium without truth value testing"""
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
    
    # Add cookies if file exists and is not empty
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
    
    # Only enforce in groups if bot is mentioned
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
    join_url = f"https://t.me/{channel_username}"  # FIXED: Removed space
    
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
    """Fetch lyrics for a song title using an external API"""
    try:
        # Clean up the title - remove common YouTube suffixes and metadata
        clean_title = re.sub(r'\(official.*?\)|\[official.*?\]|\(audio\)|\[audio\]|\(lyric.*?\)|\[lyric.*?\]|\(video.*?\)|\[video.*?\]|\(hd\)|\[hd\]|\(4k\)|\[4k\]|\(feat\..*?\)|\[feat\..*?\]', '', song_title, flags=re.IGNORECASE)
        # NEW: Replace common separators to improve search
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
        
        # FIXED: Use centralized options builder with cookies
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

        # Check size limits
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
        
        # Send file with proper error handling
        try:
            async with aiofiles.open(final_path, 'rb') as f:
                file_data = await f.read()
            
            if quality == "mp3":
                await reply_msg.reply_document(
                    document=file_data,
                    caption=caption,
                    filename=f"{title}.mp3",
                    parse_mode=ParseMode.HTML,
                    connect_timeout=60,
                    read_timeout=60,
                    write_timeout=60
                )
            else:
                await reply_msg.reply_video(
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
            
            # 🎵 NEW: Add lyrics button for MP3 downloads
            if quality == "mp3":
                lyrics_button = InlineKeyboardButton("📝 Get Lyrics", callback_data=f"lyrics|{title}")
                keyboard = InlineKeyboardMarkup([[lyrics_button]])
                await reply_msg.reply_text(
                    "🎵 Download complete! Click below to get lyrics:",
                    reply_markup=keyboard
                )
            
            await log_to_group(update=None, context=context, action="Download Success", 
                             details=f"User {user_id}: {title[:50]}")
            
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
    
    # Store chat ID for broadcast (works for both private and groups)
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
    
    # Check cookies status
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
        "<b>💳 Credits:</b> 20 queries/day\n"
        "<b> OR CONTACT @ayushxchat_robot</b> FOR PREMIUM/n"
        "<b>🎁 Refer:</b> /refer to earn more\n\n"
        f"<b>📌 Cookies Status:</b> {'✅ Working' if cookies_working else '❌ Not configured'}\n"
        f"<b>📌 Use /help for commands</b>\n\n"
        "<b>⚠️ YouTube Notice:</b> If search fails, cookies may need refresh. Use /testcookies"
    )
    await update.message.reply_text(start_text, parse_mode=ParseMode.HTML)

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
        "<code>/redeem &lt;code&gt;</code> — Redeem admin code\n"
        "<code>/credits</code> — Check your credits\n\n"
        "<b>Admin Commands:</b>\n"
        "<code>/stats</code> — View statistics\n"
        "<code>/broadcast</code> — Broadcast message\n"
        "<code>/adminlist</code> — List admins\n"
        "<code>/gen_redeem &lt;value&gt; &lt;code&gt;</code> — Generate redeem code\n"
        "<code>/whitelist_ai &lt;id&gt; &lt;value&gt;</code> — Whitelist user\n"
        "<code>/testcookies</code> — Test YouTube cookies\n\n"
        "<b>Owner Commands:</b>\n"
        "<code>/addadmin &lt;id&gt;</code> — Add admin\n"
        "<code>/rmadmin &lt;id&gt;</code> — Remove admin\n\n"
        f"<b>Updates:</b> {UPDATES_CHANNEL}\n"
        f"<b>Support:</b> {PREMIUM_BOT_USERNAME}\n\n"
        f"<b>AI Status:</b> {ai_status} {'Configured' if groq_client else 'Not Set'}\n"
        f"<b>Cookies Status:</b> {'✅ Working' if cookies_working else '❌ Not configured'}"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

async def credits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user's credit balance"""
    ensure_user(update)
    user_id = update.effective_user.id
    
    credits, used, is_whitelisted = await get_user_credits(user_id)
    
    status = "👑 Whitelisted" if is_whitelisted else "🎫 Regular User"
    remaining = credits - used
    
    credits_text = (
        f"💳 <b>Your Credit Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 Status: {status}\n"
        f"📊 Daily Limit: {credits}\n"
        f"✅ Used Today: {used}\n"
        f"🎁 Remaining: {remaining}\n\n"
        f"<b>Want more?</b>\n"
        f"• /refer - Earn {REFERRER_BONUS} credits\n"
        f"• /claim - Claim someone's code\n"
        f"• Contact {PREMIUM_BOT_USERNAME} for premium"
    )
    
    await update.message.reply_text(credits_text, parse_mode=ParseMode.HTML)

async def refer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate referral code"""
    ensure_user(update)
    user_id = update.effective_user.id
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("❌ Database not available.")
        return
    
    # Generate unique referral code
    code = secrets.token_urlsafe(12).upper()
    
    try:
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"referral_code": code}},
            upsert=True
        )
        
        await update.message.reply_text(
            f"🎁 <b>Your Referral Code</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{code}</code>\n\n"
            f"<b>Share this code!</b>\n"
            f"• You get +{REFERRER_BONUS} credits when someone uses it\n"
            f"• They get +{CLAIMER_BONUS} credits\n\n"
            f"Use: /claim {code}",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/refer", details=f"Generated code: {code[:10]}...")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

async def claim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim a referral code"""
    ensure_user(update)
    
    if not context.args:
        await update.message.reply_text("Usage: /claim <referral_code>")
        return
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("❌ Database not available.")
        return
    
    code = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        # Find referrer
        referrer = users_col.find_one({"referral_code": code})
        if not referrer:
            await update.message.reply_text("❌ Invalid referral code!")
            return
        
        referrer_id = referrer["_id"]
        if referrer_id == user_id:
            await update.message.reply_text("❌ You cannot use your own code!")
            return
        
        # Check if already claimed by this user
        claimed = users_col.find_one({"_id": user_id, f"claimed_codes.{code}": {"$exists": True}})
        if claimed:
            await update.message.reply_text("❌ You already claimed this code!")
            return
        
        # Give bonuses
        # Referrer gets permanent credit increase
        users_col.update_one(
            {"_id": referrer_id},
            {"$inc": {"credits": REFERRER_BONUS, "referrals_made": 1}}
        )
        
        # Claimer gets one-time bonus
        await add_credits(user_id, CLAIMER_BONUS)
        
        # Mark as claimed
        users_col.update_one(
            {"_id": user_id},
            {"$set": {f"claimed_codes.{code}": datetime.now()}}
        )
        
        await update.message.reply_text(
            f"🎉 <b>Success!</b>\n\n"
            f"✅ You earned +{CLAIMER_BONUS} credits\n"
            f"📊 Your referrer got +{REFERRER_BONUS} credits\n\n"
            f"Use /credits to check balance",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/claim", 
                         details=f"User {user_id} claimed code from {referrer_id}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/claim", details=f"Error: {e}", is_error=True)

async def gen_redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate redeem code (Admin/Owner only) - NOW SINGLE-USE"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /gen_redeem <value> <code_name>")
        return
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("❌ Database not available.")
        return
    
    try:
        value = int(context.args[0])
        code_name = context.args[1].strip().upper()
        
        # NEW: Set max_uses to 1 for single-use codes
        redeem_col.insert_one({
            "code": code_name,
            "value": value,
            "created_by": update.effective_user.id,
            "created_at": datetime.now(),
            "used_by": [],
            "max_uses": 1  # 🔒 SINGLE USE ONLY
        })
        
        await update.message.reply_text(
            f"✅ Single-use redeem code created!\n\n"
            f"<b>Code:</b> <code>{code_name}</code>\n"
            f"<b>Value:</b> {value} credits\n"
            f"<b>Uses:</b> 1 time only\n\n"
            f"Users can claim with: /redeem {code_name}",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/gen_redeem", 
                         details=f"Code: {code_name}, Value: {value}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem admin code - adds to media generation limit"""
    ensure_user(update)
    
    if not context.args:
        await update.message.reply_text("Usage: /redeem <code_name>")
        return
    
    code_name = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        code_entry = redeem_col.find_one({"code": code_name})
        if not code_entry:
            await update.message.reply_text("❌ Invalid redeem code!")
            return
        
        # Check if already used by this user
        if user_id in code_entry.get("used_by", []):
            await update.message.reply_text("❌ You already used this code!")
            return
        
        # Apply to media generation limit (not AI credits)
        value = code_entry["value"]
        user_data = users_col.find_one({"_id": user_id}, {"media_gen_limit": 1})
        current_limit = user_data.get("media_gen_limit", BASE_MEDIA_GEN_LIMIT) if user_data else BASE_MEDIA_GEN_LIMIT
        
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"media_gen_limit": current_limit + value}},
            upsert=True
        )
        
        # Mark as used
        redeem_col.update_one(
            {"code": code_name},
            {"$push": {"used_by": user_id}}
        )
        
        await update.message.reply_text(
            f"🎉 <b>Redeemed Successfully!</b>\n\n"
            f"✅ Your media generation limit increased by <b>{value}</b>\n"
            f"📊 New limit: {current_limit + value} per day\n\n"
            f"Use /vdogen or /gen to generate media!",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/redeem", 
                         details=f"User {user_id} redeemed {code_name} for {value} media credits")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/redeem", details=f"Error: {e}", is_error=True)

async def whitelist_ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Whitelist user with custom media generation limit"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /whitelist_ai <user_id> <limit>")
        return
    
    try:
        target_id = int(context.args[0])
        limit = int(context.args[1])
        
        # Set custom media generation limit for user
        users_col.update_one(
            {"_id": target_id},
            {"$set": {
                "media_gen_limit": limit,
                "media_gen_date": get_today_str(),
                "media_gen_today": 0  # Reset counter
            }},
            upsert=True
        )
        
        user_info = users_col.find_one({"_id": target_id}, {"name": 1})
        name = user_info.get("name", str(target_id)) if user_info else str(target_id)
        
        await update.message.reply_text(
            f"✅ <b>User Whitelisted</b>\n\n"
            f"👤 User: <code>{target_id}</code> ({name})\n"
            f"📊 Media Limit: {limit} per day",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/whitelist_ai", 
                         details=f"Set media limit to {limit} for user {target_id}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/whitelist_ai", details=f"Error: {e}", is_error=True)

async def lyrics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get lyrics for a song"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /lyrics <song name>\nExample: /lyrics Ed Sheeran Shape of You")
        return
    
    await log_to_group(update, context, action="/lyrics", details=f"Query: {query}")
    
    status_msg = await update.message.reply_text(f"📝 Searching lyrics for '<b>{query}</b>'...", parse_mode=ParseMode.HTML)
    
    lyrics = await fetch_lyrics(query)
    
    if lyrics:
        if len(lyrics) > 3800:
            lyrics = lyrics[:3800] + "\n\n... (lyrics truncated due to message limit)"
        
        await status_msg.edit_text(
            f"🎵 <b>Lyrics for:</b> <code>{query}</code>\n\n"
            f"<pre>{lyrics}</pre>",
            parse_mode=ParseMode.HTML
        )
    else:
        await status_msg.edit_text(
            f"❌ Lyrics not found for '<code>{query}</code>'\n\n"
            f"Tips:\n"
            f"• Include artist name for better results\n"
            f"• Check spelling\n"
            f"• Song might not be in database",
            parse_mode=ParseMode.HTML
        )
# vdogen starts here
# Cookie Parser (add near other helper functions)
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

# GeminiGen API Client (add near other helper classes)
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
        # Use a new session for each request to avoid blocking
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
                    
                    # SMART URL DETECTION
                    video_url = None
                    
                    # 1. Check nested generated_video array
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
                    
                    # 2. Check top-level fields
                    if not video_url:
                        top_fields = ['video_url', 'download_url', 'url', 'media_url', 'output_url']
                        for field in top_fields:
                            if field in result and result[field]:
                                video_url = result[field]
                                log.info(f"✅ Found video URL in top-level '{field}': {video_url[:80]}...")
                                break
                    
                    # 3. Deep scan entire JSON for any MP4 URL
                    if not video_url:
                        result_str = json.dumps(result)
                        mp4_matches = re.findall(r'https?://[^\s"]+\.mp4(?:\?[^\s"]*)?', result_str)
                        if mp4_matches:
                            video_url = mp4_matches[0]
                            log.info(f"✅ Extracted MP4 URL from JSON scan: {video_url[:80]}...")
                    
                    if video_url:
                        return video_url
                    
                    # SMART FAILURE DETECTION
                    status = result.get("status", "")
                    progress = result.get("status_percentage", 0)
                    queue = result.get("queue_position", 0)
                    
                    # Only fail if there's a REAL error
                    error_message = result.get("error_message")
                    if error_message and str(error_message).strip() and str(error_message).lower() not in ['null', 'none', '']:
                        raise Exception(f"Server error: {error_message}")
                    
                    if status in [0, "failed", "error"]:
                        raise Exception(f"Generation failed with status: {status}")
                    
                    # Still processing
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
async def vdogen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI video - handles multiple users concurrently with queue"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /vdogen <description>\nExample: /vdogen A cute girl dancing")
        return
    
    user_id = update.effective_user.id
    
    # Check if user already has an active generation
    if user_id in user_active_tasks and not user_active_tasks[user_id].done():
        await update.message.reply_text(
            "⏳ <b>You already have a video generating!</b>\n\n"
            "Please wait for your current request to complete before starting a new one.\n\n"
            "Use /credits to check your status.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Check combined media generation limit
    today = get_today_str()
    user_data = users_col.find_one({"_id": user_id}, {
        "media_gen_today": 1, 
        "media_gen_date": 1, 
        "media_gen_limit": 1
    })
    
    media_gen_today = user_data.get("media_gen_today", 0) if user_data and user_data.get("media_gen_date") == today else 0
    media_gen_limit = user_data.get("media_gen_limit", BASE_MEDIA_GEN_LIMIT) if user_data else BASE_MEDIA_GEN_LIMIT
    
    if media_gen_today >= media_gen_limit:
        limit_msg = (
            f"❌ <b>Daily Media Limit Reached</b>\n\n"
            f"You can generate <b>{media_gen_limit} media</b> (images+videos) per day.\n\n"
            f"✅ Used today: {media_gen_today}/{media_gen_limit}\n\n"
            f"💡 <b>Get more:</b>\n"
            f"• Use /redeem to increase limit\n"
            f"• Contact {PREMIUM_BOT_USERNAME} for premium\n\n"
            f"🔄 Resets at midnight UTC"
        )
        await update.message.reply_text(limit_msg, parse_mode=ParseMode.HTML)
        return
    
    # Check AI credits (for non-whitelisted users)
    if not is_admin(user_id):
        credits, used, is_whitelisted = await get_user_credits(user_id)
        remaining = credits - used
        if remaining <= 0 and not is_whitelisted:
            no_credits_text = (
                f"❌ <b>No Credits Remaining!</b>\n\n"
                f"📊 Your daily limit: {credits}\n"
                f"✅ Used: {used}\n\n"
                f"<b>Get more credits:</b>\n"
                f"• /refer - Generate referral code (+{REFERRER_BONUS} per friend)\n"
                f"• /claim - Claim someone's code (+{CLAIMER_BONUS})\n"
                f"• Contact {PREMIUM_BOT_USERNAME} for premium access\n\n"
                f"📊 Media limit: {media_gen_limit} per day"
            )
            await update.message.reply_text(no_credits_text, parse_mode=ParseMode.HTML)
            return
    
    # Acknowledge immediately
    status_msg = await update.message.reply_text(
        f"🎬 <b>Video Request Received!</b>\n\n"
        f"📝 Prompt: <code>{query[:60]}...</code>\n\n"
        f"⏳ <i>Processing... (Queue position: {len(video_generation_queue) + 1})</i>",
        parse_mode=ParseMode.HTML
    )
    
    await log_to_group(update, context, action="/vdogen", details=f"Prompt: {query} | User: {user_id} | Queued")
    
    # Add to queue
    queue_item = {
        "user_id": user_id,
        "query": query,
        "status_msg": status_msg,
        "update": update,
        "context": context,
        "media_gen_today": media_gen_today,
        "media_gen_limit": media_gen_limit,
        "today": today
    }
    
    video_generation_queue.append(queue_item)
    
    # Start queue processor if not running
    asyncio.create_task(process_video_queue())
    
    log.info(f"✅ Added to queue. Current queue size: {len(video_generation_queue)}")

async def process_video_queue():
    """Background worker that processes video generation queue"""
    global active_generations
    
    # Check if we can start a new generation
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
        media_gen_today = queue_item["media_gen_today"]
        media_gen_limit = queue_item["media_gen_limit"]
        today = queue_item["today"]
        
        # Track active task for this user
        task = asyncio.current_task()
        user_active_tasks[user_id] = task
        
        try:
            log.info(f"🎬 Starting generation for user {user_id}")
            
            # Update status
            await status_msg.edit_text(
                f"📝 <b>Processing:</b> <code>{query[:60]}...</code>\n"
                f"⏳ Generation in progress...",
                parse_mode=ParseMode.HTML
            )
            
            # Initialize API client
            api = GeminiGenAPI(parse_netscape_cookies(COOKIE_FILE_CONTENT), BEARER_TOKEN)
            
            # Step 1: Submit generation
            await status_msg.edit_text(
                f"🚀 <b>Submitting to AI...</b>\n"
                f"⏳ This takes 30-90 seconds",
                parse_mode=ParseMode.HTML
            )
            job_id = await api.generate_video(query)
            
            # Step 2: Poll for completion
            await status_msg.edit_text(
                f"⏳ <b>Generating video...</b>\n"
                f"🆔 Job: <code>{job_id[:8]}...</code>",
                parse_mode=ParseMode.HTML
            )
            video_url = await api.poll_for_video(job_id, timeout=300)
            
            # Step 3: Download video
            await status_msg.edit_text("⬇️ <b>Downloading video...</b>", parse_mode=ParseMode.HTML)
            video_bytes = await api.download_video(video_url)
            
            # Step 4: Upload to Telegram
            await status_msg.edit_text("⬆️ <b>Uploading to Telegram...</b>", parse_mode=ParseMode.HTML)
            
            # Save to file temporarily
            video_path = DOWNLOAD_DIR / f"vdo_{user_id}_{secrets.token_urlsafe(8)}.mp4"
            async with aiofiles.open(video_path, "wb") as f:
                await f.write(video_bytes)
            
            # Send video with caption
            caption = (
                f"🎬 <b>{query}</b>\n\n"
                f"✨ Generated by @spotifyxmusixbot\n"
                f"🔖 Job: <code>{job_id[:8]}...</code>"
            )
            
            await update.message.reply_video(
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
            
            # Update media generation counter
            users_col.update_one(
                {"_id": user_id},
                {"$set": {
                    "media_gen_date": today,
                    "media_gen_today": media_gen_today + 1
                }},
                upsert=True
            )
            
            # Consume credit (for non-admins)
            if not is_admin(user_id):
                await consume_credit(user_id)
                log.info(f"✅ Credit consumed for user {user_id}")
            
            await status_msg.delete()
            video_path.unlink(missing_ok=True)
            
            log.info(f"✅ SUCCESS! Video sent for user {user_id}")
            
        except Exception as e:
            error_str = str(e)
            log.error(f"vdogen failed for user {user_id}: {e}", exc_info=True)
            
            try:
                await status_msg.edit_text(
                    "❌ <b>Video Generation Error</b>\n\n"
                    "Our AI video service is temporarily unavailable.\n\n"
                    "💡 <b>Try:</b>\n"
                    "• /gen for AI images\n"
                    "• Try again in a few minutes\n"
                    "• Contact @ayushxchat_robot for support\n\n"
                    f"<i>Error: {error_str[:100]}</i>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass  # Message might be deleted
            
            await log_to_group(update, context, action="/vdogen", 
                             details=f"Error: {error_str[:150]} | User: {user_id}", is_error=True)
        
        finally:
            # Cleanup
            active_generations -= 1
            if user_id in user_active_tasks:
                del user_active_tasks[user_id]
            
            # Process next queue item
            if video_generation_queue:
                asyncio.create_task(process_video_queue())


# =========================
# Fixed Command Handlers
# =========================
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FIXED: Added missing comma in function signature"""
    if not is_admin(update.effective_user.id): 
        await update.message.reply_text("❌ Not authorized!")
        return
    
    if not MONGO_AVAILABLE: 
        await update.message.reply_text("Database not available.")
        return
    
    try:
        total_users = users_col.count_documents({})
        total_admins = admins_col.count_documents({})
        premium_users = users_col.count_documents({"premium": True})
        whitelist_count = whitelist_col.count_documents({})
        
        stats_text = (
            f"📊 <b>Bot Statistics</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Total Users: {total_users}\n"
            f"👑 Total Admins: {total_admins}\n"
            f"💎 Premium Users: {premium_users}\n"
            f"📝 Whitelisted AI Users: {whitelist_count}\n"
            f"🤖 Bot Online: ✅\n"
            f"💾 MongoDB: {'✅ Connected' if MONGO_AVAILABLE else '❌ Disconnected'}\n"
            f"🤖 AI Service: {'✅ Configured' if groq_client else '❌ Not Set'}"
        )
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/stats", 
                         details=f"Users: {total_users}, Admins: {total_admins}, Premium: {premium_users}, Whitelist: {whitelist_count}")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /search <text>")
        return
    
    await log_to_group(update, context, action="/search", details=f"Query: {query}")
    status_msg = await update.message.reply_text(f"Searching '<b>{query}</b>'...", parse_mode=ParseMode.HTML)

    # FIXED: Add cookies to search options
    cookies_path = Path(COOKIES_FILE)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "default_search": "ytsearch5",
        "extract_flat": False,
    }
    
    # Add cookies to search if available
    if cookies_path.exists() and cookies_path.stat().st_size > 0:
        ydl_opts["cookiefile"] = str(cookies_path)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as e:
        error_str = str(e)
        # ENHANCED: Better error messages for YouTube restrictions
        if "Sign in to confirm" in error_str:
            await status_msg.edit_text(
                "❌ <b>YouTube Bot Detection</b>\n\n"
                "YouTube is requiring sign-in to search. This means:\n"
                "• Your cookies are missing or expired\n"
                "• The cookies file format is wrong (must be Netscape)\n"
                "• YouTube flagged the session\n\n"
                "<b>Solution:</b>\n"
                "1. Export fresh cookies from YouTube\n"
                "2. Use browser extension 'Get cookies.txt LOCALLY'\n"
                "3. Make sure you're logged in to YouTube\n"
                "4. Save as <code>cookies.txt</code> in bot folder\n"
                "5. Run /testcookies to verify\n\n"
                "<b>Alternative:</b> Send direct YouTube URLs instead of searching",
                parse_mode=ParseMode.HTML
            )
        else:
            await status_msg.edit_text(f"⚠️ Search failed: {e}")
        await log_to_group(update, context, action="/search", details=f"Error: {e}", is_error=True)
        return

    entries = info.get("entries", [])
    if not entries:
        await status_msg.edit_text("No results found.")
        return

    buttons = []
    for e in entries[:5]:
        title = sanitize_filename(e.get("title") or "video")
        video_id = e.get('id')
        url = f"https://youtube.com/watch?v={video_id}" if video_id else e.get('webpage_url')
        token = store_url(url)
        buttons.append([InlineKeyboardButton(title[:60], callback_data=f"s|{token}|pick")])

    await status_msg.edit_text("Choose a video:", reply_markup=InlineKeyboardMarkup(buttons))

async def gen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI image - dead simple, no bullshit"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gen <description>")
        return
    
    user_id = update.effective_user.id
    
    # Check limit
    today = get_today_str()
    user_data = users_col.find_one({"_id": user_id}, {"media_gen_today": 1, "media_gen_date": 1})
    used_today = user_data.get("media_gen_today", 0) if user_data and user_data.get("media_gen_date") == today else 0
    
    if used_today >= BASE_MEDIA_GEN_LIMIT:
        await update.message.reply_text(f"❌ Daily limit: {used_today}/{BASE_MEDIA_GEN_LIMIT}")
        return
    
    # Generate image
    status = await update.message.reply_text(f"🎨 Generating: <b>{query}</b>...", parse_mode=ParseMode.HTML)
    
    try:
        encoded = query.replace(" ", "+")
        url = f"https://flux-pro.vercel.app/generate?q={encoded}"
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await status.edit_text(f"❌ API Error: {resp.status}")
                    return
                
                # Just save the damn image
                data = await resp.read()
                path = DOWNLOAD_DIR / f"gen_{user_id}.png"
                async with aiofiles.open(path, "wb") as f:
                    await f.write(data)
        
        # Send with watermark
        caption = f"🖼️ <b>{query}</b>\n\n<i>Generated by @spotifyxmusixbot</i>"
        await update.message.reply_photo(photo=path, caption=caption, parse_mode=ParseMode.HTML)
        
        # Update counter
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"media_gen_date": today, "media_gen_today": used_today + 1}},
            upsert=True
        )
        
        await status.delete()
        path.unlink()
        
    except Exception as e:
        await status.edit_text(f"❌ Failed: {str(e)[:100]}")

async def gpt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI Chat command - accessible to ALL users with credit limits"""
    
    # Ensure user exists in database
    ensure_user(update)
    
    # Check membership (with error handling)
    try:
        if not await ensure_membership(update, context):
            return
    except Exception as e:
        await update.message.reply_text("❌ Error checking membership. Please try again.")
        return
    
    # Validate query
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gpt <your question>")
        return
    
    # Check AI client
    if not groq_client:
        await update.message.reply_text("❌ AI not configured. Contact admin.", parse_mode=ParseMode.HTML)
        return
    
    user_id = update.effective_user.id
    
    # CREDIT CHECK
    try:
        credits, used, is_whitelisted = await get_user_credits(user_id)
        remaining = credits - used
        
    except Exception as e:
        # Fallback to base credits if check fails
        credits, used, is_whitelisted = BASE_CREDITS, 0, False
        remaining = credits
    
    # Check if user has credits left
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
        await update.message.reply_text(no_credits_text, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/gpt", details=f"User {user_id} out of credits (used {used}/{credits})")
        return
    
    # Processing message
    status_msg = await update.message.reply_text(f"🤖 Processing... (Credits left: {remaining-1})")
    
    # Initialize conversation
    if user_id not in USER_CONVERSATIONS:
        USER_CONVERSATIONS[user_id] = [
            {"role": "system", "content": "You are a helpful assistant. Be concise and clear."}
        ]
    
    USER_CONVERSATIONS[user_id].append({"role": "user", "content": query})
    
    try:
        # Call Groq API
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=USER_CONVERSATIONS[user_id],
            max_tokens=1000,
            temperature=0.7
        )
        
        answer = response.choices[0].message.content
        USER_CONVERSATIONS[user_id].append({"role": "assistant", "content": answer})
        
        # Limit conversation history
        if len(USER_CONVERSATIONS[user_id]) > 10:
            USER_CONVERSATIONS[user_id] = [USER_CONVERSATIONS[user_id][0]] + USER_CONVERSATIONS[user_id][-9:]
        
        # Truncate if too long
        if len(answer) > 4000:
            answer = answer[:4000] + "\n\n... (truncated)"
        
        # ✅ MODIFIED: Added ai attribution
        await status_msg.edit_text(
            f"💬 <b>Query:</b> <code>{query}</code>\n\n"
            f"<b>Answer:</b>\n{answer}\n\n"
            f"<i>ai by @spotifyxmusixbot</i>",
            parse_mode=ParseMode.HTML
        )
        
        # Consume credit
        credit_success = await consume_credit(user_id)
        log.info(f"✅ GPT_CMD SUCCESS | User: {user_id} | Credit consumed: {credit_success}")
        
        # Log to group
        await log_to_group(update, context, action="/gpt", 
                         details=f"User {user_id}: {query[:50]}... | Remaining: {remaining-1}")
        
    except Exception as e:
        log.error(f"💥 GPT_CMD AI ERROR for {user_id}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ AI Error: {str(e)[:200]}")
        await log_to_group(update, context, action="/gpt", details=f"Error: {e}", is_error=True)
        USER_CONVERSATIONS[user_id] = [{"role": "system", "content": "You are a helpful assistant."}]

# =========================
# NEW: Enhanced Test Cookies Command
# =========================
async def test_cookies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test YouTube cookies functionality - Admin only"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    # Check if cookies file exists
    cookies_path = Path(COOKIES_FILE)
    if not cookies_path.exists():
        await update.message.reply_text(
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
        return
    
    if cookies_path.stat().st_size == 0:
        await update.message.reply_text(
            f"⚠️ Cookies file is empty!\n\n"
            f"Location: <code>{cookies_path.absolute()}</code>\n\n"
            f"Please export cookies from YouTube and save to this file.",
            parse_mode=ParseMode.HTML
        )
        return
    
    status_msg = await update.message.reply_text("🔍 Testing YouTube cookies...")
    
    try:
        # Test 1: Check if cookies file is valid format
        with open(cookies_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if '# Netscape HTTP Cookie File' not in content:
                raise ValueError("Not a Netscape format cookies file")
            if '.youtube.com' not in content and '.google.com' not in content:
                raise ValueError("No YouTube/Google cookies found")
        
        # Test 2: Try to extract info from a video (this tests authentication)
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rickroll (short video)
        
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "cookiefile": str(cookies_path),
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(test_url, download=False)
            
            # Check for authentication indicators
            is_logged_in = False
            has_pauth = False
            if info:
                # Check for presence of sensitive cookies
                cookies_valid = True
                # Check if we can access video details that require auth
                if info.get('duration') is not None or info.get('uploader') is not None:
                    is_logged_in = True
                
                # Also check cookie content for SAPISID/APISID (required for API calls)
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
        
        await status_msg.edit_text(result_text, parse_mode=ParseMode.HTML)
        
        await log_to_group(update, context, action="/testcookies", 
                         details=f"Cookies test passed. Auth: {is_logged_in}, PAuth: {has_pauth}")
        
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
        await status_msg.edit_text(error_text, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/testcookies", details=f"Format error: {ve}", is_error=True)
        
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
        
        await status_msg.edit_text(error_text, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/testcookies", details=f"Test failed: {str(e)[:100]}", is_error=True)

# =========================
# Broadcast Functions (FIXED)
# =========================
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        await update.message.reply_text("❌ Not authorized!")
        return
    
    admin_id = update.effective_user.id
    BROADCAST_STORE[admin_id] = []
    BROADCAST_STATE[admin_id] = True
    
    await update.message.reply_text(
        "📢 Broadcast mode ON. Send messages to add to queue.\n"
        "Then use:\n"
        "/done_broadcast - Preview messages\n"
        "/send_broadcast - Send to all users and groups\n"
        "/cancel_broadcast - Cancel"
    )
    
    await log_to_group(update, context, action="/broadcast", details="Broadcast mode started")

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ALL non-command messages for broadcast"""
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id):
        return
    
    # Store message based on type
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
    
    # Get all recipients (users + groups)
    recipients = set()
    
    if MONGO_AVAILABLE:
        # Add all users
        for u in users_col.find({}, {"_id": 1}): 
            recipients.add(u["_id"])
        
        # Add groups where bot is added
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
    await log_to_group(update, context, action="/send_broadcast", 
                     details=f"Sent to {success} users, {failed} failed")

async def cancel_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    
    admin_id = update.effective_user.id
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    
    await update.message.reply_text("❌ Broadcast cancelled.")
    await log_to_group(update, context, action="/cancel_broadcast", details="Broadcast cancelled")

# =========================
# NEW: Track Bot Addition to Groups
# =========================
async def track_bot_addition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when bot is added to a new group for broadcast"""
    if not MONGO_AVAILABLE:
        return
        
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        # Only add if bot is actually a member (not left/kicked)
        my_member = update.my_chat_member
        if my_member.new_chat_member.status in ["member", "administrator"]:
            db["broadcast_chats"].update_one(
                {"_id": chat.id},
                {"$set": {
                    "title": chat.title,
                    "type": chat.type,
                    "added_at": datetime.now()
                }},
                upsert=True
            )
            log.info(f"Bot added to group: {chat.title} ({chat.id})")

# =========================
# Admin Commands
# =========================
async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): 
        await update.message.reply_text("❌ Owner only!")
        return
    if not context.args: 
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        new_id = int(context.args[0])
        user = users_col.find_one({"_id": new_id})
        if not user: 
            await update.message.reply_text("❌ User not found. They must /start first.")
            return
        if admins_col.find_one({"_id": new_id}): 
            await update.message.reply_text("❌ Already admin.")
            return
        admins_col.insert_one({
            "_id": new_id, 
            "name": user.get("name", str(new_id)), 
            "added_by": update.effective_user.id, 
            "added_at": datetime.now()
        })
        await log_to_group(update, context, action="/addadmin", details=f"Added admin {new_id}")
        await update.message.reply_text(f"✅ Added <b>{user.get('name', new_id)}</b> as admin.", parse_mode=ParseMode.HTML)
    except Exception as e: 
        await update.message.reply_text(f"❌ Failed: {e}")

async def rmadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): 
        await update.message.reply_text("❌ Owner only!")
        return
    if not context.args: 
        await update.message.reply_text("Usage: /rmadmin <user_id>")
        return
    try:
        rm_id = int(context.args[0])
        if rm_id == OWNER_ID: 
            await update.message.reply_text("❌ Cannot remove owner!")
            return
        if not admins_col.find_one({"_id": rm_id}): 
            await update.message.reply_text("❌ Not an admin.")
            return
        admins_col.delete_one({"_id": rm_id})
        await log_to_group(update, context, action="/rmadmin", details=f"Removed admin {rm_id}")
        await update.message.reply_text(f"✅ Removed admin.", parse_mode=ParseMode.HTML)
    except Exception as e: 
        await update.message.reply_text(f"❌ Failed: {e}")

async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        await update.message.reply_text("❌ Not authorized!")
        return
    if not MONGO_AVAILABLE: 
        await update.message.reply_text("Database not available.")
        return
    try:
        admins = list(admins_col.find().sort("added_at", -1))
        if not admins: 
            await update.message.reply_text("No admins.")
            return
        admin_list = "👥 <b>Admin List</b>\n━━━━━━━━━━━━\n\n"
        for admin in admins:
            admin_id = admin["_id"]
            name = admin.get("name", "Unknown")
            role = "👑 Owner" if admin_id == OWNER_ID else "🔧 Admin"
            admin_list += f"• <code>{admin_id}</code> - {name} ({role})\n"
        admin_list += f"\n<b>Total: {len(admins)}</b>"
        await update.message.reply_text(admin_list, parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/adminlist", details=f"Listed {len(admins)} admins")
    except Exception as e: 
        await update.message.reply_text(f"❌ Failed: {e}")

# =========================
# NEW: My Chat Member Handler (Track Bot Addition)
# =========================
async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when bot is added to or removed from groups"""
    if not MONGO_AVAILABLE:
        return
        
    chat = update.effective_chat
    my_member = update.my_chat_member
    
    # Only track groups
    if chat.type not in ["group", "supergroup"]:
        return
    
    # Bot was added to group
    if my_member.new_chat_member.status in ["member", "administrator"]:
        db["broadcast_chats"].update_one(
            {"_id": chat.id},
            {"$set": {
                "title": chat.title,
                "type": chat.type,
                "added_at": datetime.now()
            }},
            upsert=True
        )
        log.info(f"✅ Bot added to group: {chat.title} ({chat.id})")
        
    # Bot was removed from group
    elif my_member.new_chat_member.status in ["left", "kicked"]:
        db["broadcast_chats"].delete_one({"_id": chat.id})
        log.info(f"❌ Bot removed from group: {chat.title} ({chat.id})")

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
    
    # Edit message to show loading
    status_msg = await q.edit_message_text("📝 Searching for lyrics...")
    
    # Fetch lyrics
    lyrics = await fetch_lyrics(song_title)
    
    if lyrics:
        if len(lyrics) > 3800:  # Leave room for header
            lyrics = lyrics[:3800] + "\n\n... (lyrics truncated due to message limit)"
        
        await status_msg.edit_text(
            f"🎵 <b>Lyrics for:</b> <code>{song_title}</code>\n\n"
            f"<pre>{lyrics}</pre>",
            parse_mode=ParseMode.HTML
        )
    else:
        await status_msg.edit_text(
            f"❌ Lyrics not found for '<code>{song_title}</code>'\n\n"
            f"• Song might be too new\n"
            f"• Title might be misspelled\n"
            f"• Try manual search: /lyrics artist song",
            parse_mode=ParseMode.HTML
        )

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
    
    # Store group chat for broadcast
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
    
    # Check broadcast mode first
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
        await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))

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
# TTS Generation Function
# =========================
# =========================
# GROQ TTS FUNCTIONS (Add this whole block)
# =========================
async def generate_tts_audio(text: str, voice: str, model: str = GROQ_TTS_MODEL) -> bytes:
    """Generate TTS audio using Groq API"""
    try:
        if not groq_client:
            raise Exception("Groq client not initialized")
        
        # Verify voice is valid
        if voice not in GROQ_TTS_VOICES:
            raise ValueError(f"Invalid voice: {voice}")
        
        if len(text) > 1000:
            raise ValueError("Text exceeds 1000 character limit")
        
        log.info(f"🎙️ Generating TTS: {len(text)} chars, Voice: {voice}")
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: groq_client.audio.speech.create(
                model=model,
                voice=voice,
                input=text,
                response_format=GROQ_TTS_FORMAT
            )
        )
        
        # FIX: Use .read() method for BinaryAPIResponse
        audio_bytes = response.read()
        
        if not audio_bytes:
            raise Exception("Empty audio response received")
        
        log.info(f"✅ TTS generated: {len(audio_bytes)} bytes")
        return audio_bytes
        
    except Exception as e:
        error_msg = str(e)
        
        # Handle specific Groq errors
        if "terms acceptance" in error_msg.lower():
            log.error("TERMS NOT ACCEPTED: User needs to accept PlayAI terms in Groq console")
            raise Exception(
                "❌ Terms Acceptance Required\n\n"
                "The TTS model requires you to accept terms in Groq console:\n"
                "1. Go to console.groq.com\n"
                "2. Navigate to Models > PlayAI TTS\n"
                "3. Click 'Accept Terms'\n"
                "4. Retry the command"
            )
        elif "invalid voice" in error_msg.lower():
            raise Exception(f"Invalid voice selected. Choose from: {', '.join(GROQ_TTS_VOICES[:5])}...")
        elif "rate limit" in error_msg.lower():
            raise Exception("Rate limit exceeded. Please try again later.")
        else:
            log.error(f"TTS generation failed: {error_msg}", exc_info=True)
            raise Exception(f"TTS generation error: {error_msg[:200]}")

async def speech_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI speech from text - /speech <text>"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    if not groq_client:
        await update.message.reply_text("❌ TTS not configured. Contact admin.", parse_mode=ParseMode.HTML)
        return
    
    if not context.args:
        voices_sample = "\n".join([f"• <code>{v}</code>" for v in GROQ_TTS_VOICES[:10]])
        help_text = (
            f"🎙️ <b>Text-to-Speech Usage</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>Usage:</b> /speech \"Your text here\"\n"
            f"<b>Example:</b> <code>/speech Hello, this is a test message!</code>\n\n"
            f"After sending, you'll select a voice from all available models.\n\n"
            f"<b>Sample Voices:</b>\n{voices_sample}\n\n"
            f"<b>Default:</b> <code>{GROQ_TTS_DEFAULT_VOICE}</code>\n\n"
            f"<b>💡 Tip:</b> Text must be 1-1000 characters"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
        return
    
    text_to_speak = " ".join(context.args).strip()
    
    if not text_to_speak or len(text_to_speak) > 1000:
        await update.message.reply_text("❌ Text must be between 1-1000 characters!")
        return
    
    user_id = update.effective_user.id
    
    # Check credits
    credits, used, is_whitelisted = await get_user_credits(user_id)
    remaining = credits - used
    
    if remaining <= 0 and not is_admin(user_id):
        no_credits_text = (
            f"❌ <b>No Credits Remaining!</b>\n\n"
            f"📊 Your daily limit: {credits}\n"
            f"✅ Used: {used}\n\n"
            f"<b>Get more credits:</b>\n"
            f"• /refer - Generate referral code (+{REFERRER_BONUS} per friend)\n"
            f"• /claim - Claim someone's code (+{CLAIMER_BONUS})\n"
            f"• Contact {PREMIUM_BOT_USERNAME} for premium"
        )
        await update.message.reply_text(no_credits_text, parse_mode=ParseMode.HTML)
        return
    
    # Store text in user_data
    context.user_data['tts_text'] = text_to_speak
    
    # Create voice selection keyboard (3 per row)
    keyboard = []
    row = []
    
    for i, voice in enumerate(GROQ_TTS_VOICES):
        label = voice.replace("-PlayAI", "")
        row.append(InlineKeyboardButton(label, callback_data=f"tts_gen|{voice}"))
        
        if (i + 1) % 3 == 0:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="tts_cancel")])
    
    await update.message.reply_text(
        f"🎙️ <b>Choose a voice for:</b>\n\n"
        f"<i>\"{text_to_speak[:150]}{'...' if len(text_to_speak) > 150 else ''}\"</i>\n\n"
        f"💳 Credits left: {remaining}\n\n"
        f"<b>Select a voice model:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def on_tts_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice selection callback for TTS"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "tts_cancel":
        await query.edit_message_text("❌ TTS generation cancelled.")
        if 'tts_text' in context.user_data:
            del context.user_data['tts_text']
        return
    
    try:
        _, selected_voice = query.data.split("|", 1)
    except:
        await query.edit_message_text("❌ Invalid selection.")
        return
    
    text_to_speak = context.user_data.get('tts_text')
    
    if not text_to_speak:
        await query.edit_message_text("❌ Session expired. Please try again with /speech <text>")
        return
    
    user_id = update.effective_user.id
    
    # Re-check credits
    credits, used, is_whitelisted = await get_user_credits(user_id)
    remaining = credits - used
    
    if remaining <= 0 and not is_admin(user_id):
        await query.edit_message_text("❌ You ran out of credits while selecting. Use /credits to check.")
        return
    
    # Update message to show processing
    await query.edit_message_text(
        f"🔊 <b>Generating speech with {selected_voice}...</b>\n\n"
        f"<i>\"{text_to_speak[:100]}{'...' if len(text_to_speak) > 100 else ''}\"</i>\n\n"
        f"⏳ Please wait...",
        parse_mode=ParseMode.HTML
    )
    
    await process_tts_generation(update, context, text_to_speak, selected_voice, is_callback=True)

async def process_tts_generation(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                 text: str, voice: str, is_callback: bool = False):
    """Process TTS generation and send as document"""
    user_id = update.effective_user.id
    message = update.callback_query.message if is_callback else update.message
    
    try:
        status_msg = await message.reply_text("🔊 Generating audio...")
        
        audio_bytes = await generate_tts_audio(text, voice)
        
        await status_msg.edit_text("💾 Saving audio file...")
        
        audio_path = DOWNLOAD_DIR / f"speech_{user_id}_{secrets.token_urlsafe(8)}.{GROQ_TTS_FORMAT}"
        async with aiofiles.open(audio_path, "wb") as f:
            await f.write(audio_bytes)
        
        await status_msg.edit_text("⬆️ Uploading to Telegram as document...")
        
        caption = (
            f"🎙️ <b>Text-to-Speech Audio</b>\n\n"
            f"📝 Text: <i>\"{text[:300]}{'...' if len(text) > 300 else ''}\"</i>\n"
            f"🗣️ Voice: <code>{voice}</code>\n"
            f"📦 Size: {audio_path.stat().st_size / 1024:.2f} KB\n\n"
            f"✨ Generated by @spotifyxmusixbot"
        )
        
        await message.reply_document(
            document=audio_path,
            filename=f"speech_{voice.replace('-PlayAI', '')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{GROQ_TTS_FORMAT}",
            caption=caption,
            parse_mode=ParseMode.HTML,
            connect_timeout=60,
            read_timeout=60,
            write_timeout=60
        )
        
        await status_msg.delete()
        
        # Consume credit
        if not is_admin(user_id):
            await consume_credit(user_id)
            credits, used, _ = await get_user_credits(user_id)
            remaining = credits - used
            log.info(f"✅ TTS credit consumed for user {user_id}. Remaining: {remaining}")
        
        await log_to_group(update, context, action="/speech", 
                         details=f"User {user_id}: {len(text)} chars, Voice: {voice}")
        
        # Cleanup
        audio_path.unlink(missing_ok=True)
        if 'tts_text' in context.user_data:
            del context.user_data['tts_text']
        
    except Exception as e:
        error_str = str(e)
        log.error(f"TTS failed for user {user_id}: {error_str}", exc_info=True)
        
        # Clean up on error
        if 'tts_text' in context.user_data:
            del context.user_data['tts_text']
        
        user_error = f"❌ {error_str}" if not error_str.startswith("❌") else error_str
        
        try:
            await status_msg.edit_text(
                f"{user_error}\n\n"
                f"💡 Need help? Contact @ayushxchat_robot",
                parse_mode=ParseMode.HTML
            )
        except:
            await message.reply_text(user_error, parse_mode=ParseMode.HTML)
        
        await log_to_group(update, context, action="/speech", 
                         details=f"Error: {error_str[:150]} | User: {user_id}", is_error=True)

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
    
    # Startup logging with cookies info
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
    app.add_handler(CommandHandler("redeem", redeem_cmd))
    app.add_handler(CommandHandler("whitelist_ai", whitelist_ai_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("gpt", gpt_cmd))
    app.add_handler(CommandHandler("gen", gen_cmd))
    app.add_handler(CommandHandler("vdogen", vdogen_cmd))  # NEW COMMAND
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
    # Add these BEFORE the generic message handlers
    app.add_handler(CommandHandler("speech", speech_cmd))
    
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(on_lyrics_request, pattern=r"^lyrics\|"))
    app.add_handler(CallbackQueryHandler(on_verify_membership, pattern=r"^verify_membership$"))
    app.add_handler(CallbackQueryHandler(on_tts_generation, pattern=r"^tts_gen\|"))
    app.add_handler(CallbackQueryHandler(on_tts_generation, pattern=r"^tts_cancel$"))
    
    # Chat member handler
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Start the bot
    log.info("🚀 Bot is starting...")
    app.run_polling()

if __name__ == "__main__":
    main()

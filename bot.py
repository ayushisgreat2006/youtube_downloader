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
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "7941244038"))
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "@tonystark_jr")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "@tonystark_jr")
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "-5066591546"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Cookies configuration for YouTube
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")

# MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "youtube_bot")
MONGO_USERS = os.getenv("MONGO_USERS", "users")
MONGO_ADMINS = os.getenv("MONGO_ADMINS", "admins")
MONGO_REDEEM = os.getenv("MONGO_REDEEM", "redeem_codes")
MONGO_WHITELIST = os.getenv("MONGO_WHITELIST", "whitelist")
MONGO_WHITELIST_VDO = os.getenv("MONGO_WHITELIST_VDO", "whitelist_vdo")
MONGO_REDEEM_VDO = os.getenv("MONGO_REDEEM_VDO", "redeem_codes_vdo")

# Credit System Constants
BASE_CREDITS = 20
REFERRER_BONUS = 20
CLAIMER_BONUS = 15
PREMIUM_BOT_USERNAME = "@ayushxchat_robot"

# Video Credit System Constants
VIDEO_BASE_CREDITS = 2  # Normal users get 2 video generations per day
VIDEO_ADMIN_CREDITS = 10000  # Admins/owners get unlimited (10k)

# File size limits
DOWNLOAD_DIR = Path("downloads")
MAX_FREE_SIZE = 50 * 1024 * 1024
PREMIUM_SIZE = 450 * 1024 * 1024
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)

# Combined Media Generation Limits (for images - separate from video credits)
BASE_MEDIA_GEN_LIMIT = 10
PROXY_LIST = []
PROXY_ROTATE_ON_FAILURE = False
VIDEO_MAX_ATTEMPTS = 1
IMAGE_MAX_ATTEMPTS = 1

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
MAX_CONCURRENT_GENERATIONS = 2
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
Path(COOKIES_FILE).touch(exist_ok=True)

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
    whitelist_vdo_col = db[MONGO_WHITELIST_VDO]
    redeem_vdo_col = db[MONGO_REDEEM_VDO]
    MONGO_AVAILABLE = True
    log.info("✅ MongoDB connected")
    
    # Create indexes
    users_col.create_index("referral_code", unique=True, sparse=True)
    redeem_col.create_index("code", unique=True)
    redeem_vdo_col.create_index("code", unique=True)
    
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
    mongo = db = users_col = admins_col = redeem_col = whitelist_col = whitelist_vdo_col = redeem_vdo_col = None

# =========================
# Credit System Functions
# =========================
def get_today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

async def get_user_credits(user_id: int) -> tuple[int, int, bool]:
    """Returns (ai_credits, used_today, is_whitelisted) for AI/gen commands"""
    if not MONGO_AVAILABLE:
        return BASE_CREDITS, 0, False
    
    if is_admin(user_id) or is_owner(user_id):
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
    """Consume 1 AI credit, return True if successful"""
    if not MONGO_AVAILABLE:
        return True
    
    if is_admin(user_id) or is_owner(user_id):
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
    """Add AI credits to user (permanent)"""
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
# Video Credit System Functions (NEW)
# =========================
async def get_user_video_credits(user_id: int) -> tuple[int, int, bool]:
    """Returns (video_credits, video_used_today, is_whitelisted_vdo) for /vdogen"""
    if not MONGO_AVAILABLE:
        return VIDEO_BASE_CREDITS, 0, False
    
    if is_admin(user_id) or is_owner(user_id):
        return VIDEO_ADMIN_CREDITS, 0, True
    
    today = get_today_str()
    
    # Check video whitelist first
    whitelist_entry = whitelist_vdo_col.find_one({"_id": user_id}) if whitelist_vdo_col is not None else None
    if whitelist_entry:
        limit = whitelist_entry.get("daily_limit", VIDEO_BASE_CREDITS)
        last_date = whitelist_entry.get("last_usage_date", today)
        used = whitelist_entry.get("daily_usage", 0) if last_date == today else 0
        return limit, used, True
    
    # Regular user
    user = users_col.find_one({"_id": user_id}, {"vdogen_credits": 1, "vdogen_daily_usage": 1, "vdogen_last_date": 1})
    if not user:
        return VIDEO_BASE_CREDITS, 0, False
    
    last_date = user.get("vdogen_last_date", today)
    if last_date != today:
        # Reset daily usage
        users_col.update_one(
            {"_id": user_id},
            {"$set": {"vdogen_daily_usage": 0, "vdogen_last_date": today}}
        )
        return user.get("vdogen_credits", VIDEO_BASE_CREDITS), 0, False
    
    return user.get("vdogen_credits", VIDEO_BASE_CREDITS), user.get("vdogen_daily_usage", 0), False

async def consume_video_credit(user_id: int) -> bool:
    """Consume 1 video credit, return True if successful"""
    if not MONGO_AVAILABLE:
        return True
    
    if is_admin(user_id) or is_owner(user_id):
        return True
    
    credits, used, is_whitelisted = await get_user_video_credits(user_id)
    
    if used >= credits:
        return False
    
    today = get_today_str()
    update_fields = {"$inc": {"vdogen_daily_usage": 1}}
    
    if is_whitelisted:
        whitelist_vdo_col.update_one(
            {"_id": user_id},
            {**update_fields, "$set": {"last_usage_date": today}},
            upsert=True
        )
    else:
        users_col.update_one(
            {"_id": user_id},
            {**update_fields, "$set": {"vdogen_last_date": today}},
            upsert=True
        )
    
    return True

async def add_video_credits(user_id: int, amount: int) -> bool:
    """Add permanent video credits to user"""
    if not MONGO_AVAILABLE:
        return False
    
    try:
        users_col.update_one(
            {"_id": user_id},
            {"$inc": {"vdogen_credits": amount}},
            upsert=True
        )
        return True
    except Exception as e:
        log.error(f"Failed to add video credits to {user_id}: {e}")
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
                    "vdogen_credits": VIDEO_BASE_CREDITS,
                    "vdogen_daily_usage": 0,
                    "vdogen_last_date": get_today_str(),
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
    """Logging to group (kept for backward compatibility)"""
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

async def forward_to_log(context: ContextTypes.DEFAULT_TYPE, message):
    """Forward any message to log group (NEW)"""
    if not LOG_GROUP_ID or not message:
        return
    try:
        await message.forward(chat_id=LOG_GROUP_ID)
        log.info(f"✅ Message forwarded to log group {LOG_GROUP_ID}")
    except Exception as e:
        log.error(f"❌ Failed to forward message: {e}")

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
    """Fetch lyrics for a song title using an external API"""
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
            
            # Forward to log group (NEW)
            await forward_to_log(context, sent_msg)
            
            await status_msg.delete()
            
            # Add lyrics button for MP3 downloads
            if quality == "mp3":
                lyrics_button = InlineKeyboardButton("📝 Get Lyrics", callback_data=f"lyrics|{title}")
                keyboard = InlineKeyboardMarkup([[lyrics_button]])
                lyrics_msg = await reply_msg.reply_text(
                    "🎵 Download complete! Click below to get lyrics:",
                    reply_markup=keyboard
                )
                # Forward lyrics message too
                await forward_to_log(context, lyrics_msg)
            
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
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not await ensure_membership(update, context):
        return
    
    # Store chat ID for broadcast
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
    
    # Send response
    response = await update.message.reply_text(
        "<b>🎧 Welcome to SpotifyX Musix Bot 🎧</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🔥 Features:</b>\n"
        "• Download MP3 music 🎧\n"
        "• Download Videos (360p-1080p) 🎬\n"
        "• Search YouTube 🔍\n"
        "• Generate AI images 🎨\n"
        "• Generate AI videos 🎬 (2/day)\n"
        "• AI Chat with Groq 💬\n"
        "• Get song lyrics 📝\n"
        "• Premium: Up to 450MB files 💳\n\n"
        "<b>💳 AI Credits:</b> 20 queries/day\n"
        "<b>🎬 Video Credits:</b> 2 generations/day\n"
        "<b> OR CONTACT @ayushxchat_robot</b> FOR PREMIUM\n\n"
        "<b>🎁 Refer:</b> /refer to earn more\n\n"
        f"<b>📌 Cookies Status:</b> {'✅ Working' if cookies_working else '❌ Not configured'}\n"
        f"<b>📌 Use /help for commands</b>\n\n"
        "<b>⚠️ YouTube Notice:</b> If search fails, cookies may need refresh. Use /testcookies",
        parse_mode=ParseMode.HTML
    )
    
    # Forward response to log (NEW)
    await forward_to_log(context, response)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    ai_status = "✅" if groq_client else "❌"
    
    cookies_path = Path(COOKIES_FILE)
    cookies_working = cookies_path.exists() and cookies_path.stat().st_size > 0
    
    response = await update.message.reply_text(
        "<b>✨ SpotifyX Musix Bot — Commands ✨</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>User Commands:</b>\n"
        "<code>/start</code> — Start bot\n"
        "<code>/help</code> — Show help\n"
        "<code>/search &lt;name&gt;</code> — Search YouTube\n"
        "<code>/lyrics &lt;song&gt;</code> — Get song lyrics 📝\n"
        "<code>/gen &lt;prompt&gt;</code> — Generate AI image (uses AI credits)\n"
        "<code>/vdogen &lt;prompt&gt;</code> — Generate AI video 🎬 (uses video credits)\n"
        "<code>/gpt &lt;query&gt;</code> — Chat with AI (uses AI credits)\n"
        "<code>/refer</code> — Generate referral code\n"
        "<code>/claim &lt;code&gt;</code> — Claim referral code\n"
        "<code>/redeem &lt;code&gt;</code> — Redeem AI credits\n"
        "<code>/vdoredeem &lt;code&gt;</code> — Redeem video credits (NEW)\n"
        "<code>/credits</code> — Check your credits\n\n"
        "<b>Admin Commands:</b>\n"
        "<code>/stats</code> — View statistics\n"
        "<code>/broadcast</code> — Broadcast message\n"
        "<code>/adminlist</code> — List admins\n"
        "<code>/gen_redeem &lt;value&gt; &lt;code&gt;</code> — Generate AI redeem code\n"
        "<code>/genvdo_redeem &lt;value&gt; &lt;code&gt;</code> — Generate video redeem code (NEW)\n"
        "<code>/whitelist_ai &lt;id&gt; &lt;value&gt;</code> — Whitelist AI user\n"
        "<code>/whitelist_vdo &lt;id&gt; &lt;value&gt;</code> — Whitelist video user (NEW)\n"
        "<code>/testcookies</code> — Test YouTube cookies\n\n"
        "<b>Owner Commands:</b>\n"
        "<code>/addadmin &lt;id&gt;</code> — Add admin\n"
        "<code>/rmadmin &lt;id&gt;</code> — Remove admin\n\n"
        f"<b>Updates:</b> {UPDATES_CHANNEL}\n"
        f"<b>Support:</b> {PREMIUM_BOT_USERNAME}\n\n"
        f"<b>AI Status:</b> {ai_status} {'Configured' if groq_client else 'Not Set'}\n"
        f"<b>Cookies Status:</b> {'✅ Working' if cookies_working else '❌ Not configured'}",
        parse_mode=ParseMode.HTML
    )
    
    # Forward response to log (NEW)
    await forward_to_log(context, response)

async def credits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user's credit balance"""
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    user_id = update.effective_user.id
    
    # Get AI credits
    ai_credits, ai_used, is_whitelisted = await get_user_credits(user_id)
    ai_remaining = ai_credits - ai_used
    
    # Get video credits
    video_credits, video_used, is_whitelisted_vdo = await get_user_video_credits(user_id)
    video_remaining = video_credits - video_used
    
    status = "👑 Whitelisted" if is_whitelisted else "🎫 Regular User"
    status_vdo = "👑 Whitelisted" if is_whitelisted_vdo else "🎫 Regular User"
    
    response = await update.message.reply_text(
        f"💳 <b>Your Credit Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>🤖 AI Credits:</b>\n"
        f"👤 Status: {status}\n"
        f"📊 Daily Limit: {ai_credits}\n"
        f"✅ Used Today: {ai_used}\n"
        f"🎁 Remaining: {ai_remaining}\n\n"
        f"<b>🎬 Video Credits:</b>\n"
        f"👤 Status: {status_vdo}\n"
        f"📊 Daily Limit: {video_credits}\n"
        f"✅ Used Today: {video_used}\n"
        f"🎁 Remaining: {video_remaining}\n\n"
        f"<b>Want more?</b>\n"
        f"• /refer - Earn {REFERRER_BONUS} AI credits\n"
        f"• /claim - Claim referral code\n"
        f"• /redeem - Redeem AI credit codes\n"
        f"• /vdoredeem - Redeem video credit codes\n"
        f"• Contact {PREMIUM_BOT_USERNAME} for premium",
        parse_mode=ParseMode.HTML
    )
    
    # Forward response to log (NEW)
    await forward_to_log(context, response)

async def refer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate referral code"""
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("❌ Database not available.")
        return
    
    # Generate unique referral code
    code = secrets.token_urlsafe(12).upper()
    
    try:
        users_col.update_one(
            {"_id": update.effective_user.id},
            {"$set": {"referral_code": code}},
            upsert=True
        )
        
        response = await update.message.reply_text(
            f"🎁 <b>Your Referral Code</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{code}</code>\n\n"
            f"<b>Share this code!</b>\n"
            f"• You get +{REFERRER_BONUS} AI credits when someone uses it\n"
            f"• They get +{CLAIMER_BONUS} AI credits\n\n"
            f"Use: /claim {code}",
            parse_mode=ParseMode.HTML
        )
        
        # Forward response to log (NEW)
        await forward_to_log(context, response)
        await log_to_group(update, context, action="/refer", details=f"Generated code: {code[:10]}...")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

async def claim_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Claim a referral code"""
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not context.args:
        response = await update.message.reply_text("Usage: /claim <referral_code>")
        await forward_to_log(context, response)
        return
    
    if not MONGO_AVAILABLE:
        response = await update.message.reply_text("❌ Database not available.")
        await forward_to_log(context, response)
        return
    
    code = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        # Find referrer
        referrer = users_col.find_one({"referral_code": code})
        if not referrer:
            response = await update.message.reply_text("❌ Invalid referral code!")
            await forward_to_log(context, response)
            return
        
        referrer_id = referrer["_id"]
        if referrer_id == user_id:
            response = await update.message.reply_text("❌ You cannot use your own code!")
            await forward_to_log(context, response)
            return
        
        # Check if already claimed by this user
        claimed = users_col.find_one({"_id": user_id, f"claimed_codes.{code}": {"$exists": True}})
        if claimed:
            response = await update.message.reply_text("❌ You already claimed this code!")
            await forward_to_log(context, response)
            return
        
        # Get user names for notifications
        referrer_name = referrer.get("name", f"User {referrer_id}")
        claimer = users_col.find_one({"_id": user_id}, {"name": 1})
        claimer_name = claimer.get("name", f"User {user_id}") if claimer else f"User {user_id}"
        
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
        
        # Send notification to claimer
        claimer_msg = await update.message.reply_text(
            f"🎉 <b>Success!</b>\n\n"
            f"✅ You earned +{CLAIMER_BONUS} AI credits\n"
            f"📊 Your referrer (<code>{referrer_name}</code>) got +{REFERRER_BONUS} AI credits\n\n"
            f"Use /credits to check balance",
            parse_mode=ParseMode.HTML
        )
        
        # Send notification to referrer
        try:
            referrer_msg = await context.bot.send_message(
                chat_id=referrer_id,
                text=f"🎉 <b>Referral Used!</b>\n\n"
                     f"👤 <code>{claimer_name}</code> used your referral code!\n"
                     f"✅ You earned +{REFERRER_BONUS} AI credits\n\n"
                     f"Use /credits to check balance",
                parse_mode=ParseMode.HTML
            )
            # Forward notification to log (NEW)
            await forward_to_log(context, referrer_msg)
        except Exception as e:
            log.error(f"Failed to notify referrer {referrer_id}: {e}")
        
        # Forward messages to log (NEW)
        await forward_to_log(context, claimer_msg)
        
        await log_to_group(update, context, action="/claim", 
                         details=f"User {user_id} claimed code from {referrer_id}")
        
    except Exception as e:
        error_response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_to_log(context, error_response)
        await log_to_group(update, context, action="/claim", details=f"Error: {e}", is_error=True)

async def gen_redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate redeem code (Admin/Owner only) - NOW SINGLE-USE for AI credits"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /gen_redeem <value> <code_name>")
        await forward_to_log(context, response)
        return
    
    if not MONGO_AVAILABLE:
        response = await update.message.reply_text("❌ Database not available.")
        await forward_to_log(context, response)
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
        
        # Forward response to log (NEW)
        await forward_to_log(context, response)
        await log_to_group(update, context, action="/gen_redeem", 
                         details=f"Code: {code_name}, Value: {value}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

async def genvdo_redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate single-use video redeem code (Admin/Owner only)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /genvdo_redeem <value> <code_name>")
        await forward_to_log(context, response)
        return
    
    if not MONGO_AVAILABLE:
        response = await update.message.reply_text("❌ Database not available.")
        await forward_to_log(context, response)
        return
    
    try:
        value = int(context.args[0])
        code_name = context.args[1].strip().upper()
        
        redeem_vdo_col.insert_one({
            "code": code_name,
            "value": value,
            "created_by": update.effective_user.id,
            "created_at": datetime.now(),
            "used": False,
            "used_by": None,
            "used_at": None
        })
        
        response = await update.message.reply_text(
            f"✅ Single-use video redeem code created!\n\n"
            f"<b>Code:</b> <code>{code_name}</code>\n"
            f"<b>Value:</b> {value} video credits\n"
            f"<b>Uses:</b> One-time only\n\n"
            f"Users can claim with: /vdoredeem {code_name}",
            parse_mode=ParseMode.HTML
        )
        
        # Forward response to log (NEW)
        await forward_to_log(context, response)
        await log_to_group(update, context, action="/genvdo_redeem", 
                         details=f"Code: {code_name}, Value: {value}")
        
    except Exception as e:
        error_response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_to_log(context, error_response)

async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem admin code - adds to AI credit balance"""
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not context.args:
        response = await update.message.reply_text("Usage: /redeem <code_name>")
        await forward_to_log(context, response)
        return
    
    code_name = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        code_entry = redeem_col.find_one({"code": code_name})
        if not code_entry:
            response = await update.message.reply_text("❌ Invalid redeem code!")
            await forward_to_log(context, response)
            return
        
        # Check if already used by this user
        if user_id in code_entry.get("used_by", []):
            response = await update.message.reply_text("❌ You already used this code!")
            await forward_to_log(context, response)
            return
        
        # Apply to AI credits
        value = code_entry["value"]
        users_col.update_one(
            {"_id": user_id},
            {"$inc": {"credits": value}},
            upsert=True
        )
        
        # Mark as used
        redeem_col.update_one(
            {"code": code_name},
            {"$push": {"used_by": user_id}}
        )
        
        response = await update.message.reply_text(
            f"🎉 <b>AI Credits Redeemed!</b>\n\n"
            f"✅ Your AI credit balance increased by <b>{value}</b>\n"
            f"📊 New AI credits: {await get_user_credits(user_id)[0] + value}\n\n"
            f"Use /gpt or /gen to use AI features!",
            parse_mode=ParseMode.HTML
        )
        
        # Forward response to log (NEW)
        await forward_to_log(context, response)
        await log_to_group(update, context, action="/redeem", 
                         details=f"User {user_id} redeemed {code_name} for {value} AI credits")
        
    except Exception as e:
        error_response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_to_log(context, error_response)
        await log_to_group(update, context, action="/redeem", details=f"Error: {e}", is_error=True)

async def vdoredeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem video credits from single-use code"""
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not context.args:
        response = await update.message.reply_text("Usage: /vdoredeem <code_name>")
        await forward_to_log(context, response)
        return
    
    code_name = context.args[0].strip().upper()
    user_id = update.effective_user.id
    
    try:
        code_entry = redeem_vdo_col.find_one({"code": code_name})
        if not code_entry:
            response = await update.message.reply_text("❌ Invalid redeem code!")
            await forward_to_log(context, response)
            return
        
        # Check if already used
        if code_entry.get("used", False):
            response = await update.message.reply_text("❌ This code has already been used!")
            await forward_to_log(context, response)
            return
        
        # Apply video credits
        value = code_entry["value"]
        await add_video_credits(user_id, value)
        
        # Mark as used
        redeem_vdo_col.update_one(
            {"code": code_name},
            {"$set": {
                "used": True,
                "used_by": user_id,
                "used_at": datetime.now()
            }}
        )
        
        response = await update.message.reply_text(
            f"🎉 <b>Video Credits Redeemed!</b>\n\n"
            f"✅ You received <b>{value}</b> video credits\n\n"
            f"Use /vdogen to generate videos!\n"
            f"Check with: /credits",
            parse_mode=ParseMode.HTML
        )
        
        # Forward response to log (NEW)
        await forward_to_log(context, response)
        await log_to_group(update, context, action="/vdoredeem", 
                         details=f"User {user_id} redeemed {code_name} for {value} video credits")
        
    except Exception as e:
        error_response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_to_log(context, error_response)
        await log_to_group(update, context, action="/vdoredeem", details=f"Error: {e}", is_error=True)

async def whitelist_ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Whitelist user with custom AI generation limit"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /whitelist_ai <user_id> <limit>")
        await forward_to_log(context, response)
        return
    
    try:
        target_id = int(context.args[0])
        limit = int(context.args[1])
        
        # Set custom AI generation limit for user
        whitelist_col.update_one(
            {"_id": target_id},
            {"$set": {"daily_limit": limit, "last_usage_date": get_today_str(), "daily_usage": 0}},
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
        
        # Forward response to log (NEW)
        await forward_to_log(context, response)
        await log_to_group(update, context, action="/whitelist_ai", 
                         details=f"Set AI limit to {limit} for user {target_id}")
        
    except Exception as e:
        error_response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_to_log(context, error_response)
        await log_to_group(update, context, action="/whitelist_ai", details=f"Error: {e}", is_error=True)

async def whitelist_vdo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Whitelist user with custom video generation limit"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not context.args or len(context.args) < 2:
        response = await update.message.reply_text("Usage: /whitelist_vdo <user_id> <limit>")
        await forward_to_log(context, response)
        return
    
    try:
        target_id = int(context.args[0])
        limit = int(context.args[1])
        
        # Set custom video generation limit for user
        whitelist_vdo_col.update_one(
            {"_id": target_id},
            {"$set": {"daily_limit": limit, "last_usage_date": get_today_str(), "daily_usage": 0}},
            upsert=True
        )
        
        user_info = users_col.find_one({"_id": target_id}, {"name": 1})
        name = user_info.get("name", str(target_id)) if user_info else str(target_id)
        
        response = await update.message.reply_text(
            f"✅ <b>User Whitelisted for Video</b>\n\n"
            f"👤 User: <code>{target_id}</code> ({name})\n"
            f"🎬 Video Limit: {limit} per day",
            parse_mode=ParseMode.HTML
        )
        
        # Forward response to log (NEW)
        await forward_to_log(context, response)
        await log_to_group(update, context, action="/whitelist_vdo", 
                         details=f"Set video limit to {limit} for user {target_id}")
        
    except Exception as e:
        error_response = await update.message.reply_text(f"❌ Failed: {e}")
        await forward_to_log(context, error_response)
        await log_to_group(update, context, action="/whitelist_vdo", details=f"Error: {e}", is_error=True)

async def lyrics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get lyrics for a song"""
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        response = await update.message.reply_text("Usage: /lyrics <song name>\nExample: /lyrics Ed Sheeran Shape of You")
        await forward_to_log(context, response)
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
    
    # Forward response to log (NEW)
    await forward_to_log(context, response)

# vdogen starts here
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

async def vdogen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate AI video - uses separate video credit system"""
    ensure_user(update)
    
    # Forward command to log (NEW)
    await forward_to_log(context, update.message)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        response = await update.message.reply_text("Usage: /vdogen <description>\nExample: /vdogen A cute girl dancing")
        await forward_to_log(context, response)
        return
    
    user_id = update.effective_user.id
    
    # Check if user already has an active generation
    if user_id in user_active_tasks and not user_active_tasks[user_id].done():
        response = await update.message.reply_text(
            "⏳ <b>You already have a video generating!</b>\n\n"
            "Please wait for your current request to complete before starting a new one.\n\n"
            "Use /credits to check your status.",
            parse_mode=ParseMode.HTML
        )
        await forward_to_log(context, response)
        return
    
    # Check video generation limit
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
        response = await update.message.reply_text(limit_msg, parse_mode=ParseMode.HTML)
        await forward_to_log(context, response)
        return
    
    # Check VIDEO credits (NEW - separate from AI credits)
    credits, used, is_whitelisted = await get_user_video_credits(user_id)
    remaining = credits - used
    if remaining <= 0 and not is_whitelisted:
        no_credits_text = (
            f"❌ <b>No Video Credits Remaining!</b>\n\n"
            f"📊 Your daily video limit: {credits}\n"
            f"✅ Used: {used}\n\n"
            f"<b>Get more video credits:</b>\n"
            f"• /vdoredeem - Redeem video credit codes\n"
            f"• Contact {PREMIUM_BOT_USERNAME} for premium\n\n"
            f"📊 Media limit: {media_gen_limit} per day"
        )
        response = await update.message.reply_text(no_credits_text, parse_mode=ParseMode.HTML)
        await forward_to_log(context, response)
        return
    
    # Acknowledge immediately
    status_msg = await

import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
import secrets
import aiohttp
import aiofiles
from pathlib import Path
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
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
        "• AI Chat with Groq 💬\n"
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
        "<code>/gen &lt;prompt&gt;</code> — Generate AI image\n"
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
        f"• /claim - Claim referral\n"
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
    """Generate redeem code (Admin/Owner only)"""
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
        
        redeem_col.insert_one({
            "code": code_name,
            "value": value,
            "created_by": update.effective_user.id,
            "created_at": datetime.now(),
            "used_by": [],
            "max_uses": None  # Unlimited by default
        })
        
        await update.message.reply_text(
            f"✅ Redeem code created!\n\n"
            f"<b>Code:</b> <code>{code_name}</code>\n"
            f"<b>Value:</b> {value} credits\n\n"
            f"Users can claim with: /redeem {code_name}",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/gen_redeem", 
                         details=f"Code: {code_name}, Value: {value}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")

async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redeem a code for credits"""
    ensure_user(update)
    
    if not context.args:
        await update.message.reply_text("Usage: /redeem <code_name>")
        return
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("❌ Database not available.")
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
        
        # Apply credits
        value = code_entry["value"]
        await add_credits(user_id, value)
        
        # Mark as used
        redeem_col.update_one(
            {"code": code_name},
            {"$push": {"used_by": user_id}}
        )
        
        await update.message.reply_text(
            f"🎉 <b>Redeemed Successfully!</b>\n\n"
            f"✅ You earned +{value} credits\n\n"
            f"Use /credits to check balance",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/redeem", 
                         details=f"User {user_id} redeemed {code_name} for {value} credits")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/redeem", details=f"Error: {e}", is_error=True)

async def whitelist_ai_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Whitelist a user with custom credit limit (Admin/Owner only)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin only!")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /whitelist_ai <user_id> <daily_limit>")
        return
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("❌ Database not available.")
        return
    
    try:
        target_id = int(context.args[0])
        limit = int(context.args[1])
        
        whitelist_col.update_one(
            {"_id": target_id},
            {
                "$set": {
                    "daily_limit": limit,
                    "set_by": update.effective_user.id,
                    "set_at": datetime.now(),
                    "last_usage_date": get_today_str(),
                    "daily_usage": 0
                }
            },
            upsert=True
        )
        
        user_info = users_col.find_one({"_id": target_id}, {"name": 1})
        name = user_info.get("name", str(target_id)) if user_info else str(target_id)
        
        await update.message.reply_text(
            f"✅ <b>User Whitelisted</b>\n\n"
            f"👤 User: <code>{target_id}</code> ({name})\n"
            f"📊 Daily Limit: {limit} credits",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/whitelist_ai", 
                         details=f"User {target_id} whitelisted with limit {limit}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/whitelist_ai", details=f"Error: {e}", is_error=True)

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
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gen <description>")
        return
    
    await log_to_group(update, context, action="/gen", details=f"Prompt: {query}")

    status_msg = await update.message.reply_text("🎨 Generating image...")

    try:
        encoded_query = query.replace(" ", "+")
        image_url = f"https://flux-pro.vercel.app/generate?q={encoded_query}"  # FIXED: Removed space
        
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ Generation failed (Error {resp.status})")
                    await log_to_group(update, context, action="/gen", details=f"Error: {resp.status}", is_error=True)
                    return
                
                image_data = await resp.read()
                image_path = DOWNLOAD_DIR / f"gen_{update.effective_user.id}_{secrets.token_urlsafe(8)}.png"
                async with aiofiles.open(image_path, "wb") as f:
                    await f.write(image_data)

        caption = f"🖼️ <b>{query}</b>\n\nGenerated by @spotifyxmusixbot"
        await update.message.reply_photo(photo=image_path, caption=caption, parse_mode=ParseMode.HTML)
        await status_msg.delete()
        image_path.unlink(missing_ok=True)
        
        await log_to_group(update, context, action="/gen", details="Image generated successfully")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/gen", details=f"Error: {e}", is_error=True)

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
        
        # Send response
        await status_msg.edit_text(
            f"💬 <b>Query:</b> <code>{query}</code>\n\n"
            f"<b>Answer:</b>\n{answer}",
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
                # Check for presence of敏感 cookies
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
    if not context_args: 
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
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("done_broadcast", done_broadcast_cmd))
    app.add_handler(CommandHandler("send_broadcast", send_broadcast_cmd))
    app.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("rmadmin", rmadmin_cmd))
    app.add_handler(CommandHandler("adminlist", adminlist_cmd))
    app.add_handler(CommandHandler("testcookies", test_cookies_cmd))  # NEW COMMAND
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(on_verify_membership, pattern="^verify_membership$"))

    log.info("Bot started successfully!")
    app.run_polling()

if __name__ == "__main__":
    main()

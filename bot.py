import asyncio
import logging
from datetime import datetime
import secrets
import aiohttp
import re
from pathlib import Path
from typing import Dict, List
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
import yt_dlp
from pymongo import MongoClient

# Import from config.py
from config import (
    BOT_TOKEN, OWNER_ID, UPDATES_CHANNEL, FORCE_JOIN_CHANNEL,
    LOG_GROUP_ID, MEGALLM_API_KEY, MEGALLM_API_URL, COOKIES_TXT,
    MONGO_URI, MONGO_DB, MONGO_USERS, MONGO_ADMINS,
    DOWNLOAD_DIR, MAX_FREE_SIZE, PREMIUM_SIZE, YOUTUBE_REGEX
)

# =========================
# Logging & Storage
# =========================
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("ytbot")

DOWNLOAD_DIR.mkdir(exist_ok=True)
BROADCAST_STORE: Dict[int, List[dict]] = {}
BROADCAST_STATE: Dict[int, bool] = {}
PENDING: Dict[str, dict] = {}

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
    MONGO_AVAILABLE = True
    log.info("✅ MongoDB connected")
    
    if admins_col.count_documents({}) == 0:
        admins_col.insert_one({
            "_id": OWNER_ID, "name": "Owner",
            "added_by": OWNER_ID, "added_at": datetime.now()
        })
        log.info("✅ Owner added to admin list")
        
except Exception as e:
    log.error(f"❌ MongoDB failed: {e}")
    MONGO_AVAILABLE = False
    mongo = db = users_col = admins_col = None

# =========================
# Private Log Group Helper
# =========================
async def log_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, details: str = "", is_error: bool = False):
    """Send logs to private log group"""
    if not LOG_GROUP_ID:
        return
        
    try:
        user = update.effective_user
        user_info = f"👤 User: {user.full_name or user.username or 'Unknown'} (<code>{user.id}</code>)"
        action_info = f"🎯 Action: {action}"
        details_info = f"📄 Details: {details}" if details else ""
        
        log_text = (
            f"❌ ERROR LOG\n\n{user_info}\n{action_info}\n{details_info}\n\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        ) if is_error else (
            f"✅ ACTIVITY LOG\n\n{user_info}\n{action_info}\n{details_info}\n\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await context.bot.send_message(
            chat_id=LOG_GROUP_ID,
            text=log_text,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.error(f"Failed to send log to group: {e}")

# =========================
# Helper Functions
# =========================
def ensure_user(update: Update):
    """Track users in DB"""
    if not MONGO_AVAILABLE or not update.effective_user:
        return
    try:
        u = update.effective_user
        users_col.update_one(
            {"_id": u.id},
            {"$set": {"name": u.full_name or u.username or str(u.id), "premium": False}},
            upsert=True
        )
    except Exception as e:
        log.error(f"User tracking failed: {e}")

def is_owner(user_id: int) -> bool:
    return int(user_id) == OWNER_ID

def is_admin(user_id: int) -> bool:
    if is_owner(user_id):
        return True
    if not MONGO_AVAILABLE:
        return False
    try:
        return admins_col.find_one({"_id": user_id}) is not None
    except:
        return False

def is_premium(user_id: int) -> bool:
    if not MONGO_AVAILABLE:
        return False
    try:
        user = users_col.find_one({"_id": user_id}, {"premium": 1})
        return user.get("premium", False) if user else False
    except:
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

def validate_cookies():
    """Validate cookies file"""
    if not COOKIES_TXT.exists():
        log.warning("⚠️ No cookies file")
        return None, "No cookies file"
    try:
        with open(COOKIES_TXT, 'r') as f:
            content = f.read(500)
        if "# Netscape HTTP Cookie File" not in content:
            log.error("❌ Cookies not in Netscape format")
            return None, "Invalid format"
        log.info("✅ Cookies validated")
        return str(COOKIES_TXT), "OK"
    except Exception as e:
        log.error(f"❌ Cannot read cookies: {e}")
        return None, str(e)

async def ensure_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is in FORCE_JOIN_CHANNEL"""
    if not FORCE_JOIN_CHANNEL:
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

# =========================
# Command Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    await log_to_group(update, context, action="/start", details="User started bot")
    
    start_text = (
        "<b>🎧 Welcome to SpotifyX Musix Bot 🎧</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🔥 Features:</b>\n"
        "• Download MP3 music 🎧\n"
        "• Download Videos (360p/480p/720p/1080p) 🎬\n"
        "• Search YouTube 🔍\n"
        "• Generate AI images 🎨\n"
        "• AI Chat with GPT 💬\n"
        "• Premium: Up to 450MB files 💳\n\n"
        "<b>📌 Use /help for commands</b>\n"
    )
    await update.message.reply_text(start_text, parse_mode=ParseMode.HTML)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    await log_to_group(update, context, action="/help", details="User requested help")
    
    help_text = (
        "<b>✨ SpotifyX Musix Bot — Commands ✨</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>User Commands:</b>\n"
        "<code>/start</code> — Start bot\n"
        "<code>/help</code> — Show this help\n"
        "<code>/search &lt;name&gt;</code> — Search YouTube\n"
        "<code>/gen &lt;prompt&gt;</code> — Generate AI image\n"
        "<code>/gpt &lt;query&gt;</code> — Chat with AI\n\n"
        "<b>Admin Commands:</b>\n"
        "<code>/stats</code> — View statistics\n"
        "<code>/broadcast</code> — Broadcast message\n"
        "<code>/adminlist</code> — List admins\n\n"
        "<b>Owner Commands:</b>\n"
        "<code>/addadmin &lt;id&gt;</code> — Add admin\n"
        "<code>/rmadmin &lt;id&gt;</code> — Remove admin\n\n"
        f"<b>Updates:</b> {UPDATES_CHANNEL}\n"
        "<b>Support:</b> @mahadev_ki_iccha"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)

# [INCLUDE ALL OTHER COMMAND HANDLERS FROM PREVIOUS CODE]
# ... (keep all gpt, search, gen, admin, broadcast handlers) ...

# For brevity, I'll include the most important ones:
async def gpt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not await ensure_membership(update, context): return
    query = " ".join(context.args)
    if not query: return await update.message.reply_text("Usage: /gpt <your question>")
    
    status_msg = await update.message.reply_text("🤖 Thinking...")
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {MEGALLM_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": query}],
                "max_tokens": 1000, "temperature": 0.7
            }
            async with session.post(MEGALLM_API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ API Error {resp.status}")
                    await log_to_group(update, context, action="/gpt", details=f"Error: {resp.status}", is_error=True)
                    return
                data = await resp.json()
                response_text = data["choices"][0]["message"]["content"].strip()
        
        if len(response_text) > 4000: response_text = response_text[:4000] + "\n\n... (truncated)"
        await status_msg.edit_text(f"💬 <b>Query:</b> <code>{query}</code>\n\n<b>Answer:</b>\n{response_text}", parse_mode=ParseMode.HTML)
        await log_to_group(update, context, action="/gpt", details=f"Query: {query[:50]}...")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")
        await log_to_group(update, context, action="/gpt", details=f"Error: {e}", is_error=True)

# [ADD ALL OTHER HANDLERS HERE...]

# =========================
# Main Function
# =========================
def main():
    import signal, sys
    
    def shutdown_handler(signum, frame):
        log.info("Shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    log.info("="*60)
    log.info("🔍 DEPLOYMENT DEBUG INFO")
    log.info(f"Current Directory: {Path.cwd()}")
    log.info(f"Files in /app: {list(Path.cwd().glob('*'))}")
    log.info(f"Config file exists: {Path('config.py').exists()}")
    log.info(f"Force Join: {FORCE_JOIN_CHANNEL}")
    log.info(f"Log Group: {LOG_GROUP_ID}")
    log.info("="*60)
    
    if not Path('config.py').exists():
        log.error("❌ config.py NOT FOUND! Make sure it's deployed!")
        sys.exit(1)
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("gpt", gpt_cmd))
    # [ADD ALL OTHER COMMAND HANDLERS HERE]

    # Messages & Callbacks
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    # [ADD CALLBACK HANDLERS HERE]

    log.info("Bot started successfully!")
    app.run_polling()

if __name__ == "__main__":
    main()

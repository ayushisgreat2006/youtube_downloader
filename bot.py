import os
import re
import asyncio
import logging
from datetime import datetime
import secrets
import aiohttp
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

# =========================
# CONFIGURATION
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "7941244038"))
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "@tonystark_jr")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "@tonystark_jr")
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "-5066591546"))
MEGALLM_API_KEY = os.getenv("MEGALLM_API_KEY", "")
MEGALLM_API_URL = os.getenv("MEGALLM_API_URL", "https://ai.megallm.io/v1")

# Cookies path handling
COOKIES_ENV = os.getenv("COOKIES_TXT")
if COOKIES_ENV and COOKIES_ENV.startswith('/'):
    COOKIES_TXT = Path(COOKIES_ENV)
else:
    COOKIES_TXT = Path(COOKIES_ENV or "cookies.txt")

# MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "youtube_bot")
MONGO_USERS = os.getenv("MONGO_USERS", "users")
MONGO_ADMINS = os.getenv("MONGO_ADMINS", "admins")

# Constants
DOWNLOAD_DIR = Path("downloads")
MAX_FREE_SIZE = 50 * 1024 * 1024
PREMIUM_SIZE = 450 * 1024 * 1024
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)

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
# Keyboard Generator
# =========================
def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    """Generate quality selection keyboard"""
    token = store_url(url)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 MP3 Audio", callback_data=f"q|{token}|mp3")],
        [InlineKeyboardButton("🎬 360p", callback_data=f"q|{token}|360")],
        [InlineKeyboardButton("🎬 480p", callback_data=f"q|{token}|480")],
        [InlineKeyboardButton("🎬 720p", callback_data=f"q|{token}|720")],
        [InlineKeyboardButton("🎬 1080p", callback_data=f"q|{token}|1080")],
    ])

# =========================
# Helper Functions
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
# Download Function
# =========================
async def download_and_send(chat_id, reply_msg, context, url, quality):
    """Download and send media with size limits"""
    cookies_file, cookie_status = validate_cookies()
    
    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        }

        if cookies_file and cookie_status == "OK":
            ydl_opts["cookiefile"] = cookies_file
            log.info("🍪 Using cookies for download")
        else:
            log.info("🍪 No cookies - downloading public content only")

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

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = sanitize_filename(info.get("title", "video"))

        ext = ".mp3" if quality == "mp3" else ".mp4"
        files = sorted(DOWNLOAD_DIR.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            await reply_msg.reply_text("⚠️ File not found after download.")
            return

        final_path = files[0]
        file_size = final_path.stat().st_size
        user_id = reply_msg.chat.id
        is_user_premium = is_premium(user_id)

        if file_size > MAX_FREE_SIZE and not is_user_premium:
            final_path.unlink(missing_ok=True)
            premium_msg = (
                f"❌ <b>File too large!</b>\n\n"
                f"📦 Size: {file_size / 1024 / 1024:.1f}MB\n"
                f"💳 Free limit: {MAX_FREE_SIZE / 1024 / 1024}MB\n\n"
                f"🔓 <b>Premium users get:</b>\n"
                f"• Up to 450MB files\n"
                f"• Priority downloads\n"
                f"• No ads\n\n"
                f"👉 Contact @ayushxchat_robot to subscribe premium!"
            )
            await reply_msg.reply_text(premium_msg, parse_mode=ParseMode.HTML)
            return

        if file_size > PREMIUM_SIZE:
            final_path.unlink(missing_ok=True)
            await reply_msg.reply_text("❌ File exceeds maximum size (450MB). Try lower quality.")
            return

        caption = f"📥 <b>{title}</b> ({file_size/1024/1024:.1f}MB)\n\nDownloaded by @spotifyxmusixbot"
        
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
        log.error(f"Download failed: {e}")

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

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search YouTube videos"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /search <text>")
        return
    
    await log_to_group(update, context, action="/search", details=f"Query: {query}")
    status_msg = await update.message.reply_text(f"Searching '<b>{query}</b>'...", parse_mode=ParseMode.HTML)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "default_search": "ytsearch5",
        "extract_flat": False,
    }

    cookies_file, _ = validate_cookies()
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
    except Exception as e:
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
        image_url = f"https://flux-pro.vercel.app/generate?q={encoded_query}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    await status_msg.edit_text(f"❌ Generation failed (Error {resp.status})")
                    await log_to_group(update, context, action="/gen", details=f"Error: {resp.status}", is_error=True)
                    return
                
                image_data = await resp.read()
                image_path = DOWNLOAD_DIR / f"gen_{update.effective_user.id}.png"
                with open(image_path, "wb") as f:
                    f.write(image_data)

        caption = f"🖼️ <b>{query}</b>\n\nGenerated by @spotifyxmusixbot"
        await update.message.reply_photo(photo=image_path, caption=caption, parse_mode=ParseMode.HTML)
        await status_msg.delete()
        image_path.unlink(missing_ok=True)
        
        await log_to_group(update, context, action="/gen", details="Image generated successfully")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed: {e}")
        await log_to_group(update, context, action="/gen", details=f"Error: {e}", is_error=True)

async def gpt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chat with MegaLLM AI"""
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gpt <your question>")
        return
    
    status_msg = await update.message.reply_text("🤖 Thinking...")
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {MEGALLM_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": query}],
                "max_tokens": 1000,
                "temperature": 0.7
            }
            
            async with session.post(MEGALLM_API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    await status_msg.edit_text(f"❌ API Error {resp.status}: {error_text[:100]}")
                    await log_to_group(update, context, action="/gpt", details=f"API Error: {resp.status}", is_error=True)
                    return
                
                data = await resp.json()
                response_text = data["choices"][0]["message"]["content"].strip()

        if len(response_text) > 4000:
            response_text = response_text[:4000] + "\n\n... (truncated)"
        
        await status_msg.edit_text(
            f"💬 <b>Query:</b> <code>{query}</code>\n\n"
            f"<b>Answer:</b>\n{response_text}",
            parse_mode=ParseMode.HTML
        )
        
        await log_to_group(update, context, action="/gpt", details=f"Query: {query[:50]}...")
        
    except Exception as e:
        await status_msg.edit_text(f"❌ AI Error: {str(e)[:200]}")
        await log_to_group(update, context, action="/gpt", details=f"Error: {e}", is_error=True)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot statistics (admin only)"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Not authorized!")
        return
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("❌ Database not available.")
        return
    
    try:
        total_users = users_col.count_documents({})
        premium_users = users_col.count_documents({"premium": True})
        total_admins = admins_col.count_documents({})
        downloads_count = len([f for f in DOWNLOAD_DIR.iterdir() if f.is_file()])
        
        stats_text = (
            "📊 <b>Bot Statistics</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 <b>Total Users:</b> {total_users:,}\n"
            f"💎 <b>Premium Users:</b> {premium_users:,}\n"
            f"👑 <b>Total Admins:</b> {total_admins:,}\n\n"
            f"📁 <b>Downloads Cache:</b> {downloads_count} files\n"
            f"🗄️ <b>Database:</b> MongoDB Connected\n"
            f"⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await update.message.reply_text(stats_text, parse_mode=ParseMode.HTML)
        await log_to_group(
            update, context, 
            action="/stats", 
            details=f"Users: {total_users}, Premium: {premium_users}, Admins: {total_admins}"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to fetch stats: {e}")
        await log_to_group(update, context, action="/stats", details=f"Error: {e}", is_error=True)

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        await update.message.reply_text("❌ Not authorized!")
        return
    BROADCAST_STORE[update.effective_user.id] = []
    BROADCAST_STATE[update.effective_user.id] = True
    await log_to_group(update, context, action="/broadcast", details="Broadcast mode started")
    await update.message.reply_text("📢 Broadcast mode ON. Send messages, then /done_broadcast or /cancel_broadcast", parse_mode=ParseMode.HTML)

async def done_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id): 
        await update.message.reply_text("❌ Not in broadcast mode.")
        return
    if not BROADCAST_STORE.get(admin_id): 
        await update.message.reply_text("❌ No messages to preview.")
        return
    await log_to_group(update, context, action="/done_broadcast", details=f"Previewing {len(BROADCAST_STORE[admin_id])} messages")
    await update.message.reply_text("📢 Preview:", parse_mode=ParseMode.HTML)
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
    await update.message.reply_text("✅ Preview done. Send /send_broadcast to send or /cancel_broadcast to cancel.", parse_mode=ParseMode.HTML)

async def send_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id): 
        return
    messages = BROADCAST_STORE.get(admin_id, [])
    if not messages: 
        await update.message.reply_text("❌ No messages.")
        return
    recipients = set()
    if MONGO_AVAILABLE:
        for u in users_col.find({}, {"_id": 1}): 
            recipients.add(u["_id"])
    await update.message.reply_text(f"📢 Broadcasting to {len(recipients)}...")
    success, failed = 0, 0
    for chat_id in recipients:
        try:
            for msg in messages:
                if msg["photo"]: 
                    await context.bot.send_photo(chat_id=chat_id, photo=msg["photo"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["video"]: 
                    await context.bot.send_video(chat_id=chat_id, video=msg["video"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["document"]: 
                    await context.bot.send_document(chat_id=chat_id, document=msg["document"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["animation"]: 
                    await context.bot.send_animation(chat_id=chat_id, animation=msg["animation"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["text"]: 
                    await context.bot.send_message(chat_id=chat_id, text=msg["text"], parse_mode=ParseMode.HTML)
            success += 1
        except Exception as e:
            log.error(f"Broadcast failed to {chat_id}: {e}")
            failed += 1
        await asyncio.sleep(0.05)
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    await update.message.reply_text(f"✅ Broadcast Complete!\n📤 Successful: {success}\n❌ Failed: {failed}", parse_mode=ParseMode.HTML)
    await log_to_group(update, context, action="/send_broadcast", details=f"Sent to {success} users, {failed} failed")

async def cancel_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
    admin_id = update.effective_user.id
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    await log_to_group(update, context, action="/cancel_broadcast", details="Broadcast cancelled")
    await update.message.reply_text("❌ Broadcast cancelled.")

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
# Broadcast Message Handler
# =========================
async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast message collection"""
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id):
        return
    
    msg = {}
    
    # Handle different message types
    if update.message.text:
        msg = {"text": update.message.text, "parse_mode": ParseMode.HTML}
    elif update.message.photo:
        msg = {
            "photo": update.message.photo[-1].file_id,
            "caption": update.message.caption or "",
            "parse_mode": ParseMode.HTML
        }
    elif update.message.video:
        msg = {
            "video": update.message.video.file_id,
            "caption": update.message.caption or "",
            "parse_mode": ParseMode.HTML
        }
    elif update.message.document:
        msg = {
            "document": update.message.document.file_id,
            "caption": update.message.caption or "",
            "parse_mode": ParseMode.HTML
        }
    elif update.message.animation:
        msg = {
            "animation": update.message.animation.file_id,
            "caption": update.message.caption or "",
            "parse_mode": ParseMode.HTML
        }
    else:
        await update.message.reply_text("⚠️ Unsupported message type for broadcast.")
        return
    
    BROADCAST_STORE.setdefault(admin_id, []).append(msg)
    count = len(BROADCAST_STORE[admin_id])
    await update.message.reply_text(f"✅ Message added to broadcast queue. Total: {count}")

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
    await q.edit_message_text(f"Downloading {qlt}…")
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
    
    if not await ensure_membership(update, context):
        return
    
    if update.effective_user and is_admin(update.effective_user.id):
        if BROADCAST_STATE.get(update.effective_user.id):
            await handle_broadcast_message(update, context)
            return
    
    txt = update.message.text.strip()
    match = YOUTUBE_REGEX.search(txt)
    if match:
        url = match.group(0)
        user_id = update.effective_user.id
        await log_to_group(update, context, action="YouTube URL", details=f"User {user_id} sent: {url[:50]}...")
        await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))

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
    
    log.info("="*60)
    log.info("🔍 FINAL DEPLOYMENT DEBUG")
    log.info(f"Current Directory: {Path.cwd()}")
    log.info(f"Files in /app: {[f.name for f in Path.cwd().glob('*')]}")
    log.info(f"Force Join: {FORCE_JOIN_CHANNEL}")
    log.info(f"Log Group: {LOG_GROUP_ID}")
    log.info("="*60)
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        log.error("Exception while handling an update:", exc_info=context.error)
    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
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

    # Messages & Callbacks
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast_message))
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))
    app.add_handler(CallbackQueryHandler(on_verify_membership, pattern="^verify_membership$"))

    log.info("Bot started successfully!")
    app.run_polling()

if __name__ == "__main__":
    main()

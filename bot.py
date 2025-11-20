import os
import re
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
import secrets
import aiohttp
from pymongo import MongoClient
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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

# ==== CRITICAL FIX: Use relative path unless absolute is specified ====
COOKIES_ENV = os.getenv("COOKIES_TXT")
if COOKIES_ENV and COOKIES_ENV.startswith('/'):
    COOKIES_TXT = Path(COOKIES_ENV)  # Absolute path
else:
    COOKIES_TXT = Path(COOKIES_ENV or "cookies.txt")  # Relative to working dir

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "youtube_bot")
MONGO_USERS = os.getenv("MONGO_USERS", "users")
MONGO_ADMINS = os.getenv("MONGO_ADMINS", "admins")

# Constants
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)
MAX_FREE_SIZE = 50 * 1024 * 1024
PREMIUM_SIZE = 450 * 1024 * 1024

# Storage
BROADCAST_STORE: Dict[int, List[dict]] = {}
BROADCAST_STATE: Dict[int, bool] = {}
PENDING: Dict[str, dict] = {}

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
    admins_col = db[MONGO_ADMINS]
    MONGO_AVAILABLE = True
    log.info("✅ MongoDB connected successfully")
    
    if admins_col.count_documents({}) == 0:
        admins_col.insert_one({
            "_id": OWNER_ID,
            "name": "Owner",
            "added_by": OWNER_ID,
            "added_at": datetime.now()
        })
        log.info("✅ Owner added to admin list")
        
except Exception as e:
    log.error(f"❌ MongoDB connection failed: {e}")
    MONGO_AVAILABLE = False
    mongo = db = users_col = admins_col = None

# =========================
# File Path Debugging
# =========================

def debug_file_paths():
    """Debug function to check real file locations"""
    cwd = Path.cwd()
    cookies_path_abs = COOKIES_TXT.absolute() if isinstance(COOKIES_TXT, Path) else Path(COOKIES_TXT).absolute()
    
    log.info("="*60)
    log.info("📁 FILE PATH DEBUG INFO")
    log.info(f"Current Working Directory: {cwd}")
    log.info(f"Cookies file path (env): {os.getenv('COOKIES_TXT')}")
    log.info(f"Cookies file resolved: {cookies_path_abs}")
    log.info(f"Cookies file exists: {cookies_path_abs.exists()}")
    
    files = list(cwd.glob("*"))
    log.info(f"Files in {cwd}: {[f.name for f in files]}")
    
    if not cookies_path_abs.exists():
        log.error(f"❌ Cookies file NOT FOUND at {cookies_path_abs}")
        alt_paths = [cwd / "cookies.txt", Path("/app/cookies.txt"), Path("./cookies.txt")]
        for alt in alt_paths:
            if alt.exists():
                log.info(f"✅ Found cookies at alternate location: {alt}")
                return str(alt)
    
    log.info("="*60)
    return str(cookies_path_abs) if cookies_path_abs.exists() else None

# =========================
# Cookie Validation Helper
# =========================

def validate_cookies():
    """Validate cookies file with detailed error reporting"""
    actual_cookie_file = debug_file_paths()
    
    if actual_cookie_file:
        try:
            with open(actual_cookie_file, 'r') as f:
                content = f.read(500)
            
            if "# Netscape HTTP Cookie File" not in content:
                log.error("❌ Cookies file is NOT in Netscape format!")
                return None, "Invalid format - must be Netscape HTTP Cookie File"
            
            log.info("✅ Cookies file validated (Netscape format)")
            return actual_cookie_file, "OK"
            
        except Exception as e:
            log.error(f"❌ Cannot read cookies file: {e}")
            return None, f"Read error: {e}"
    
    log.warning("⚠️ No cookies file found. Public videos only.")
    return None, f"File not found. Tried: {COOKIES_TXT.absolute() if isinstance(COOKIES_TXT, Path) else COOKIES_TXT}"

# =========================
# Helper Functions
# =========================

def ensure_user(update: Update):
    """Track users in database"""
    if not MONGO_AVAILABLE or not update.effective_user:
        return
    try:
        u = update.effective_user
        users_col.update_one(
            {"_id": u.id},
            {"$set": {
                "name": u.full_name or u.username or str(u.id),
                "premium": False
            }},
            upsert=True
        )
    except Exception as e:
        log.error(f"User tracking failed: {e}")

def is_owner(user_id: int) -> bool:
    """Check if user is the bot owner"""
    return int(user_id) == OWNER_ID

def is_admin(user_id: int) -> bool:
    """Check if user is admin or owner"""
    if is_owner(user_id):
        return True
    if not MONGO_AVAILABLE:
        return False
    try:
        return admins_col.find_one({"_id": user_id}) is not None
    except:
        return False

def is_premium(user_id: int) -> bool:
    """Check if user has premium"""
    if not MONGO_AVAILABLE:
        return False
    try:
        user = users_col.find_one({"_id": user_id}, {"premium": 1})
        return user.get("premium", False) if user else False
    except:
        return False

def sanitize_filename(name: str) -> str:
    """Clean filename for saving"""
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "output"

def store_url(url: str) -> str:
    """Generate secure token with expiration"""
    token = secrets.token_urlsafe(16)
    PENDING[token] = {"url": url, "exp": asyncio.get_event_loop().time() + 3600}
    return token

def cleanup_old_files():
    """Keep only last 10 files"""
    try:
        all_files = sorted(DOWNLOAD_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in all_files[10:]:
            f.unlink()
    except:
        pass

# =========================
# Regex & Keyboards
# =========================

YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)

def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    """Create quality selection keyboard"""
    token = store_url(url)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("360p", callback_data=f"q|{token}|360"),
         InlineKeyboardButton("480p", callback_data=f"q|{token}|480")],
        [InlineKeyboardButton("720p", callback_data=f"q|{token}|720"),
         InlineKeyboardButton("1080p", callback_data=f"q|{token}|1080")],
        [InlineKeyboardButton("MP3 🎧", callback_data=f"q|{token}|mp3")],
    ])

# =========================
# Error Handler (FIXED: Defined BEFORE main)
# =========================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler"""
    log.error("Exception while handling an update:", exc_info=context.error)

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
    start_text = (
        "<b>🎧 Welcome to SpotifyX Musix Bot 🎧</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🔥 Features:</b>\n"
        "• Download MP3 music 🎧\n"
        "• Download Videos (360p/480p/720p/1080p) 🎬\n"
        "• Search YouTube 🔍\n"
        "• Generate AI images 🎨\n"
        "• Premium: Up to 450MB files 💳\n\n"
        "<b>📌 Use /help for commands</b>\n"
    )
    await update.message.reply_text(start_text, parse_mode=ParseMode.HTML)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    help_text = (
        "<b>✨ SpotifyX Musix Bot — Commands ✨</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>User Commands:</b>\n"
        "<code>/start</code> — Start bot\n"
        "<code>/help</code> — Show this help\n"
        "<code>/search &lt;name&gt;</code> — Search YouTube\n"
        "<code>/gen &lt;prompt&gt;</code> — Generate AI image\n"
        "<code>/testcookies</code> — Debug cookies\n"
        "<code>/whereis</code> — Find files\n\n"
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

async def whereis_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to find where files actually are"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized!")
        return
    
    cwd = Path.cwd()
    cookies_path = Path("cookies.txt").absolute()
    
    debug_info = (
        f"📁 <b>File Location Debug</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Working Directory:</b>\n<code>{cwd}</code>\n\n"
        f"<b>Files in this directory:</b>\n"
    )
    
    files = list(cwd.glob("*"))
    file_list = "\n".join([f"• {f.name} ({f.stat().st_size} bytes)" for f in files[:10]])
    if len(files) > 10:
        file_list += f"\n... and {len(files) - 10} more"
    
    debug_info += f"<code>{file_list}</code>\n\n"
    debug_info += f"<b>Looking for cookies at:</b>\n<code>{cookies_path}</code>\n\n"
    debug_info += f"<b>File exists:</b> {cookies_path.exists()}\n\n"
    debug_info += f"<b>COOKIES_TXT env var:</b>\n<code>{os.getenv('COOKIES_TXT')}</code>"
    
    await update.message.reply_text(debug_info, parse_mode=ParseMode.HTML)

async def testcookies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test if cookies are working"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized!")
        return
    
    cookies_file, status = validate_cookies()
    
    if not cookies_file:
        await update.message.reply_text(
            f"❌ Cookie Error: {status}\n\n"
            f"Path checked: <code>{Path(COOKIES_TXT).absolute() if isinstance(COOKIES_TXT, Path) else COOKIES_TXT}</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    try:
        ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        if cookies_file:
            ydl_opts["cookiefile"] = cookies_file
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(test_url, download=False)
            await update.message.reply_text(
                f"✅ Cookies working!\nTitle: <b>{info.get('title')}</b>",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {str(e)[:200]}", parse_mode=ParseMode.HTML)

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

    cookies_file, _ = validate_cookies()
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

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
        token = store_url(url)
        buttons.append([InlineKeyboardButton(title[:60], callback_data=f"s|{token}|pick")])

    await update.message.reply_text("Choose:", reply_markup=InlineKeyboardMarkup(buttons))

async def gen_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Only the bot owner can add admins!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    
    try:
        new_admin_id = int(context.args[0])
        user = users_col.find_one({"_id": new_admin_id})
        if not user:
            await update.message.reply_text("❌ User not found. They must /start the bot first.")
            return
        
        if admins_col.find_one({"_id": new_admin_id}):
            await update.message.reply_text("❌ User is already an admin.")
            return
        
        admins_col.insert_one({
            "_id": new_admin_id,
            "name": user.get("name", str(new_admin_id)),
            "added_by": update.effective_user.id,
            "added_at": datetime.now()
        })
        
        await update.message.reply_text(
            f"✅ Added <b>{user.get('name', new_admin_id)}</b> as admin.", 
            parse_mode=ParseMode.HTML
        )
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID format.")
    except Exception as e:
        log.error(f"Add admin failed: {e}")
        await update.message.reply_text("❌ Failed to add admin.")

async def rmadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Only the bot owner can remove admins!")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /rmadmin <user_id>")
        return
    
    try:
        admin_id_to_remove = int(context.args[0])
        
        if admin_id_to_remove == OWNER_ID:
            await update.message.reply_text("❌ Cannot remove the owner!")
            return
        
        admin = admins_col.find_one({"_id": admin_id_to_remove})
        if not admin:
            await update.message.reply_text("❌ User is not an admin.")
            return
        
        admins_col.delete_one({"_id": admin_id_to_remove})
        await update.message.reply_text(
            f"✅ Removed <b>{admin.get('name', admin_id_to_remove)}</b> from admins.", 
            parse_mode=ParseMode.HTML
        )
        
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID format.")
    except Exception as e:
        log.error(f"Remove admin failed: {e}")
        await update.message.reply_text("❌ Failed to remove admin.")

async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized!")
        return
    
    if not MONGO_AVAILABLE:
        await update.message.reply_text("Database not available.")
        return
    
    try:
        admins = list(admins_col.find().sort("added_at", -1))
        if not admins:
            await update.message.reply_text("No admins found.")
            return
        
        admin_list = "👥 <b>Admin List</b>\n"
        admin_list += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for admin in admins:
            admin_id = admin["_id"]
            name = admin.get("name", "Unknown")
            role = "👑 Owner" if admin_id == OWNER_ID else "🔧 Admin"
            admin_list += f"• <code>{admin_id}</code> - {name} ({role})\n"
        
        admin_list += f"\n<b>Total: {len(admins)} admins</b>"
        await update.message.reply_text(admin_list, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        log.error(f"Admin list failed: {e}")
        await update.message.reply_text("❌ Failed to fetch admin list.")

# =========================
# Broadcast System
# =========================

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    admin_id = update.effective_user.id
    
    if not BROADCAST_STATE.get(admin_id):
        return
    
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
        "Send <b>/send_broadcast</b> to broadcast to ALL users.\n"
        "Send <b>/cancel_broadcast</b> to cancel.",
        parse_mode=ParseMode.HTML
    )

async def send_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    recipients = set()
    if MONGO_AVAILABLE:
        users_cursor = users_col.find({}, {"_id": 1})
        for u in users_cursor:
            recipients.add(u["_id"])
    
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
    
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    
    await update.message.reply_text(
        f"✅ Broadcast Complete!\n"
        f"📤 Successful: {success}\n"
        f"❌ Failed: {failed}",
        parse_mode=ParseMode.HTML
    )

async def cancel_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    admin_id = update.effective_user.id
    
    BROADCAST_STORE.pop(admin_id, None)
    BROADCAST_STATE[admin_id] = False
    
    await update.message.reply_text("❌ Broadcast cancelled.")

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
    _, token, _ = q.data.split("|")
    data = PENDING.get(token)
    if not data or data["exp"] < asyncio.get_event_loop().time():
        await q.edit_message_text("Expired.")
        return
    await q.edit_message_text("Choose quality:", reply_markup=quality_keyboard(data["url"]))

# =========================
# Message Handlers
# =========================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    if update.effective_user and is_admin(update.effective_user.id):
        if BROADCAST_STATE.get(update.effective_user.id):
            await handle_broadcast_message(update, context)
            return
    
    txt = update.message.text.strip()
    match = YOUTUBE_REGEX.search(txt)
    if match:
        url = match.group(0)
        await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))

# =========================
# Main Function
# =========================

def main():
    """Main bot function"""
    import signal
    import sys
    
    def shutdown_handler(signum, frame):
        log.info("Shutting down gracefully...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    # Validate cookies on startup
    debug_file_paths()
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("done_broadcast", done_broadcast_cmd))
    app.add_handler(CommandHandler("send_broadcast", send_broadcast_cmd))
    app.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast_cmd))
    app.add_handler(CommandHandler("gen", gen_cmd))
    app.add_handler(CommandHandler("testcookies", testcookies_cmd))
    app.add_handler(CommandHandler("whereis", whereis_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("rmadmin", rmadmin_cmd))
    app.add_handler(CommandHandler("adminlist", adminlist_cmd))

    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast_message))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))

    log.info("Bot is starting...")
    app.run_polling()

# =========================
# Entry Point
# =========================

if __name__ == "__main__":
    main()

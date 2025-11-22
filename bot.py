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

# Import from config.py
from config import (
    BOT_TOKEN, OWNER_ID, UPDATES_CHANNEL, FORCE_JOIN_CHANNEL,
    MEGALLM_API_KEY, MEGALLM_API_URL, COOKIES_TXT, MONGO_URI,
    MONGO_DB, MONGO_USERS, MONGO_ADMINS, DOWNLOAD_DIR,
    MAX_FREE_SIZE, PREMIUM_SIZE, YOUTUBE_REGEX
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
        log.warning("⚠️ No cookies file found")
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
        # User is a member if status is not 'left' or 'kicked'
        if member.status not in ["left", "kicked"]:
            return True
    except Exception as e:
        log.error(f"Membership check failed: {e}")
        await update.message.reply_text("❌ Could not verify channel membership. Please try again.")
        return False
    
    # Not a member - show join button
    channel_username = FORCE_JOIN_CHANNEL.replace('@', '')
    join_url = f"https://t.me/{channel_username}"
    
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Join Channel 🔔", url=join_url),
        InlineKeyboardButton("✅ Verify", callback_data="verify_membership")
    ]])
    
    await update.message.reply_text(
        f"⚠️ <b>You must join {FORCE_JOIN_CHANNEL} to use this bot!</b>\n\n"
        f"Please join the channel and click 'Verify'.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    return False

# =========================
# Command Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    # Force join check
    if not await ensure_membership(update, context):
        return
    
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
    help_text = (
        "<b>✨ SpotifyX Musix Bot — Commands ✨</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>User Commands:</b>\n"
        "<code>/start</code> — Start bot\n"
        "<code>/help</code> — Show this help\n"
        "<code>/search &lt;name&gt;</code> — Search YouTube\n"
        "<code>/gen &lt;prompt&gt;</code> — Generate AI image\n"
        "<code>/gpt &lt;query&gt;</code> — Chat with AI\n"
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

async def gpt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /gpt command - Chat with MegaLLM AI"""
    ensure_user(update)
    
    # Force join check
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gpt <your question>\nExample: `/gpt What is the capital of France?`")
        return

    status_msg = await update.message.reply_text("🤖 Thinking...")

    try:
        async with aiohttp.ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {MEGALLM_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "gpt-3.5-turbo",  # Adjust if MegaLLM uses different model names
                "messages": [{"role": "user", "content": query}],
                "max_tokens": 1000,
                "temperature": 0.7
            }
            
            async with session.post(MEGALLM_API_URL, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    await status_msg.edit_text(f"❌ API Error {resp.status}: {error_text[:100]}")
                    return
                
                data = await resp.json()
                response_text = data["choices"][0]["message"]["content"].strip()

        # Split long responses (Telegram limit: 4096 chars)
        if len(response_text) > 4000:
            response_text = response_text[:4000] + "\n\n... (truncated)"
        
        await status_msg.edit_text(
            f"💬 <b>Query:</b> <code>{query}</code>\n\n"
            f"<b>Answer:</b>\n{response_text}\n\n"
            f"───\nGenerated by MegaLLM AI",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ AI Error: {str(e)[:200]}")

async def whereis_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    cwd = Path.cwd()
    cookies_path = COOKIES_TXT.absolute()
    
    debug_info = (
        f"📁 <b>File Location Debug</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Working Directory:</b>\n<code>{cwd}</code>\n\n"
        f"<b>Files in /app:</b>\n"
    )
    
    files = list(cwd.glob("*"))
    file_list = "\n".join([f"• {f.name}" for f in files[:10]])
    debug_info += f"<code>{file_list}</code>\n\n"
    debug_info += f"<b>Cookies path:</b>\n<code>{cookies_path}</code>\n"
    debug_info += f"<b>Exists:</b> {cookies_path.exists()}"
    
    await update.message.reply_text(debug_info, parse_mode=ParseMode.HTML)

async def testcookies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    cookies_file, status = validate_cookies()
    
    if not cookies_file:
        await update.message.reply_text(f"❌ {status}", parse_mode=ParseMode.HTML)
        return
    
    await update.message.reply_text("✅ Cookies file is valid!")

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    
    if not await ensure_membership(update, context):
        return
    
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
    
    if not await ensure_membership(update, context):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Usage: /gen <description>")
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
        await update.message.reply_photo(photo=image_path, caption=caption, parse_mode=ParseMode.HTML)
        await status_msg.delete()
        image_path.unlink(missing_ok=True)

    except Exception as e:
        await status_msg.edit_text(f"❌ Failed: {e}")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized!")
        return
    
    if not MONGO_AVAILABLE:
        return
        
    total = users_col.count_documents({})
    docs = users_col.find().limit(50)
    preview = "\n".join([f"{d['name']} — {d['_id']}" for d in docs])
    await update.message.reply_text(f"👥 Users: {total}\n\n{preview}")

# [ADDADMIN, RMADMIN, ADMINLIST, BROADCAST HANDLERS remain same as before]

# ... (Keep all the broadcast, admin handlers from previous version) ...
# For brevity, I'm including them but collapsed in this prompt view
# --- START: Include previous broadcast and admin handlers here ---
# [Paste the broadcast_cmd, handle_broadcast_message, done_broadcast_cmd, 
#  send_broadcast_cmd, cancel_broadcast_cmd, addadmin_cmd, rmadmin_cmd, 
#  adminlist_cmd functions from the previous complete code]
# --- END: Include previous handlers ---

# SINCE I CAN'T REFER BACK, I'LL REWRITE THEM COMPACTLY:
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return await update.message.reply_text("❌ Not authorized!")
    BROADCAST_STORE[update.effective_user.id] = []; BROADCAST_STATE[update.effective_user.id] = True
    await update.message.reply_text("📢 Broadcast mode ON. Send messages, then /done_broadcast or /cancel_broadcast", parse_mode=ParseMode.HTML)

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id): return
    msg_data = {
        "text": update.message.text,
        "photo": update.message.photo[-1].file_id if update.message.photo else None,
        "video": update.message.video.file_id if update.message.video else None,
        "document": update.message.document.file_id if update.message.document else None,
        "animation": update.message.animation.file_id if update.message.animation else None,
        "caption": update.message.caption,
        "parse_mode": ParseMode.HTML if update.message.caption_entities else None
    }
    BROADCAST_STORE[admin_id].append(msg_data)
    await update.message.reply_text(f"✅ Message #{len(BROADCAST_STORE[admin_id])} added")

async def done_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id): await update.message.reply_text("❌ Not in broadcast mode."); return
    if not BROADCAST_STORE.get(admin_id): await update.message.reply_text("❌ No messages to preview."); return
    await update.message.reply_text("📢 Preview:", parse_mode=ParseMode.HTML)
    for msg in BROADCAST_STORE[admin_id]:
        if msg["photo"]: await update.message.reply_photo(photo=msg["photo"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["video"]: await update.message.reply_video(video=msg["video"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["document"]: await update.message.reply_document(document=msg["document"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["animation"]: await update.message.reply_animation(animation=msg["animation"], caption=msg["caption"], parse_mode=msg["parse_mode"])
        elif msg["text"]: await update.message.reply_text(msg["text"], parse_mode=Parse_mode.HTML)
    await update.message.reply_text("✅ Preview done. Send /send_broadcast to send or /cancel_broadcast to cancel.", parse_mode=ParseMode.HTML)

async def send_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    admin_id = update.effective_user.id
    if not BROADCAST_STATE.get(admin_id): return
    messages = BROADCAST_STORE.get(admin_id, [])
    if not messages: await update.message.reply_text("❌ No messages."); return
    recipients = set()
    if MONGO_AVAILABLE:
        for u in users_col.find({}, {"_id": 1}): recipients.add(u["_id"])
    await update.message.reply_text(f"📢 Broadcasting to {len(recipients)}...")
    success, failed = 0, 0
    for chat_id in recipients:
        try:
            for msg in messages:
                if msg["photo"]: await context.bot.send_photo(chat_id=chat_id, photo=msg["photo"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["video"]: await context.bot.send_video(chat_id=chat_id, video=msg["video"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["document"]: await context.bot.send_document(chat_id=chat_id, document=msg["document"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["animation"]: await context.bot.send_animation(chat_id=chat_id, animation=msg["animation"], caption=msg["caption"], parse_mode=msg["parse_mode"])
                elif msg["text"]: await context.bot.send_message(chat_id=chat_id, text=msg["text"], parse_mode=ParseMode.HTML)
            success += 1
        except Exception as e:
            log.error(f"Broadcast failed to {chat_id}: {e}")
            failed += 1
        await asyncio.sleep(0.05)
    BROADCAST_STORE.pop(admin_id, None); BROADCAST_STATE[admin_id] = False
    await update.message.reply_text(f"✅ Broadcast Complete!\n📤 Successful: {success}\n❌ Failed: {failed}", parse_mode=ParseMode.HTML)

async def cancel_broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    admin_id = update.effective_user.id
    BROADCAST_STORE.pop(admin_id, None); BROADCAST_STATE[admin_id] = False
    await update.message.reply_text("❌ Broadcast cancelled.")

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text("❌ Owner only!")
    if not context.args: return await update.message.reply_text("Usage: /addadmin <user_id>")
    try:
        new_id = int(context.args[0])
        user = users_col.find_one({"_id": new_id})
        if not user: return await update.message.reply_text("❌ User not found. They must /start first.")
        if admins_col.find_one({"_id": new_id}): return await update.message.reply_text("❌ Already admin.")
        admins_col.insert_one({"_id": new_id, "name": user.get("name", str(new_id)), "added_by": update.effective_user.id, "added_at": datetime.now()})
        await update.message.reply_text(f"✅ Added <b>{user.get('name', new_id)}</b> as admin.", parse_mode=ParseMode.HTML)
    except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

async def rmadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text("❌ Owner only!")
    if not context.args: return await update.message.reply_text("Usage: /rmadmin <user_id>")
    try:
        rm_id = int(context.args[0])
        if rm_id == OWNER_ID: return await update.message.reply_text("❌ Cannot remove owner!")
        if not admins_col.find_one({"_id": rm_id}): return await update.message.reply_text("❌ Not an admin.")
        admins_col.delete_one({"_id": rm_id})
        await update.message.reply_text(f"✅ Removed admin.", parse_mode=ParseMode.HTML)
    except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return await update.message.reply_text("❌ Not authorized!")
    if not MONGO_AVAILABLE: return
    try:
        admins = list(admins_col.find().sort("added_at", -1))
        if not admins: return await update.message.reply_text("No admins.")
        admin_list = "👥 <b>Admin List</b>\n━━━━━━━━━━━━\n\n"
        for admin in admins:
            admin_id = admin["_id"]
            name = admin.get("name", "Unknown")
            role = "👑 Owner" if admin_id == OWNER_ID else "🔧 Admin"
            admin_list += f"• <code>{admin_id}</code> - {name} ({role})\n"
        admin_list += f"\n<b>Total: {len(admins)}</b>"
        await update.message.reply_text(admin_list, parse_mode=ParseMode.HTML)
    except Exception as e: await update.message.reply_text(f"❌ Failed: {e}")

# =========================
# Callback Handlers
# =========================
async def on_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try: _, token, qlt = q.data.split("|")
    except: return
    data = PENDING.get(token)
    if not data or data["exp"] < asyncio.get_event_loop().time():
        await q.edit_message_text("Session expired."); return
    await q.edit_message_text(f"Downloading {qlt}…")
    await download_and_send(q.message.chat.id, q.message, context, data["url"], qlt)

async def on_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try: _, token, _ = q.data.split("|")
    except: return
    data = PENDING.get(token)
    if not data or data["exp"] < asyncio.get_event_loop().time():
        await q.edit_message_text("Expired."); return
    await q.edit_message_text("Choose quality:", reply_markup=quality_keyboard(data["url"]))

async def on_verify_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify channel membership callback"""
    q = update.callback_query
    await q.answer()
    
    try:
        member = await context.bot.get_chat_member(
            chat_id=FORCE_JOIN_CHANNEL,
            user_id=q.from_user.id
        )
        if member.status not in ["left", "kicked"]:
            await q.edit_message_text("✅ Verified! You can now use the bot.")
            await start(update, context)  # Re-show start message
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
    
    # Membership check for all text messages
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
        await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))

# =========================
# Main Function
# =========================
def main():
    import signal, sys
    
    def shutdown_handler(signum, frame):
        log.info("Shutting down...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    
    # Debug on startup
    log.info("="*50)
    log.info(f"FORCE_JOIN_CHANNEL: {FORCE_JOIN_CHANNEL}")
    log.info(f"COOKIES_TXT exists: {COOKIES_TXT.exists()}")
    log.info("="*50)
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("gpt", gpt_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("gen", gen_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("done_broadcast", done_broadcast_cmd))
    app.add_handler(CommandHandler("send_broadcast", send_broadcast_cmd))
    app.add_handler(CommandHandler("cancel_broadcast", cancel_broadcast_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("rmadmin", rmadmin_cmd))
    app.add_handler(CommandHandler("adminlist", adminlist_cmd))
    app.add_handler(CommandHandler("testcookies", testcookies_cmd))
    app.add_handler(CommandHandler("whereis", whereis_cmd))

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

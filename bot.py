# bot.py
import os
import re
import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Set, Optional, Tuple

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp
import subprocess

# =========================
# CONFIG / ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "7941244038"))
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "")
COOKIES_TXT = os.getenv("COOKIES_TXT")  # optional; paste full cookies.txt contents

# Paths
DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"
DOWNLOAD_DIR = Path("downloads"); DOWNLOAD_DIR.mkdir(exist_ok=True)

# Limits
AUDIO_SIZE_LIMIT = 49 * 1024 * 1024  # ~49MB for sendAudio compatibility
DOC_SIZE_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB

# Logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("ytbot")

# Memory
PENDING: Dict[str, str] = {}
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)

# =========================
# Helpers
# =========================
def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def ensure_user(update: Update):
    users = load_json(USERS_FILE, {})
    u = update.effective_user
    if not u:
        return
    key = str(u.id)
    disp = (u.full_name or u.username or str(u.id)).strip()
    if key not in users:
        users[key] = {"name": disp}
        save_json(USERS_FILE, users)

def get_admins() -> Set[int]:
    data = load_json(ADMINS_FILE, {"admins": [OWNER_ID]})
    ids = set()
    for i in data.get("admins", []):
        try:
            ids.add(int(i))
        except Exception:
            pass
    if OWNER_ID not in ids:
        ids.add(OWNER_ID)
    return ids

def set_admins(admin_ids: Set[int]):
    save_json(ADMINS_FILE, {"admins": sorted(list(admin_ids))})

def is_admin(user_id: int) -> bool:
    return user_id in get_admins()

def sanitize_filename(name: str) -> str:
    # remove bad filesystem chars
    name = name.replace("/", "-").replace("\\", "-")
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]+', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "output"
    return name

def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    token = str(abs(hash((url, os.urandom(4)))))[:10]
    PENDING[token] = url
    btns = [
        [InlineKeyboardButton("360p", callback_data=f"q|{token}|360"),
         InlineKeyboardButton("480p", callback_data=f"q|{token}|480")],
        [InlineKeyboardButton("720p", callback_data=f"q|{token}|720"),
         InlineKeyboardButton("1080p", callback_data=f"q|{token}|1080")],
        [InlineKeyboardButton("MP3", callback_data=f"q|{token}|mp3")],
    ]
    return InlineKeyboardMarkup(btns)

# =========================
# yt-dlp core (with optional cookie support)
# =========================
async def download_and_send(chat_id: int, reply_message, context: ContextTypes.DEFAULT_TYPE, url: str, quality: str):
    # reply_message is a Message instance we'll use to reply to the user
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass  # ignore if chat action fails

    # base ydl options
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "noplaylist": True,
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
        # restrict height
        try:
            h = int(quality)
        except Exception:
            h = 720
        ydl_opts["format"] = f"bestvideo[height<={h}]+bestaudio/best/best[height<={h}]"
        ydl_opts["merge_output_format"] = "mp4"

    # cookie support: if COOKIES_TXT provided, write a temporary cookie file
    if COOKIES_TXT:
        try:
            cookie_path = Path("/tmp/cookies.txt")
            cookie_path.write_text(COOKIES_TXT, encoding="utf-8")
            ydl_opts["cookiefile"] = str(cookie_path)
            log.info("cookiefile written to /tmp/cookies.txt")
        except Exception as e:
            log.exception("failed to write cookies: %s", e)

    # run yt-dlp
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = sanitize_filename(info.get("title") or "output")
    except Exception as e:
        log.exception("yt-dlp failed")
        await reply_message.reply_text(f"⚠️ Download failed: {e}")
        return

    # find the latest file with expected extension
    ext = ".mp3" if quality == "mp3" else ".mp4"
    candidates = sorted(DOWNLOAD_DIR.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        await reply_message.reply_text("⚠️ Download finished but file not found.")
        return
    final_path = candidates[0]

    # small sanity
    try:
        if final_path.stat().st_size < 512:
            await reply_message.reply_text("⚠️ Downloaded file is too small (incomplete).")
            return
    except Exception:
        pass

    caption = f"Here ya go 😎\n Downloaded by :- @spotifyxmusixbot"

    # send file (mp3 vs mp4)
    try:
        if quality == "mp3":
            safe_name = f"{title}.mp3"
            safe_path = DOWNLOAD_DIR / safe_name
            if final_path != safe_path:
                try:
                    final_path.rename(safe_path)
                    final_path = safe_path
                except Exception:
                    data = final_path.read_bytes()
                    safe_path.write_bytes(data)
                    final_path = safe_path

            size = final_path.stat().st_size
            # use UPLOAD_DOCUMENT action for broader compatibility
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
            except Exception:
                pass

            if size <= AUDIO_SIZE_LIMIT:
                # try to send as audio; fallback to document if it fails
                try:
                    with open(final_path, "rb") as f:
                        await reply_message.reply_audio(audio=InputFile(f, filename=safe_name),
                                                        caption=caption, title=title, performer="@spotifyxmusixbot 🎧")
                except Exception as e:
                    log.exception("audio send failed, sending as document: %s", e)
                    with open(final_path, "rb") as f:
                        await reply_message.reply_document(InputFile(f, filename=safe_name), caption=f"(Fallback)\n{caption}")
            else:
                # big file => send as document
                with open(final_path, "rb") as f:
                    await reply_message.reply_document(InputFile(f, filename=safe_name), caption=caption)
        else:
            # video
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VIDEO)
            except Exception:
                pass
            with open(final_path, "rb") as f:
                # some PTB versions accept file-like objects; use InputFile wrapper
                await reply_message.reply_video(video=InputFile(f, filename=final_path.name), caption=caption)
    except Exception as e:
        log.exception("sending file failed")
        await reply_message.reply_text(f"⚠️ Upload failed: {e}")
        return

    # cleanup (remove other old files, keep final_path)
    try:
        for p in DOWNLOAD_DIR.glob("*"):
            if p.is_file() and p != final_path:
                try: p.unlink()
                except: pass
    except Exception:
        pass

# =========================
# Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = "Yo, I’m alive ⚡\nSend a YouTube link or use /search <query>\n"
    if UPDATES_CHANNEL:
        text += f"\nUpdates: {UPDATES_CHANNEL}"
    await update.message.reply_text(text)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = (
        "/start - start\n"
        "/help - this menu\n"
        "/search <query> - search YouTube (top 5)\n"
        "Send a YouTube link directly to get quality buttons\n\n"
        "Admin:\n"
        "/stats - user count + preview\n"
        "/broadcast <text> (or reply to media) - broadcast\n"
        "/addadmin <user_id> - add admin (owner only)\n"
        "/rmadmin <user_id> - remove admin (owner only)\n"
    )
    await update.message.reply_text(text)

async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = (update.message.text or "").strip()
    if not text:
        return
    # check for youtube link
    m = YOUTUBE_REGEX.search(text)
    if m:
        url = m.group(0)
        await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))
    else:
        # not a link — ignore to avoid spam
        return

async def on_quality_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    try:
        _kind, token, choice = data.split("|", 2)
    except Exception:
        await q.edit_message_text("Invalid selection.")
        return
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("Session expired. Send link again.")
        return
    # edit to show progress
    await q.edit_message_text(f"Downloading {choice}…")
    # use q.message to reply/send
    await download_and_send(chat_id=q.message.chat_id, reply_message=q.message, context=context, url=url, quality=choice)

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /search <query>")
        return
    await update.message.reply_text(f"Searching: {query}…")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "default_search": "ytsearch5",
        "extract_flat": "in_playlist",
        "noplaylist": True,
    }
    # apply cookies if present for search too (helps with blocked region results)
    if COOKIES_TXT:
        try:
            cookie_path = Path("/tmp/cookies.txt")
            cookie_path.write_text(COOKIES_TXT, encoding="utf-8")
            ydl_opts["cookiefile"] = str(cookie_path)
        except Exception:
            pass
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            res = ydl.extract_info(query, download=False)
            entries = res.get("entries", []) if isinstance(res, dict) else []
    except Exception as e:
        await update.message.reply_text(f"⚠️ Search failed: {e}")
        return
    if not entries:
        await update.message.reply_text("No results.")
        return
    buttons = []
    for e in entries[:5]:
        title = sanitize_filename(e.get("title") or "video")
        url = e.get("url") or e.get("webpage_url")
        if url and not url.startswith("http"):
            url = f"https://youtube.com/watch?v={url}"
        token = str(abs(hash((url, os.urandom(4)))))[:10]
        PENDING[token] = url
        buttons.append([InlineKeyboardButton(title[:60], callback_data=f"s|{token}|pick")])
    await update.message.reply_text("Pick a result:", reply_markup=InlineKeyboardMarkup(buttons))

async def on_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    q = update.callback_query
    await q.answer()
    try:
        _kind, token, _ = q.data.split("|", 2)
    except Exception:
        return
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("Expired. Use /search again.")
        return
    await q.edit_message_text("Choose quality:", reply_markup=quality_keyboard(url))

# Admin commands
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    users = load_json(USERS_FILE, {})
    count = len(users)
    preview_lines = [f"{meta.get('name','')} — {uid}" for uid, meta in users.items()][:60]
    preview = "\n".join(preview_lines)
    await update.message.reply_text(f"Users: {count}\n\n{preview}")
    # attach full file if large
    if count and len(preview) > 3500:
        try:
            tmp = DATA_DIR / "users_list.json"
            tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
            await update.message.reply_document(InputFile(tmp), caption=f"All users ({count})")
        except Exception:
            pass

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    users = load_json(USERS_FILE, {})
    targets = [int(uid) for uid in users.keys()]
    # if reply to media -> broadcast that
    if update.message.reply_to_message:
        src = update.message.reply_to_message
        sent = 0
        for uid in targets:
            try:
                if src.photo:
                    file_id = src.photo[-1].file_id
                    await context.bot.send_photo(uid, file_id, caption=src.caption or "")
                elif src.video:
                    await context.bot.send_video(uid, src.video.file_id, caption=src.caption or "")
                elif src.audio:
                    await context.bot.send_audio(uid, src.audio.file_id, caption=src.caption or "")
                elif src.document:
                    await context.bot.send_document(uid, src.document.file_id, caption=src.caption or "")
                else:
                    await context.bot.send_message(uid, src.text or "")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await update.message.reply_text(f"Broadcasted to {sent}/{len(targets)} users.")
        return
    # else text broadcast
    text = " ".join(context.args) if context.args else None
    if not text:
        await update.message.reply_text("Reply to a media OR use: /broadcast <text>")
        return
    sent = 0
    for uid in targets:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
            await asyncio.sleep(0.03)
        except Exception:
            pass
    await update.message.reply_text(f"Broadcasted to {sent}/{len(targets)} users.")

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        uid = int(context.args[0])
    except Exception:
        await update.message.reply_text("Invalid user_id")
        return
    admins = get_admins(); admins.add(uid); set_admins(admins)
    await update.message.reply_text(f"Added admin: {uid}")

async def rmadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id != OWNER_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /rmadmin <user_id>")
        return
    try:
        uid = int(context.args[0])
    except Exception:
        await update.message.reply_text("Invalid user_id")
        return
    admins = get_admins();
    if uid == OWNER_ID:
        await update.message.reply_text("Owner cannot be removed.")
        return
    if uid in admins:
        admins.remove(uid); set_admins(admins)
        await update.message.reply_text(f"Removed admin: {uid}")
    else:
        await update.message.reply_text("Not an admin.")

# =========================
# Main
# =========================
def main():
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN env var")

    # debug: show data folder contents in logs
    try:
        print("DEBUG → data folder contents:", os.listdir("data") if os.path.exists("data") else "NO DATA FOLDER")
    except Exception:
        pass

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("rmadmin", rmadmin_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_quality_pressed, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))

    # message handler (only links)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_or_link))

    log.info("Bot running…")
    app.run_polling()

if __name__ == "__main__":
    main()

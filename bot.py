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
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "@tonystark_jr")
COOKIES_TXT = os.getenv("COOKIES_TXT")

# Paths
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("ytbot")

# Globals
PENDING: Dict[str, str] = {}
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)
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

def is_admin(user_id: int) -> bool:
    admins = load_json(ADMINS_FILE, {"admins": [OWNER_ID]}).get("admins", [])
    return int(user_id) in admins or int(user_id) == OWNER_ID

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

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

async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, quality: str):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
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
        ydl_opts["format"] = f"bestvideo[height<={quality}]+bestaudio/best"

    # cookies
    if COOKIES_TXT:
        try:
            cookie_path = Path("/tmp/cookies.txt")
            cookie_path.write_text(COOKIES_TXT, encoding="utf-8")
            ydl_opts["cookiefile"] = str(cookie_path)
        except Exception as e:
            log.error(f"Failed writing cookie file: {e}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = sanitize_filename(info.get("title", "video"))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Download failed: {e}")
        return

    ext = ".mp3" if quality == "mp3" else ".mp4"
    file = sorted(DOWNLOAD_DIR.glob(f"*{ext}"), key=lambda p: p.stat().st_mtime, reverse=True)[0]
    cap = f"Here ya go 😎\nSource: {url}"

    try:
        if quality == "mp3":
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_AUDIO)
            with open(file, "rb") as f:
                await update.message.reply_audio(audio=InputFile(f, filename=file.name), caption=cap, title=title)
        else:
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
            with open(file, "rb") as f:
                await update.message.reply_video(video=InputFile(f, filename=file.name), caption=cap)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Upload failed: {e}")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = (
        "Yo, I’m alive ⚡\n"
        "Send a YouTube link and pick a quality.\n\n"
        "Created by @mahadev_ki_iccha"
    )
    await update.message.reply_text(text)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "/start - wake up bot\n"
        "/help - show this menu\n"
        "/search <query> - search on YouTube\n"
        "Send a link to download 🎬"
    )
    await update.message.reply_text(txt)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = update.message.text.strip()
    match = YOUTUBE_REGEX.search(text)
    if match:
        url = match.group(0)
        await update.message.reply_text("Choose quality 👇", reply_markup=quality_keyboard(url))

async def on_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        _, token, qual = q.data.split("|")
    except ValueError:
        return
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("Session expired.")
        return
    await q.edit_message_text(f"Downloading {qual}…")
    fake = Update(update.update_id, message=q.message)
    await download_and_send(fake, context, url, qual)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(on_quality, pattern=r"^q\|"))
    log.info("✅ Bot running with cookie support.")
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import re
import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Set, List, Optional, Tuple

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
# ENV / CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # <- set on Railway/Koyeb env vars
OWNER_ID = int(os.getenv("OWNER_ID", "7941244038"))  # your TG ID
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "@tonystark_jr")  # optional, shown in /start
COOKIES_TXT_ENV = os.getenv("COOKIES_TXT")  # optional: paste cookies file content as env var

# File persistence (ephemeral on redeploys)
DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"
DOWNLOAD_DIR = Path("downloads"); DOWNLOAD_DIR.mkdir(exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("ytbot")

# In-memory map: short id -> URL (for callbacks)
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

def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_user(update: Update) -> None:
    users = load_json(USERS_FILE, {})  # {str(uid): {"name": "..."}}
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
    cookiefile = write_cookies_if_any()


def human_list_users(users_map: Dict[str, Dict[str, str]], limit: int = 60) -> Tuple[str, Optional[str]]:
    lines = []
    for uid, meta in users_map.items():
        name = meta.get("name", uid)
        lines.append(f"{name} — {uid}")
    lines.sort()
    preview = "\n".join(lines[:limit])
    if len(lines) > limit:
        return preview + f"\n… and {len(lines) - limit} more", "\n".join(lines)
    return preview, None


def quality_keyboard(url: str) -> InlineKeyboardMarkup:
    # create a short token
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


def sanitize_filename(name: str) -> str:
    name = name.replace("/", "-").replace("\\", "-")
    name = re.sub(r"\s+", " ", name).strip()
    return name

# =========================
# yt-dlp core
# =========================

async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, quality: str):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    cookiefile = write_cookies_if_any()

    # Build format
    if quality == "mp3":
        ydl_opts = {
            "outtmpl": str(DOWNLOAD_DIR / "@spotifyxmusixbot - %(title)s.%(ext)s"),
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
    else:
        h = int(quality)
        ydl_opts = {
            "outtmpl": str(DOWNLOAD_DIR / "@spotifyxmusixbot - %(title)s.%(ext)s"),
            "format": f"bestvideo[height<={h}]+bestaudio/best/best[height<={h}]",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }

    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Find actual output path
            if quality == "mp3":
                out_ext = ".mp3"
            else:
                out_ext = ".mp4"
            base = ydl.prepare_filename(info)
            # base might include format tags; locate a file that exists with ext
            title = info.get("title") or "output"
            final_guess = DOWNLOAD_DIR / f"@spotifyxmusixbot - {sanitize_filename(title)}{out_ext}"
            if final_guess.exists():
                final_path = final_guess
            else:
                # fallback: scan directory for newest file
                files = sorted(DOWNLOAD_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
                final_path = files[0] if files else Path(base)

        # Telegram limits ~2GB for bot uploads
        size_mb = final_path.stat().st_size / (1024*1024)
        if size_mb > 1990 and quality != "mp3":
            await update.message.reply_text("File >2GB. Compressing to fit…")
            compressed = final_path.with_name(final_path.stem + "_720p.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", str(final_path),
                "-vf", "scale=-2:720",
                "-c:v", "libx264", "-b:v", "2000k", "-preset", "veryfast", "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "128k",
                str(compressed)
            ]
            subprocess.run(cmd, check=True)
            final_path = compressed

        await context.bot.send_chat_action(chat_id=update.effective_chat.id,
                                           action=ChatAction.UPLOAD_VIDEO if quality != "mp3" else ChatAction.UPLOAD_AUDIO)

        cap = f"Here ya go 😎\nSource: {url}"
        if quality == "mp3":
            await update.message.reply_audio(audio=InputFile(final_path), caption=cap)
        else:
            await update.message.reply_video(video=InputFile(final_path), caption=cap)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")


# =========================
# Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    msg = [
        "yo, I’m alive ⚡",
        "send a YouTube link and pick a quality, or /help",
    ]
    if UPDATES_CHANNEL:
        msg.append(f"updates: {UPDATES_CHANNEL}")
    await update.message.reply_text("\n".join(msg))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = (
        "Commands:\n"
        "/start – welcome\n"
        "/help – this menu\n"
        "/search <query> – search YouTube and pick from results\n"
        "(send a YouTube link directly to get quality buttons)\n\n"
        "Admin only:\n"
        "/stats – show user count + list\n"
        "/broadcast <text> – send text broadcast to all users (or reply to a photo/video to broadcast media)\n"
        "/addadmin <user_id> – owner only\n"
        "/rmadmin <user_id> – owner only\n"
    )
    await update.message.reply_text(text)


async def handle_text_or_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    text = (update.message.text or "").strip()

    # Only react to YouTube links. Otherwise ignore (to avoid triggering on all msgs)
    if not YOUTUBE_REGEX.search(text):
        return

    url = YOUTUBE_REGEX.search(text).group(0)
    await update.message.reply_text("Choose quality:", reply_markup=quality_keyboard(url))


async def on_quality_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    try:
        _kind, token, choice = data.split("|", 2)
    except ValueError:
        return
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("Session expired. Send the link again.")
        return
    await q.edit_message_text(f"Downloading {choice}…")
    await download_and_send(update=Update(update.update_id, message=q.message), context=context, url=url, quality=choice)


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
    if write_cookies_if_any():
        ydl_opts["cookiefile"] = "/tmp/cookies.txt"

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

    # Show top 5 as buttons -> pressing leads to quality menu
    buttons = []
    for e in entries[:5]:
        title = sanitize_filename(e.get("title") or "video")
        url = e.get("url") or e.get("webpage_url")
        if url and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        token = str(abs(hash((url, os.urandom(4)))))[:10]
        PENDING[token] = url
        buttons.append([InlineKeyboardButton(title[:60], callback_data=f"s|{token}|pick")])

    await update.message.reply_text("Pick a result:", reply_markup=InlineKeyboardMarkup(buttons))


async def on_search_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    try:
        _kind, token, _ = data.split("|", 2)
    except ValueError:
        return
    url = PENDING.get(token)
    if not url:
        await q.edit_message_text("Expired. Use /search again.")
        return
    await q.edit_message_text("Choose quality:", reply_markup=quality_keyboard(url))


# -------- Admin / Owner --------

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    users = load_json(USERS_FILE, {})
    count = len(users)
    preview, full = human_list_users(users)
    await update.message.reply_text(f"Users: {count}\n\n{preview}")
    if full and len(full) > 3500:
        tmp = DATA_DIR / "users_list.txt"
        tmp.write_text(full, encoding="utf-8")
        await update.message.reply_document(InputFile(tmp), caption=f"All users ({count})")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    users = load_json(USERS_FILE, {})
    targets = [int(uid) for uid in users.keys()]

    # Case 1: reply to media (photo/video/document/audio) -> broadcast that
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

    # Case 2: text argument
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

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("rmadmin", rmadmin_cmd))

    # Callback buttons
    app.add_handler(CallbackQueryHandler(on_quality_pressed, pattern=r"^q\|"))
    app.add_handler(CallbackQueryHandler(on_search_pick, pattern=r"^s\|"))

    # Only respond to messages that are YouTube links (no generic text)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_or_link))

    log.info("Bot running…")
    app.run_polling()


if __name__ == "__main__":
    main()

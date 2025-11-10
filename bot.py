import os, shutil, asyncio
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import yt_dlp
import subprocess
from pathlib import Path

BOT_TOKEN = os.getenv("BOT_TOKEN")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

def write_cookies_if_any():
    cookies_env = os.getenv("COOKIES_TXT")
    if cookies_env:
        p = Path("/tmp/cookies.txt")
        p.write_text(cookies_env, encoding="utf-8")
        return str(p)
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send a YouTube link. I’ll pull 720p for you 😎")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = (update.message.text or "").strip()
    if not url.startswith("http"):
        await update.message.reply_text("Drop a valid link, boss.")
        return

    await update.message.reply_text("On it… downloading ⏳")
    cookiefile = write_cookies_if_any()

    ydl_opts = {
        "outtmpl": str(DOWNLOAD_DIR / "%(title)s.%(ext)s"),
        "format": "bestvideo[height<=720]+bestaudio/best/best[height<=720]",
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
            fn = Path(ydl.prepare_filename(info))
            # ensure final merged mp4 path
            if fn.suffix != ".mp4":
                mp4 = fn.with_suffix(".mp4")
                if mp4.exists():
                    final_path = mp4
                else:
                    final_path = fn
            else:
                final_path = fn

        # if > ~1.9GB, compress to fit Telegram 2GB cap
        size_mb = final_path.stat().st_size / (1024*1024)
        if size_mb > 1900:
            await update.message.reply_text("Big boi file 😮 — compressing to fit Telegram…")
            compressed = final_path.with_name(final_path.stem + "_720p.mp4")
            # 720p ~2Mbps video + 128k audio
            cmd = [
                "ffmpeg", "-y", "-i", str(final_path),
                "-vf", "scale=-2:720",
                "-c:v", "libx264", "-b:v", "2000k", "-preset", "veryfast", "-movflags", "+faststart",
                "-c:a", "aac", "-b:a", "128k",
                str(compressed)
            ]
            subprocess.run(cmd, check=True)
            final_path = compressed

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
        await update.message.reply_video(video=open(final_path, "rb"), caption="here ya go 😎")

        # optional cleanup (keep latest only)
        for p in DOWNLOAD_DIR.glob("*"):
            try:
                if p.is_file():
                    p.unlink()
            except Exception:
                pass

    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN env var")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    print("Bot running on Koyeb 🚀")
    app.run_polling()


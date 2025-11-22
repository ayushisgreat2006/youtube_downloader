import os
import re
from pathlib import Path

# =========================
# ENVIRONMENT CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "7941244038"))
UPDATES_CHANNEL = os.getenv("UPDATES_CHANNEL", "")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "@tonystark_jr")
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "-5066591546"))
MEGALLM_API_KEY = os.getenv("MEGALLM_API_KEY", "sk-mega-c38fc3f49a44cb1ab5aef67538dc222e0c56c21de5dc8418afe1b9769b68300d")
MEGALLM_API_URL = "https://megallm.io/v1/chat/completions"

# Cookies
COOKIES_ENV = os.getenv("COOKIES_TXT")
if COOKIES_ENV and COOKIES_ENV.startswith('/'):
    COOKIES_TXT = Path(COOKIES_ENV)
else:
    COOKIES_TXT = Path(COOKIES_ENV or "cookies.txt")

# MongoDB
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "youtube_bot")
MONGO_USERS = os.getenv("MONGO_USERS", "users")
MONGO_ADMINS = os.getenv("MONGO_ADMINS", "admins")

# =========================
# CONSTANTS
# =========================
DOWNLOAD_DIR = Path("downloads")
MAX_FREE_SIZE = 50 * 1024 * 1024
PREMIUM_SIZE = 450 * 1024 * 1024

# Regex
YOUTUBE_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/[\w\-?&=/%]+", re.I)

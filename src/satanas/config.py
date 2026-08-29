import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv(os.path.join(BASE_DIR, ".env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SAT_RFC = os.getenv("SAT_RFC", "").strip().upper()
SAT_PASSWORD = os.getenv("SAT_PASSWORD", "")
ALLOWED_USER_IDS = {int(x) for x in os.getenv("ALLOWED_USER_IDS", "").split(",") if x.strip()}

CFDI_DIR = os.path.expanduser("~/.satanas")
CFDI_STATE_FILE = os.path.join(CFDI_DIR, "cfdi_session.json")
CFDI_DB = os.path.join(CFDI_DIR, "cfdi.db")
CFDI_FILES_DIR = os.path.join(CFDI_DIR, "files")

try:
    SYNC_MONTHS_BACK = max(1, int(os.getenv("SYNC_MONTHS_BACK", "12")))
except ValueError:
    SYNC_MONTHS_BACK = 12



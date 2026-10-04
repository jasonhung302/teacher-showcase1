"""系統設定：全部可用環境變數覆寫（見 env.example.json）。"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_JSON_PATH = BASE_DIR / "env.json"


def _load_json_env(path: Path) -> None:
    """讀取專案根目錄的 env.json 並寫入環境變數；已存在的環境變數優先。

    格式同 Azure App Service 應用程式設定的「進階編輯」：
    [{"name": "SECRET_KEY", "value": "...", "slotSetting": false}, ...]
    """
    if not path.is_file():
        return
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # 格式錯誤時直接中止，避免正式機默默用預設值（例如隨機 SECRET_KEY）啟動
        raise RuntimeError(f"{path.name} 不是合法的 JSON：{exc}") from exc
    if not isinstance(settings, list):
        raise RuntimeError(f"{path.name} 最外層必須是陣列（每筆含 name、value）")
    for item in settings:
        if not isinstance(item, dict) or "name" not in item:
            raise RuntimeError(f"{path.name} 的每一筆都必須是含 name 與 value 的物件")
        val = item.get("value")
        if val is None:
            continue
        # slotSetting 只有 Azure 用得到，本機讀取時忽略
        os.environ.setdefault(str(item["name"]), str(val))


_load_json_env(ENV_JSON_PATH)


def _bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class Config:
    SITE_NAME = os.environ.get("SITE_NAME", "智慧教室 教師數位課程展示平台")
    SITE_TAGLINE = os.environ.get("SITE_TAGLINE", "看見每一位老師的數位教學創新")

    # 正式環境一定要設定一組長隨機字串，否則每次重啟 Session 都會失效
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()

    # PostgreSQL 連線字串（必填），例：postgresql://user:password@host:5432/dbname?sslmode=require
    DATABASE_URL = os.environ.get("DATABASE_URL", "")

    # 上傳檔與備份的存放位置（資料庫本身在 PostgreSQL）
    INSTANCE_DIR = Path(os.environ.get("INSTANCE_DIR", BASE_DIR / "instance"))
    UPLOAD_FOLDER = Path(os.environ.get("UPLOAD_FOLDER", INSTANCE_DIR / "uploads"))

    # ---- 上傳限制 ----
    IMAGE_MAX_BYTES = _int("IMAGE_MAX_MB", 5) * 1024 * 1024
    PDF_MAX_BYTES = _int("PDF_MAX_MB", 20) * 1024 * 1024
    MAX_CONTENT_LENGTH = (_int("PDF_MAX_MB", 20) + 2) * 1024 * 1024   # 整個請求上限
    IMAGE_MAX_SIDE = 2000          # 圖片長邊縮至 2000px，並移除 EXIF（含 GPS）
    MAX_PHOTOS = 20

    # ---- Session ----
    SESSION_COOKIE_NAME = "ts_flash"           # Flask 內建 cookie：僅存 CSRF token 與提示訊息
    AUTH_COOKIE_NAME = "ts_sid"                # 登入 Session（伺服器端儲存）
    SESSION_IDLE_MINUTES = _int("SESSION_IDLE_MINUTES", 30)
    SESSION_ABSOLUTE_HOURS = _int("SESSION_ABSOLUTE_HOURS", 8)
    SESSION_COOKIE_SECURE = _bool("COOKIE_SECURE", False)    # HTTPS 上線時設 1
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # ---- 登入保護 ----
    LOGIN_MAX_FAILURES = 5
    LOGIN_LOCK_MINUTES = 15
    RESET_TOKEN_MINUTES = 30

    # ---- 郵件（未設定 SMTP_HOST 時，重設連結會寫進伺服器 log） ----
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = _int("SMTP_PORT", 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "no-reply@example.edu.tw")
    SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)
    BASE_URL = os.environ.get("BASE_URL", "")   # 例：https://teacher.example.edu.tw

    TIMEZONE = os.environ.get("TIMEZONE", "Asia/Taipei")
    TRUST_PROXY = _bool("TRUST_PROXY", False)   # 放在 Nginx 後面時設 1
    TESTING = False

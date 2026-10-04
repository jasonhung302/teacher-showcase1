"""資料庫存取：只使用參數化查詢（? 佔位符），杜絕 SQL Injection。"""
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app, g

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0)


def ts(dt=None):
    """回傳 UTC 時間字串，可直接用字串比較大小。"""
    return (dt or now_utc()).strftime(TS_FORMAT)


def local_today():
    """台北時間的今天日期字串 YYYY-MM-DD（瀏覽統計、公告、帳號期限使用）。"""
    from datetime import timedelta
    return (now_utc() + timedelta(hours=8)).strftime("%Y-%m-%d")


def ts_in(**delta):
    return ts(now_utc() + timedelta(**delta))


def parse_ts(value):
    if not value:
        return None
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


def connect(path):
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


# 舊版資料庫升級：只「新增」欄位，絕不刪除或改動既有資料。
# 新資料表由 schema.sql 的 CREATE TABLE IF NOT EXISTS 自動建立。
MIGRATION_COLUMNS = [
    ("users", "prev_login_at", "TEXT"),
    ("users", "valid_from", "TEXT"),
    ("users", "valid_until", "TEXT"),
    ("users", "keep_public_after_expiry", "INTEGER NOT NULL DEFAULT 1"),
    ("uploaded_files", "thumb_name", "TEXT"),
]


def _migrate(conn):
    for table, column, decl in MIGRATION_COLUMNS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_thumb ON uploaded_files(thumb_name)")
    # 舊版已發布的老師：把目前公開版本寫入發布歷史，之後才能還原
    conn.execute(
        "INSERT INTO publish_history (profile_id, snapshot, published_at)"
        " SELECT id, published_snapshot, published_at FROM teacher_profiles p"
        " WHERE status = 'published' AND published_snapshot IS NOT NULL AND published_at IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM publish_history h WHERE h.profile_id = p.id)")


def init_db(app):
    app.config["INSTANCE_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)
    conn = connect(app.config["DATABASE"])
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        # 先補欄位再跑 schema（schema 裡的索引可能用到新欄位）
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "users" in existing:
            for table, column, decl in MIGRATION_COLUMNS:
                if table in existing:
                    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                    if column not in cols:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        with app.open_resource("schema.sql") as f:
            conn.executescript(f.read().decode("utf-8"))
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def get_setting(key, default=None):
    row = query("SELECT value FROM settings WHERE key = ?", (key,), one=True)
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    execute("INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

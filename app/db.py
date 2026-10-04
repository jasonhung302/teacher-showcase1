"""資料庫存取（PostgreSQL / psycopg 3）：只使用參數化查詢（%s 佔位符），杜絕 SQL Injection。"""
from datetime import datetime, timedelta, timezone

import psycopg
from flask import current_app, g
from psycopg.rows import dict_row

TS_FORMAT = "%Y-%m-%d %H:%M:%S"
# 啟動建表用的 advisory lock：多個 worker／執行個體同時啟動時，一次只讓一個執行 schema.sql
SCHEMA_LOCK_ID = 7461001


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


def connect(dsn):
    """建立連線。採 autocommit：每個語句各自提交；多語句交易請用 `with conn.transaction():`。"""
    return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, connect_timeout=10)


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_URL"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, args=(), one=False):
    rows = get_db().execute(sql, args or None).fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, args=()):
    return get_db().execute(sql, args or None).rowcount


def insert(sql, args=()):
    """執行 INSERT 並回傳新資料列的 id（PostgreSQL 沒有 lastrowid，改用 RETURNING）。"""
    return get_db().execute(sql + " RETURNING id", args or None).fetchone()["id"]


def init_db(app):
    if not app.config.get("DATABASE_URL"):
        raise RuntimeError("未設定 DATABASE_URL：請在 env.json 或環境變數指定 PostgreSQL 連線字串（見 env.example.json）")
    app.config["INSTANCE_DIR"].mkdir(parents=True, exist_ok=True)
    app.config["UPLOAD_FOLDER"].mkdir(parents=True, exist_ok=True)
    with app.open_resource("schema.sql") as f:
        schema = f.read().decode("utf-8")
    conn = connect(app.config["DATABASE_URL"])
    try:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_ID,))
            conn.execute(schema)      # 不帶參數 → 可一次執行整份 schema（多個語句）
    finally:
        conn.close()


def get_setting(key, default=None):
    row = query("SELECT value FROM settings WHERE key = %s", (key,), one=True)
    return row["value"] if row and row["value"] is not None else default


def set_setting(key, value):
    execute("INSERT INTO settings (key, value) VALUES (%s, %s)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

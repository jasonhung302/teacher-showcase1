"""備份與還原：資料庫（每張資料表匯出成 CSV，不需停機）＋上傳檔，打包成一個 zip。

不依賴 pg_dump：用 PostgreSQL 的 COPY 直接匯出／匯入，伺服器版本升級也不受影響。
"""
import os
import re
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

from .db import connect
from .storage import get_storage

NAME_RE = re.compile(r"^backup-\d{8}-\d{6}(-\d+)?\.zip$")
UPLOAD_ENTRY_RE = re.compile(r"^uploads/[A-Za-z0-9_.-]+$")
KEEP = 14

# 備份涵蓋的資料表（依外鍵相依順序排列，還原時照此順序匯入）。
# 新增資料表時必須加到這裡，否則不會被備份（tests 會檢查是否與 schema.sql 一致）。
TABLES = (
    "users", "sessions", "password_resets", "teacher_profiles", "uploaded_files", "teacher_photos",
    "educations", "teaching_experiences", "specialties", "awards", "certifications", "courses",
    "lesson_plans", "audit_logs", "publish_history", "page_views", "announcements", "settings",
)


def backup_dir(cfg):
    d = cfg["INSTANCE_DIR"] / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _table_entry(table):
    return f"db/{table}.csv"


def _dump_tables(conn, folder):
    """把所有資料表匯出成 CSV。整個匯出在同一個快照內，備份期間的寫入不會造成資料表之間不一致。"""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        for table in TABLES:
            with open(os.path.join(folder, f"{table}.csv"), "wb") as fh, \
                    cur.copy(f"COPY {table} TO STDOUT WITH (FORMAT csv, HEADER true)") as copy:
                for chunk in copy:
                    fh.write(chunk)


def create_backup(cfg, prefix="backup", record=True):
    """建立備份，回傳 zip 路徑。可在網站執行中呼叫。"""
    stamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y%m%d-%H%M%S")
    target = backup_dir(cfg) / f"{prefix}-{stamp}.zip"
    n = 1
    while target.exists():                     # 同一秒內多次備份：加序號，絕不覆蓋
        target = backup_dir(cfg) / f"{prefix}-{stamp}-{n}.zip"
        n += 1
    with tempfile.TemporaryDirectory() as tmp, connect(cfg["DATABASE_URL"]) as conn:
        _dump_tables(conn, tmp)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for table in TABLES:
                zf.write(os.path.join(tmp, f"{table}.csv"), _table_entry(table))
            storage = get_storage(cfg)       # 本機資料夾或 Azure Blob 容器
            for name in storage.names():
                data = storage.get(name)
                if data is not None:
                    zf.writestr(f"uploads/{name}", data, compress_type=zipfile.ZIP_STORED)
        if not record:
            return target
        # 只保留最近 KEEP 份
        for old in list_backups(cfg)[KEEP:]:
            old["path"].unlink(missing_ok=True)
        conn.execute("INSERT INTO settings (key, value) VALUES ('last_backup_at', %s)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),))
    return target


def list_backups(cfg):
    items = []
    for p in backup_dir(cfg).iterdir():
        if NAME_RE.match(p.name):
            items.append({"name": p.name, "path": p, "size": p.stat().st_size})
    return sorted(items, key=lambda x: x["path"].stat().st_mtime, reverse=True)


def _csv_columns(zf, table, known):
    """讀取 CSV 標題列的欄位名稱；只接受資料表上真的存在的欄位（備份檔內容不可直接拿來組 SQL）。"""
    with zf.open(_table_entry(table)) as fh:
        header = fh.readline().decode("utf-8").strip()
    columns = header.split(",")
    unknown = [c for c in columns if c not in known]
    if not header or unknown:
        raise ValueError(f"備份檔的 {table} 欄位與目前資料庫不符：{', '.join(unknown) or '缺少標題列'}")
    return columns


def _load_tables(conn, zf):
    """以備份內容取代所有資料表。整個過程在同一個交易內：任何一步失敗，資料庫維持還原前的狀態。"""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL DEFERRED")      # teacher_profiles ↔ uploaded_files 互相參照
        cur.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        for table in TABLES:
            cur.execute("SELECT column_name FROM information_schema.columns"
                        " WHERE table_schema = current_schema() AND table_name = %s", (table,))
            known = {r["column_name"] for r in cur.fetchall()}
            columns = _csv_columns(zf, table, known)
            with zf.open(_table_entry(table)) as src, \
                    cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN WITH (FORMAT csv, HEADER true)") as copy:
                while chunk := src.read(64 * 1024):
                    copy.write(chunk)
            if "id" in known:
                # 匯入時帶著原本的 id，自動編號要接續在最大值之後，否則下一筆新增會撞號
                cur.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'),"
                            f" COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)", (table,))


def _mime_of(name):
    ext = name.rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "pdf": "application/pdf"}.get(ext)


def restore_backup(cfg, zip_path):
    """還原（請先停止網站）。目前資料會先另存一份 before-restore-*.zip，避免還原錯誤無法回頭。"""
    zip_path = os.fspath(zip_path)
    entries = {_table_entry(t) for t in TABLES}
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "app.db" in names:
            raise ValueError("這是舊版（SQLite）的備份檔，無法還原到 PostgreSQL")
        if not entries.issubset(names):
            raise ValueError("這不是本系統的備份檔（缺少資料表內容）")
        for n in names:
            if n not in entries and not UPLOAD_ENTRY_RE.match(n):
                raise ValueError(f"備份檔含有不明檔案：{n}")
        safety_named = create_backup(cfg, prefix="before-restore", record=False)
        with connect(cfg["DATABASE_URL"]) as conn:
            _load_tables(conn, zf)
        storage = get_storage(cfg)
        storage.clear()
        for n in names:
            if n.startswith("uploads/"):
                storage.put(n.split("/", 1)[1], zf.read(n), _mime_of(n))
    return safety_named

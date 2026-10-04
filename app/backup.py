"""備份與還原：資料庫（SQLite 線上備份，不需停機）＋上傳檔，打包成一個 zip。"""
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone

NAME_RE = re.compile(r"^backup-\d{8}-\d{6}(-\d+)?\.zip$")
KEEP = 14


def backup_dir(cfg):
    d = cfg["INSTANCE_DIR"] / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_backup(cfg, prefix="backup", record=True):
    """建立備份，回傳 zip 路徑。可在網站執行中呼叫。"""
    stamp = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y%m%d-%H%M%S")
    target = backup_dir(cfg) / f"{prefix}-{stamp}.zip"
    n = 1
    while target.exists():                     # 同一秒內多次備份：加序號，絕不覆蓋
        target = backup_dir(cfg) / f"{prefix}-{stamp}-{n}.zip"
        n += 1
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = os.path.join(tmp, "app.db")
        src = sqlite3.connect(str(cfg["DATABASE"]))
        dst = sqlite3.connect(db_copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_copy, "app.db")
            uploads = cfg["UPLOAD_FOLDER"]
            if uploads.is_dir():
                for p in sorted(uploads.iterdir()):
                    if p.is_file():
                        zf.write(p, f"uploads/{p.name}", compress_type=zipfile.ZIP_STORED)
    if not record:
        return target
    # 只保留最近 KEEP 份
    for old in list_backups(cfg)[KEEP:]:
        old["path"].unlink(missing_ok=True)
    conn = sqlite3.connect(str(cfg["DATABASE"]))
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES ('last_backup_at', ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()
    finally:
        conn.close()
    return target


def list_backups(cfg):
    items = []
    for p in backup_dir(cfg).iterdir():
        if NAME_RE.match(p.name):
            items.append({"name": p.name, "path": p, "size": p.stat().st_size})
    return sorted(items, key=lambda x: x["path"].stat().st_mtime, reverse=True)


def restore_backup(cfg, zip_path):
    """還原（請先停止網站）。目前資料會先另存一份 before-restore-*.zip，避免還原錯誤無法回頭。"""
    zip_path = os.fspath(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "app.db" not in names:
            raise ValueError("這不是本系統的備份檔（缺少 app.db）")
        for n in names:
            if n != "app.db" and not re.match(r"^uploads/[A-Za-z0-9_.-]+$", n):
                raise ValueError(f"備份檔含有不明檔案：{n}")
        safety_named = create_backup(cfg, prefix="before-restore", record=False)
        for suffix in ("", "-wal", "-shm"):
            p = cfg["DATABASE"].with_name(cfg["DATABASE"].name + suffix)
            if p.exists():
                p.unlink()
        uploads = cfg["UPLOAD_FOLDER"]
        if uploads.exists():
            shutil.rmtree(uploads)
        uploads.mkdir(parents=True)
        with zf.open("app.db") as src, open(cfg["DATABASE"], "wb") as dst:
            shutil.copyfileobj(src, dst)
        for n in names:
            if n.startswith("uploads/"):
                with zf.open(n) as src, open(uploads / n.split("/", 1)[1], "wb") as dst:
                    shutil.copyfileobj(src, dst)
    return safety_named

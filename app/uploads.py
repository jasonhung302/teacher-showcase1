"""檔案上傳：副檔名白名單 + 檔頭（magic bytes）檢查 + Pillow 重新編碼 + 隨機檔名。"""
import io
import json
import os
import re
import unicodedata
import uuid

from flask import current_app, g
from PIL import Image, ImageOps, UnidentifiedImageError

from .db import execute, query, ts

IMAGE_EXTS = {"jpg", "jpeg", "png"}
PDF_EXTS = {"pdf"}

# 每種用途允許的檔案類型
CATEGORY_KINDS = {
    "avatar": {"image"},
    "class_photo": {"image"},
    "lesson_plan": {"pdf"},
    "material": {"pdf"},
    "certificate": {"image", "pdf"},
}

Image.MAX_IMAGE_PIXELS = 50_000_000   # 防止解壓縮炸彈


class UploadError(ValueError):
    pass


def clean_original_name(name):
    """保留中文檔名供下載顯示，但移除路徑、控制字元與危險符號。"""
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*;]", "", name).strip(" .")
    if not name:
        name = "file"
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    return (stem[:80] + ("." + ext[:10] if ext else "")) or "file"


def _ext_of(name):
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _process_image(data, max_side):
    if not (data.startswith(b"\xff\xd8\xff") or data.startswith(b"\x89PNG\r\n\x1a\n")):
        raise UploadError("圖片內容不是有效的 JPG 或 PNG")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        if fmt not in ("JPEG", "PNG"):
            raise UploadError("圖片格式僅接受 JPG、PNG")
        img = ImageOps.exif_transpose(img)
        img.thumbnail((max_side, max_side))
        out = io.BytesIO()
        if fmt == "JPEG":
            img.convert("RGB").save(out, "JPEG", quality=85, optimize=True)   # 重新編碼 = 去除 EXIF/GPS
            return out.getvalue(), "jpg", "image/jpeg"
        if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            img = img.convert("RGBA")
        img.save(out, "PNG", optimize=True)
        return out.getvalue(), "png", "image/png"
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise UploadError("圖片檔案損毀或無法辨識") from exc


def _check_pdf(data):
    if not data.startswith(b"%PDF-"):
        raise UploadError("檔案內容不是有效的 PDF")
    if b"%%EOF" not in data[-2048:]:
        raise UploadError("PDF 檔案不完整")
    # 盡力阻擋內嵌腳本或啟動外部程式的 PDF
    if re.search(rb"/(JavaScript|JS|Launch|EmbeddedFile)\b", data):
        raise UploadError("PDF 含有腳本或內嵌檔案，基於安全考量無法上傳，請另存為一般 PDF 後再試")


def save_upload(file_storage, category, profile_id):
    """驗證並儲存檔案，回傳 uploaded_files.id。"""
    cfg = current_app.config
    if file_storage is None or not file_storage.filename:
        raise UploadError("請選擇要上傳的檔案")
    kinds = CATEGORY_KINDS[category]
    original = clean_original_name(file_storage.filename)
    ext = _ext_of(original)

    if ext in IMAGE_EXTS and "image" in kinds:
        limit = cfg["IMAGE_MAX_BYTES"]
        kind = "image"
    elif ext in PDF_EXTS and "pdf" in kinds:
        limit = cfg["PDF_MAX_BYTES"]
        kind = "pdf"
    else:
        allowed = " / ".join(sorted(({"JPG", "JPEG", "PNG"} if "image" in kinds else set())
                                    | ({"PDF"} if "pdf" in kinds else set())))
        raise UploadError(f"不支援的檔案格式，僅接受 {allowed}")

    data = file_storage.stream.read(limit + 1)
    if len(data) > limit:
        raise UploadError(f"檔案超過 {limit // 1024 // 1024} MB 上限")
    if not data:
        raise UploadError("檔案是空的")

    if kind == "image":
        data, ext, mime = _process_image(data, cfg["IMAGE_MAX_SIDE"])
        original = original.rsplit(".", 1)[0] + "." + ext
    else:
        _check_pdf(data)
        ext, mime = "pdf", "application/pdf"

    stored_name = f"{uuid.uuid4().hex}.{ext}"
    folder = cfg["UPLOAD_FOLDER"]
    folder.mkdir(parents=True, exist_ok=True)
    _write_new(folder / stored_name, data)
    thumb = make_thumbnail(data) if kind == "image" else None

    return execute(
        "INSERT INTO uploaded_files (profile_id, uploaded_by, category, stored_name, original_name,"
        " mime_type, size_bytes, thumb_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (profile_id, g.user["id"] if g.get("user") else None, category, stored_name,
         original, mime, len(data), thumb, ts()),
    )


def _write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)   # 絕不覆蓋既有檔案
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


THUMB_SIDE = 720


def make_thumbnail(data, folder=None):
    """產生 WebP 縮圖（長邊 720px），公開頁卡片與相簿使用，原圖只在放大檢視時載入。"""
    folder = folder or current_app.config["UPLOAD_FOLDER"]
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail((THUMB_SIDE, THUMB_SIDE))
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
            out = io.BytesIO()
            img.save(out, "WEBP", quality=80, method=4)
    except (OSError, ValueError):
        return None
    name = f"t_{uuid.uuid4().hex}.webp"
    _write_new(folder / name, out.getvalue())
    return name


def backfill_thumbnails(conn, folder):
    """舊版上傳的圖片沒有縮圖：啟動時補做一次（只處理缺少的）。"""
    rows = conn.execute("SELECT id, stored_name FROM uploaded_files"
                        " WHERE thumb_name IS NULL AND mime_type IN ('image/jpeg', 'image/png')").fetchall()
    for fid, name in rows:
        path = folder / name
        if not path.is_file():
            continue
        thumb = make_thumbnail(path.read_bytes(), folder)
        if thumb:
            conn.execute("UPDATE uploaded_files SET thumb_name = ? WHERE id = ?", (thumb, fid))
    conn.commit()


def published_file_names():
    """所有已發布頁面、以及可還原的發布歷史所引用的檔案（這些檔案即使被老師在草稿中刪除也要保留）。"""
    names = set()
    for row in query("SELECT published_snapshot AS s FROM teacher_profiles"
                     " WHERE status = 'published' AND published_snapshot IS NOT NULL"
                     " UNION ALL SELECT snapshot AS s FROM publish_history"):
        names.update(json.loads(row["s"]).get("files", []))
    return names


def discard_file(file_id):
    """草稿中移除檔案：先軟刪除，若沒有已發布頁面在用就立即清掉實體檔。"""
    if not file_id:
        return
    execute("UPDATE uploaded_files SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL", (ts(), file_id))
    purge_deleted_files()


def purge_deleted_files():
    keep = published_file_names()
    for row in query("SELECT id, stored_name, thumb_name FROM uploaded_files WHERE deleted_at IS NOT NULL"):
        if row["stored_name"] in keep:
            continue
        folder = current_app.config["UPLOAD_FOLDER"]
        path = folder / row["stored_name"]
        try:
            path.unlink(missing_ok=True)
            if row["thumb_name"]:
                (folder / row["thumb_name"]).unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception("刪除檔案失敗 %s", path)
            continue
        execute("DELETE FROM uploaded_files WHERE id = ?", (row["id"],))

"""檔案上傳：副檔名白名單 + 檔頭（magic bytes）檢查 + Pillow 重新編碼 + 隨機檔名。"""
import io
import json
import re
import unicodedata
import uuid

from flask import current_app, g
from PIL import Image, ImageOps, UnidentifiedImageError

from .db import execute, insert, query, ts
from .storage import get_storage

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
    storage = get_storage()
    storage.put(stored_name, data, mime)
    thumb = make_thumbnail(data, storage) if kind == "image" else None

    return insert(
        "INSERT INTO uploaded_files (profile_id, uploaded_by, category, stored_name, original_name,"
        " mime_type, size_bytes, thumb_name, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (profile_id, g.user["id"] if g.get("user") else None, category, stored_name,
         original, mime, len(data), thumb, ts()),
    )


THUMB_SIDE = 720


def make_thumbnail(data, storage=None):
    """產生 WebP 縮圖（長邊 720px），公開頁卡片與相簿使用，原圖只在放大檢視時載入。"""
    storage = storage or get_storage()
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
    storage.put(name, out.getvalue(), "image/webp")
    return name


def backfill_thumbnails(conn, storage):
    """替還沒有縮圖的圖片補做縮圖（啟動時與建立示範資料後執行，只處理缺少的）。"""
    rows = conn.execute("SELECT id, stored_name FROM uploaded_files"
                        " WHERE thumb_name IS NULL AND mime_type IN ('image/jpeg', 'image/png')").fetchall()
    for row in rows:
        data = storage.get(row["stored_name"])
        if data is None:
            continue
        thumb = make_thumbnail(data, storage)
        if thumb:
            conn.execute("UPDATE uploaded_files SET thumb_name = %s WHERE id = %s", (thumb, row["id"]))


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
    execute("UPDATE uploaded_files SET deleted_at = %s WHERE id = %s AND deleted_at IS NULL", (ts(), file_id))
    purge_deleted_files()


def purge_deleted_files():
    keep = published_file_names()
    storage = get_storage()
    for row in query("SELECT id, stored_name, thumb_name FROM uploaded_files WHERE deleted_at IS NOT NULL"):
        if row["stored_name"] in keep:
            continue
        try:
            storage.delete(row["stored_name"])
            if row["thumb_name"]:
                storage.delete(row["thumb_name"])
        except Exception:      # noqa: BLE001 — 本機 OSError 或 Azure 連線錯誤：下次再清
            current_app.logger.exception("刪除檔案失敗 %s", row["stored_name"])
            continue
        execute("DELETE FROM uploaded_files WHERE id = %s", (row["id"],))

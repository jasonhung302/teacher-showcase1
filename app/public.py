"""公開網站：首頁、教師一頁網、QR Code、檔案下載。訪客只能讀取『已發布快照』。"""
import io
import json
import re

from flask import (Blueprint, Response, abort, current_app, g, make_response, render_template, request,
                   send_file)

from .db import local_today, parse_ts, query
from .profiles import COUNTIES
from .qr import to_svg
from .services import (PUBLIC_VISIBLE_SQL, VIEW_COOKIE, public_url, record_view, seen_cookie_value, seen_today,
                       should_count_view, site_root, view_stats)
from .storage import get_storage

bp = Blueprint("public", __name__)

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


def published_rows():
    return query(
        "SELECT p.id, p.published_snapshot, p.published_at FROM teacher_profiles p JOIN users u ON u.id = p.user_id"
        f" WHERE p.status = 'published' AND p.published_snapshot IS NOT NULL AND {PUBLIC_VISIBLE_SQL}"
        " ORDER BY p.teacher_code", (local_today(),))


@bp.route("/")
def home():
    rows = published_rows()
    teachers = []
    for r in rows:
        t = json.loads(r["published_snapshot"])
        t["_pid"], t["_published_at"] = r["id"], r["published_at"]
        teachers.append(t)
    q = (request.args.get("q") or "").strip()[:50]
    county = request.args.get("county", "")
    subject = request.args.get("subject", "")
    school = (request.args.get("school") or "").strip()[:50]

    all_subjects = sorted({s for t in teachers for s in t.get("subjects", [])})
    used_counties = [c for c in COUNTIES if any(t["county"] == c for t in teachers)]

    def match(t):
        if county and t["county"] != county:
            return False
        if subject and subject not in t.get("subjects", []):
            return False
        if school and school.lower() not in t["school_name"].lower():
            return False
        if q:
            hay = " ".join([t["teacher_name"], t["school_name"], t["county"], t["job_title"],
                            " ".join(t.get("subjects", [])),
                            " ".join(c["title"] for c in t.get("courses", [])),
                            " ".join(s["name"] for s in t.get("specialties", []))]).lower()
            if q.lower() not in hay:
                return False
        return True

    filtering = bool(q or county or subject or school)
    results = [t for t in teachers if match(t)]

    # 熱門（近 30 天瀏覽）與最新發布，只在沒有篩選時顯示
    popular, latest, hot_courses = [], [], []
    if not filtering and len(teachers) >= 3:
        stats = view_stats()
        ranked = sorted(teachers, key=lambda t: stats.get(t["_pid"], {}).get("d30", 0), reverse=True)
        popular = [t for t in ranked if stats.get(t["_pid"], {}).get("d30", 0) > 0][:4]
        latest = sorted(teachers, key=lambda t: t["_published_at"] or "", reverse=True)[:4]
        for t in (popular or latest):
            for c in t.get("courses", [])[:1]:
                hot_courses.append({"teacher": t, "course": c})
    return render_template("public/home.html", teachers=results, total=len(teachers),
                           counties=used_counties, subjects=all_subjects, filtering=filtering,
                           popular=popular, latest=latest, hot_courses=hot_courses[:4],
                           filters={"q": q, "county": county, "subject": subject, "school": school})


def _published_or_404(code):
    if not CODE_RE.match(code):
        abort(404)
    row = query(
        "SELECT p.id, p.user_id, p.published_snapshot FROM teacher_profiles p JOIN users u ON u.id = p.user_id"
        f" WHERE p.teacher_code = %s AND p.status = 'published' AND {PUBLIC_VISIBLE_SQL}",
        (code, local_today()), one=True)
    if row is None or not row["published_snapshot"]:
        abort(404)   # 草稿、未發布、已停用、已到期：一律當作不存在
    return row


@bp.route("/teacher/<code>")
def teacher(code):
    row = _published_or_404(code)
    count = should_count_view(row["user_id"], g.get("user")) and not seen_today(code)
    if count:
        record_view(row["id"])
    resp = make_response(render_template("public/teacher.html", t=json.loads(row["published_snapshot"]),
                                         preview=False, page_url=public_url(code), site_root=site_root()))
    if count:
        resp.set_cookie(VIEW_COOKIE, seen_cookie_value(code), max_age=86400, httponly=True,
                        secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return resp


@bp.route("/teacher/<code>/qr.svg")
def teacher_qr(code):
    _published_or_404(code)
    resp = Response(to_svg(public_url(code)), mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.route("/files/<name>")
def file(name):
    """檔案存取規則：
       - 已發布快照有引用 → 任何人可讀（縮圖跟著原圖的權限）
       - 否則只有檔案擁有者本人或管理員可讀（例如草稿預覽、獲獎證明）
    """
    if not re.match(r"^(t_)?[0-9a-f]{32}\.(jpg|png|pdf|webp)$", name):
        abort(404)
    is_thumb = name.startswith("t_")
    f = query("SELECT f.*, p.user_id AS owner_id, p.status, p.published_snapshot, u.is_active, u.valid_until,"
              " u.keep_public_after_expiry FROM uploaded_files f JOIN teacher_profiles p ON p.id = f.profile_id"
              " JOIN users u ON u.id = p.user_id WHERE " + ("f.thumb_name = %s" if is_thumb else "f.stored_name = %s"),
              (name,), one=True)
    if f is None:
        abort(404)
    visible = f["is_active"] and (not f["valid_until"] or f["valid_until"] >= local_today()
                                  or f["keep_public_after_expiry"])
    public_ok = bool(f["status"] == "published" and visible and f["published_snapshot"]
                     and f["stored_name"] in json.loads(f["published_snapshot"]).get("files", []))
    user = g.get("user")
    private_ok = user is not None and f["deleted_at"] is None and (
        user["role"] == "admin" or user["id"] == f["owner_id"])
    if not (public_ok or private_ok):
        abort(404)
    data = get_storage().get(name)
    if data is None:
        abort(404)
    mime = "image/webp" if is_thumb else f["mime_type"]
    is_pdf = mime == "application/pdf"
    # 檔名是隨機產生且內容永不改變，直接當作 ETag
    resp = send_file(io.BytesIO(data), mimetype=mime, as_attachment=is_pdf, download_name=f["original_name"],
                     etag=name, last_modified=parse_ts(f["created_at"]),
                     max_age=3600 if public_ok else 0, conditional=True)
    resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    if not public_ok:
        resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /dashboard\nDisallow: /admin\nDisallow: /account\nSitemap: {site_root()}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    urls = [site_root() + "/"] + [public_url(json.loads(r["published_snapshot"])["teacher_code"])
                                  for r in published_rows()]
    body = ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(f"<url><loc>{u}</loc></url>" for u in urls) + "</urlset>")
    return Response(body, mimetype="application/xml")


@bp.route("/healthz")
def healthz():
    query("SELECT 1")
    return {"status": "ok"}

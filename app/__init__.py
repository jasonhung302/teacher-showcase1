"""Flask 應用程式工廠。"""
import logging
from zoneinfo import ZoneInfo

from flask import Flask, g, render_template, request, flash, redirect
from markupsafe import Markup, escape
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from .config import Config
from . import db as dbmod
from .security import (apply_auth_cookie, check_csrf, csrf_token, enforce_password_change,
                       load_logged_in_user)


def _backfill_thumbs(app):
    from .uploads import backfill_thumbnails
    conn = dbmod.connect(app.config["DATABASE_URL"])
    try:
        backfill_thumbnails(conn, app.config["UPLOAD_FOLDER"])
    except Exception:      # noqa: BLE001 — 縮圖失敗不影響網站啟動
        logging.getLogger(__name__).exception("產生縮圖失敗")
    finally:
        conn.close()


def create_app(overrides=None):
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    dbmod.init_db(app)
    app.teardown_appcontext(dbmod.close_db)
    _backfill_thumbs(app)

    # ---------------- 每個請求的安全檢查（順序很重要）
    @app.before_request
    def _security_gate():
        load_logged_in_user()          # 1. 驗證登入 Session
        check_csrf()                   # 2. 所有 POST 檢查 CSRF
        return enforce_password_change()   # 3. 首次登入強制改密碼

    @app.after_request
    def _security_headers(resp):
        apply_auth_cookie(resp)
        h = resp.headers
        h.setdefault("Content-Security-Policy",
                     "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self'; "
                     "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if app.config["SESSION_COOKIE_SECURE"]:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if g.get("user") is not None or request.path.startswith(("/dashboard", "/admin", "/account")):
            h["Cache-Control"] = "no-store"     # 後台頁面不被瀏覽器或代理快取
        return resp

    # ---------------- 模板工具
    try:
        tz = ZoneInfo(app.config["TIMEZONE"])
    except Exception:   # Windows 未安裝 tzdata 時，退回台灣固定時區 UTC+8
        from datetime import timedelta, timezone
        tz = timezone(timedelta(hours=8))

    @app.template_filter("localtime")
    def _localtime(value, fmt="%Y-%m-%d %H:%M"):
        dt = dbmod.parse_ts(value)
        return dt.astimezone(tz).strftime(fmt) if dt else "—"

    @app.template_filter("nl2br")
    def _nl2br(value):
        """先 escape 再換行，確保使用者輸入不會變成 HTML（防 XSS）。"""
        if not value:
            return ""
        return Markup("<br>".join(escape(line) for line in str(value).splitlines()))

    @app.template_filter("filesize")
    def _filesize(n):
        n = n or 0
        return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"

    @app.context_processor
    def _inject():
        from flask import url_for

        def eurl(name, **kw):
            """在『教師編輯器』中產生網址；同一套頁面供老師本人與管理員協助編輯共用。"""
            return url_for(f"{request.blueprint}.{name}", **kw)

        def file_url(name, thumb=None):
            """有縮圖就用縮圖（WebP，較小），沒有就用原圖。"""
            return url_for("public.file", name=thumb or name)

        return {
            "file_url": file_url,
            "csrf_token": csrf_token,
            "site_name": app.config["SITE_NAME"],
            "site_tagline": app.config["SITE_TAGLINE"],
            "current_user": g.get("user"),
            "eurl": eurl,
        }

    # ---------------- 藍圖
    from .auth import bp as auth_bp
    from .public import bp as public_bp
    from .admin import bp as admin_bp
    from .editor import bp as editor_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    # 同一個編輯器藍圖註冊兩次：
    #   /dashboard/...                      → 老師編輯自己的資料
    #   /admin/teachers/<uid>/edit/...      → 管理員協助修改指定老師
    app.register_blueprint(editor_bp, url_prefix="/dashboard", name="me")
    app.register_blueprint(editor_bp, url_prefix="/admin/teachers/<int:uid>/edit", name="assist")

    from .cli import register_cli
    register_cli(app)

    # ---------------- 錯誤頁
    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_e):
        mb = app.config["MAX_CONTENT_LENGTH"] // 1024 // 1024
        flash(f"檔案太大，單次上傳上限為 {mb} MB。", "error")
        return redirect(request.referrer or "/"), 303

    @app.errorhandler(HTTPException)
    def _http_error(e):
        titles = {400: "請求有誤", 403: "沒有權限", 404: "找不到頁面", 405: "不支援的操作",
                  429: "嘗試次數過多"}
        return render_template("errors/error.html", code=e.code,
                               title=titles.get(e.code, "發生錯誤"),
                               message=e.description if e.code in (400, 429) else None), e.code

    return app

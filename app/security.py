"""安全核心：密碼雜湊、伺服器端 Session、CSRF、權限裝飾器、操作紀錄。"""
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

from flask import abort, current_app, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import execute, parse_ts, query, ts, ts_in, now_utc

# ---------------------------------------------------------------- 密碼
_HASH_METHOD = "scrypt" if hasattr(hashlib, "scrypt") else "pbkdf2:sha256:600000"
# 帳號不存在時也做一次雜湊比對，讓回應時間一致，避免以時間差猜帳號
_DUMMY_HASH = generate_password_hash("dummy-password-for-timing", method=_HASH_METHOD)


def hash_password(raw):
    return generate_password_hash(raw, method=_HASH_METHOD)


def verify_password(pw_hash, raw):
    return check_password_hash(pw_hash or _DUMMY_HASH, raw or "")


def password_problems(raw, username=""):
    """回傳不符合密碼規則的原因清單（空清單代表通過）。"""
    problems = []
    if len(raw) < 8:
        problems.append("至少 8 個字元")
    if len(raw) > 128:
        problems.append("不可超過 128 個字元")
    if not re.search(r"[A-Za-z]", raw) or not re.search(r"\d", raw):
        problems.append("需同時包含英文字母與數字")
    if username and raw.lower() == username.lower():
        problems.append("不可與帳號相同")
    return problems


def generate_temp_password():
    """產生好唸好輸入的臨時密碼（避開 0/O、1/l 等易混字元）。"""
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz"
    digits = "23456789"
    body = "".join(secrets.choice(letters) for _ in range(6))
    return body + "".join(secrets.choice(digits) for _ in range(4))


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- Session
def create_login_session(user_id):
    token = secrets.token_urlsafe(32)
    execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sha256(token), user_id, ts(), ts(),
         ts_in(hours=current_app.config["SESSION_ABSOLUTE_HOURS"]),
         client_ip(), (request.user_agent.string or "")[:255]),
    )
    g.set_auth_cookie = token
    # 登入後換新 CSRF token，避免 session fixation
    session.pop("_csrf", None)


def destroy_current_session():
    token = request.cookies.get(current_app.config["AUTH_COOKIE_NAME"])
    if token:
        execute("DELETE FROM sessions WHERE token_hash = ?", (sha256(token),))
    g.clear_auth_cookie = True
    session.clear()


def destroy_user_sessions(user_id, keep_current=False):
    """密碼變更、重設、停用帳號時，踢出該使用者所有登入裝置。"""
    if keep_current and request.cookies.get(current_app.config["AUTH_COOKIE_NAME"]):
        current = sha256(request.cookies[current_app.config["AUTH_COOKIE_NAME"]])
        execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, current))
    else:
        execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def load_logged_in_user():
    """每個請求開頭執行：驗證 Cookie → 查 Session → 檢查逾時與帳號狀態。"""
    g.user = None
    g.session_expired = False
    token = request.cookies.get(current_app.config["AUTH_COOKIE_NAME"])
    if not token:
        return
    row = query(
        "SELECT s.id AS sid, s.last_seen_at, s.expires_at, u.* FROM sessions s"
        " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (sha256(token),), one=True,
    )
    if row is None:
        g.clear_auth_cookie = True
        return
    now = now_utc()
    idle_limit = current_app.config["SESSION_IDLE_MINUTES"] * 60
    last_seen = parse_ts(row["last_seen_at"])
    if (parse_ts(row["expires_at"]) <= now
            or (now - last_seen).total_seconds() > idle_limit
            or not row["is_active"]
            or not _window_ok(row)):
        execute("DELETE FROM sessions WHERE id = ?", (row["sid"],))
        g.clear_auth_cookie = True
        g.session_expired = bool(row["is_active"])
        return
    if (now - last_seen).total_seconds() > 60:   # 降低寫入頻率
        execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (ts(now), row["sid"]))
    g.user = row


def _window_ok(user):
    from .services import account_window_ok
    return account_window_ok(user)


def apply_auth_cookie(response):
    cfg = current_app.config
    if getattr(g, "set_auth_cookie", None):
        response.set_cookie(
            cfg["AUTH_COOKIE_NAME"], g.set_auth_cookie,
            max_age=cfg["SESSION_ABSOLUTE_HOURS"] * 3600,
            httponly=True, secure=cfg["SESSION_COOKIE_SECURE"], samesite="Lax", path="/",
        )
    elif getattr(g, "clear_auth_cookie", False):
        response.delete_cookie(cfg["AUTH_COOKIE_NAME"], path="/")
    return response


# ---------------------------------------------------------------- CSRF
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not expected or not sent or not hmac.compare_digest(sent, expected):
        abort(400, description="表單已失效（CSRF 驗證失敗），請重新整理頁面後再送出。")


# ---------------------------------------------------------------- 權限裝飾器
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


admin_required = role_required("admin")


def enforce_password_change():
    """首次登入 / 被重設密碼者，在改密碼前只能進入少數頁面。"""
    if g.user is None or not g.user["must_change_password"]:
        return None
    allowed = {"auth.change_password", "auth.logout", "static", "public.healthz"}
    if request.endpoint in allowed:
        return None
    flash("為了帳號安全，請先設定您的新密碼。", "warning")
    return redirect(url_for("auth.change_password"))


def safe_next_url(target):
    """只允許站內相對路徑，避免 Open Redirect。"""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


# ---------------------------------------------------------------- 其他
def client_ip():
    return (request.remote_addr or "")[:64]


def audit(action, target=None, detail=None, actor_id=None):
    if actor_id is None and getattr(g, "user", None) is not None:
        actor_id = g.user["id"]
    execute(
        "INSERT INTO audit_logs (actor_id, action, target, detail, ip, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (actor_id, action, target, (detail or "")[:500], client_ip(), ts()),
    )


class IpRateLimiter:
    """簡易記憶體型限流（單一程序）：同一 IP 在時間窗內最多 N 次。"""

    def __init__(self, limit, window_seconds):
        self.limit = limit
        self.window = window_seconds
        self.hits = defaultdict(deque)
        self.lock = Lock()

    def hit(self, key):
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self):
        with self.lock:
            self.hits.clear()


login_limiter = IpRateLimiter(limit=20, window_seconds=600)
forgot_limiter = IpRateLimiter(limit=5, window_seconds=600)

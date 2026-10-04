"""登入、登出、忘記密碼、重設密碼、修改密碼。"""
import secrets

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)

from .db import execute, parse_ts, query, ts, ts_in, now_utc
from .mail import send_mail
from .security import (audit, create_login_session, destroy_current_session, destroy_user_sessions,
                       forgot_limiter, hash_password, login_limiter, login_required,
                       password_problems, safe_next_url, sha256, verify_password, client_ip)

bp = Blueprint("auth", __name__)


def home_for(user):
    return url_for("admin.dashboard") if user["role"] == "admin" else url_for("me.overview")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(home_for(g.user))
    if g.get("session_expired"):
        flash("登入已逾時，請重新登入。", "warning")

    if request.method == "POST":
        if not login_limiter.hit(client_ip()):
            abort(429, description="登入嘗試次數過多，請 10 分鐘後再試。")
        username = request.form.get("username", "").strip()[:64]
        password = request.form.get("password", "")
        user = query("SELECT * FROM users WHERE username = ?", (username,), one=True)

        # 帳號鎖定檢查
        if user and user["locked_until"] and parse_ts(user["locked_until"]) > now_utc():
            verify_password(None, password)
            flash(f"登入失敗次數過多，帳號已暫時鎖定 {current_app.config['LOGIN_LOCK_MINUTES']} 分鐘。", "error")
            return render_template("auth/login.html", username=username), 429

        ok = verify_password(user["password_hash"] if user else None, password)
        if not ok or not user or not user["is_active"]:
            if user and not ok:
                fails = user["failed_login_count"] + 1
                locked = None
                if fails >= current_app.config["LOGIN_MAX_FAILURES"]:
                    locked, fails = ts_in(minutes=current_app.config["LOGIN_LOCK_MINUTES"]), 0
                execute("UPDATE users SET failed_login_count = ?, locked_until = ? WHERE id = ?",
                        (fails, locked, user["id"]))
                audit("login_failed", f"user:{user['id']}", actor_id=user["id"])
            # 不透露是帳號錯、密碼錯還是被停用
            flash("帳號或密碼錯誤，或帳號已停用。", "error")
            return render_template("auth/login.html", username=username), 401

        from .services import account_window_ok
        if not account_window_ok(user):
            audit("login_outside_validity", f"user:{user['id']}", actor_id=user["id"])
            flash("此帳號目前不在開放使用期間內，如有疑問請洽系統管理員。", "error")
            return render_template("auth/login.html", username=username), 403

        execute("UPDATE users SET failed_login_count = 0, locked_until = NULL, prev_login_at = last_login_at,"
                " last_login_at = ? WHERE id = ?", (ts(), user["id"]))
        create_login_session(user["id"])
        audit("login", f"user:{user['id']}", actor_id=user["id"])
        if user["must_change_password"]:
            return redirect(url_for("auth.change_password"))
        return redirect(safe_next_url(request.args.get("next")) or home_for(user))

    return render_template("auth/login.html", username="")


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user is not None:
        audit("logout", f"user:{g.user['id']}")
    destroy_current_session()
    flash("您已安全登出。", "success")
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    forced = bool(g.user["must_change_password"])
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        errors = []
        if not verify_password(g.user["password_hash"], current):
            errors.append("目前密碼不正確")
        if new != confirm:
            errors.append("兩次輸入的新密碼不一致")
        if current and new == current:
            errors.append("新密碼不可與目前密碼相同")
        errors += [f"新密碼{p}" for p in password_problems(new, g.user["username"])]
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("auth/change_password.html", forced=forced), 400
        execute("UPDATE users SET password_hash = ?, must_change_password = 0, password_changed_at = ?,"
                " updated_at = ? WHERE id = ?", (hash_password(new), ts(), ts(), g.user["id"]))
        destroy_user_sessions(g.user["id"], keep_current=True)   # 其他裝置全部登出
        audit("password_changed", f"user:{g.user['id']}")
        flash("密碼已更新。", "success")
        return redirect(home_for(g.user))
    return render_template("auth/change_password.html", forced=forced)


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        if not forgot_limiter.hit(client_ip()):
            abort(429, description="請求次數過多，請稍後再試。")
        ident = request.form.get("identifier", "").strip()[:120]
        user = query("SELECT * FROM users WHERE is_active = 1 AND (username = ? OR lower(email) = lower(?))",
                     (ident, ident), one=True) if ident else None
        if user and user["email"]:
            token = secrets.token_urlsafe(32)
            execute("UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                    (ts(), user["id"]))
            execute("INSERT INTO password_resets (user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                    (user["id"], sha256(token),
                     ts_in(minutes=current_app.config["RESET_TOKEN_MINUTES"]), ts()))
            base = current_app.config["BASE_URL"].rstrip("/") or request.host_url.rstrip("/")
            link = base + url_for("auth.reset_password", token=token)
            send_mail(
                user["email"], f"[{current_app.config['SITE_NAME']}] 重設密碼",
                f"{user['display_name']} 您好：\n\n請於 {current_app.config['RESET_TOKEN_MINUTES']} 分鐘內"
                f"點擊下列連結重設密碼：\n{link}\n\n若非您本人申請，請忽略此信，您的密碼不會被變更。",
            )
            audit("password_reset_requested", f"user:{user['id']}", actor_id=user["id"])
        # 無論帳號是否存在都顯示相同訊息，避免帳號列舉
        flash("若該帳號存在且設有 Email，重設連結已寄出，請於 30 分鐘內完成設定。"
              "沒有設定 Email 的老師，請洽系統管理員協助重設。", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    row = query("SELECT r.*, u.username FROM password_resets r JOIN users u ON u.id = r.user_id"
                " WHERE r.token_hash = ? AND r.used_at IS NULL AND u.is_active = 1",
                (sha256(token[:200]),), one=True)
    if row is None or parse_ts(row["expires_at"]) <= now_utc():
        flash("重設連結無效或已過期，請重新申請。", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        new = request.form.get("new_password", "")
        if new != request.form.get("confirm_password", ""):
            flash("兩次輸入的新密碼不一致", "error")
            return render_template("auth/reset_password.html", token=token), 400
        problems = password_problems(new, row["username"])
        if problems:
            for p in problems:
                flash(f"新密碼{p}", "error")
            return render_template("auth/reset_password.html", token=token), 400
        execute("UPDATE users SET password_hash = ?, must_change_password = 0, failed_login_count = 0,"
                " locked_until = NULL, password_changed_at = ?, updated_at = ? WHERE id = ?",
                (hash_password(new), ts(), ts(), row["user_id"]))
        execute("UPDATE password_resets SET used_at = ? WHERE id = ?", (ts(), row["id"]))
        destroy_user_sessions(row["user_id"])
        audit("password_reset_done", f"user:{row['user_id']}", actor_id=row["user_id"])
        flash("密碼已重設，請使用新密碼登入。", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", token=token)

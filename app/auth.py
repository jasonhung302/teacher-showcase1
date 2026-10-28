"""登入、登出、忘記密碼（請洽管理員）、修改密碼。"""
from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)

from .db import execute, parse_ts, query, ts, now_utc
from .security import (audit, create_login_session, destroy_current_session, destroy_user_sessions,
                       hash_password, login_limiter, login_required,
                       password_problems, safe_next_url, verify_password, client_ip)

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
        user = query("SELECT * FROM users WHERE lower(username) = lower(%s)", (username,), one=True)

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
                execute("UPDATE users SET failed_login_count = %s, locked_until = %s WHERE id = %s",
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
                " last_login_at = %s WHERE id = %s", (ts(), user["id"]))
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
        execute("UPDATE users SET password_hash = %s, must_change_password = 0, password_changed_at = %s,"
                " updated_at = %s WHERE id = %s", (hash_password(new), ts(), ts(), g.user["id"]))
        destroy_user_sessions(g.user["id"], keep_current=True)   # 其他裝置全部登出
        audit("password_changed", f"user:{g.user['id']}")
        flash("密碼已更新。", "success")
        return redirect(home_for(g.user))
    return render_template("auth/change_password.html", forced=forced)


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """忘記密碼一律由管理員在後台『重設密碼』產生臨時密碼，網站不寄送重設信。"""
    return render_template("auth/forgot_password.html")

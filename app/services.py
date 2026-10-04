"""跨模組共用的小功能：公開網址、瀏覽統計、公告、帳號有效期限。"""
from datetime import datetime, timedelta

from flask import current_app, request

from .db import execute, local_today, query


# ---------------------------------------------------------------- 公開網址
def site_root():
    base = current_app.config.get("BASE_URL", "").rstrip("/")
    return base or request.url_root.rstrip("/")


def public_url(code):
    return f"{site_root()}/teacher/{code}"


# ---------------------------------------------------------------- 帳號有效期限
def account_window_ok(user):
    """帳號是否在有效期間內（空白代表不限）。管理員帳號通常不設定。"""
    today = local_today()
    if user["valid_from"] and today < user["valid_from"]:
        return False
    if user["valid_until"] and today > user["valid_until"]:
        return False
    return True


# 公開頁可見條件：帳號啟用，且（未設定到期日／尚未到期／設定為到期後仍保留公開頁）
PUBLIC_VISIBLE_SQL = ("u.is_active = 1 AND (u.valid_until IS NULL OR u.valid_until = '' OR u.valid_until >= %s"
                      " OR u.keep_public_after_expiry = 1)")


# ---------------------------------------------------------------- 瀏覽統計
BOT_MARKERS = ("bot", "crawl", "spider", "slurp", "facebookexternalhit", "preview", "headless", "curl", "wget",
               "python-requests", "linkedinshare", "embedly", "whatsapp", "line-poker")
VIEW_COOKIE = "ts_seen"


def should_count_view(profile_owner_id, user):
    ua = (request.user_agent.string or "").lower()
    if not ua or any(m in ua for m in BOT_MARKERS):
        return False
    if user is not None and (user["role"] == "admin" or user["id"] == profile_owner_id):
        return False      # 老師自己看、管理員檢查都不計入
    return True


def seen_today(code):
    """同一瀏覽器同一天重複開啟只算一次。Cookie 只記『今天看過哪些代碼』，不含任何個人資料。"""
    raw = request.cookies.get(VIEW_COOKIE, "")
    day, _, codes = raw.partition(":")
    return day == local_today().replace("-", "") and code in codes.split(",")


def seen_cookie_value(code):
    raw = request.cookies.get(VIEW_COOKIE, "")
    day, _, codes = raw.partition(":")
    today = local_today().replace("-", "")
    lst = codes.split(",") if day == today and codes else []
    if code not in lst:
        lst.append(code)
    return f"{today}:{','.join(lst[-60:])}"


def record_view(profile_id):
    execute("INSERT INTO page_views (profile_id, day, views) VALUES (%s, %s, 1)"
            " ON CONFLICT(profile_id, day) DO UPDATE SET views = page_views.views + 1", (profile_id, local_today()))


def _day_offset(days):
    d = datetime.strptime(local_today(), "%Y-%m-%d") - timedelta(days=days - 1)
    return d.strftime("%Y-%m-%d")


def view_stats(profile_id=None):
    """回傳 {profile_id: {"total", "d7", "d30"}}；指定 profile_id 時只回傳該筆。"""
    d7, d30 = _day_offset(7), _day_offset(30)
    sql = ("SELECT profile_id, SUM(views) AS total,"
           " SUM(CASE WHEN day >= %s THEN views ELSE 0 END) AS d7,"
           " SUM(CASE WHEN day >= %s THEN views ELSE 0 END) AS d30 FROM page_views")
    args = [d7, d30]
    if profile_id is not None:
        sql += " WHERE profile_id = %s"
        args.append(profile_id)
    sql += " GROUP BY profile_id"
    stats = {r["profile_id"]: {"total": r["total"], "d7": r["d7"], "d30": r["d30"]} for r in query(sql, args)}
    if profile_id is not None:
        return stats.get(profile_id, {"total": 0, "d7": 0, "d30": 0})
    return stats


def daily_views(profile_id, days=30):
    start = _day_offset(days)
    rows = {r["day"]: r["views"] for r in query(
        "SELECT day, views FROM page_views WHERE profile_id = %s AND day >= %s", (profile_id, start))}
    out = []
    base = datetime.strptime(start, "%Y-%m-%d")
    for i in range(days):
        d = (base + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append((d, rows.get(d, 0)))
    return out


# ---------------------------------------------------------------- 公告
def active_announcements():
    today = local_today()
    return query("SELECT * FROM announcements"
                 " WHERE (starts_on IS NULL OR starts_on = '' OR starts_on <= %s)"
                 " AND (ends_on IS NULL OR ends_on = '' OR ends_on >= %s)"
                 " ORDER BY is_pinned DESC, id DESC", (today, today))

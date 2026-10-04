"""教師資料的讀取、公開快照（發布）、完成度計算。

重要設計：公開頁面一律由 build_public_snapshot() 產生，
它用『白名單』挑選欄位，手機與 Email 根本不會進入快照，因此不可能出現在公開頁。
"""
import json

from .db import execute, query, ts

COUNTIES = ["臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市", "嘉義市",
            "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
            "臺東縣", "澎湖縣", "金門縣", "連江縣"]

GRADES = ["一年級", "二年級", "三年級", "四年級", "五年級", "六年級", "七年級", "八年級", "九年級",
          "高一", "高二", "高三", "跨年級"]

SUBJECTS = ["國語文", "英語文", "本土語文", "數學", "自然科學", "社會", "藝術", "綜合活動", "健康與體育",
            "生活課程", "科技", "資訊", "跨領域", "其他"]

# ---------------------------------------------------------------- 多筆資料類型（設定驅動）
# 資料表名稱只從此白名單取得，絕不由使用者輸入組 SQL。
ITEM_TYPES = {
    "educations": {
        "table": "educations", "label": "學歷", "title_field": "school",
        "fields": [
            {"name": "school", "label": "學校", "required": True, "max": 100},
            {"name": "department", "label": "系所", "max": 100},
            {"name": "degree", "label": "學位", "type": "select", "options": ["學士", "碩士", "博士", "其他"]},
            {"name": "period", "label": "就讀期間", "max": 40, "placeholder": "例：2008–2012"},
        ],
    },
    "experiences": {
        "table": "teaching_experiences", "label": "教學經歷", "title_field": "organization",
        "fields": [
            {"name": "organization", "label": "服務單位", "required": True, "max": 100},
            {"name": "role", "label": "職務／任教", "max": 100, "placeholder": "例：五年級導師、資訊組長"},
            {"name": "period", "label": "期間", "max": 40, "placeholder": "例：2018–迄今"},
            {"name": "description", "label": "說明", "type": "textarea", "max": 500},
        ],
    },
    "specialties": {
        "table": "specialties", "label": "教學專長", "title_field": "name",
        "fields": [
            {"name": "name", "label": "專長項目", "required": True, "max": 50, "placeholder": "例：生成式 AI 融入教學"},
            {"name": "description", "label": "補充說明", "type": "textarea", "max": 300},
        ],
    },
    "awards": {
        "table": "awards", "label": "獲獎紀錄", "title_field": "title", "proof": True,
        "fields": [
            {"name": "title", "label": "獎項名稱", "required": True, "max": 120},
            {"name": "issuer", "label": "頒發單位", "max": 100},
            {"name": "award_date", "label": "獲獎年月", "max": 20, "placeholder": "例：2025-06"},
            {"name": "description", "label": "說明", "type": "textarea", "max": 500},
        ],
    },
    "certifications": {
        "table": "certifications", "label": "認證紀錄", "title_field": "name", "proof": True,
        "fields": [
            {"name": "name", "label": "認證名稱", "required": True, "max": 120},
            {"name": "issuer", "label": "發證單位", "max": 100},
            {"name": "cert_date", "label": "取得年月", "max": 20, "placeholder": "例：2024-11"},
            {"name": "description", "label": "說明", "type": "textarea", "max": 500},
        ],
    },
}

PROFILE_FIELDS = [
    # name, label, max, required_for_publish
    ("county", "縣市", 10, True),
    ("school_name", "學校全銜", 100, True),
    ("teacher_name", "講師姓名", 50, True),
    ("job_title", "職稱", 50, False),
    ("phone", "手機號碼", 20, False),
    ("contact_email", "電子郵件", 120, False),
    ("slogan", "Slogan", 60, False),
    ("philosophy", "教育理念", 1000, False),
]

COURSE_FIELDS = [
    ("grade", "授課年級", 20),
    ("student_count", "學生數", 5),
    ("subject", "科目／領域", 30),
    ("title", "課程名稱", 100),
    ("unit", "單元", 100),
    ("objectives", "學習目標", 1500),
    ("intro", "展示課程介紹", 2000),
    ("four_learning", "四學運用", 1500),
    ("ai_usage", "AI 運用", 1500),
]


def touch_profile(profile_id):
    execute("UPDATE teacher_profiles SET updated_at = %s WHERE id = %s", (ts(), profile_id))


def get_profile_by_user(user_id):
    return query("SELECT * FROM teacher_profiles WHERE user_id = %s", (user_id,), one=True)


def _file_name(file_id):
    if not file_id:
        return None
    row = query("SELECT stored_name FROM uploaded_files WHERE id = %s AND deleted_at IS NULL", (file_id,), one=True)
    return row["stored_name"] if row else None


def load_items(kind, profile_id):
    table = ITEM_TYPES[kind]["table"]
    sql = (f"SELECT t.*, f.stored_name AS proof_name, f.original_name AS proof_original "
           f"FROM {table} t LEFT JOIN uploaded_files f ON f.id = t.proof_file_id "
           f"WHERE t.profile_id = %s ORDER BY t.sort_order, t.id") if ITEM_TYPES[kind].get("proof") else \
          f"SELECT * FROM {table} WHERE profile_id = %s ORDER BY sort_order, id"
    return query(sql, (profile_id,))


def load_courses(profile_id):
    courses = []
    for c in query("SELECT * FROM courses WHERE profile_id = %s ORDER BY sort_order, id", (profile_id,)):
        plans = query(
            "SELECT lp.*, f.stored_name, f.original_name, f.size_bytes FROM lesson_plans lp"
            " JOIN uploaded_files f ON f.id = lp.file_id"
            " WHERE lp.course_id = %s AND lp.profile_id = %s ORDER BY lp.plan_type, lp.id",
            (c["id"], profile_id))
        courses.append({**dict(c), "plans": [dict(p) for p in plans]})
    return courses


def load_photos(profile_id):
    return query("SELECT p.*, f.stored_name, f.thumb_name FROM teacher_photos p"
                 " JOIN uploaded_files f ON f.id = p.file_id"
                 " WHERE p.profile_id = %s ORDER BY p.sort_order, p.id", (profile_id,))


def _file_row(file_id):
    if not file_id:
        return None
    return query("SELECT stored_name, thumb_name FROM uploaded_files WHERE id = %s AND deleted_at IS NULL",
                 (file_id,), one=True)


def build_public_snapshot(profile_id):
    """產生可公開的資料（預覽與發布共用同一份邏輯，所見即所得）。"""
    p = query("SELECT * FROM teacher_profiles WHERE id = %s", (profile_id,), one=True)
    files = []

    def ref(name):
        if name:
            files.append(name)
        return name

    av = _file_row(p["avatar_file_id"])
    avatar = ref(av["stored_name"]) if av else None
    avatar_thumb = av["thumb_name"] if av else None
    photos = [{"file": ref(r["stored_name"]), "thumb": r["thumb_name"], "caption": r["caption"] or ""}
              for r in load_photos(profile_id)]

    def pick(rows, keys):
        return [{k: (r[k] or "") for k in keys} for r in rows]

    courses = []
    for c in load_courses(profile_id):
        courses.append({
            **{k: (c[k] if c[k] is not None else "") for k, _l, _m in COURSE_FIELDS},
            "plans": [{"title": lp["title"], "type": lp["plan_type"], "file": ref(lp["stored_name"]),
                       "original_name": lp["original_name"], "size": lp["size_bytes"]} for lp in c["plans"]],
        })

    subjects = []
    for c in courses:
        if c["subject"] and c["subject"] not in subjects:
            subjects.append(c["subject"])

    return {
        # ---- 公開欄位白名單：phone / contact_email 刻意不在此 ----
        "teacher_code": p["teacher_code"],
        "county": p["county"] or "",
        "school_name": p["school_name"] or "",
        "teacher_name": p["teacher_name"] or "",
        "job_title": p["job_title"] or "",
        "slogan": p["slogan"] or "",
        "philosophy": p["philosophy"] or "",
        "avatar": avatar,
        "avatar_thumb": avatar_thumb,
        "photos": photos,
        "educations": pick(load_items("educations", profile_id), ["school", "department", "degree", "period"]),
        "experiences": pick(load_items("experiences", profile_id), ["organization", "role", "period", "description"]),
        "specialties": pick(load_items("specialties", profile_id), ["name", "description"]),
        "awards": pick(load_items("awards", profile_id), ["title", "issuer", "award_date", "description"]),
        "certifications": pick(load_items("certifications", profile_id), ["name", "issuer", "cert_date", "description"]),
        "courses": courses,
        "subjects": subjects,
        "files": files,
        "generated_at": ts(),
    }


def publish_problems(profile_id):
    p = query("SELECT * FROM teacher_profiles WHERE id = %s", (profile_id,), one=True)
    missing = [label for name, label, _m, req in PROFILE_FIELDS if req and not (p[name] or "").strip()]
    if not query("SELECT 1 FROM courses WHERE profile_id = %s LIMIT 1", (profile_id,)):
        missing.append("至少一門數位學習課程")
    return missing


HISTORY_KEEP = 10


def publish(profile_id, actor_id=None):
    snap = json.dumps(build_public_snapshot(profile_id), ensure_ascii=False)
    now = ts()
    execute("UPDATE teacher_profiles SET status = 'published', published_snapshot = %s, published_at = %s,"
            " updated_at = %s WHERE id = %s", (snap, now, now, profile_id))
    execute("INSERT INTO publish_history (profile_id, snapshot, published_at, published_by) VALUES (%s, %s, %s, %s)",
            (profile_id, snap, now, actor_id))
    # 只保留最近 N 個版本
    execute("DELETE FROM publish_history WHERE profile_id = %s AND id NOT IN"
            " (SELECT id FROM publish_history WHERE profile_id = %s ORDER BY id DESC LIMIT %s)",
            (profile_id, profile_id, HISTORY_KEEP))


def restore_version(profile_id, history_id, actor_id=None):
    """把公開頁換回某個歷史版本（只影響訪客看到的內容，不會覆蓋目前草稿）。"""
    row = query("SELECT * FROM publish_history WHERE id = %s AND profile_id = %s", (history_id, profile_id), one=True)
    if row is None:
        return False
    now = ts()
    execute("UPDATE teacher_profiles SET status = 'published', published_snapshot = %s, published_at = %s"
            " WHERE id = %s", (row["snapshot"], now, profile_id))
    execute("INSERT INTO publish_history (profile_id, snapshot, published_at, published_by) VALUES (%s, %s, %s, %s)",
            (profile_id, row["snapshot"], now, actor_id))
    return True


def publish_history(profile_id):
    rows = query("SELECT h.id, h.published_at, h.snapshot, u.display_name AS by_name, u.role AS by_role"
                 " FROM publish_history h LEFT JOIN users u ON u.id = h.published_by"
                 " WHERE h.profile_id = %s ORDER BY h.id DESC", (profile_id,))
    out = []
    for r in rows:
        snap = json.loads(r["snapshot"])
        out.append({"id": r["id"], "published_at": r["published_at"], "by_name": r["by_name"],
                    "by_role": r["by_role"], "courses": len(snap.get("courses", [])),
                    "photos": len(snap.get("photos", [])), "slogan": snap.get("slogan", "")})
    return out


def unpublish(profile_id):
    execute("UPDATE teacher_profiles SET status = 'draft', published_snapshot = NULL, updated_at = %s"
            " WHERE id = %s", (ts(), profile_id))


def has_unpublished_changes(p):
    return p["status"] == "published" and p["published_at"] and p["updated_at"] > p["published_at"]


def checklist(profile_id):
    """可操作的完成度清單：每一項都有『必填／選填』與『前往填寫』的連結目標。"""
    p = query("SELECT * FROM teacher_profiles WHERE id = %s", (profile_id,), one=True)

    def has(table):
        return bool(query(f"SELECT 1 FROM {table} WHERE profile_id = %s LIMIT 1", (profile_id,)))

    courses = query("SELECT * FROM courses WHERE profile_id = %s", (profile_id,))
    basic_missing = [label for name, label in (("county", "縣市"), ("school_name", "學校全銜"),
                                               ("teacher_name", "講師姓名"), ("job_title", "職稱"))
                     if not p[name]]
    contact_missing = [label for name, label in (("phone", "手機"), ("contact_email", "Email")) if not p[name]]
    lacking = lambda field: [c["title"] for c in courses if not c[field]]  # noqa: E731

    items = [
        # key, 標題, 必填?, 完成?, 說明（缺什麼）, (endpoint, kwargs, anchor)
        ("basic", "基本資料", True, not basic_missing, "缺：" + "、".join(basic_missing), ("profile", {}, "public")),
        ("contact", "聯繫資料（不公開）", True, not contact_missing, "缺：" + "、".join(contact_missing),
         ("profile", {}, "contact")),
        ("avatar", "個人照片", True, bool(p["avatar_file_id"]), "", ("profile", {}, "avatar")),
        ("philosophy", "教育理念與 Slogan", True, bool(p["philosophy"]) and bool(p["slogan"]),
         "缺：" + "、".join(x for x, ok in (("教育理念", p["philosophy"]), ("Slogan", p["slogan"])) if not ok),
         ("profile", {}, "public")),
        ("specialties", "教學專長", True, has("specialties"), "", ("items", {"kind": "specialties"}, None)),
        ("experiences", "教學經歷", True, has("teaching_experiences"), "", ("items", {"kind": "experiences"}, None)),
        ("photos", "課堂照片", True, has("teacher_photos"), "", ("photos", {}, None)),
        ("courses", "數位學習課程", True, bool(courses), "", ("courses", {}, None)),
        ("course_content", "學習目標與課程介紹", True,
         bool(courses) and not [c for c in courses if not (c["objectives"] and c["intro"])],
         "未完成：" + "、".join(c["title"] for c in courses if not (c["objectives"] and c["intro"])),
         ("courses", {}, None)),
        ("four", "四學運用", True, bool(courses) and not lacking("four_learning"),
         "未完成：" + "、".join(lacking("four_learning")), ("courses", {}, None)),
        ("ai", "AI 運用", True, bool(courses) and not lacking("ai_usage"),
         "未完成：" + "、".join(lacking("ai_usage")), ("courses", {}, None)),
        ("plans", "完整教案", True, has("lesson_plans"), "", ("courses", {}, None)),
        ("educations", "學歷", False, has("educations"), "", ("items", {"kind": "educations"}, None)),
        ("awards", "獲獎紀錄", False, has("awards"), "", ("items", {"kind": "awards"}, None)),
        ("certifications", "認證紀錄", False, has("certifications"), "", ("items", {"kind": "certifications"}, None)),
    ]
    out = []
    for key, label, required, done, hint, target in items:
        if not courses and key in ("course_content", "four", "ai"):
            hint = "請先新增課程"
        out.append({"key": key, "label": label, "required": required, "done": bool(done),
                    "hint": "" if done or hint.endswith("：") else hint, "target": target})
    return out


def completeness(profile_id):
    """必填項目的完成百分比，回傳 (百分比, 尚缺的必填項目)。"""
    items = [i for i in checklist(profile_id) if i["required"]]
    done = sum(1 for i in items if i["done"])
    return round(done * 100 / len(items)), [i["label"] for i in items if not i["done"]]

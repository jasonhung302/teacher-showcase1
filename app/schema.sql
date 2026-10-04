-- =====================================================================
-- 教師個人資料與數位課程展示網站系統  資料庫結構 (SQLite)
-- 時間欄位一律存 UTC，格式 'YYYY-MM-DD HH:MM:SS'，顯示時轉為台北時間
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------- 帳號 ----------
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash        TEXT    NOT NULL,                 -- scrypt 雜湊，永不存明碼
    role                 TEXT    NOT NULL CHECK (role IN ('admin', 'teacher')),
    display_name         TEXT    NOT NULL,
    email                TEXT,                             -- 帳號 Email（忘記密碼用）
    is_active            INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 1,       -- 首次登入強制改密碼
    failed_login_count   INTEGER NOT NULL DEFAULT 0,
    locked_until         TEXT,
    last_login_at        TEXT,
    prev_login_at        TEXT,                             -- 上一次登入（給老師看自己的登入資訊）
    valid_from           TEXT,                             -- 帳號有效期限（YYYY-MM-DD，空白=不限）
    valid_until          TEXT,
    keep_public_after_expiry INTEGER NOT NULL DEFAULT 1,  -- 帳號到期後公開頁是否保留
    password_changed_at  TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

-- ---------- 伺服器端 Session（可撤銷、可過期） ----------
CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash    TEXT    NOT NULL UNIQUE,                 -- 只存 SHA-256，Cookie 外洩 DB 也比對不回
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TEXT    NOT NULL,
    last_seen_at  TEXT    NOT NULL,
    expires_at    TEXT    NOT NULL,                        -- 絕對到期
    ip            TEXT,
    user_agent    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- ---------- 忘記密碼 ----------
CREATE TABLE IF NOT EXISTS password_resets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT    NOT NULL UNIQUE,
    expires_at  TEXT    NOT NULL,
    used_at     TEXT,
    created_at  TEXT    NOT NULL
);

-- ---------- 教師基本資料（與 users 1:1） ----------
CREATE TABLE IF NOT EXISTS teacher_profiles (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    teacher_code       TEXT    NOT NULL UNIQUE,            -- 公開網址 /teacher/001
    county             TEXT,                               -- 1-1 縣市
    school_name        TEXT,                               -- 1-2 學校全銜
    teacher_name       TEXT,                               -- 1-3 講師姓名
    job_title          TEXT,                               -- 1-4 職稱
    phone              TEXT,                               -- 1-5 手機（內部聯繫，絕不公開）
    contact_email      TEXT,                               -- 1-6 電子郵件（內部聯繫，絕不公開）
    philosophy         TEXT,                               -- 1-7 教育理念
    slogan             TEXT,                               -- 1-7 Slogan
    avatar_file_id     INTEGER REFERENCES uploaded_files(id) ON DELETE SET NULL,  -- 1-9 個人照片
    status             TEXT    NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    published_snapshot TEXT,                               -- 發布當下的公開資料 JSON（訪客只看得到這份）
    published_at       TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL                    -- 任何子資料變動都會更新
);

-- ---------- 上傳檔案（統一管理） ----------
CREATE TABLE IF NOT EXISTS uploaded_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    category      TEXT    NOT NULL CHECK (category IN ('avatar','class_photo','lesson_plan','material','certificate')),
    stored_name   TEXT    NOT NULL UNIQUE,                 -- 隨機 UUID 檔名，避免重複與路徑攻擊
    original_name TEXT    NOT NULL,                        -- 清理過的原始檔名（下載時使用）
    mime_type     TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    thumb_name    TEXT,                                  -- 圖片縮圖（WebP），列表與相簿使用
    created_at    TEXT    NOT NULL,
    deleted_at    TEXT                                     -- 軟刪除：已發布頁面仍引用時保留實體檔
);
CREATE INDEX IF NOT EXISTS idx_files_profile ON uploaded_files(profile_id);

-- ---------- 1-8 課堂照片 ----------
CREATE TABLE IF NOT EXISTS teacher_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    file_id     INTEGER NOT NULL REFERENCES uploaded_files(id),
    caption     TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_profile ON teacher_photos(profile_id);

-- ---------- 學歷 ----------
CREATE TABLE IF NOT EXISTS educations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    school      TEXT    NOT NULL,
    department  TEXT,
    degree      TEXT,
    period      TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edu_profile ON educations(profile_id);

-- ---------- 1-10 教學經歷 ----------
CREATE TABLE IF NOT EXISTS teaching_experiences (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id   INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    organization TEXT    NOT NULL,
    role         TEXT,
    period       TEXT,
    description  TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp_profile ON teaching_experiences(profile_id);

-- ---------- 1-10 教學專長 ----------
CREATE TABLE IF NOT EXISTS specialties (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spec_profile ON specialties(profile_id);

-- ---------- 1-11 獲獎 ----------
CREATE TABLE IF NOT EXISTS awards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    title         TEXT    NOT NULL,
    issuer        TEXT,
    award_date    TEXT,
    description   TEXT,
    proof_file_id INTEGER REFERENCES uploaded_files(id) ON DELETE SET NULL,  -- 證明文件（不公開）
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_awards_profile ON awards(profile_id);

-- ---------- 1-11 認證 ----------
CREATE TABLE IF NOT EXISTS certifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    name          TEXT    NOT NULL,
    issuer        TEXT,
    cert_date     TEXT,
    description   TEXT,
    proof_file_id INTEGER REFERENCES uploaded_files(id) ON DELETE SET NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_certs_profile ON certifications(profile_id);

-- ---------- 2-x 數位學習課程 ----------
CREATE TABLE IF NOT EXISTS courses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id     INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    grade          TEXT,                                  -- 2-1 授課年級
    student_count  INTEGER,                               -- 2-2 學生數
    subject        TEXT,                                  -- 2-3 科目/領域
    title          TEXT    NOT NULL,                      -- 2-4 課程名稱
    unit           TEXT,                                  -- 2-5 單元
    objectives     TEXT,                                  -- 2-6 學習目標
    intro          TEXT,                                  -- 2-7 展示課程介紹
    four_learning  TEXT,                                  -- 2-8 四學運用
    ai_usage       TEXT,                                  -- 2-9 AI 運用
    sort_order     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_courses_profile ON courses(profile_id);

-- ---------- 2-10 完整教案 / 教材附件 ----------
CREATE TABLE IF NOT EXISTS lesson_plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    course_id   INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    file_id     INTEGER NOT NULL REFERENCES uploaded_files(id),
    title       TEXT    NOT NULL,
    plan_type   TEXT    NOT NULL CHECK (plan_type IN ('lesson_plan', 'material')),
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_course ON lesson_plans(course_id);
CREATE INDEX IF NOT EXISTS idx_plans_profile ON lesson_plans(profile_id);

-- ---------- 操作紀錄 ----------
CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT    NOT NULL,
    target      TEXT,
    detail      TEXT,
    ip          TEXT,
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);

-- ---------- 發布歷史（可還原上一個已發布版本） ----------
CREATE TABLE IF NOT EXISTS publish_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    snapshot      TEXT    NOT NULL,
    published_at  TEXT    NOT NULL,
    published_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_history_profile ON publish_history(profile_id, id);

-- ---------- 公開頁瀏覽統計（只存每日總數，不記錄 IP 或個人資料） ----------
CREATE TABLE IF NOT EXISTS page_views (
    profile_id  INTEGER NOT NULL REFERENCES teacher_profiles(id) ON DELETE CASCADE,
    day         TEXT    NOT NULL,                          -- 台北時間 YYYY-MM-DD
    views       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, day)
);

-- ---------- 管理員公告 ----------
CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    body        TEXT,
    starts_on   TEXT,                                      -- YYYY-MM-DD，空白=立即
    ends_on     TEXT,                                      -- YYYY-MM-DD，空白=不限
    is_pinned   INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

-- ---------- 系統設定（填寫截止日、最近備份時間…） ----------
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

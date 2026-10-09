"""權限與安全測試（unittest）。

需要一個可連線的 PostgreSQL 測試資料庫，以環境變數 TEST_DATABASE_URL 指定
（預設 postgresql://localhost/teacher_showcase_test）。每個測試會在裡面建立專屬的 schema，結束後刪除。

執行：python -m unittest discover -s tests -t . -v
"""
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import psycopg
from PIL import Image
from psycopg.conninfo import make_conninfo

os.environ.setdefault("SECRET_KEY", "test-secret")
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/teacher_showcase_test")

from app import create_app                     # noqa: E402
from app.security import forgot_limiter, login_limiter  # noqa: E402


def png_bytes(size=(40, 30), color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg_with_exif():
    buf = io.BytesIO()
    img = Image.new("RGB", (50, 50), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # 每個測試一個獨立的 schema：互不干擾，也不會動到同一個資料庫裡的其他資料
        self.schema = f"test_{uuid.uuid4().hex}"
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA {self.schema}")
        self.addCleanup(self._drop_schema)
        self.dsn = make_conninfo(TEST_DATABASE_URL, options=f"-c search_path={self.schema}")
        self.app = create_app({"TESTING": True, "INSTANCE_DIR": Path(self.tmp), "DATABASE_URL": self.dsn,
                               "UPLOAD_FOLDER": Path(self.tmp) / "uploads"})
        login_limiter.reset()
        forgot_limiter.reset()
        with self.app.app_context():
            from app.seed import seed
            seed(self.app)
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _drop_schema(self):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    # ---- helpers
    def csrf(self, client=None, path="/login"):
        client = client or self.client
        html = client.get(path).get_data(as_text=True)
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        return m.group(1) if m else None

    def login(self, username, password="Demo1234", client=None):
        client = client or self.client
        token = self.csrf(client)
        return client.post("/login", data={"username": username, "password": password, "csrf_token": token})

    def post(self, path, data=None, client=None, csrf_from="/"):
        client = client or self.client
        data = dict(data or {})
        data["csrf_token"] = self.csrf(client, csrf_from)
        return client.post(path, data=data, content_type="multipart/form-data")

    def db(self, sql, args=()):
        """直接對測試資料庫下 SQL；回傳 tuple 資料列（非查詢語句回傳空清單）。"""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            cur = conn.execute(sql, args or None)
            return cur.fetchall() if cur.description else []

    def uid(self, username):
        return self.db("SELECT id FROM users WHERE username = %s", (username,))[0][0]

    def course_of(self, username):
        return self.db("SELECT c.id FROM courses c JOIN teacher_profiles p ON p.id = c.profile_id"
                       " JOIN users u ON u.id = p.user_id WHERE u.username = %s", (username,))[0][0]


class AuthTests(Base):
    def test_password_is_hashed(self):
        h = self.db("SELECT password_hash FROM users WHERE username='t001'")[0][0]
        self.assertNotIn("Demo1234", h)
        self.assertTrue(h.startswith(("scrypt:", "pbkdf2:")))

    def test_username_is_case_insensitive(self):
        """帳號不分大小寫：T001 可登入 t001，也不能另外建立只差大小寫的帳號。"""
        self.assertEqual(self.login("T001").status_code, 302)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.db("INSERT INTO users (username, password_hash, role, display_name, created_at, updated_at)"
                    " VALUES ('T001', 'x', 'teacher', 'dup', '-', '-')")
        c = self.app.test_client()
        self.login("admin", "Admin1234", client=c)
        r = self.post("/admin/teachers/new", {"username": "T002", "display_name": "重複", "teacher_code": "099"},
                      client=c, csrf_from="/admin/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("此帳號已被使用", r.get_data(as_text=True))

    def test_login_success_and_logout(self):
        r = self.login("t001")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/dashboard", r.headers["Location"])
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)
        self.post("/logout")
        self.assertEqual(self.client.get("/dashboard/").status_code, 302)

    def test_wrong_password_generic_message(self):
        r = self.login("t001", "wrong-pass1")
        self.assertEqual(r.status_code, 401)
        r2 = self.login("nobody", "wrong-pass1")
        self.assertEqual(r2.status_code, 401)

    def test_account_lockout(self):
        for _ in range(5):
            self.login("t002", "bad-pass1")
        r = self.login("t002", "Demo1234")          # 正確密碼也要被擋
        self.assertEqual(r.status_code, 429)

    def test_first_login_forces_password_change(self):
        r = self.login("t004")
        self.assertIn("/account/password", r.headers["Location"])
        r = self.client.get("/dashboard/")
        self.assertIn("/account/password", r.headers["Location"])
        r = self.post("/account/password", {"current_password": "Demo1234", "new_password": "short",
                                            "confirm_password": "short"}, csrf_from="/account/password")
        self.assertEqual(r.status_code, 400)
        r = self.post("/account/password", {"current_password": "Demo1234", "new_password": "NewPass2026",
                                            "confirm_password": "NewPass2026"}, csrf_from="/account/password")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_csrf_required(self):
        r = self.client.post("/login", data={"username": "t001", "password": "Demo1234"})
        self.assertEqual(r.status_code, 400)
        self.login("t001")
        r = self.client.post("/dashboard/publish")
        self.assertEqual(r.status_code, 400)

    def test_session_idle_timeout(self):
        self.login("t001")
        self.db("UPDATE sessions SET last_seen_at = '2000-01-01 00:00:00'")
        r = self.client.get("/dashboard/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_session_token_not_stored_in_plain(self):
        self.login("t001")
        cookie = self.client.get_cookie("ts_sid").value
        stored = self.db("SELECT token_hash FROM sessions")[0][0]
        self.assertNotEqual(cookie, stored)

    def test_forgot_and_reset_password(self):
        token = self.csrf(path="/forgot-password")
        with self.assertLogs(self.app.logger, "WARNING") as logs:
            self.client.post("/forgot-password", data={"identifier": "t001", "csrf_token": token})
        link = re.search(r"(/reset-password/\S+)", "\n".join(logs.output)).group(1)
        r = self.post(link, {"new_password": "Reset2026x", "confirm_password": "Reset2026x"}, csrf_from=link)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.login("t001", "Reset2026x").status_code, 302)
        # 連結只能用一次
        r = self.client.get(link)
        self.assertEqual(r.status_code, 302)

    def test_contact_email_fills_account_email(self):
        """管理員未設帳號 Email 時，老師自己填的 Email 會成為忘記密碼的收信信箱。"""
        self.assertIsNone(self.db("SELECT email FROM users WHERE username='t003'")[0][0])
        self.login("t003")
        base = {"county": "新北市", "school_name": "X", "teacher_name": "王小明"}
        # 自動儲存不寫入帳號 Email（避免存到打到一半的 Email）
        self.post("/dashboard/autosave/profile", {**base, "contact_email": "new.teacher@example.co"})
        self.assertIsNone(self.db("SELECT email FROM users WHERE username='t003'")[0][0])
        # 按「儲存草稿」才寫入
        self.post("/dashboard/profile", {**base, "contact_email": "new.teacher@example.com"})
        self.assertEqual(self.db("SELECT email FROM users WHERE username='t003'")[0][0], "new.teacher@example.com")
        # 寫入後老師再改不會變更帳號 Email
        self.post("/dashboard/profile", {**base, "contact_email": "other@example.com"})
        self.assertEqual(self.db("SELECT email FROM users WHERE username='t003'")[0][0], "new.teacher@example.com")
        token = self.csrf(client=self.app.test_client(), path="/forgot-password")
        with self.assertLogs(self.app.logger, "WARNING") as logs:
            self.app.test_client().post("/forgot-password", data={"identifier": "new.teacher@example.com",
                                                                  "csrf_token": token})
        self.assertIn("new.teacher@example.com", "\n".join(logs.output))

    def test_contact_email_filled_on_publish(self):
        self.db("UPDATE teacher_profiles SET contact_email = 'pub@example.com' WHERE teacher_code = '001'")
        self.db("UPDATE users SET email = NULL WHERE username='t001'")
        self.login("t001")
        self.post("/dashboard/publish")
        self.assertEqual(self.db("SELECT email FROM users WHERE username='t001'")[0][0], "pub@example.com")

    def test_fill_account_emails_cli(self):
        self.db("UPDATE teacher_profiles SET contact_email = 'cli@example.com' WHERE teacher_code = '001'")
        self.db("UPDATE users SET email = NULL WHERE username='t001'")
        out = self.app.test_cli_runner().invoke(args=["fill-account-emails"]).output
        self.assertIn("已補上 1 位", out)
        self.assertEqual(self.db("SELECT email FROM users WHERE username='t001'")[0][0], "cli@example.com")

    def test_contact_email_does_not_override_admin_email(self):
        self.db("UPDATE users SET email = 'admin.set@example.edu.tw' WHERE username='t001'")
        self.login("t001")
        self.post("/dashboard/profile", {"county": "新北市", "school_name": "X", "teacher_name": "林怡君",
                                         "contact_email": "changed@example.com"})
        self.assertEqual(self.db("SELECT email FROM users WHERE username='t001'")[0][0], "admin.set@example.edu.tw")

    def test_contact_email_taken_by_other_account_not_synced(self):
        taken = self.db("SELECT email FROM users WHERE username='t001'")[0][0]
        self.login("t003")
        r = self.post("/dashboard/profile", {"county": "新北市", "school_name": "X", "teacher_name": "王小明",
                                             "contact_email": taken})
        self.assertIsNone(self.db("SELECT email FROM users WHERE username='t003'")[0][0])
        self.assertIn("已被其他帳號使用", self.client.get(r.headers["Location"]).get_data(as_text=True))

    def test_open_redirect_blocked(self):
        token = self.csrf()
        r = self.client.post("/login?next=//evil.example.com", data={"username": "t001", "password": "Demo1234",
                                                                      "csrf_token": token})
        self.assertNotIn("evil", r.headers["Location"])


class PermissionTests(Base):
    def test_teacher_cannot_access_admin(self):
        self.login("t001")
        self.assertEqual(self.client.get("/admin/").status_code, 403)
        self.assertEqual(self.client.get(f"/admin/teachers/{self.uid('t002')}/edit/").status_code, 403)
        self.assertEqual(self.post(f"/admin/teachers/{self.uid('t002')}/reset-password").status_code, 403)

    def test_teacher_cannot_touch_other_teachers_course(self):
        self.login("t001")
        other = self.course_of("t002")
        self.assertEqual(self.client.get(f"/dashboard/courses/{other}/edit").status_code, 404)
        r = self.post(f"/dashboard/courses/{other}/edit", {"title": "HACKED"})
        self.assertEqual(r.status_code, 404)
        r = self.post(f"/dashboard/courses/{other}/delete")
        self.assertEqual(r.status_code, 404)
        title = self.db("SELECT title FROM courses WHERE id = %s", (other,))[0][0]
        self.assertNotEqual(title, "HACKED")

    def test_teacher_cannot_touch_other_teachers_items(self):
        self.login("t001")
        exp = self.db("SELECT e.id FROM teaching_experiences e JOIN teacher_profiles p ON p.id = e.profile_id"
                      " JOIN users u ON u.id = p.user_id WHERE u.username='t002'")[0][0]
        self.assertEqual(self.post(f"/dashboard/items/experiences/{exp}/delete").status_code, 404)
        self.assertEqual(len(self.db("SELECT 1 FROM teaching_experiences WHERE id = %s", (exp,))), 1)
        photo = self.db("SELECT ph.id FROM teacher_photos ph JOIN teacher_profiles p ON p.id = ph.profile_id"
                        " JOIN users u ON u.id = p.user_id WHERE u.username='t002'")[0][0]
        self.assertEqual(self.post(f"/dashboard/photos/{photo}/delete").status_code, 404)

    def test_unknown_item_kind_rejected(self):
        self.login("t001")
        self.assertEqual(self.client.get("/dashboard/items/users").status_code, 404)

    def test_teacher_cannot_read_other_draft_files(self):
        name = self.db("SELECT f.stored_name FROM uploaded_files f JOIN teacher_profiles p ON p.id=f.profile_id"
                       " JOIN users u ON u.id=p.user_id WHERE u.username='t003'")[0][0]
        self.assertEqual(self.client.get(f"/files/{name}").status_code, 404)        # 訪客
        self.login("t001")
        self.assertEqual(self.client.get(f"/files/{name}").status_code, 404)        # 其他老師
        admin = self.app.test_client()
        self.login("admin", "Admin1234", client=admin)
        self.assertEqual(admin.get(f"/files/{name}").status_code, 200)               # 管理員

    def test_admin_can_assist_edit(self):
        self.login("admin", "Admin1234")
        uid = self.uid("t002")
        self.assertEqual(self.client.get(f"/admin/teachers/{uid}/edit/").status_code, 200)
        cid = self.course_of("t002")
        r = self.post(f"/admin/teachers/{uid}/edit/courses/{cid}/edit", {"title": "管理員修正後的課程"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db("SELECT title FROM courses WHERE id=%s", (cid,))[0][0], "管理員修正後的課程")
        # 透過 t002 的網址也不能改 t001 的課程
        r = self.post(f"/admin/teachers/{uid}/edit/courses/{self.course_of('t001')}/edit", {"title": "x"})
        self.assertEqual(r.status_code, 404)

    def test_admin_create_disable_reset(self):
        self.login("admin", "Admin1234")
        r = self.post("/admin/teachers/new", {"username": "t005", "display_name": "新老師", "teacher_code": "005"})
        self.assertEqual(r.status_code, 200)
        temp = re.search(r'class="secret">([^<]+)<', r.get_data(as_text=True)).group(1)
        t = self.app.test_client()
        self.assertIn("/account/password", self.login("t005", temp, client=t).headers["Location"])
        uid = self.uid("t005")
        self.post(f"/admin/teachers/{uid}/toggle-active")
        self.assertEqual(t.get("/account/password").status_code, 302)     # 已被踢出
        self.assertEqual(self.login("t005", temp, client=t).status_code, 401)

    def test_admin_delete_teacher(self):
        uid = self.uid("t001")
        files = [n for row in self.db("SELECT f.stored_name, f.thumb_name FROM uploaded_files f"
                                      " JOIN teacher_profiles p ON p.id = f.profile_id WHERE p.user_id = %s", (uid,))
                 for n in row if n]
        self.assertTrue(files)
        t = self.app.test_client()
        self.login("t001", client=t)
        self.login("admin", "Admin1234")
        # 確認帳號輸入錯誤 → 不刪除
        self.post(f"/admin/teachers/{uid}/delete", {"confirm_username": "t002"})
        self.assertEqual(len(self.db("SELECT 1 FROM users WHERE id = %s", (uid,))), 1)
        # 教師本人不能刪除
        self.assertEqual(self.post(f"/admin/teachers/{uid}/delete", {"confirm_username": "t001"}, client=t)
                         .status_code, 403)
        r = self.post(f"/admin/teachers/{uid}/delete", {"confirm_username": "t001"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.db("SELECT 1 FROM users WHERE id = %s", (uid,)), [])
        self.assertEqual(self.db("SELECT 1 FROM teacher_profiles WHERE user_id = %s", (uid,)), [])
        for name in files:
            self.assertFalse((self.app.config["UPLOAD_FOLDER"] / name).exists())
        self.assertEqual(self.app.test_client().get("/teacher/001").status_code, 404)
        self.assertEqual(t.get("/dashboard/").status_code, 302)           # Session 已失效
        self.assertEqual(self.db("SELECT detail FROM audit_logs WHERE action = 'teacher_deleted'")[0][0][:4], "t001")
        self.assertEqual(self.post(f"/admin/teachers/{uid}/delete", {"confirm_username": "t001"}).status_code, 404)

    def test_disabled_teacher_page_hidden(self):
        self.login("admin", "Admin1234")
        self.post(f"/admin/teachers/{self.uid('t001')}/toggle-active")
        self.assertEqual(self.app.test_client().get("/teacher/001").status_code, 404)


class PublishTests(Base):
    def test_private_contact_never_public(self):
        html = self.client.get("/teacher/001").get_data(as_text=True)
        self.assertIn("林怡君", html)
        self.assertNotIn("0912-345-678", html)
        self.assertNotIn("yijun.lin@example.edu.tw", html)
        snap = self.db("SELECT published_snapshot FROM teacher_profiles WHERE teacher_code='001'")[0][0]
        self.assertNotIn("0912", snap)
        self.assertNotIn("phone", json.loads(snap))
        # 預覽也不能出現
        self.login("t001")
        prev = self.client.get("/dashboard/preview").get_data(as_text=True)
        self.assertNotIn("0912-345-678", prev)

    def test_draft_edits_not_visible_until_publish(self):
        self.login("t001")
        self.post("/dashboard/profile", {"county": "新北市", "school_name": "新北市板橋區示範國民小學",
                                         "teacher_name": "林怡君", "slogan": "全新的草稿標語"})
        visitor = self.app.test_client()
        self.assertNotIn("全新的草稿標語", visitor.get("/teacher/001").get_data(as_text=True))
        self.assertIn("全新的草稿標語", self.client.get("/dashboard/preview").get_data(as_text=True))
        self.post("/dashboard/publish")
        self.assertIn("全新的草稿標語", visitor.get("/teacher/001").get_data(as_text=True))
        self.post("/dashboard/unpublish")
        self.assertEqual(visitor.get("/teacher/001").status_code, 404)
        self.assertNotIn("林怡君", visitor.get("/").get_data(as_text=True))

    def test_unpublished_teacher_not_listed(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("林怡君", html)
        self.assertNotIn("張雅婷", html)
        self.assertEqual(self.client.get("/teacher/003").status_code, 404)

    def test_xss_escaped(self):
        self.login("t001")
        payload = '<script>alert(1)</script>'
        self.post("/dashboard/profile", {"county": "新北市", "school_name": "X", "teacher_name": "林怡君",
                                         "philosophy": payload})
        self.post("/dashboard/publish")
        html = self.app.test_client().get("/teacher/001").get_data(as_text=True)
        self.assertNotIn(payload, html)
        self.assertIn("&lt;script&gt;", html)

    def test_sql_injection_harmless(self):
        r = self.client.get("/?q=' OR 1=1 --&county=x' OR '1'='1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/teacher/001' OR '1'='1").status_code, 404)
        self.login("t001")
        self.post("/dashboard/items/specialties", {"name": "x'); DROP TABLE users; --"})
        self.assertTrue(self.db("SELECT COUNT(*) FROM users")[0][0] > 0)

    def test_search_filters(self):
        html = self.client.get("/?county=臺中市").get_data(as_text=True)
        self.assertIn("陳建宏", html)
        self.assertNotIn("林怡君", html)
        html = self.client.get("/?subject=數學").get_data(as_text=True)
        self.assertIn("林怡君", html)
        self.assertNotIn("陳建宏", html)


class UploadTests(Base):
    def setUp(self):
        super().setUp()
        self.login("t003")

    def upload_photo(self, data, name):
        return self.post("/dashboard/photos", {"photos": (io.BytesIO(data), name)})

    def test_valid_png_saved_with_random_name(self):
        self.upload_photo(png_bytes(), "../../etc/passwd.png")
        row = self.db("SELECT stored_name, original_name FROM uploaded_files WHERE category='class_photo'")[-1]
        self.assertRegex(row[0], r"^[0-9a-f]{32}\.png$")
        self.assertNotIn("/", row[1])

    def test_exif_removed(self):
        self.upload_photo(jpeg_with_exif(), "a.jpg")
        name = self.db("SELECT stored_name FROM uploaded_files WHERE category='class_photo'")[-1][0]
        data = (self.app.config["UPLOAD_FOLDER"] / name).read_bytes()
        self.assertNotIn(b"SecretCameraMaker", data)

    def test_fake_image_rejected(self):
        before = self.db("SELECT COUNT(*) FROM uploaded_files")[0][0]
        self.upload_photo(b"<?php system($_GET['c']); ?>", "shell.png")
        self.upload_photo(b"%PDF-1.4 not an image", "x.jpg")
        self.upload_photo(png_bytes(), "x.gif")
        self.assertEqual(self.db("SELECT COUNT(*) FROM uploaded_files")[0][0], before)

    def test_image_size_limit(self):
        self.app.config["IMAGE_MAX_BYTES"] = 100
        before = self.db("SELECT COUNT(*) FROM uploaded_files")[0][0]
        self.upload_photo(png_bytes((300, 300)), "big.png")
        self.assertEqual(self.db("SELECT COUNT(*) FROM uploaded_files")[0][0], before)

    def test_pdf_validation(self):
        r = self.post("/dashboard/courses/new", {"title": "測試課程"})
        cid = int(re.search(r"/courses/(\d+)/edit", r.headers["Location"]).group(1))
        from app.seed import demo_pdf
        self.post(f"/dashboard/courses/{cid}/plans", {"plan_type": "lesson_plan", "file": (io.BytesIO(demo_pdf("ok")), "教案.pdf")})
        self.post(f"/dashboard/courses/{cid}/plans", {"plan_type": "lesson_plan", "file": (io.BytesIO(png_bytes()), "fake.pdf")})
        bad = demo_pdf("x").replace(b"/Type /Catalog", b"/Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >>")
        self.post(f"/dashboard/courses/{cid}/plans", {"plan_type": "lesson_plan", "file": (io.BytesIO(bad), "js.pdf")})
        rows = self.db("SELECT original_name FROM uploaded_files WHERE category='lesson_plan' AND profile_id ="
                       " (SELECT profile_id FROM courses WHERE id = %s)", (cid,))
        self.assertEqual([r[0] for r in rows], ["教案.pdf"])

    def test_deleted_file_kept_while_published_then_purged(self):
        c = self.app.test_client()
        self.login("t001", client=c)
        photo_id, name = self.db("SELECT ph.id, f.stored_name FROM teacher_photos ph JOIN uploaded_files f ON f.id = ph.file_id"
                                 " JOIN teacher_profiles p ON p.id = ph.profile_id WHERE p.teacher_code='001'")[0]
        self.post(f"/dashboard/photos/{photo_id}/delete", client=c)
        visitor = self.app.test_client()
        self.assertEqual(visitor.get(f"/files/{name}").status_code, 200)   # 已發布版本仍可看
        self.post("/dashboard/publish", client=c)
        self.assertEqual(visitor.get(f"/files/{name}").status_code, 404)   # 重新發布後訪客看不到
        # 仍被發布歷史引用（可還原）→ 實體檔暫時保留
        self.assertTrue((self.app.config["UPLOAD_FOLDER"] / name).exists())
        for _ in range(10):                                                # 超出保留的 10 個版本後清除
            self.post("/dashboard/publish", client=c)
        self.assertFalse((self.app.config["UPLOAD_FOLDER"] / name).exists())


if __name__ == "__main__":
    unittest.main()

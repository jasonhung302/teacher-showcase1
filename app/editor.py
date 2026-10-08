"""教師資料編輯器。

這個藍圖會被註冊兩次（見 app/__init__.py）：
  name="me"     /dashboard/...                 老師本人，資料一律取自登入者的 user_id
  name="assist" /admin/teachers/<uid>/edit/... 僅限管理員協助修改

權限關鍵：
  1. 網址裡『沒有』任何老師 ID 可以被老師竄改；老師的 profile 由 Session 決定。
  2. 所有子資料（課程、照片、經歷…）都以 `WHERE id = %s AND profile_id = %s` 查詢，
     即使猜到別人的課程 ID，也只會得到 404。
"""
from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from .db import execute, get_setting, insert, query, ts
from .profiles import (COUNTIES, COURSE_FIELDS, GRADES, ITEM_TYPES, PROFILE_FIELDS, SUBJECTS,
                       build_public_snapshot, checklist, completeness, has_unpublished_changes, load_courses,
                       load_items, load_photos, publish, publish_history, publish_problems, restore_version,
                       touch_profile, unpublish)
from .qr import to_png, to_svg
from .security import audit
from .services import active_announcements, daily_views, public_url, view_stats
from .uploads import UploadError, discard_file, purge_deleted_files, save_upload

bp = Blueprint("editor", __name__)


# ---------------------------------------------------------------- 存取控制
@bp.url_value_preprocessor
def _pull_uid(_endpoint, values):
    g.assist_uid = values.pop("uid", None) if values else None


@bp.url_defaults
def _add_uid(endpoint, values):
    if "uid" not in values and g.get("assist_uid") is not None \
            and current_app.url_map.is_endpoint_expecting(endpoint, "uid"):
        values["uid"] = g.assist_uid


@bp.before_request
def _resolve_target_profile():
    if g.user is None:
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
    if request.blueprint == "assist":
        if g.user["role"] != "admin":
            abort(403)
        profile = query("SELECT p.* FROM teacher_profiles p JOIN users u ON u.id = p.user_id"
                        " WHERE p.user_id = %s AND u.role = 'teacher'", (g.assist_uid,), one=True)
        g.assisting = True
    else:
        if g.user["role"] == "admin":
            return redirect(url_for("admin.dashboard"))
        if g.user["role"] != "teacher":
            abort(403)
        profile = query("SELECT * FROM teacher_profiles WHERE user_id = %s", (g.user["id"],), one=True)
        g.assisting = False
    if profile is None:
        abort(404)
    g.profile = profile


def pid():
    return g.profile["id"]


def owned_or_404(table, item_id):
    """所有『依 ID 取得單筆資料』都必須經過這裡。table 只來自程式內白名單。"""
    row = query(f"SELECT * FROM {table} WHERE id = %s AND profile_id = %s", (item_id, pid()), one=True)
    if row is None:
        abort(404)
    return row


def changed(action, detail=None):
    touch_profile(pid())
    audit(("assist_" if g.assisting else "") + action, f"profile:{pid()}", detail)


def read_form(fields):
    """依欄位設定讀取並驗證表單，回傳 (data, errors)。"""
    data, errors = {}, []
    for f in fields:
        val = (request.form.get(f["name"]) or "").strip()
        if f.get("type") == "select" and val and val not in f["options"]:
            val = ""
        if f.get("max") and len(val) > f["max"]:
            errors.append(f"{f['label']}不可超過 {f['max']} 字")
        if f.get("required") and not val:
            errors.append(f"請填寫{f['label']}")
        data[f["name"]] = val or None
    return data, errors


def flash_errors(errors):
    for e in errors:
        flash(e, "error")


def ctx(**kw):
    pct, missing = completeness(pid())
    av = query("SELECT stored_name FROM uploaded_files WHERE id = %s AND profile_id = %s AND deleted_at IS NULL",
               (g.profile["avatar_file_id"], pid()), one=True) if g.profile["avatar_file_id"] else None
    return dict(profile=g.profile, pct=pct, missing=missing, assisting=g.assisting,
                avatar_name=av["stored_name"] if av else None, public_url=public_url(g.profile["teacher_code"]),
                has_changes=has_unpublished_changes(g.profile), item_types=ITEM_TYPES, **kw)


# ---------------------------------------------------------------- 總覽
@bp.route("/")
def overview():
    items = checklist(pid())
    for it in items:
        endpoint, kwargs, anchor = it["target"]
        it["url"] = eurl(endpoint, **kwargs) + (f"#{anchor}" if anchor else "")
    owner = query("SELECT last_login_at, prev_login_at FROM users WHERE id = %s", (g.profile["user_id"],), one=True)
    deadline = get_setting("deadline")
    days_left = None
    if deadline:
        from datetime import date
        from .db import local_today
        days_left = (date.fromisoformat(deadline) - date.fromisoformat(local_today())).days
    daily = daily_views(pid(), 30)
    return render_template("editor/overview.html", **ctx(
        checklist=items, problems=publish_problems(pid()), stats=view_stats(pid()),
        daily=daily, daily_max=max([v for _d, v in daily] + [1]), history=publish_history(pid()),
        announcements=active_announcements(), deadline=deadline, days_left=days_left, owner=owner))


# ---------------------------------------------------------------- 基本資料
def _read_profile_form():
    fields = [{"name": n, "label": l, "max": m} for n, l, m, _r in PROFILE_FIELDS]
    data, errors = read_form(fields)
    if data["county"] and data["county"] not in COUNTIES:
        errors.append("請從清單選擇縣市")
    if data["contact_email"] and "@" not in data["contact_email"]:
        errors.append("電子郵件格式不正確")
    if data["phone"] and not all(ch.isdigit() or ch in "-+() " for ch in data["phone"]):
        errors.append("手機號碼只能包含數字與 - + ( )")
    return data, errors


def _write_profile_text(data):
    cols = [n for n, *_ in PROFILE_FIELDS]
    execute(f"UPDATE teacher_profiles SET {', '.join(f'{c} = %s' for c in cols)} WHERE id = %s",
            [data[c] for c in cols] + [pid()])
    if data["teacher_name"]:      # 讓帳號顯示名稱與講師姓名同步
        execute("UPDATE users SET display_name = %s, updated_at = %s WHERE id = %s",
                (data["teacher_name"], ts(), g.profile["user_id"]))


def autosave_response(errors):
    if errors:
        return jsonify(ok=False, errors=errors), 400
    from .db import parse_ts
    from datetime import timedelta
    local = (parse_ts(ts()) + timedelta(hours=8)).strftime("%H:%M")
    return jsonify(ok=True, saved_at=local)


@bp.route("/autosave/profile", methods=["POST"])
def autosave_profile():
    """自動儲存（只處理文字欄位；照片仍需按『儲存草稿』上傳）。"""
    data, errors = _read_profile_form()
    if not errors:
        _write_profile_text(data)
        touch_profile(pid())
    return autosave_response(errors)


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        data, errors = _read_profile_form()
        avatar = request.files.get("avatar")
        new_avatar_id = None
        if not errors and avatar and avatar.filename:
            try:
                new_avatar_id = save_upload(avatar, "avatar", pid())
            except UploadError as e:
                errors.append(f"個人照片：{e}")
        if errors:
            flash_errors(errors)
            return render_template("editor/profile.html", **ctx(form=data, counties=COUNTIES)), 400

        _write_profile_text(data)
        old_avatar = g.profile["avatar_file_id"]
        if new_avatar_id or request.form.get("remove_avatar"):
            execute("UPDATE teacher_profiles SET avatar_file_id = %s WHERE id = %s", (new_avatar_id, pid()))
            discard_file(old_avatar)
        changed("profile_saved")
        flash("基本資料已儲存為草稿。", "success")
        return redirect(eurl("profile"))
    return render_template("editor/profile.html", **ctx(form=dict(g.profile), counties=COUNTIES))


def eurl(name, **kw):
    return url_for(f"{request.blueprint}.{name}", **kw)


# ---------------------------------------------------------------- 多筆資料（學歷/經歷/專長/獲獎/認證）
def item_type_or_404(kind):
    if kind not in ITEM_TYPES:
        abort(404)
    return ITEM_TYPES[kind]


def _save_item(kind, cfg, existing=None):
    data, errors = read_form(cfg["fields"])
    proof_id = None
    if not errors and cfg.get("proof"):
        proof = request.files.get("proof")
        if proof and proof.filename:
            try:
                proof_id = save_upload(proof, "certificate", pid())
            except UploadError as e:
                errors.append(f"證明文件：{e}")
    if errors:
        return errors
    names = [f["name"] for f in cfg["fields"]]
    table = cfg["table"]
    if existing is None:
        order = query(f"SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM {table} WHERE profile_id = %s",
                      (pid(),), one=True)["n"]
        cols = names + ["profile_id", "sort_order", "created_at"] + (["proof_file_id"] if cfg.get("proof") else [])
        vals = [data[n] for n in names] + [pid(), order, ts()] + ([proof_id] if cfg.get("proof") else [])
        execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals)
    else:
        sets = ", ".join(f"{n} = %s" for n in names)
        execute(f"UPDATE {table} SET {sets} WHERE id = %s AND profile_id = %s",
                [data[n] for n in names] + [existing["id"], pid()])
        if cfg.get("proof") and (proof_id or request.form.get("remove_proof")):
            execute(f"UPDATE {table} SET proof_file_id = %s WHERE id = %s AND profile_id = %s",
                    (proof_id, existing["id"], pid()))
            discard_file(existing["proof_file_id"])
    changed(f"{kind}_saved")
    return []


@bp.route("/items/<kind>", methods=["GET", "POST"])
def items(kind):
    cfg = item_type_or_404(kind)
    if request.method == "POST":
        errors = _save_item(kind, cfg)
        if errors:
            flash_errors(errors)
            return render_template("editor/items.html", **ctx(kind=kind, cfg=cfg, rows=load_items(kind, pid()),
                                                              form=request.form)), 400
        flash(f"已新增一筆{cfg['label']}。", "success")
        return redirect(eurl("items", kind=kind))
    return render_template("editor/items.html", **ctx(kind=kind, cfg=cfg, rows=load_items(kind, pid()), form={}))


@bp.route("/items/<kind>/<int:item_id>/edit", methods=["GET", "POST"])
def item_edit(kind, item_id):
    cfg = item_type_or_404(kind)
    row = owned_or_404(cfg["table"], item_id)
    if request.method == "POST":
        errors = _save_item(kind, cfg, existing=row)
        if errors:
            flash_errors(errors)
            return render_template("editor/item_edit.html", **ctx(kind=kind, cfg=cfg, row=row, form=request.form)), 400
        flash(f"{cfg['label']}已更新。", "success")
        return redirect(eurl("items", kind=kind))
    proof = None
    if cfg.get("proof") and row["proof_file_id"]:
        proof = query("SELECT * FROM uploaded_files WHERE id = %s AND profile_id = %s",
                      (row["proof_file_id"], pid()), one=True)
    return render_template("editor/item_edit.html", **ctx(kind=kind, cfg=cfg, row=row, form=dict(row), proof=proof))


@bp.route("/items/<kind>/<int:item_id>/delete", methods=["POST"])
def item_delete(kind, item_id):
    cfg = item_type_or_404(kind)
    row = owned_or_404(cfg["table"], item_id)
    execute(f"DELETE FROM {cfg['table']} WHERE id = %s AND profile_id = %s", (item_id, pid()))
    if cfg.get("proof"):
        discard_file(row["proof_file_id"])
    changed(f"{kind}_deleted")
    flash(f"已刪除一筆{cfg['label']}。", "success")
    return redirect(eurl("items", kind=kind))


@bp.route("/items/<kind>/<int:item_id>/move/<direction>", methods=["POST"])
def item_move(kind, item_id, direction):
    cfg = item_type_or_404(kind)
    owned_or_404(cfg["table"], item_id)
    rows = [r["id"] for r in load_items(kind, pid())]
    i = rows.index(item_id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(rows):
        rows[i], rows[j] = rows[j], rows[i]
        for order, rid in enumerate(rows):
            execute(f"UPDATE {cfg['table']} SET sort_order = %s WHERE id = %s AND profile_id = %s", (order, rid, pid()))
        changed(f"{kind}_reordered")
    return redirect(eurl("items", kind=kind))


REORDER_TABLES = {**{k: v["table"] for k, v in ITEM_TYPES.items()},
                  "photos": "teacher_photos", "courses": "courses"}


@bp.route("/reorder/<kind>", methods=["POST"])
def reorder(kind):
    """拖曳排序：前端送出新的 ID 順序；後端確認『剛好是自己的全部資料』才更新 sort_order。"""
    table = REORDER_TABLES.get(kind)
    if table is None:
        abort(404)
    try:
        ids = [int(x) for x in (request.form.get("ids") or "").split(",") if x.strip()]
    except ValueError:
        abort(400)
    owned = [r["id"] for r in query(f"SELECT id FROM {table} WHERE profile_id = %s", (pid(),))]
    if sorted(ids) != sorted(owned):
        return jsonify(ok=False, error="資料已變動，請重新整理頁面後再排序。"), 409
    for order, rid in enumerate(ids):
        execute(f"UPDATE {table} SET sort_order = %s WHERE id = %s AND profile_id = %s", (order, rid, pid()))
    changed(f"{kind}_reordered")
    return autosave_response([])


# ---------------------------------------------------------------- 課堂照片
@bp.route("/photos", methods=["GET", "POST"])
def photos():
    if request.method == "POST":
        files = [f for f in request.files.getlist("photos") if f and f.filename]
        existing = len(load_photos(pid()))
        limit = current_app.config["MAX_PHOTOS"]
        if not files:
            flash("請選擇要上傳的照片。", "error")
        elif existing + len(files) > limit:
            flash(f"課堂照片最多 {limit} 張，目前已有 {existing} 張。", "error")
        else:
            caption = (request.form.get("caption") or "").strip()[:100] or None
            ok = 0
            for f in files:
                try:
                    fid = save_upload(f, "class_photo", pid())
                except UploadError as e:
                    flash(f"「{f.filename[:40]}」{e}", "error")
                    continue
                execute("INSERT INTO teacher_photos (profile_id, file_id, caption, sort_order, created_at)"
                        " VALUES (%s, %s, %s, %s, %s)", (pid(), fid, caption, existing + ok, ts()))
                ok += 1
            if ok:
                changed("photos_uploaded", f"{ok} files")
                flash(f"已上傳 {ok} 張課堂照片。", "success")
        return redirect(eurl("photos"))
    return render_template("editor/photos.html", **ctx(rows=load_photos(pid())))


@bp.route("/photos/<int:photo_id>/caption", methods=["POST"])
def photo_caption(photo_id):
    owned_or_404("teacher_photos", photo_id)
    caption = (request.form.get("caption") or "").strip()[:100] or None
    execute("UPDATE teacher_photos SET caption = %s WHERE id = %s AND profile_id = %s", (caption, photo_id, pid()))
    changed("photo_caption")
    flash("照片說明已更新。", "success")
    return redirect(eurl("photos"))


@bp.route("/photos/<int:photo_id>/delete", methods=["POST"])
def photo_delete(photo_id):
    row = owned_or_404("teacher_photos", photo_id)
    execute("DELETE FROM teacher_photos WHERE id = %s AND profile_id = %s", (photo_id, pid()))
    discard_file(row["file_id"])
    changed("photo_deleted")
    flash("照片已刪除。", "success")
    return redirect(eurl("photos"))


# ---------------------------------------------------------------- 數位學習課程
def _read_course():
    data, errors = {}, []
    for name, label, mx in COURSE_FIELDS:
        val = (request.form.get(name) or "").strip()
        if len(val) > mx:
            errors.append(f"{label}不可超過 {mx} 字")
        data[name] = val or None
    if not data["title"]:
        errors.append("請填寫課程名稱")
    if data["student_count"]:
        if not data["student_count"].isdigit() or not 0 < int(data["student_count"]) <= 999:
            errors.append("學生數請填 1–999 的整數")
        else:
            data["student_count"] = int(data["student_count"])
    return data, errors


def _course_form_ctx(course=None, form=None):
    return ctx(course=course, form=form or {}, grades=GRADES, subjects=SUBJECTS)


def _write_course(course_id, data):
    names = [n for n, *_ in COURSE_FIELDS]
    execute(f"UPDATE courses SET {', '.join(f'{n} = %s' for n in names)}, updated_at = %s"
            f" WHERE id = %s AND profile_id = %s", [data[n] for n in names] + [ts(), course_id, pid()])


@bp.route("/autosave/course/<int:course_id>", methods=["POST"])
def autosave_course(course_id):
    owned_or_404("courses", course_id)
    data, errors = _read_course()
    if not errors:
        _write_course(course_id, data)
        touch_profile(pid())
    return autosave_response(errors)


@bp.route("/courses")
def courses():
    return render_template("editor/courses.html", **ctx(rows=load_courses(pid())))


@bp.route("/courses/new", methods=["GET", "POST"])
def course_new():
    if request.method == "POST":
        data, errors = _read_course()
        if errors:
            flash_errors(errors)
            return render_template("editor/course_form.html", **_course_form_ctx(form=request.form)), 400
        names = [n for n, *_ in COURSE_FIELDS]
        order = query("SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM courses WHERE profile_id = %s",
                      (pid(),), one=True)["n"]
        cid = insert(f"INSERT INTO courses ({', '.join(names)}, profile_id, sort_order, created_at, updated_at)"
                     f" VALUES ({', '.join(['%s'] * len(names))}, %s, %s, %s, %s)",
                     [data[n] for n in names] + [pid(), order, ts(), ts()])
        changed("course_created", f"course:{cid}")
        flash("課程已建立，接著可以上傳完整教案。", "success")
        return redirect(eurl("course_edit", course_id=cid) + "#plans")
    return render_template("editor/course_form.html", **_course_form_ctx())


@bp.route("/courses/<int:course_id>/edit", methods=["GET", "POST"])
def course_edit(course_id):
    course = owned_or_404("courses", course_id)
    plans = [c for c in load_courses(pid()) if c["id"] == course_id][0]["plans"]
    if request.method == "POST":
        data, errors = _read_course()
        if errors:
            flash_errors(errors)
            return render_template("editor/course_form.html",
                                   **_course_form_ctx(course=course, form=request.form), plans=plans), 400
        _write_course(course_id, data)
        changed("course_saved", f"course:{course_id}")
        flash("課程已儲存為草稿。", "success")
        return redirect(eurl("course_edit", course_id=course_id))
    return render_template("editor/course_form.html", **_course_form_ctx(course=course, form=dict(course)), plans=plans)


@bp.route("/courses/<int:course_id>/delete", methods=["POST"])
def course_delete(course_id):
    owned_or_404("courses", course_id)
    file_ids = [r["file_id"] for r in query("SELECT file_id FROM lesson_plans WHERE course_id = %s AND profile_id = %s",
                                             (course_id, pid()))]
    execute("DELETE FROM lesson_plans WHERE course_id = %s AND profile_id = %s", (course_id, pid()))
    execute("DELETE FROM courses WHERE id = %s AND profile_id = %s", (course_id, pid()))
    for fid in file_ids:
        discard_file(fid)
    changed("course_deleted", f"course:{course_id}")
    flash("課程已刪除。", "success")
    return redirect(eurl("courses"))


@bp.route("/courses/<int:course_id>/plans", methods=["POST"])
def plan_upload(course_id):
    owned_or_404("courses", course_id)
    plan_type = request.form.get("plan_type")
    if plan_type not in ("lesson_plan", "material"):
        abort(400)
    title = (request.form.get("title") or "").strip()[:100]
    f = request.files.get("file")
    try:
        fid = save_upload(f, plan_type, pid())
    except UploadError as e:
        flash(str(e), "error")
        return redirect(eurl("course_edit", course_id=course_id) + "#plans")
    if not title:
        title = query("SELECT original_name FROM uploaded_files WHERE id = %s", (fid,), one=True)["original_name"]
    execute("INSERT INTO lesson_plans (profile_id, course_id, file_id, title, plan_type, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)", (pid(), course_id, fid, title, plan_type, ts()))
    changed("plan_uploaded", f"course:{course_id}")
    flash("檔案已上傳。", "success")
    return redirect(eurl("course_edit", course_id=course_id) + "#plans")


@bp.route("/courses/<int:course_id>/plans/<int:plan_id>/delete", methods=["POST"])
def plan_delete(course_id, plan_id):
    owned_or_404("courses", course_id)
    plan = owned_or_404("lesson_plans", plan_id)
    if plan["course_id"] != course_id:
        abort(404)
    execute("DELETE FROM lesson_plans WHERE id = %s AND profile_id = %s", (plan_id, pid()))
    discard_file(plan["file_id"])
    changed("plan_deleted", f"course:{course_id}")
    flash("檔案已刪除。", "success")
    return redirect(eurl("course_edit", course_id=course_id) + "#plans")


# ---------------------------------------------------------------- 預覽 / 發布
@bp.route("/preview")
def preview():
    snap = build_public_snapshot(pid())
    return render_template("public/teacher.html", t=snap, preview=True, back_url=eurl("overview"))


@bp.route("/publish", methods=["POST"])
def do_publish():
    problems = publish_problems(pid())
    if problems:
        flash("發布前請先完成：" + "、".join(problems), "error")
        return redirect(eurl("overview"))
    publish(pid(), g.user["id"])
    purge_deleted_files()
    audit(("assist_" if g.assisting else "") + "published", f"profile:{pid()}")
    flash("已發布！訪客現在可以看到最新內容。", "success")
    return redirect(eurl("overview"))


@bp.route("/history/<int:history_id>/restore", methods=["POST"])
def restore(history_id):
    if not restore_version(pid(), history_id, g.user["id"]):
        abort(404)
    audit(("assist_" if g.assisting else "") + "version_restored", f"profile:{pid()}", f"history:{history_id}")
    flash("已將公開頁還原為所選版本。您目前的草稿內容沒有被更動。", "success")
    return redirect(eurl("overview") + "#history")


# ---------------------------------------------------------------- QR Code
@bp.route("/qr.svg")
def qr_svg():
    resp = Response(to_svg(public_url(g.profile["teacher_code"])), mimetype="image/svg+xml")
    if request.args.get("download"):
        resp.headers["Content-Disposition"] = f"attachment; filename=qrcode_{g.profile['teacher_code']}.svg"
    return resp


@bp.route("/qr.png")
def qr_png():
    resp = Response(to_png(public_url(g.profile["teacher_code"]), scale=16), mimetype="image/png")
    resp.headers["Content-Disposition"] = f"attachment; filename=qrcode_{g.profile['teacher_code']}.png"
    return resp


@bp.route("/unpublish", methods=["POST"])
def do_unpublish():
    unpublish(pid())
    purge_deleted_files()
    audit(("assist_" if g.assisting else "") + "unpublished", f"profile:{pid()}")
    flash("已取消發布，公開頁面已下架。資料仍保留為草稿。", "success")
    return redirect(eurl("overview"))

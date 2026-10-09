// 前端互動（不處理任何權限，權限全部由後端判斷）
// 功能：選單、確認對話、檔案檢查、自動儲存、未儲存提醒、字數、拖曳排序、Lightbox、
//      照片裁切與壓縮、分享、複製、列印、表格下載。
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  // ---------------------------------------------------------------- 頭像載入失敗 → 改顯示預設人物圖示
  function avatarFailed(img) {
    var fb = img.nextElementSibling;
    if (fb && fb.classList.contains("avatar-fallback")) { fb.hidden = false; img.remove(); }
  }
  $$("img[data-avatar-img]").forEach(function (img) {
    if (img.complete && img.naturalWidth === 0) avatarFailed(img);
    else img.addEventListener("error", function () { avatarFailed(img); });
  });

  function csrf() {
    var el = $('input[name="csrf_token"]');
    return el ? el.value : "";
  }

  // ---------------------------------------------------------------- 小提示
  var toastEl;
  function toast(msg, kind) {
    if (!toastEl) {
      toastEl = document.createElement("div");
      toastEl.className = "toast";
      toastEl.setAttribute("role", "status");
      toastEl.setAttribute("aria-live", "polite");
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.className = "toast show" + (kind ? " toast-" + kind : "");
    clearTimeout(toastEl._t);
    toastEl._t = setTimeout(function () { toastEl.className = "toast"; }, 2600);
  }

  function postForm(url, fd) {
    if (!fd.has("csrf_token")) fd.append("csrf_token", csrf());
    return fetch(url, {
      method: "POST", body: fd, credentials: "same-origin",
      headers: { "X-CSRF-Token": csrf(), "Accept": "application/json" }
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, errors: ["伺服器回應異常（" + r.status + "）"] }; })
        .then(function (j) { j._status = r.status; return j; });
    });
  }

  // ---------------------------------------------------------------- 手機選單
  var toggle = $(".nav-toggle"), nav = $("#site-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  // 下拉選單（details）點外面自動關閉
  document.addEventListener("click", function (e) {
    $$("details.menu[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });

  // ---------------------------------------------------------------- 危險操作確認
  $$("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) { e.preventDefault(); e.stopImmediatePropagation(); }
    });
  });

  // ---------------------------------------------------------------- 字數提示
  $$("[data-count]").forEach(function (el) {
    var max = parseInt(el.getAttribute("maxlength") || "0", 10);
    if (!max) return;
    var c = document.createElement("span");
    c.className = "char-count";
    c.setAttribute("aria-hidden", "true");
    el.insertAdjacentElement("afterend", c);
    function upd() {
      var n = el.value.length;
      c.textContent = n + " / " + max;
      c.classList.toggle("near", n > max * 0.9);
    }
    el.addEventListener("input", upd);
    upd();
  });

  // ---------------------------------------------------------------- 未儲存提醒 + 自動儲存
  var dirtyForms = new Set();
  window.addEventListener("beforeunload", function (e) {
    if (dirtyForms.size) { e.preventDefault(); e.returnValue = ""; return ""; }
  });

  function statusEls() { return $$("[data-save-status]"); }
  function setStatus(text, kind) {
    statusEls().forEach(function (el) {
      el.textContent = text;
      el.className = "save-status" + (kind ? " is-" + kind : "");
    });
  }

  $$("form[data-warn-unsaved], form[data-autosave]").forEach(function (form) {
    var url = form.getAttribute("data-autosave");
    var timer = null, version = 0, savedVersion = 0, saving = false, submitting = false;

    function textFormData() {
      var fd = new FormData(form);
      $$('input[type="file"]', form).forEach(function (f) { fd.delete(f.name); });
      fd.delete("remove_avatar");
      return fd;
    }
    function hasPendingFiles() {
      return $$('input[type="file"]', form).some(function (f) { return f.files && f.files.length; });
    }
    function refreshDirty() {
      if (!submitting && (version !== savedVersion || hasPendingFiles())) dirtyForms.add(form);
      else dirtyForms.delete(form);
    }
    function save() {
      if (!url || saving || version === savedVersion) return;
      saving = true;
      var v = version;
      setStatus("儲存中…", "saving");
      postForm(url, textFormData()).then(function (j) {
        saving = false;
        if (j.ok) {
          savedVersion = v;
          setStatus(v === version ? "已自動儲存 ✓ " + j.saved_at : "有尚未儲存的修改", v === version ? "saved" : "dirty");
          if (hasPendingFiles()) setStatus("文字已儲存 ✓ " + j.saved_at + "（照片請按儲存草稿上傳）", "dirty");
        } else {
          setStatus("無法自動儲存：" + (j.errors || ["請檢查欄位"]).join("、"), "error");
        }
        refreshDirty();
        if (version !== savedVersion) schedule();
      }).catch(function () {
        saving = false;
        setStatus("網路中斷，尚未儲存（恢復連線後會再試）", "error");
        refreshDirty();
        schedule(8000);
      });
    }
    function schedule(ms) {
      if (!url) return;
      clearTimeout(timer);
      timer = setTimeout(save, ms || 2000);
    }
    function onEdit(e) {
      if (e.target.type === "file") { refreshDirty(); return; }
      version++;
      refreshDirty();
      if (url) { setStatus("有尚未儲存的修改", "dirty"); schedule(); }
    }
    form.addEventListener("input", onEdit);
    form.addEventListener("change", function (e) { if (e.target.tagName === "SELECT" || e.target.type === "checkbox" || e.target.type === "file") onEdit(e); });
    form.addEventListener("submit", function () { submitting = true; dirtyForms.delete(form); clearTimeout(timer); });
    // 切換分頁或關閉前，盡量把最後的修改送出
    document.addEventListener("visibilitychange", function () {
      if (url && document.visibilityState === "hidden" && version !== savedVersion && navigator.sendBeacon) {
        var fd = textFormData();
        if (!fd.has("csrf_token")) fd.append("csrf_token", csrf());
        if (navigator.sendBeacon(url, fd)) savedVersion = version;
        refreshDirty();
      }
    });
  });

  // ---------------------------------------------------------------- 檔案選擇
  $$('.file-drop input[type="file"]').forEach(function (input) {
    var box = input.closest(".file-drop");
    var text = $(".file-drop-text", box);
    var original = text.textContent;
    var maxBytes = parseFloat(input.getAttribute("data-max-mb") || "0") * 1024 * 1024;
    input.addEventListener("change", function () {
      box.classList.remove("has-file", "has-error");
      if (!input.files.length) { text.textContent = original; return; }
      if (input.hasAttribute("data-crop") || input.hasAttribute("data-compress")) return;   // 交給裁切／壓縮處理
      var tooBig = Array.prototype.filter.call(input.files, function (f) { return maxBytes && f.size > maxBytes; });
      if (tooBig.length) {
        box.classList.add("has-error");
        text.textContent = "「" + tooBig[0].name + "」超過大小限制，請重新選擇";
        input.value = "";
        return;
      }
      markFiles(input);
    });
  });
  function markFiles(input) {
    var box = input.closest(".file-drop");
    if (!box) return;
    box.classList.remove("has-error");
    box.classList.add("has-file");
    $(".file-drop-text", box).textContent = input.files.length === 1 ? "已選擇：" + input.files[0].name
      : "已選擇 " + input.files.length + " 個檔案";
  }
  function setFiles(input, files) {
    var dt = new DataTransfer();
    files.forEach(function (f) { dt.items.add(f); });
    input.files = dt.files;
  }

  // 送出時停用按鈕，避免重複送出
  $$("form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (e.defaultPrevented) return;
      if (form._busy) { e.preventDefault(); toast("照片處理中，請稍候…"); return; }
      var btn = $('button[type="submit"]', form);
      if (btn && form.method.toLowerCase() === "post") setTimeout(function () { btn.disabled = true; }, 0);
    });
  });

  // ---------------------------------------------------------------- 圖片工具
  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () { resolve({ img: img, url: url }); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("無法讀取圖片")); };
      img.src = url;
    });
  }
  function fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
  function canvasToFile(canvas, name, quality) {
    return new Promise(function (resolve) {
      canvas.toBlob(function (blob) {
        resolve(new File([blob], name.replace(/\.(png|jpe?g)$/i, "") + ".jpg", { type: "image/jpeg" }));
      }, "image/jpeg", quality || 0.86);
    });
  }

  // 課堂照片：上傳前預覽＋自動壓縮（伺服器仍會再驗證與縮圖）
  $$("input[data-compress]").forEach(function (input) {
    var maxSide = parseInt(input.getAttribute("data-compress"), 10) || 2000;
    var maxBytes = parseFloat(input.getAttribute("data-max-mb") || "5") * 1024 * 1024;
    var preview = $(input.getAttribute("data-preview") || "");
    var form = input.form;
    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files);
      if (!files.length) { if (preview) preview.innerHTML = ""; return; }
      form._busy = true;
      if (preview) preview.innerHTML = '<p class="small muted">照片處理中…</p>';
      Promise.all(files.map(function (f) {
        if (!/^image\/(jpeg|png)$/.test(f.type)) return Promise.resolve({ file: f, error: "格式不支援（僅 JPG／PNG）" });
        return loadImage(f).then(function (r) {
          var w = r.img.naturalWidth, h = r.img.naturalHeight, scale = Math.min(1, maxSide / Math.max(w, h));
          if (scale === 1 && f.size <= 1.5 * 1024 * 1024) { return { file: f, url: r.url, w: w, h: h }; }
          var c = document.createElement("canvas");
          c.width = Math.round(w * scale); c.height = Math.round(h * scale);
          var ctx = c.getContext("2d");
          ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
          ctx.drawImage(r.img, 0, 0, c.width, c.height);
          return canvasToFile(c, f.name, 0.85).then(function (nf) {
            return { file: nf.size < f.size ? nf : f, url: r.url, w: c.width, h: c.height, orig: f.size };
          });
        }).catch(function () { return { file: f, error: "無法讀取圖片" }; });
      })).then(function (results) {
        var ok = results.filter(function (r) { return !r.error && r.file.size <= maxBytes; });
        setFiles(input, ok.map(function (r) { return r.file; }));
        if (ok.length) markFiles(input);
        if (preview) {
          preview.innerHTML = "";
          results.forEach(function (r) {
            var fig = document.createElement("figure");
            fig.className = "up-thumb" + (r.error || r.file.size > maxBytes ? " up-bad" : "");
            if (r.url) { var im = document.createElement("img"); im.src = r.url; im.alt = ""; fig.appendChild(im); }
            var cap = document.createElement("figcaption");
            cap.textContent = r.error ? r.error : (r.file.size > maxBytes ? "壓縮後仍超過 5 MB" :
              fmtSize(r.file.size) + (r.orig && r.orig > r.file.size ? "（已壓縮，原 " + fmtSize(r.orig) + "）" : ""));
            fig.appendChild(cap);
            preview.appendChild(fig);
          });
        }
        form._busy = false;
      });
    });
  });

  // 個人照片：瀏覽器內裁切（1:1 或 4:5）
  $$("input[data-crop]").forEach(function (input) {
    input.addEventListener("change", function () {
      var f = input.files[0];
      if (!f) return;
      if (!/^image\/(jpeg|png)$/.test(f.type)) { toast("僅支援 JPG／PNG 照片", "error"); input.value = ""; return; }
      loadImage(f).then(function (r) { openCropper(input, f, r.img); })
        .catch(function () { toast("無法讀取這張照片", "error"); input.value = ""; });
    });
  });

  function openCropper(input, file, img) {
    var ratio = 1, zoom = 1, ox = 0, oy = 0;
    var modal = document.createElement("div");
    modal.className = "modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", "裁切個人照片");
    modal.innerHTML =
      '<div class="modal-panel cropper">' +
      '<h2>裁切個人照片</h2>' +
      '<p class="small muted">拖曳照片調整位置，用下方滑桿縮放。</p>' +
      '<div class="crop-stage"><canvas width="480" height="480" aria-hidden="true"></canvas></div>' +
      '<div class="crop-controls">' +
      '<div class="seg" role="group" aria-label="比例"><button type="button" data-r="1" class="on">1:1 方形</button>' +
      '<button type="button" data-r="0.8">4:5 直式</button></div>' +
      '<label class="zoom-label">縮放 <input type="range" min="1" max="4" step="0.01" value="1" aria-label="縮放"></label>' +
      '</div>' +
      '<div class="form-actions"><button type="button" class="btn btn-primary" data-ok>套用</button>' +
      '<button type="button" class="btn btn-ghost" data-cancel>取消</button></div></div>';
    document.body.appendChild(modal);
    var canvas = $("canvas", modal), ctx = canvas.getContext("2d"), range = $('input[type="range"]', modal);
    var lastFocus = document.activeElement;

    function frame() {   // 裁切框大小（畫布座標）
      var W = canvas.width, H = canvas.height;
      var fw = ratio >= 1 ? W * 0.84 : H * 0.84 * ratio, fh = fw / ratio;
      return { x: (W - fw) / 2, y: (H - fh) / 2, w: fw, h: fh };
    }
    function baseScale() {
      var fr = frame();
      return Math.max(fr.w / img.naturalWidth, fr.h / img.naturalHeight);
    }
    function clamp() {
      var fr = frame(), s = baseScale() * zoom;
      var iw = img.naturalWidth * s, ih = img.naturalHeight * s;
      var maxX = (iw - fr.w) / 2, maxY = (ih - fr.h) / 2;
      ox = Math.max(-maxX, Math.min(maxX, ox));
      oy = Math.max(-maxY, Math.min(maxY, oy));
    }
    function draw() {
      clamp();
      var W = canvas.width, H = canvas.height, s = baseScale() * zoom, fr = frame();
      var iw = img.naturalWidth * s, ih = img.naturalHeight * s;
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = "#1c2733"; ctx.fillRect(0, 0, W, H);
      ctx.drawImage(img, W / 2 - iw / 2 + ox, H / 2 - ih / 2 + oy, iw, ih);
      ctx.fillStyle = "rgba(10,20,30,.55)";
      ctx.fillRect(0, 0, W, fr.y); ctx.fillRect(0, fr.y + fr.h, W, H - fr.y - fr.h);
      ctx.fillRect(0, fr.y, fr.x, fr.h); ctx.fillRect(fr.x + fr.w, fr.y, W - fr.x - fr.w, fr.h);
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.strokeRect(fr.x, fr.y, fr.w, fr.h);
    }
    var drag = null;
    canvas.addEventListener("pointerdown", function (e) {
      canvas.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, ox: ox, oy: oy };
    });
    canvas.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var k = canvas.width / canvas.getBoundingClientRect().width;
      ox = drag.ox + (e.clientX - drag.x) * k; oy = drag.oy + (e.clientY - drag.y) * k;
      draw();
    });
    canvas.addEventListener("pointerup", function () { drag = null; });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      zoom = Math.max(1, Math.min(4, zoom * (e.deltaY < 0 ? 1.08 : 0.93)));
      range.value = zoom; draw();
    }, { passive: false });
    range.addEventListener("input", function () { zoom = parseFloat(range.value); draw(); });
    $$("[data-r]", modal).forEach(function (b) {
      b.addEventListener("click", function () {
        $$("[data-r]", modal).forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on"); ratio = parseFloat(b.getAttribute("data-r")); draw();
      });
    });
    function close(apply) {
      if (!apply) input.value = "";
      modal.remove();
      document.removeEventListener("keydown", onKey);
      if (lastFocus) lastFocus.focus();
      input.dispatchEvent(new Event("change", { bubbles: true }));   // 讓未儲存提醒更新
    }
    function onKey(e) { if (e.key === "Escape") close(false); }
    document.addEventListener("keydown", onKey);
    $("[data-cancel]", modal).addEventListener("click", function () { close(false); });
    $("[data-ok]", modal).addEventListener("click", function () {
      var fr = frame(), s = baseScale() * zoom, W = canvas.width, H = canvas.height;
      var iw = img.naturalWidth * s, ih = img.naturalHeight * s;
      var sx = (fr.x - (W / 2 - iw / 2 + ox)) / s, sy = (fr.y - (H / 2 - ih / 2 + oy)) / s;
      var sw = fr.w / s, sh = fr.h / s;
      var outW = Math.min(1000, Math.round(sw)), outH = Math.round(outW / ratio);
      var out = document.createElement("canvas");
      out.width = outW; out.height = outH;
      var octx = out.getContext("2d");
      octx.fillStyle = "#fff"; octx.fillRect(0, 0, outW, outH);
      octx.drawImage(img, sx, sy, sw, sh, 0, 0, outW, outH);
      canvasToFile(out, file.name, 0.9).then(function (nf) {
        setFiles(input, [nf]);
        markFiles(input);
        var pv = $("[data-avatar-preview]");
        if (pv) {
          pv.innerHTML = "";
          var im = document.createElement("img");
          im.src = URL.createObjectURL(nf); im.alt = "裁切後的照片（尚未儲存）";
          pv.appendChild(im);
          pv.classList.toggle("ratio-45", ratio < 1);
        }
        modal.remove();
        document.removeEventListener("keydown", onKey);
        input.dispatchEvent(new Event("input", { bubbles: true }));
        toast("已裁切，請按「儲存草稿」上傳照片");
      });
    });
    draw();
    $("[data-ok]", modal).focus();
  }

  // ---------------------------------------------------------------- 拖曳排序（滑鼠、觸控、鍵盤）
  $$(".sortable[data-reorder]").forEach(function (list) {
    var url = list.getAttribute("data-reorder");
    var saveTimer = null;
    function ids() { return $$(":scope > [data-id]", list).map(function (li) { return li.getAttribute("data-id"); }); }
    var before = ids().join(",");
    function persist() {
      var now = ids().join(",");
      if (now === before) return;
      setStatus("儲存順序中…", "saving");
      var fd = new FormData(); fd.append("ids", now);
      postForm(url, fd).then(function (j) {
        if (j.ok) { before = now; setStatus("順序已儲存 ✓ " + j.saved_at, "saved"); }
        else { setStatus(j.error || "排序失敗，請重新整理", "error"); }
      }).catch(function () { setStatus("網路中斷，順序未儲存", "error"); });
    }
    function persistSoon() { clearTimeout(saveTimer); saveTimer = setTimeout(persist, 600); }

    var dragging = null;
    list.addEventListener("pointerdown", function (e) {
      var handle = e.target.closest(".drag-handle");
      if (!handle || !list.contains(handle) || e.button > 0) return;
      e.preventDefault();
      dragging = handle.closest("[data-id]");
      dragging.classList.add("dragging");
      list.classList.add("is-sorting");
      handle.setPointerCapture(e.pointerId);
    });
    list.addEventListener("pointermove", function (e) {
      if (!dragging) return;
      var el = document.elementFromPoint(e.clientX, e.clientY);
      var over = el && el.closest("[data-id]");
      if (!over || over === dragging || over.parentNode !== list) {
        autoScroll(e.clientY);
        return;
      }
      var r = over.getBoundingClientRect();
      var horizontal = getComputedStyle(list).display === "grid" && r.width < list.clientWidth * 0.6;
      var after = horizontal ? (e.clientX > r.left + r.width / 2) : (e.clientY > r.top + r.height / 2);
      list.insertBefore(dragging, after ? over.nextSibling : over);
      autoScroll(e.clientY);
    });
    function end() {
      if (!dragging) return;
      dragging.classList.remove("dragging");
      list.classList.remove("is-sorting");
      dragging = null;
      persist();
    }
    list.addEventListener("pointerup", end);
    list.addEventListener("pointercancel", end);
    function autoScroll(y) {
      if (y < 60) window.scrollBy(0, -12);
      else if (y > window.innerHeight - 60) window.scrollBy(0, 12);
    }
    list.addEventListener("keydown", function (e) {
      var handle = e.target.closest(".drag-handle");
      if (!handle) return;
      var item = handle.closest("[data-id]");
      var k = e.key;
      if (k === "ArrowUp" || k === "ArrowLeft") {
        if (item.previousElementSibling) { list.insertBefore(item, item.previousElementSibling); }
      } else if (k === "ArrowDown" || k === "ArrowRight") {
        if (item.nextElementSibling) { list.insertBefore(item.nextElementSibling, item); }
      } else { return; }
      e.preventDefault();
      handle.focus();
      var pos = ids().indexOf(item.getAttribute("data-id")) + 1;
      setStatus("移到第 " + pos + " 個", "dirty");
      persistSoon();
    });
  });

  // ---------------------------------------------------------------- 照片 Lightbox
  $$("[data-lightbox]").forEach(function (gallery) {
    var links = $$("a[data-full]", gallery);
    if (!links.length) return;
    var box, imgEl, capEl, countEl, idx = 0, lastFocus;
    function build() {
      box = document.createElement("div");
      box.className = "lightbox";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      box.setAttribute("aria-label", "照片檢視");
      box.innerHTML = '<div class="lb-top"><span class="lb-count" aria-live="polite"></span>' +
        '<button type="button" class="lb-btn lb-close" aria-label="關閉（Esc）">✕</button></div>' +
        '<button type="button" class="lb-btn lb-prev" aria-label="上一張">‹</button>' +
        '<figure class="lb-figure"><img alt=""><figcaption></figcaption></figure>' +
        '<button type="button" class="lb-btn lb-next" aria-label="下一張">›</button>';
      document.body.appendChild(box);
      imgEl = $("img", box); capEl = $("figcaption", box); countEl = $(".lb-count", box);
      $(".lb-close", box).addEventListener("click", close);
      $(".lb-prev", box).addEventListener("click", function () { show(idx - 1); });
      $(".lb-next", box).addEventListener("click", function () { show(idx + 1); });
      box.addEventListener("click", function (e) { if (e.target === box || e.target.classList.contains("lb-figure")) close(); });
      var sx = null, sy = null;
      box.addEventListener("touchstart", function (e) { sx = e.touches[0].clientX; sy = e.touches[0].clientY; }, { passive: true });
      box.addEventListener("touchend", function (e) {
        if (sx === null) return;
        var dx = e.changedTouches[0].clientX - sx, dy = e.changedTouches[0].clientY - sy;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) show(idx + (dx < 0 ? 1 : -1));
        else if (dy > 90 && Math.abs(dy) > Math.abs(dx)) close();
        sx = sy = null;
      });
    }
    function show(i) {
      idx = (i + links.length) % links.length;
      var a = links[idx];
      imgEl.src = a.getAttribute("data-full");
      var cap = a.getAttribute("data-caption") || "";
      imgEl.alt = cap || ("第 " + (idx + 1) + " 張照片");
      capEl.textContent = cap;
      countEl.textContent = (idx + 1) + " / " + links.length;
      var single = links.length < 2;
      $(".lb-prev", box).hidden = single; $(".lb-next", box).hidden = single;
      var nxt = links[(idx + 1) % links.length];
      if (nxt) { var pre = new Image(); pre.src = nxt.getAttribute("data-full"); }
    }
    function onKey(e) {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowLeft") show(idx - 1);
      else if (e.key === "ArrowRight") show(idx + 1);
      else if (e.key === "Tab") {             // 焦點留在燈箱內
        var f = $$("button:not([hidden])", box), first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    function open(i) {
      if (!box) build();
      lastFocus = document.activeElement;
      box.classList.add("open");
      document.body.classList.add("no-scroll");
      document.addEventListener("keydown", onKey);
      show(i);
      $(".lb-close", box).focus();
    }
    function close() {
      box.classList.remove("open");
      document.body.classList.remove("no-scroll");
      document.removeEventListener("keydown", onKey);
      imgEl.removeAttribute("src");
      if (lastFocus) lastFocus.focus();
    }
    links.forEach(function (a, i) {
      a.addEventListener("click", function (e) { e.preventDefault(); open(i); });
    });
  });

  // ---------------------------------------------------------------- 分享
  var sheet = $("#share-sheet");
  function openSheet() {
    if (!sheet) return;
    sheet.hidden = false;
    document.body.classList.add("no-scroll");
    $("[data-share-close]", sheet).focus();
  }
  function closeSheet() {
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    document.body.classList.remove("no-scroll");
    var b = $("[data-share]"); if (b) b.focus();
  }
  if (sheet) {
    $("[data-share-close]", sheet).addEventListener("click", closeSheet);
    sheet.addEventListener("click", function (e) { if (e.target === sheet) closeSheet(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSheet(); });
  }
  $$("[data-share]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var data = { title: btn.getAttribute("data-title"), text: btn.getAttribute("data-text"), url: btn.getAttribute("data-url") };
      var mobile = window.matchMedia("(pointer: coarse)").matches;
      if (navigator.share && mobile) {
        navigator.share(data).catch(function (err) { if (err && err.name !== "AbortError") openSheet(); });
      } else { openSheet(); }
    });
  });

  // ---------------------------------------------------------------- 複製、列印
  $$("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.getAttribute("data-copy");
      function fallback() {
        var ta = document.createElement("textarea");
        ta.value = text; ta.setAttribute("readonly", ""); ta.className = "sr-only";
        document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); toast("已複製連結"); } catch (e) { window.prompt("請複製以下連結：", text); }
        ta.remove();
      }
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () { toast("已複製連結"); }, fallback);
      } else { fallback(); }
    });
  });
  $$("[data-print]").forEach(function (b) { b.addEventListener("click", function () { window.print(); }); });

  // ---------------------------------------------------------------- 表格下載成 CSV（臨時密碼只在瀏覽器端產生檔案，不經伺服器保存）
  $$("[data-download-table]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var table = $(btn.getAttribute("data-download-table"));
      if (!table) return;
      var rows = $$("tr", table).map(function (tr) {
        return $$("th,td", tr).map(function (c) {
          var v = c.textContent.trim().replace(/\s+/g, " ");
          if (/^[=+\-@]/.test(v)) v = "'" + v;
          return '"' + v.replace(/"/g, '""') + '"';
        }).join(",");
      });
      var blob = new Blob(["﻿" + rows.join("\r\n")], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = btn.getAttribute("data-filename") || "export.csv";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    });
  });
})();

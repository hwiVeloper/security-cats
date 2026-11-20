/* 설정 화면 (큰 모달): 서비스·서버 추가/수정/삭제, 샘플 줄 형식 테스트, 실행될 명령 보기, 설정 파일 불러오기.
 * 전역 NapiSettings.open({add}). app.js의 NapiApp.onSettings / onAddService가 부른다.
 * 저장할 때는 get_state를 다시 불러 그 시점의 메모(notes)를 서비스 이름별로 옮겨 담는다.
 * (설정 화면을 연 뒤에 저장된 메모가 사라지지 않게. 이름을 바꾼 서비스는 원래 이름의 메모를 가져간다.)
 */
(function (root) {
  "use strict";
  var R = root.NapiRender;
  var el = R.el;
  var doc = root.document;

  var FORMATS = [
    ["webtob", "WebtoB"], ["apache_common", "Apache (common)"], ["apache_combined", "Apache (combined)"],
    ["nginx_combined", "nginx (combined)"], ["custom", "직접 지정"]
  ];
  var TIME_FORMATS = [["clf", "CLF (10/Oct/2026:13:55:36 +0900)"], ["iso8601", "ISO 8601 (2026-10-10T13:55:36)"]];
  var CUSTOM_FIELDS = [
    ["method_field", "메서드 필드"], ["path_field", "경로 필드"], ["status_field", "상태 코드 필드"], ["time_field", "시간 필드"]
  ];
  var WINDOW_LABEL = { "90": "90일", "180": "180일", "365": "1년", "all": "전체" };
  var TIER_NOTICE = "한 서비스에는 같은 요청을 한 번만 기록하는 서버(같은 계층)만 넣어 주세요";
  var SAMPLE_NOTICE = "서버에서 로그를 가져오지 않아요. 직접 한 줄 붙여 넣어 주세요";

  var ui = null; // 열려 있는 설정 화면
  var keySeq = 0;
  var fieldSeq = 0;

  function api() { return root.pywebview.api; }
  function app() { return root.NapiApp; }
  function isErr(res) { return res && typeof res === "object" && !Array.isArray(res) && res.ok === false && typeof res.error === "string"; }
  function clone(v) { return v === undefined || v === null ? v : JSON.parse(JSON.stringify(v)); }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function trim(v) { return v === null || v === undefined ? "" : String(v).trim(); }

  // ------------------------------------------------------------ 초안 (draft)

  function normServer(sv) {
    sv = clone(sv) || {};
    return {
      name: sv.name || "", host: sv.host || "", user: sv.user || "", log_glob: sv.log_glob || "",
      port: sv.port === undefined || sv.port === null ? 22 : sv.port,
      auth: sv.auth === "key" ? "key" : "password",
      key_file: sv.key_file || null,
      log_format: sv.log_format || "webtob",
      custom_format: sv.custom_format || null
    };
  }

  function normService(s) {
    s = clone(s) || {};
    var spec = s.spec || {};
    var pm = s.path_mapping || {};
    var th = s.thresholds || {};
    return {
      name: s.name || "",
      spec: { type: spec.type || "openapi", source: spec.source === "file" ? "file" : "url", location: spec.location || "" },
      servers: (s.servers || []).map(normServer),
      path_mapping: { strip_prefix: pm.strip_prefix || "", add_prefix: pm.add_prefix || "" },
      thresholds: {
        awake_days: th.awake_days === undefined ? 30 : th.awake_days,
        min_coverage_days: th.min_coverage_days === undefined ? 90 : th.min_coverage_days
      },
      exclude_paths: (s.exclude_paths || []).slice(),
      notes: s.notes || {}
    };
  }

  function wrap(svc, orig) {
    return { key: ++keySeq, orig: orig, svc: svc, snap: orig === null ? null : JSON.stringify(svc), armDelete: false };
  }

  function buildDraft(config) {
    config = config || {};
    return {
      version: config.version || 1,
      settings: clone(config.settings) || {},
      services: (config.services || []).map(function (s) { return wrap(normService(s), s.name); }),
      removed: false
    };
  }

  function isDirty(w) { return w.snap === null || JSON.stringify(w.svc) !== w.snap; }
  function anyDirty() { return ui.draft.removed || ui.draft.services.some(isDirty); }
  function selected() {
    var list = ui.draft.services;
    for (var i = 0; i < list.length; i++) if (list[i].key === ui.sel) return list[i];
    return null;
  }

  function uniqueName(base, taken) {
    var name = base, n = 2;
    while (taken.indexOf(name) >= 0) name = base + " " + n++;
    return name;
  }

  function toNum(v) {
    var s = trim(v);
    return /^[0-9]+$/.test(s) ? parseInt(s, 10) : s;
  }
  function posInt(v, max) {
    var n = typeof v === "number" ? v : (/^[0-9]+$/.test(trim(v)) ? parseInt(trim(v), 10) : NaN);
    return isFinite(n) && n >= 1 && (!max || n <= max) ? n : null;
  }

  // ------------------------------------------------------------ 오류

  function addErr(w, idx, fieldName, msg) {
    var e = ui.errors[w.key] || (ui.errors[w.key] = { svc: {}, servers: {} });
    var bucket = idx === null ? e.svc : (e.servers[idx] || (e.servers[idx] = {}));
    (bucket[fieldName] || (bucket[fieldName] = [])).push(msg);
  }
  function errsOf(w) { return ui.errors[w.key] || { svc: {}, servers: {} }; }
  function hasErr(w) { return !!ui.errors[w.key]; }
  function errText(bucket, name) { return bucket && bucket[name] ? bucket[name].join(" ") : null; }

  function serverField(text) {
    if (/호스트/.test(text)) return "host";
    if (/키 ?파일/.test(text)) return "key_file";
    if (/사용자 정의 형식|custom|필드 번호/.test(text)) return "log_format";
    if (/로그 경로/.test(text)) return "log_glob";
    if (/서버 이름/.test(text)) return "name";
    if (/포트/.test(text)) return "port";
    if (/계정|사용자/.test(text)) return "user";
    return "_";
  }
  function serviceField(text) {
    if (/서비스 이름/.test(text)) return "name";
    if (/명세/.test(text)) return "spec";
    if (/기준값/.test(text)) return "thresholds";
    if (/제외 경로/.test(text)) return "exclude";
    return "_";
  }

  function servicesNamed(name) {
    return ui.draft.services.filter(function (w) { return trim(w.svc.name) === name; });
  }

  // validate_config의 문구("서비스 'A' / 서버 'B': ...", "서비스 'A': ...")를 해당 칸에 붙인다.
  // 맞출 수 없는 문구는 위쪽 목록에 둔다.
  function routeError(msg) {
    msg = String(msg);
    var m = /^서비스 '([\s\S]*?)' \/ 서버 '([\s\S]*?)': ([\s\S]*)$/.exec(msg);
    if (m) {
      var placed = false;
      servicesNamed(m[1]).forEach(function (w) {
        for (var i = 0; i < w.svc.servers.length; i++) {
          if (trim(w.svc.servers[i].name) === m[2]) { addErr(w, i, serverField(m[3]), m[3]); placed = true; return; }
        }
      });
      if (placed) return;
    }
    m = /^서비스 '([\s\S]*?)': ([\s\S]*)$/.exec(msg);
    if (m) {
      var hits = servicesNamed(m[1]);
      hits.forEach(function (w) { addErr(w, null, serviceField(m[2]), m[2]); });
      if (hits.length) return;
    }
    if (/^서비스 이름이 비어/.test(msg)) {
      var empty = servicesNamed("");
      empty.forEach(function (w) { addErr(w, null, "name", msg); });
      if (empty.length) return;
    }
    ui.top.push(msg);
  }

  // 백엔드가 검사하지 않는 값 (숫자, 필수 입력)만 미리 본다. 나머지는 save_config의 검사에 맡긴다.
  function validateDraft() {
    var count = 0;
    function add(w, idx, f, msg) { addErr(w, idx, f, msg); count++; }
    ui.draft.services.forEach(function (w) {
      var s = w.svc;
      if (!trim(s.spec.location)) add(w, null, "spec", "명세 위치를 입력해 주세요.");
      if (!posInt(s.thresholds.awake_days) || !posInt(s.thresholds.min_coverage_days)) {
        add(w, null, "thresholds", "기준값은 1 이상의 숫자로 입력해 주세요.");
      }
      var seen = {};
      s.servers.forEach(function (sv, i) {
        var nm = trim(sv.name);
        if (!nm) add(w, i, "name", "서버 이름을 입력해 주세요.");
        else if (seen["#" + nm]) add(w, i, "name", "같은 이름의 서버가 이미 있어요.");
        seen["#" + nm] = true;
        if (!posInt(sv.port, 65535)) add(w, i, "port", "포트는 1~65535 사이 숫자로 입력해 주세요.");
        if (!trim(sv.user)) add(w, i, "user", "접속 계정을 입력해 주세요.");
        if (sv.log_format === "custom" && !customPayload(sv.custom_format)) {
          add(w, i, "log_format", "필드 번호는 1 이상의 숫자로 모두 입력해 주세요.");
        }
      });
    });
    return count;
  }

  // ------------------------------------------------------------ 저장용 dict (화면 전용 값은 넣지 않는다)

  function customPayload(c) {
    if (!c) return null;
    var out = {};
    for (var i = 0; i < CUSTOM_FIELDS.length; i++) {
      var n = posInt(c[CUSTOM_FIELDS[i][0]]);
      if (!n) return null;
      out[CUSTOM_FIELDS[i][0]] = n;
    }
    out.time_format = c.time_format === "iso8601" ? "iso8601" : "clf";
    return out;
  }

  function serverPayload(sv) {
    var custom = sv.log_format === "custom";
    return {
      name: trim(sv.name), host: trim(sv.host), user: trim(sv.user), log_glob: trim(sv.log_glob),
      port: posInt(sv.port, 65535), auth: sv.auth,
      key_file: sv.auth === "key" ? (trim(sv.key_file) || null) : null,
      log_format: sv.log_format,
      custom_format: custom ? customPayload(sv.custom_format) : null
    };
  }

  function buildPayload(currentNotes, currentSettings) {
    return {
      version: ui.draft.version,
      settings: currentSettings || ui.draft.settings,
      services: ui.draft.services.map(function (w) {
        var s = w.svc;
        var notes = w.orig !== null && Object.prototype.hasOwnProperty.call(currentNotes, w.orig)
          ? currentNotes[w.orig] : (s.notes || {});
        return {
          name: trim(s.name),
          spec: { type: s.spec.type, source: s.spec.source, location: trim(s.spec.location) },
          servers: s.servers.map(serverPayload),
          path_mapping: { strip_prefix: trim(s.path_mapping.strip_prefix), add_prefix: trim(s.path_mapping.add_prefix) },
          thresholds: { awake_days: posInt(s.thresholds.awake_days), min_coverage_days: posInt(s.thresholds.min_coverage_days) },
          exclude_paths: s.exclude_paths.map(trim).filter(Boolean),
          notes: notes
        };
      })
    };
  }

  // ------------------------------------------------------------ 입력 칸 도우미

  function input(type, value, onInput, attrs) {
    var a = { type: type, autocomplete: "off", spellcheck: "false" };
    Object.keys(attrs || {}).forEach(function (k) { a[k] = attrs[k]; });
    var n = el("input", a);
    n.value = value === null || value === undefined ? "" : String(value);
    n.addEventListener("input", function () { onInput(n.value); });
    return n;
  }
  function select(options, value, onChange) {
    var s = el("select");
    options.forEach(function (o) {
      var opt = el("option", { value: o[0] }, o[1]);
      if (o[0] === value) opt.selected = true;
      s.appendChild(opt);
    });
    s.addEventListener("change", function () { onChange(s.value); });
    return s;
  }
  function field(label, control, err, opts) {
    opts = opts || {};
    var id = "set-f-" + (++fieldSeq);
    control.id = id;
    var errNode = null;
    if (err) {
      errNode = el("span", { class: "field-err", id: id + "-err" }, err);
      control.setAttribute("aria-invalid", "true");
      control.setAttribute("aria-describedby", id + "-err");
    }
    return el("div", { class: "field" + (opts.full ? " full" : ""), hidden: opts.hidden },
      el("label", { for: id }, label), control,
      opts.hint ? el("span", { class: "muted small" }, opts.hint) : null, errNode);
  }
  function cardErr(text) { return text ? el("p", { class: "card-err" }, "⚠ " + text) : null; }

  // ------------------------------------------------------------ 화면

  function open(opts) {
    opts = opts || {};
    if (ui) return;
    var st = app().state;
    ui = {
      draft: buildDraft(st.config), sel: null, errors: {}, top: [], busy: false, closeArmed: false,
      configError: st.error || null, configPath: st.configPath || "", root: el("div"), msg: el("p", { class: "form-msg", role: "status" }),
      handle: null, focus: null, scrollTop: 0
    };
    if (opts.add) {
      addService();
    } else {
      var hit = ui.draft.services.filter(function (w) { return w.orig === st.current; })[0] || ui.draft.services[0];
      ui.sel = hit ? hit.key : null;
    }
    ui.saveBtn = el("button", { type: "button", class: "btn pri", onclick: save }, "저장");
    ui.closeBtn = el("button", { type: "button", class: "btn", onclick: function () { ui.handle.dismiss(); } }, "닫기");
    render();
    ui.handle = app().openModal({
      title: "설정", wide: true, body: ui.root, foot: [ui.msg, ui.closeBtn, ui.saveBtn],
      initialFocus: ui.focus || undefined, onDismiss: tryClose
    });
    if (opts.add && ui.focus) ui.focus.select();
  }

  function tryClose() {
    if (!ui) return true;
    if (ui.busy) return false;
    if (anyDirty() && !ui.closeArmed) {
      ui.closeArmed = true;
      ui.msg.textContent = "저장하지 않은 변경이 있어요. 한 번 더 닫으면 변경을 버려요";
      return false;
    }
    ui = null;
    return true;
  }

  function setBusy(on) {
    ui.busy = on;
    ui.saveBtn.disabled = on;
    ui.closeBtn.disabled = on;
  }

  function bodyScroller() { return ui.handle ? ui.handle.box.querySelector(".modal-body") : null; }

  function render(keepScroll) {
    var sc = bodyScroller();
    var top = keepScroll && sc ? sc.scrollTop : 0;
    var rootNode = ui.root;
    clear(rootNode);
    ui.focus = null;
    ui.listNames = {};

    rootNode.appendChild(el("div", { class: "set-path" },
      "설정 파일: ", el("span", { class: "mono" }, ui.configPath || "—"), " ",
      el("button", { type: "button", class: "btn small", onclick: loadFile, title: "다른 설정 파일(YAML)을 불러와요. 이 화면에서 바꾼 내용은 버려져요" },
        "설정 파일 불러오기")));
    if (ui.configError) {
      rootNode.appendChild(el("p", { class: "notice warn-box", role: "alert" },
        "⚠ 설정 파일을 읽지 못했어요: " + ui.configError + " — 저장하면 지금 파일을 이 화면의 내용으로 새로 써요."));
    }
    var nErr = Object.keys(ui.errors).length;
    if (ui.top.length || nErr) {
      var items = ui.top.map(function (t) { return el("li", null, t); });
      if (nErr) items.unshift(el("li", null, "표시된 칸을 고쳐 주세요. (⚠ 표시된 서비스: " + nErr + "개)"));
      rootNode.appendChild(el("ul", { class: "set-errors" + (items.length === 1 ? " solo" : ""), role: "alert" }, items));
    }

    var list = el("nav", { class: "set-list", "aria-label": "서비스" });
    ui.draft.services.forEach(function (w) {
      var nameNode = el("span", { class: "name" }, w.svc.name || "(이름 없음)");
      ui.listNames[w.key] = nameNode;
      var on = w.key === ui.sel;
      list.appendChild(el("button", { type: "button", class: "svc" + (on ? " on" : ""), "aria-current": on ? "true" : null,
        onclick: function () {
          if (ui.sel === w.key) return;
          ui.draft.services.forEach(function (x) { x.armDelete = false; });
          ui.sel = w.key;
          render();
        } },
        nameNode, hasErr(w) ? el("span", { class: "bad", title: "고칠 곳이 있어요" }, "⚠") : null));
    });
    list.appendChild(el("button", { type: "button", class: "add", onclick: function () { addService(); render(); if (ui.focus) { ui.focus.focus(); ui.focus.select(); } } }, "＋ 서비스 추가"));

    var form = el("div", { class: "set-form" });
    var w = selected();
    if (w) renderService(form, w);
    else form.appendChild(el("p", { class: "empty-note" }, "서비스가 없어요. 왼쪽의 ＋ 서비스 추가를 눌러 주세요"));
    rootNode.appendChild(el("div", { class: "set-grid" }, list, form));

    if (sc) sc.scrollTop = top;
  }

  function addService() {
    var names = ui.draft.services.map(function (x) { return x.svc.name; });
    var w = wrap(normService({ name: uniqueName("새 서비스", names) }), null);
    ui.draft.services.push(w);
    ui.sel = w.key;
    ui.closeArmed = false;
  }

  function changed() {
    ui.closeArmed = false;
    if (ui.msg.textContent && !ui.busy) ui.msg.textContent = "";
  }

  function renderService(form, w) {
    var s = w.svc;
    var E = errsOf(w);
    var nameInput = input("text", s.name, function (v) {
      s.name = v; changed();
      if (ui.listNames[w.key]) ui.listNames[w.key].textContent = v || "(이름 없음)";
    });
    ui.focus = nameInput;
    var locInput = input("text", s.spec.location, function (v) { s.spec.location = v; changed(); },
      { placeholder: s.spec.source === "file" ? "C:\\specs\\api.yaml" : "https://swagger.example/v3/api-docs" });
    var sourceSel = select([["url", "URL"], ["file", "파일"]], s.spec.source, function (v) {
      s.spec.source = v; changed();
      locInput.placeholder = v === "file" ? "C:\\specs\\api.yaml" : "https://swagger.example/v3/api-docs";
    });

    form.appendChild(el("fieldset", null, el("legend", null, "서비스"),
      cardErr(errText(E.svc, "_")),
      el("div", { class: "fields" },
        field("서비스명", nameInput, errText(E.svc, "name")),
        field("명세 형식", select([["openapi", "OpenAPI/Swagger"]], s.spec.type, function (v) { s.spec.type = v; changed(); })),
        field("명세 출처", sourceSel),
        field("명세 위치", locInput, errText(E.svc, "spec"), { full: true }))));

    form.appendChild(el("fieldset", null, el("legend", null, "경로 보정"),
      el("p", { class: "muted small", style: "margin:0 0 6px" }, "로그의 경로 앞부분을 빼거나(strip) 붙여서(add) 명세 경로와 맞춰요. 예: /api 를 빼기"),
      el("div", { class: "fields" },
        field("앞에서 뺄 경로 (strip)", input("text", s.path_mapping.strip_prefix, function (v) { s.path_mapping.strip_prefix = v; changed(); }, { placeholder: "/api" })),
        field("앞에 붙일 경로 (add)", input("text", s.path_mapping.add_prefix, function (v) { s.path_mapping.add_prefix = v; changed(); }, { placeholder: "/v1" })))));

    var thErr = errText(E.svc, "thresholds");
    form.appendChild(el("fieldset", null, el("legend", null, "기준값"),
      el("div", { class: "fields" },
        field("깨어있음 기준 (최근 N일 안에 호출)", input("number", s.thresholds.awake_days,
          function (v) { s.thresholds.awake_days = toNum(v); changed(); }, { min: "1", step: "1", inputmode: "numeric" }), thErr),
        field("필요한 최소 로그 기간 (일)", input("number", s.thresholds.min_coverage_days,
          function (v) { s.thresholds.min_coverage_days = toNum(v); changed(); }, { min: "1", step: "1", inputmode: "numeric" })))));

    var ex = el("textarea", { rows: "3", spellcheck: "false", placeholder: "/internal/**\n/actuator/*" });
    ex.value = s.exclude_paths.join("\n");
    ex.addEventListener("input", function () { s.exclude_paths = ex.value.split(/\r?\n/); changed(); });
    form.appendChild(el("fieldset", null, el("legend", null, "제외 경로"),
      el("div", { class: "fields" }, field("한 줄에 하나씩 (* 와 ** 를 쓸 수 있어요)", ex, errText(E.svc, "exclude"), { full: true }))));

    var serverBox = el("fieldset", null, el("legend", null, "서버 (" + s.servers.length + "대)"),
      el("p", { class: "notice" }, TIER_NOTICE));
    s.servers.forEach(function (sv, i) { serverBox.appendChild(renderServer(w, sv, i, E.servers[i] || {})); });
    var previewMsg = el("span", { class: "muted small", role: "status" });
    serverBox.appendChild(el("div", { class: "row-btns" },
      el("button", { type: "button", class: "btn", onclick: function () {
        var names = s.servers.map(function (x) { return trim(x.name); });
        var n = s.servers.length + 1;
        while (names.indexOf("서버 " + n) >= 0) n++;
        s.servers.push(normServer({ name: "서버 " + n }));
        delete ui.errors[w.key];
        changed();
        render(true);
      } }, "＋ 서버 추가"),
      el("button", { type: "button", class: "btn", onclick: function () { preview(w, previewMsg); } }, "실행될 명령 보기"),
      previewMsg));
    form.appendChild(serverBox);

    var notes = Object.keys(s.notes || {}).length;
    var del = el("button", { type: "button", class: "btn danger" }, w.armDelete ? "한 번 더 누르면 삭제해요" : "이 서비스 삭제");
    del.addEventListener("click", function () {
      if (!w.armDelete) { w.armDelete = true; render(true); return; }
      var i = ui.draft.services.indexOf(w);
      ui.draft.services.splice(i, 1);
      if (w.orig !== null) ui.draft.removed = true;
      delete ui.errors[w.key];
      var next = ui.draft.services[Math.min(i, ui.draft.services.length - 1)];
      ui.sel = next ? next.key : null;
      changed();
      render();
    });
    form.appendChild(el("div", { class: "row-btns" }, del,
      w.armDelete && notes ? el("span", { class: "warn small" }, "이 서비스의 메모 " + notes + "개도 함께 지워져요") : null,
      el("span", { class: "muted small" }, "저장을 눌러야 파일에 반영돼요")));
  }

  function renderServer(w, sv, i, E) {
    function set(k) { return function (v) { sv[k] = v; changed(); }; }
    var keyField = field("키파일 경로", input("text", sv.key_file, function (v) { sv.key_file = v || null; changed(); },
      { placeholder: "C:\\Users\\me\\.ssh\\id_ed25519" }), errText(E, "key_file"), { hidden: sv.auth !== "key" });

    var c = sv.custom_format || {};
    var customBox = el("div", { class: "fields full", hidden: sv.log_format !== "custom", style: "grid-column:1/-1" },
      CUSTOM_FIELDS.map(function (f) {
        return field(f[1], input("number", c[f[0]], function (v) {
          sv.custom_format = sv.custom_format || { time_format: "clf" };
          sv.custom_format[f[0]] = toNum(v); changed();
        }, { min: "1", step: "1", inputmode: "numeric" }));
      }),
      field("시간 형식", select(TIME_FORMATS, c.time_format || "clf", function (v) {
        sv.custom_format = sv.custom_format || {};
        sv.custom_format.time_format = v; changed();
      })),
      el("span", { class: "muted small", style: "grid-column:1/-1" }, "필드 번호는 줄을 공백으로 나눈 칸의 순서예요 (1부터)."));

    var legendName = el("span", null, sv.name || "서버 " + (i + 1));
    var nameInput = input("text", sv.name, function (v) { sv.name = v; changed(); legendName.textContent = v || "서버 " + (i + 1); });

    var sample = sampleBox(sv);
    var card = el("fieldset", { class: "server-card" }, el("legend", null, legendName),
      cardErr(errText(E, "_")),
      el("div", { class: "fields" },
        field("이름", nameInput, errText(E, "name")),
        field("호스트", input("text", sv.host, set("host"), { placeholder: "10.0.0.11" }), errText(E, "host")),
        field("포트", input("number", sv.port, function (v) { sv.port = toNum(v); changed(); },
          { min: "1", max: "65535", step: "1", inputmode: "numeric" }), errText(E, "port")),
        field("계정 (user)", input("text", sv.user, set("user"), { placeholder: "napi" }), errText(E, "user")),
        field("인증", select([["password", "비밀번호"], ["key", "키파일"]], sv.auth, function (v) {
          sv.auth = v; changed(); keyField.hidden = v !== "key";
        })),
        keyField,
        field("로그 파일 (glob)", input("text", sv.log_glob, set("log_glob"), { placeholder: "/logs/webtob/access.log*" }),
          errText(E, "log_glob"), { full: true, hint: "디렉터리 부분에는 * 를 쓸 수 없어요. .gz 파일도 읽어요." }),
        field("로그 형식", select(FORMATS, sv.log_format, function (v) {
          sv.log_format = v; changed(); customBox.hidden = v !== "custom";
        }), errText(E, "log_format")),
        customBox),
      el("div", { class: "row-btns" },
        el("button", { type: "button", class: "btn small", "aria-expanded": "false", onclick: function (e) {
          sample.hidden = !sample.hidden;
          e.currentTarget.setAttribute("aria-expanded", sample.hidden ? "false" : "true");
          if (!sample.hidden) sample.querySelector("textarea").focus();
        } }, "샘플 줄로 형식 테스트"),
        el("button", { type: "button", class: "btn small danger", onclick: function () {
          w.svc.servers.splice(i, 1);
          delete ui.errors[w.key];
          changed();
          render(true);
        } }, "서버 삭제")),
      sample);
    return card;
  }

  // ------------------------------------------------------------ 샘플 줄 테스트

  function sampleBox(sv) {
    var ta = el("textarea", { rows: "2", spellcheck: "false", "aria-label": "샘플 로그 한 줄",
      placeholder: '10.1.2.3 - - [18/Nov/2026:10:00:00 +0900] "GET /v1/payments/123?x=1 HTTP/1.1" 200 512' });
    var out = el("div", { "aria-live": "polite" });
    function runTest() {
      clear(out);
      var lines = ta.value.split(/\r?\n/).filter(function (l) { return trim(l); });
      if (!lines.length) { out.appendChild(el("p", { class: "sample-bad" }, "로그 한 줄을 붙여 넣어 주세요")); return; }
      var custom = null;
      if (sv.log_format === "custom") {
        custom = customPayload(sv.custom_format);
        if (!custom) { out.appendChild(el("p", { class: "sample-bad" }, "직접 지정 형식의 필드 번호를 먼저 입력해 주세요")); return; }
      }
      api().test_log_line(sv.log_format, custom, lines[0]).then(function (res) {
        clear(out);
        if (!res || res.ok !== true) {
          out.appendChild(el("p", { class: "sample-bad" }, "✕ " + ((res && res.error) || "이 형식으로 읽을 수 없는 줄이에요")));
          return;
        }
        out.appendChild(el("dl", { class: "sample-out" },
          el("dt", null, "메서드"), el("dd", null, res.method),
          el("dt", null, "경로 (원문)"), el("dd", null, res.path),
          el("dt", null, "정규화 경로"), el("dd", null, res.normalized),
          el("dt", null, "상태 코드"), el("dd", null, String(res.status)),
          el("dt", null, "날짜"), el("dd", null, R.fmtDate(res.date))));
        if (lines.length > 1) out.appendChild(el("p", { class: "muted small" }, "첫 줄만 테스트했어요"));
      }, function () {
        clear(out);
        out.appendChild(el("p", { class: "sample-bad" }, "테스트하지 못했어요"));
      });
    }
    ta.addEventListener("paste", function () { setTimeout(runTest, 0); });
    var box = el("div", { class: "sample", hidden: true },
      el("p", { class: "notice" }, SAMPLE_NOTICE),
      ta,
      el("div", { class: "row-btns" }, el("button", { type: "button", class: "btn small", onclick: runTest }, "테스트")),
      out);
    return box;
  }

  // ------------------------------------------------------------ 실행될 명령 보기

  function preview(w, msgNode) {
    var saved = ((app().state.config && app().state.config.services) || []).some(function (s) { return s.name === w.orig; });
    if (w.orig === null || !saved) { msgNode.textContent = "먼저 저장해 주세요. 저장된 설정으로 명령을 만들어요"; return; }
    msgNode.textContent = "";
    var window = app().state.window;
    var dirty = isDirty(w);
    api().preview_commands(w.orig, window).then(function (res) {
      if (isErr(res)) { msgNode.textContent = res.error; return; }
      var names = Object.keys(res || {});
      var body = [
        el("p", { class: "muted" }, "분석 기간 " + (WINDOW_LABEL[window] || window) +
          " 기준으로, 저장된 설정에서 만든 명령이에요. 서버에서는 읽기만 하고 파일을 만들지 않아요."),
        dirty ? el("p", { class: "notice warn-box" }, "저장하지 않은 변경은 반영되지 않았어요") : null
      ];
      if (!names.length) body.push(el("p", { class: "empty-note" }, "등록된 서버가 없어요"));
      names.forEach(function (n) {
        var text = String(res[n]);
        var copyBtn = el("button", { type: "button", class: "btn small" }, "복사");
        copyBtn.addEventListener("click", function () { copyText(text, copyBtn); });
        body.push(el("div", { class: "cmd-head" }, el("h4", null, n), copyBtn));
        body.push(el("pre", { class: "cmd" }, text));
      });
      var closeBtn = el("button", { type: "button", class: "btn" }, "닫기");
      var h = app().openModal({ title: w.orig + " — 실행될 명령", body: body, foot: [closeBtn], cls: "cmd-modal" });
      closeBtn.addEventListener("click", function () { h.close(); });
    }, function () { msgNode.textContent = "명령을 만들지 못했어요"; });
  }

  function copyText(text, btn) {
    function done(ok) {
      btn.textContent = ok ? "복사했어요" : "복사하지 못했어요";
      setTimeout(function () { btn.textContent = "복사"; }, 1600);
    }
    function fallback() {
      var ta = el("textarea", { readonly: true, style: "position:fixed;left:-9999px;top:0" });
      ta.value = text;
      btn.parentNode.appendChild(ta); // 모달 안 (바깥은 inert라 선택이 안 된다)
      ta.select();
      var ok = false;
      try { ok = doc.execCommand("copy"); } catch (e) { ok = false; }
      ta.parentNode.removeChild(ta);
      btn.focus();
      return ok;
    }
    if (root.navigator.clipboard && root.navigator.clipboard.writeText) {
      root.navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallback()); });
    } else {
      done(fallback());
    }
  }

  // ------------------------------------------------------------ 설정 파일 불러오기

  function loadFile() {
    if (ui.busy) return;
    setBusy(true);
    var u = ui;
    api().open_config_dialog().then(function (res) {
      if (u !== ui) return;
      if (isErr(res)) { setBusy(false); ui.top = [res.error]; render(true); return; }
      if (!res || !res.ok) { setBusy(false); return; } // 고르지 않음
      return app().refresh().then(function () {
        if (u !== ui) return;
        setBusy(false);
        var st = app().state;
        ui.draft = buildDraft(st.config);
        ui.configError = st.error || null;
        ui.configPath = st.configPath || res.path;
        ui.errors = {};
        ui.top = [];
        ui.closeArmed = false;
        var first = ui.draft.services[0];
        ui.sel = first ? first.key : null;
        render();
        ui.msg.textContent = "설정 파일을 불러왔어요";
      });
    }).catch(function () {
      if (u !== ui) return;
      setBusy(false);
      ui.top = ["설정 파일을 불러오지 못했어요"];
      render(true);
    });
  }

  // ------------------------------------------------------------ 저장

  function firstErrorKey() {
    var list = ui.draft.services;
    for (var i = 0; i < list.length; i++) if (hasErr(list[i])) return list[i].key;
    return null;
  }

  function showErrors(msg) {
    var cur = selected();
    if (!cur || !hasErr(cur)) {
      var k = firstErrorKey();
      if (k !== null) ui.sel = k;
    }
    ui.msg.textContent = msg;
    render(true);
    var bad = ui.root.querySelector("[aria-invalid='true']");
    if (bad) bad.focus();
  }

  function save() {
    if (!ui || ui.busy) return;
    var u = ui;
    ui.errors = {};
    ui.top = [];
    var n = validateDraft();
    if (n) { showErrors("입력값을 확인해 주세요 (" + n + "곳)"); return; }
    setBusy(true);
    ui.msg.textContent = "저장하는 중…";
    api().get_state().then(function (st) {
      // 지금 파일에 있는 메모를 서비스 이름별로 가져온다 (화면을 연 뒤에 저장된 메모 보존).
      var notes = Object.create(null);
      var settings = null;
      if (st && !isErr(st) && st.config && !st.error) {
        (st.config.services || []).forEach(function (s) { notes[s.name] = s.notes || {}; });
        settings = st.config.settings || null;
      }
      return api().save_config(buildPayload(notes, settings));
    }).then(function (res) {
      if (u !== ui) return;
      setBusy(false);
      if (res && res.ok) {
        var w = selected();
        var name = w ? trim(w.svc.name) : null;
        ui = null;
        u.handle.close();
        app().toast("설정을 저장했어요");
        return app().refresh().then(function () {
          var names = ((app().state.config && app().state.config.services) || []).map(function (s) { return s.name; });
          if (name && names.indexOf(name) >= 0) return app().selectService(name);
        });
      }
      var errors = (res && res.errors) || (res && res.error ? [res.error] : ["저장하지 못했어요"]);
      errors.forEach(routeError);
      showErrors("저장하지 못했어요. 표시된 곳을 고쳐 주세요");
    }).catch(function () {
      if (u !== ui) return;
      setBusy(false);
      ui.msg.textContent = "저장하지 못했어요";
    });
  }

  root.NapiSettings = { open: open };
})(window);

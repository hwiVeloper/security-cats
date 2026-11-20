/* 설정 화면 (큰 모달): 서비스·서버 추가/수정/삭제, 샘플 줄 형식 테스트, 실행될 명령 보기, 설정 파일 불러오기.
 * 전역 NapiSettings.open({add}). app.js의 NapiApp.onSettings / onAddService가 부른다.
 * 저장할 때는 get_state를 다시 불러 그 시점의 메모(notes)를 서비스 이름별로 옮겨 담는다.
 * (설정 화면을 연 뒤에 저장된 메모가 사라지지 않게. 이름을 바꾼 서비스는 원래 이름의 메모를 가져간다.)
 * 로그 형식 탭은 formats.js(NapiFormats)가 그린다. 형식 정의는 log_formats로 함께 저장한다.
 * 로그 형식은 서버가 아니라 로그 경로 줄마다 고른다 (서버의 log_paths: [{glob, log_format, custom_format}]).
 *
 * 비밀번호·키파일 암호 칸: 값은 그 입력 칸(DOM의 value 속성 아님)에만 있고 초안·설정 dict·localStorage에는
 * 넣지 않는다. 다시 그려도 값이 남도록 서버마다 같은 입력 칸 노드를 다시 쓴다 (ui.secrets).
 * save_config가 성공한 뒤에만 값이 있는 칸을 set_password / set_passphrase로 보내고, 보내는 즉시 칸을 비운다.
 * 상태("이번 실행 동안 입력됨 ✓")는 저장된 설정 기준의 secret_status로 보여 준다.
 */
(function (root) {
  "use strict";
  var R = root.NapiRender;
  var F = root.NapiFormats;
  var el = R.el;
  var doc = root.document;

  var TIME_FORMATS = [["clf", "CLF (10/Oct/2026:13:55:36 +0900)"], ["iso8601", "ISO 8601 (2026-10-10T13:55:36)"]];
  var CUSTOM_FIELDS = [
    ["method_field", "메서드 필드"], ["path_field", "경로 필드"], ["status_field", "상태 코드 필드"], ["time_field", "시간 필드"]
  ];
  var WINDOW_LABEL = { "90": "90일", "180": "180일", "365": "1년", "all": "전체" };
  var TIER_NOTICE = "한 서비스에는 같은 요청을 한 번만 기록하는 서버(같은 계층)만 넣어 주세요";
  var SECRET_HINT = "저장되지 않아요. 프로그램을 끄면 사라져요";
  var SECRET_SET = "이번 실행 동안 입력됨 ✓";
  var GLOB_HINT = "경로마다 /로 시작하는 전체 경로를 적어요. 디렉터리 부분에는 * ? [ 를 쓸 수 없어요. .gz 파일도 읽어요.";
  var FORMATS_TAB = "formats"; // ui.sel: 로그 형식 탭

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

  // 로그 경로 한 줄: { glob, log_format, custom_format }. custom_format은 형식을 바꿔도 초안에 남겨 두고
  // (다시 "직접 지정"을 고르면 되살아난다) 저장할 때 log_format이 custom인 경로만 보낸다.
  function normPath(p, fmt, custom) {
    if (p === null || typeof p !== "object") p = { glob: p };
    return {
      glob: p.glob === null || p.glob === undefined ? "" : String(p.glob),
      log_format: p.log_format || fmt || "webtob",
      custom_format: clone(p.custom_format !== undefined ? p.custom_format : custom) || null
    };
  }

  function normServer(sv) {
    sv = clone(sv) || {};
    var paths;
    if (Array.isArray(sv.log_paths) && sv.log_paths.length) {
      paths = sv.log_paths.map(function (p) { return normPath(p, sv.log_format, sv.custom_format); });
    } else if (Array.isArray(sv.log_globs) && sv.log_globs.length) {
      // 예전 모양(get_state는 log_paths만 보낸다): 경로마다 서버 형식을 물려받는다
      paths = sv.log_globs.map(function (g) { return normPath(g, sv.log_format, sv.custom_format); });
    } else {
      paths = [normPath("", sv.log_format, sv.custom_format)];
    }
    return {
      name: sv.name || "", host: sv.host || "", user: sv.user || "",
      log_paths: paths,
      port: sv.port === undefined || sv.port === null ? 22 : sv.port,
      auth: sv.auth === "key" ? "key" : "password",
      key_file: sv.key_file || null
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
    var formats = F.build(config.log_formats);
    return {
      version: config.version || 1,
      settings: clone(config.settings) || {},
      services: (config.services || []).map(function (s) { return wrap(normService(s), s.name); }),
      formats: formats,
      formatsSnap: F.snapshot(formats),
      removed: false
    };
  }

  function isDirty(w) { return w.snap === null || JSON.stringify(w.svc) !== w.snap; }
  function anyDirty() {
    return ui.draft.removed || ui.draft.services.some(isDirty) || F.snapshot(ui.draft.formats) !== ui.draft.formatsSnap ||
      ui.secrets.some(function (r) { return r.node.value !== ""; });
  }
  // 초안의 모든 서버의 모든 로그 경로 (형식 이름 바꾸기가 쓴다).
  function eachPath(fn) {
    ui.draft.services.forEach(function (w) {
      w.svc.servers.forEach(function (sv) { sv.log_paths.forEach(fn); });
    });
  }
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
  function addFmtErr(f, fieldName, msg) {
    var e = ui.fmtErrors[f.key] || (ui.fmtErrors[f.key] = {});
    (e[fieldName] || (e[fieldName] = [])).push(msg);
  }
  function hasFmtErr() { return Object.keys(ui.fmtErrors).length > 0; }
  // 경로 줄을 넣거나 빼면 줄 번호가 밀리므로 그 서버의 경로 오류("glob:N", "fmt:N", 목록 전체)를 비운다.
  function dropPathErrs(w, i) {
    var e = ui.errors[w.key];
    var b = e && e.servers[i];
    if (!b) return;
    Object.keys(b).forEach(function (k) { if (/^glob:|^fmt:|^log_paths$/.test(k)) delete b[k]; });
  }

  // 로그 경로 문구는 끝에 경로가 붙는다 (": /logs/x"). 경로 안의 글자로 칸을 잘못 고르지 않게 맨 앞만 본다.
  // 경로의 형식 문구("경로 /logs/x: 모르는 로그 형식이에요: …")는 pathField가 먼저 본다.
  function serverField(text) {
    if (/^(로그 )?경로|^모르는 로그 형식|사용자 정의 형식|custom|필드 번호/.test(text)) return "log_paths";
    if (/호스트/.test(text)) return "host";
    if (/키 ?파일/.test(text)) return "key_file";
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
    if (F.routeError(ui.draft.formats, msg, addFmtErr)) return;
    var m = /^서비스 '([\s\S]*?)' \/ 서버 '([\s\S]*?)': ([\s\S]*)$/.exec(msg);
    if (m) {
      var placed = false;
      servicesNamed(m[1]).forEach(function (w) {
        for (var i = 0; i < w.svc.servers.length; i++) {
          if (trim(w.svc.servers[i].name) === m[2]) { addErr(w, i, pathField(w.svc.servers[i], m[3]), m[3]); placed = true; return; }
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

  // 서버 문구 중 경로가 들어 있는 것을 그 경로 줄에 붙인다. 줄을 못 찾으면 경로 목록 전체 칸.
  //   "경로 /logs/x: 모르는 로그 형식이에요: a" (형식 오류) → "fmt:<번호>"
  //   "로그 경로는 ...: /logs/x" (경로 오류, 끝에 경로)    → "glob:<번호>"
  // 경로 안에 ": "가 있을 수 있어 문구를 자르지 않고 초안의 경로와 맞춰 본다 (가장 긴 경로가 이긴다).
  function pathField(sv, text) {
    var paths = sv.log_paths, best = -1, bestLen = -1, i, g;
    if (/^경로 /.test(text)) {
      for (i = 0; i < paths.length; i++) {
        g = trim(paths[i].glob);
        if (g.length > bestLen && text.indexOf("경로 " + g + ": ") === 0) { best = i; bestLen = g.length; }
      }
      return best >= 0 ? "fmt:" + best : "log_paths";
    }
    var f = serverField(text);
    if (f !== "log_paths") return f;
    for (i = 0; i < paths.length; i++) {
      g = ": " + trim(paths[i].glob);
      if (g.length > bestLen && text.length >= g.length && text.slice(text.length - g.length) === g) { best = i; bestLen = g.length; }
    }
    return best >= 0 ? "glob:" + best : f;
  }

  // napi/config.py _glob_problem(validate_config) + napi/remote/command.py split_glob과 같은 규칙.
  function globProblem(g) {
    if (!g) return "경로를 입력해 주세요. 필요 없으면 삭제해 주세요.";
    if (/[\x00-\x1f]/.test(g)) return "경로에 제어 문자(탭, 줄바꿈 등)를 쓸 수 없어요.";
    if (g.charAt(0) !== "/") return "'/'로 시작하는 전체 경로를 입력해 주세요.";
    var cut = g.lastIndexOf("/");
    var dir = g.slice(0, cut + 1); // 끝의 "/"까지 (파일 이름 바로 앞의 "//"도 본다)
    if (/[*?\[]/.test(dir)) return "디렉터리 부분에는 * ? [ 를 쓸 수 없어요.";
    if (/\/\/|\/\.\/|\/\.\.\//.test(dir)) return "경로에 // 나 . / .. 를 쓸 수 없어요.";
    if (cut === g.length - 1) return "파일 이름 패턴을 끝에 적어 주세요 (예: access.log*).";
    return null;
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
        var globs = {};
        sv.log_paths.forEach(function (p, gi) {
          var g = trim(p.glob);
          var problem = globProblem(g);
          if (!problem && globs["#" + g]) problem = "같은 경로가 이미 있어요.";
          globs["#" + g] = true;
          if (problem) add(w, i, "glob:" + gi, problem);
          if (p.log_format === "custom" && !customPayload(p.custom_format)) {
            add(w, i, "fmt:" + gi, "필드 번호는 1 이상의 숫자로 모두 입력해 주세요.");
          }
        });
      });
    });
    count += F.validate(ui.draft.formats, addFmtErr);
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

  // 경로마다 형식을 보낸다. 서버 단위 log_globs / log_format / custom_format은 보내지 않는다
  // (log_paths와 함께 오면 백엔드가 설정 오류로 본다).
  function pathPayload(p) {
    var custom = p.log_format === "custom";
    return { glob: trim(p.glob), log_format: p.log_format, custom_format: custom ? customPayload(p.custom_format) : null };
  }

  function serverPayload(sv) {
    return {
      name: trim(sv.name), host: trim(sv.host), user: trim(sv.user), log_paths: sv.log_paths.map(pathPayload),
      port: posInt(sv.port, 65535), auth: sv.auth,
      key_file: sv.auth === "key" ? (trim(sv.key_file) || null) : null
    };
  }

  function buildPayload(currentNotes, currentSettings) {
    return {
      version: ui.draft.version,
      settings: currentSettings || ui.draft.settings,
      log_formats: F.payload(ui.draft.formats),
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
      el("label", { for: id }, label), opts.addon ? el("div", { class: "with-btn" }, control, opts.addon) : control,
      opts.hint ? el("span", { class: "muted small" }, opts.hint) : null, errNode);
  }
  function cardErr(text) { return text ? el("p", { class: "card-err" }, "⚠ " + text) : null; }

  // ------------------------------------------------------------ 화면

  function open(opts) {
    opts = opts || {};
    if (ui) return;
    var st = app().state;
    ui = {
      draft: buildDraft(st.config), sel: null, errors: {}, fmtErrors: {}, top: [], busy: false, closeArmed: false,
      configError: st.error || null, configPath: st.configPath || "", root: el("div"), msg: el("p", { class: "form-msg", role: "status" }),
      handle: null, focus: null, scrollTop: 0,
      secrets: [], // [{sv, kind: "pw"|"pp", node}] 비밀값 입력 칸 (값은 node.value에만)
      secretViews: [], // 지금 그려진 서버 카드의 상태 줄 다시 그리기 함수
      status: { pw: {}, pp: {} } // 저장된 설정 기준 secret_status: 키 → {status, ref: {service, server}}
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
    refreshStatus();
  }

  function tryClose() {
    if (!ui) return true;
    if (ui.busy) return false;
    if (anyDirty() && !ui.closeArmed) {
      ui.closeArmed = true;
      ui.msg.textContent = "저장하지 않은 변경이 있어요. 한 번 더 닫으면 변경을 버려요";
      return false;
    }
    wipeSecrets(ui);
    ui = null;
    return true;
  }

  // ------------------------------------------------------------ 비밀번호 / 키파일 암호

  function tripleKey(user, host, port) { return trim(user) + "\u0000" + trim(host) + "\u0000" + String(posInt(port, 65535)); }

  function secretNode(sv, kind) {
    for (var i = 0; i < ui.secrets.length; i++) {
      if (ui.secrets[i].sv === sv && ui.secrets[i].kind === kind) return ui.secrets[i].node;
    }
    var node = el("input", { type: "password", autocomplete: "off", spellcheck: "false" });
    node.addEventListener("input", changed);
    ui.secrets.push({ sv: sv, kind: kind, node: node });
    return node;
  }

  // 지운 서버의 칸은 값을 비우고 목록에서 뺀다.
  function dropSecrets(servers) {
    ui.secrets = ui.secrets.filter(function (r) {
      if (servers.indexOf(r.sv) < 0) return true;
      r.node.value = "";
      return false;
    });
  }

  function wipeSecrets(u) {
    u.secrets.forEach(function (r) { r.node.value = ""; });
    u.secrets = [];
  }

  // 저장된 설정의 서비스마다 secret_status를 불러 (user, host, port) / key_file별 상태를 만든다.
  // 비밀번호는 (user, host, port)마다 하나라서 같은 세 값을 쓰는 서버는 상태가 같다.
  function refreshStatus() {
    var u = ui;
    var services = (app().state.config && app().state.config.services) || [];
    return Promise.all(services.map(function (s) {
      return api().secret_status(s.name).then(function (res) { return { s: s, res: res }; },
        function () { return { s: s, res: null }; });
    })).then(function (list) {
      if (u !== ui) return;
      var pw = {}, pp = {};
      list.forEach(function (item) {
        if (!item.res || isErr(item.res)) return;
        (item.s.servers || []).forEach(function (sv) {
          var st = item.res[sv.name];
          if (typeof st !== "string") return;
          var entry = { status: st, ref: { service: item.s.name, server: sv.name } };
          if (sv.auth === "key") { if (sv.key_file && !pp[sv.key_file]) pp[sv.key_file] = entry; }
          else if (!pw[tripleKey(sv.user, sv.host, sv.port)]) pw[tripleKey(sv.user, sv.host, sv.port)] = entry;
        });
      });
      ui.status = { pw: pw, pp: pp };
      redrawSecrets();
    });
  }

  // 초안 서버의 지금 값으로 찾은 상태. 저장된 설정에 없는 조합이면 null (저장한 뒤에 알 수 있다).
  function secretState(sv) {
    if (sv.auth === "key") return trim(sv.key_file) ? ui.status.pp[trim(sv.key_file)] || null : null;
    return ui.status.pw[tripleKey(sv.user, sv.host, sv.port)] || null;
  }

  // 서버 카드의 상태 줄만 다시 그린다 (입력 중인 칸의 포커스를 지키려고 화면 전체는 다시 그리지 않는다).
  // 같은 (계정, 호스트, 포트) 안내는 다른 카드의 값에도 달라지므로 모든 카드를 다시 그린다.
  function redrawSecrets() { ui.secretViews.forEach(function (draw) { draw(); }); }

  function clearSecret(ref, kindLabel) {
    if (ui.busy) return;
    var u = ui;
    api().clear_secret(ref.service, ref.server).then(function (res) {
      if (u !== ui) return;
      if (isErr(res)) { ui.msg.textContent = res.error; return; }
      return refreshStatus().then(function () { if (u === ui) ui.msg.textContent = kindLabel + "를 지웠어요"; });
    }, function () { if (u === ui) ui.msg.textContent = kindLabel + "를 지우지 못했어요"; });
  }

  // save_config가 성공한 뒤에 부른다. 값이 있는 칸만 하나씩 보내고, 보내기 직전에 칸을 비운다.
  // → Promise<실패 개수>
  function sendSecrets(u) {
    var jobs = [];
    u.secrets.forEach(function (r) {
      var sv = r.sv;
      if (r.kind === "pw" && sv.auth === "password") {
        jobs.push({ node: r.node, send: function (v) { return api().set_password(trim(sv.user), trim(sv.host), posInt(sv.port, 65535), v, null); } });
      } else if (r.kind === "pp" && sv.auth === "key" && trim(sv.key_file)) {
        jobs.push({ node: r.node, send: function (v) { return api().set_passphrase(trim(sv.key_file), v); } });
      }
    });
    var failed = 0;
    var chain = Promise.resolve();
    jobs.forEach(function (job) {
      chain = chain.then(function () {
        var v = job.node.value;
        job.node.value = "";
        if (v === "") return;
        var p = job.send(v);
        v = null;
        return p.then(function (res) { if (isErr(res)) failed++; }, function () { failed++; });
      });
    });
    return chain.then(function () {
      wipeSecrets(u); // 인증 방식이 바뀌어 보내지 않은 칸도 비운다
      return failed;
    });
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
    ui.secretViews = [];

    rootNode.appendChild(el("div", { class: "set-path" },
      "설정 파일: ", el("span", { class: "mono" }, ui.configPath || "—"), " ",
      el("button", { type: "button", class: "btn small", onclick: loadFile, title: "다른 설정 파일(YAML)을 불러와요. 이 화면에서 바꾼 내용은 버려져요" },
        "설정 파일 불러오기")));
    if (ui.configError) {
      rootNode.appendChild(el("p", { class: "notice warn-box", role: "alert" },
        "⚠ 설정 파일을 읽지 못했어요: " + ui.configError + " — 저장하면 지금 파일을 이 화면의 내용으로 새로 써요."));
    }
    var nErr = Object.keys(ui.errors).length;
    if (ui.top.length || nErr || hasFmtErr()) {
      var items = ui.top.map(function (t) { return el("li", null, t); });
      if (hasFmtErr()) items.unshift(el("li", null, "로그 형식 탭에 고칠 곳이 있어요."));
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
    var fmtOn = ui.sel === FORMATS_TAB;
    list.appendChild(el("button", { type: "button", class: "svc fmt-tab" + (fmtOn ? " on" : ""), "aria-current": fmtOn ? "true" : null,
      onclick: function () {
        if (ui.sel === FORMATS_TAB) return;
        ui.draft.services.forEach(function (x) { x.armDelete = false; });
        ui.sel = FORMATS_TAB;
        render();
      } },
      el("span", { class: "name" }, "로그 형식 (" + ui.draft.formats.length + "개)"),
      hasFmtErr() ? el("span", { class: "bad", title: "고칠 곳이 있어요" }, "⚠") : null));

    var form = el("div", { class: "set-form" });
    var w = selected();
    if (fmtOn) renderFormats(form);
    else if (w) renderService(form, w);
    else form.appendChild(el("p", { class: "empty-note" }, "서비스가 없어요. 왼쪽의 ＋ 서비스 추가를 눌러 주세요"));
    rootNode.appendChild(el("div", { class: "set-grid" }, list, form));

    if (sc) sc.scrollTop = top;
  }

  function renderFormats(form) {
    F.renderPanel(form, {
      entries: ui.draft.formats,
      services: ui.draft.services.map(function (w) { return w.svc; }),
      errorsOf: function (f) { return ui.fmtErrors[f.key]; },
      onRename: function (f, text) { F.rename(ui.draft.formats, f, text, eachPath); changed(); },
      onChange: changed,
      onAdd: function () {
        F.create(ui.draft.formats);
        changed();
        render(true);
        var cards = ui.root.querySelectorAll(".fmt-card");
        var last = cards.length ? cards[cards.length - 1].querySelector("input") : null; // 새 형식의 이름 칸
        if (last) { last.focus(); last.select(); }
      },
      onDelete: function (f) {
        var i = ui.draft.formats.indexOf(f);
        if (i < 0 || F.usedBy(f, ui.draft.services.map(function (w) { return w.svc; })).length) return;
        ui.draft.formats.splice(i, 1);
        delete ui.fmtErrors[f.key];
        changed();
        render(true);
      },
      input: input,
      field: field,
      api: api
    });
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
      dropSecrets(w.svc.servers);
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
    function set(k) { return function (v) { sv[k] = v; changed(); redrawSecrets(); }; }

    // 인증: 비밀번호 칸 또는 키파일 경로(+ 불러오기) / 키파일 암호 칸. 비밀값 칸은 서버마다 같은 노드를 다시 쓴다.
    var pwField = field("비밀번호", secretNode(sv, "pw"), null, { hidden: sv.auth !== "password", hint: SECRET_HINT });
    var keyInput = input("text", sv.key_file, function (v) { sv.key_file = v || null; changed(); redrawSecrets(); },
      { placeholder: "C:\\Users\\me\\.ssh\\id_ed25519" });
    var pickBtn = el("button", { type: "button", class: "btn small", onclick: function () { pickKeyFile(sv, keyInput); } }, "키파일 불러오기…");
    var keyField = field("키파일 경로", keyInput, errText(E, "key_file"), { hidden: sv.auth !== "key", addon: pickBtn });
    var ppField = field("키파일 암호(있으면)", secretNode(sv, "pp"), null, { hidden: sv.auth !== "key", hint: SECRET_HINT });
    var secretBox = el("div", { class: "secret-state", "aria-live": "polite" });
    function drawSecret() {
      clear(secretBox);
      var isKey = sv.auth === "key";
      var st = secretState(sv);
      if (st && st.status === "set") {
        var label = isKey ? "키파일 암호" : "비밀번호";
        secretBox.appendChild(el("span", { class: "secret-ok" }, SECRET_SET));
        secretBox.appendChild(el("button", { type: "button", class: "btn small", "aria-label": label + " 지우기",
          onclick: function () { clearSecret(st.ref, label); } }, "지우기"));
      }
      // 같은 (계정, 호스트, 포트) / 같은 키파일을 쓰는 서버는 비밀값을 함께 쓴다 → 지우면 함께 지워진다.
      var mates = w.svc.servers.filter(function (o) {
        if (o === sv || o.auth !== sv.auth) return false;
        return isKey ? !!trim(sv.key_file) && trim(o.key_file) === trim(sv.key_file)
          : tripleKey(o.user, o.host, o.port) === tripleKey(sv.user, sv.host, sv.port);
      }).map(function (o) { return trim(o.name) || "(이름 없음)"; });
      if (mates.length) {
        secretBox.appendChild(el("span", { class: "muted small" }, isKey
          ? "같은 키파일을 쓰는 서버(" + mates.join(", ") + ")와 키파일 암호를 함께 써요. 지우면 함께 지워져요"
          : "같은 계정·호스트·포트를 쓰는 서버(" + mates.join(", ") + ")와 비밀번호를 함께 써요. 지우면 함께 지워져요"));
      }
      secretBox.hidden = !secretBox.firstChild;
    }
    drawSecret();
    ui.secretViews.push(drawSecret);

    var legendName = el("span", null, sv.name || "서버 " + (i + 1));
    var nameInput = input("text", sv.name, function (v) {
      sv.name = v; changed(); legendName.textContent = v || "서버 " + (i + 1); redrawSecrets();
    });

    var card = el("fieldset", { class: "server-card" }, el("legend", null, legendName),
      cardErr(errText(E, "_")),
      el("div", { class: "fields" },
        field("이름", nameInput, errText(E, "name")),
        field("호스트", input("text", sv.host, set("host"), { placeholder: "10.0.0.11" }), errText(E, "host")),
        field("포트", input("number", sv.port, function (v) { sv.port = toNum(v); changed(); redrawSecrets(); },
          { min: "1", max: "65535", step: "1", inputmode: "numeric" }), errText(E, "port")),
        field("계정 (user)", input("text", sv.user, set("user"), { placeholder: "napi" }), errText(E, "user")),
        field("인증", select([["password", "비밀번호"], ["key", "키파일"]], sv.auth, function (v) {
          sv.auth = v; changed();
          pwField.hidden = v !== "password";
          keyField.hidden = ppField.hidden = v !== "key";
          redrawSecrets();
        })),
        pwField, keyField, ppField, secretBox,
        pathList(w, sv, i, E)),
      el("div", { class: "row-btns" },
        el("button", { type: "button", class: "btn small danger", onclick: function () {
          w.svc.servers.splice(i, 1);
          dropSecrets([sv]);
          delete ui.errors[w.key];
          changed();
          render(true);
        } }, "서버 삭제")));
    return card;
  }

  // 로그 경로 목록: 줄마다 [경로 입력 칸] [로그 형식 ▾] [삭제] (마지막 하나는 지울 수 없다).
  // "직접 지정"이면 그 줄 아래에 필드 번호 칸이 열리고, 샘플 줄 테스트도 줄마다 있다.
  // 경로 오류와 그 경로의 형식 오류는 그 줄 아래에 보인다.
  function pathList(w, sv, i, E) {
    var box = el("div", { class: "field full glob-list", role: "group" });
    var labelId = "set-f-" + (++fieldSeq);
    box.setAttribute("aria-labelledby", labelId);
    function draw(focusIdx) {
      clear(box);
      box.appendChild(el("span", { class: "lbl", id: labelId }, "로그 파일 (glob)과 로그 형식"));
      var inputs = [];
      sv.log_paths.forEach(function (p, gi) {
        var item = pathRow(w, sv, i, E, p, gi, function (next) {
          sv.log_paths.splice(gi, 1);
          dropPathErrs(w, i);
          changed();
          draw(next);
        });
        inputs.push(item.input);
        box.appendChild(item.node);
      });
      var allErr = errText(E, "log_paths");
      if (allErr) box.appendChild(el("span", { class: "field-err" }, allErr));
      box.appendChild(el("div", null, el("button", { type: "button", class: "btn small", onclick: function () {
        // 바로 위 경로의 형식(과 필드 번호)을 물려받는다
        var prev = sv.log_paths[sv.log_paths.length - 1];
        sv.log_paths.push(prev ? normPath({ glob: "", log_format: prev.log_format, custom_format: prev.custom_format })
          : normPath("", "webtob", null));
        dropPathErrs(w, i);
        changed();
        draw(sv.log_paths.length - 1);
      } }, "＋ 경로 추가")));
      box.appendChild(el("span", { class: "muted small" }, GLOB_HINT));
      if (focusIdx !== undefined && inputs[focusIdx]) inputs[focusIdx].focus();
    }
    draw();
    return box;
  }

  // 경로 한 줄. onDelete(다음에 포커스할 줄 번호). → { node, input }
  function pathRow(w, sv, i, E, p, gi, onDelete) {
    var n = gi + 1;
    var globErr = errText(E, "glob:" + gi);
    var fmtErr = errText(E, "fmt:" + gi);
    var inp = input("text", p.glob, function (v) { p.glob = v; changed(); },
      { placeholder: "/logs/webtob/access.log*", "aria-label": "로그 경로 " + n });
    var c = p.custom_format || {};
    var customBox = el("div", { class: "fields path-custom", hidden: p.log_format !== "custom" },
      CUSTOM_FIELDS.map(function (f) {
        return field(f[1], input("number", c[f[0]], function (v) {
          p.custom_format = p.custom_format || { time_format: "clf" };
          p.custom_format[f[0]] = toNum(v); changed();
        }, { min: "1", step: "1", inputmode: "numeric" }));
      }),
      field("시간 형식", select(TIME_FORMATS, c.time_format || "clf", function (v) {
        p.custom_format = p.custom_format || {};
        p.custom_format.time_format = v; changed();
      })),
      el("span", { class: "muted small", style: "grid-column:1/-1" }, "필드 번호는 줄을 공백으로 나눈 칸의 순서예요 (1부터)."),
      el("span", { class: "muted small", style: "grid-column:1/-1",
        text: "새로 설정한다면 '로그 형식' 메뉴에서 형식을 추가해 쓰는 걸 권장해요 (필드 번호 방식은 예전 설정 호환용이에요)" }));
    var fmtSel = F.select(ui.draft.formats, p.log_format, function (v) {
      p.log_format = v; changed(); customBox.hidden = v !== "custom";
    });
    fmtSel.setAttribute("aria-label", "로그 경로 " + n + " 형식");
    var errs = [];
    function invalid(control, text) {
      var errId = "set-f-" + (++fieldSeq) + "-err";
      control.setAttribute("aria-invalid", "true");
      control.setAttribute("aria-describedby", errId);
      errs.push(el("span", { class: "field-err", id: errId }, text));
    }
    if (globErr) invalid(inp, globErr);
    if (fmtErr) invalid(fmtSel, fmtErr);
    var sample = F.sampleBox(function (line) {
      var custom = null;
      if (p.log_format === "custom") {
        custom = customPayload(p.custom_format);
        if (!custom) return "직접 지정 형식의 필드 번호를 먼저 입력해 주세요";
      }
      return api().test_log_line(p.log_format, custom, line, F.payload(ui.draft.formats));
    });
    var node = el("div", { class: "path-item" },
      el("div", { class: "glob-row" }, inp, fmtSel,
        el("button", { type: "button", class: "btn small", disabled: sv.log_paths.length <= 1,
          "aria-label": "로그 경로 " + n + " 삭제", onclick: function () {
            onDelete(Math.min(gi, sv.log_paths.length - 2));
          } }, "삭제")),
      errs,
      customBox,
      el("div", { class: "path-tools" }, F.toggleButton("샘플 줄로 형식 테스트", sample)),
      sample);
    return { node: node, input: inp };
  }

  function pickKeyFile(sv, keyInput) {
    if (ui.busy) return;
    var u = ui;
    api().pick_key_file().then(function (res) {
      if (u !== ui) return;
      if (isErr(res)) { ui.msg.textContent = res.error; return; }
      if (!res || !res.ok || !res.path) return; // 고르지 않음
      sv.key_file = String(res.path);
      keyInput.value = sv.key_file;
      changed();
      redrawSecrets();
      keyInput.focus();
    }, function () { if (u === ui) ui.msg.textContent = "키파일을 고르지 못했어요"; });
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
        wipeSecrets(ui); // 버린 초안의 서버에 입력한 값
        ui.draft = buildDraft(st.config);
        ui.configError = st.error || null;
        ui.configPath = st.configPath || res.path;
        ui.errors = {};
        ui.fmtErrors = {};
        ui.top = [];
        ui.closeArmed = false;
        var first = ui.draft.services[0];
        ui.sel = first ? first.key : null;
        render();
        ui.msg.textContent = "설정 파일을 불러왔어요";
        refreshStatus();
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
    var here = ui.sel === FORMATS_TAB ? hasFmtErr() : (selected() !== null && hasErr(selected()));
    if (!here) {
      var k = firstErrorKey();
      if (k !== null) ui.sel = k;
      else if (hasFmtErr()) ui.sel = FORMATS_TAB;
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
    ui.fmtErrors = {};
    ui.top = [];
    F.settle(ui.draft.formats, eachPath);
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
      if (res && res.ok) {
        // 설정이 저장된 뒤에만 비밀값을 보낸다. 보내는 동안에도 저장 중 상태(닫기·저장 막힘)를 유지한다.
        return sendSecrets(u).then(function (failed) {
          if (u !== ui) return;
          setBusy(false);
          return afterSave(u, failed);
        });
      }
      setBusy(false);
      var errors = (res && res.errors) || (res && res.error ? [res.error] : ["저장하지 못했어요"]);
      errors.forEach(routeError);
      showErrors("저장하지 못했어요. 표시된 곳을 고쳐 주세요");
    }).catch(function () {
      if (u !== ui) return;
      setBusy(false);
      ui.msg.textContent = "저장하지 못했어요";
    });
  }

  function afterSave(u, failed) {
    var w = selected();
    var name = w ? trim(w.svc.name) : null;
    ui = null;
    u.handle.close();
    app().toast(failed ? "설정을 저장했어요. 비밀번호·키파일 암호 " + failed + "개를 전달하지 못했어요" : "설정을 저장했어요");
    return app().refresh().then(function () {
      var names = ((app().state.config && app().state.config.services) || []).map(function (s) { return s.name; });
      if (name && names.indexOf(name) >= 0) return app().selectService(name);
    });
  }

  root.NapiSettings = { open: open };
})(window);

/* 설정 화면의 "로그 형식" 탭: 기본 제공 형식(읽기 전용)과 사용자 형식(이름 + LogFormat 패턴). 전역 NapiFormats.
 * settings.js가 초안(draft)과 입력 칸 도우미(input, field)를 넘겨서 부른다. 이 파일은 따로 상태를 갖지 않는다.
 *
 * 초안의 형식 항목: { key, name, pattern, cur }
 *   name: 입력 칸 그대로의 이름 (편집 중이라 비었거나 겹칠 수 있다)
 *   cur:  초안의 서버들이 log_format으로 가리키는 이름. 이름을 고치면 겹치지 않는 올바른 이름일 때만
 *         cur와 그 형식을 쓰는 서버의 log_format을 함께 바꾼다 (편집 도중에 다른 형식과 합쳐지지 않게).
 */
(function (root) {
  "use strict";
  var R = root.NapiRender;
  var el = R.el;

  // napi/logformat.py BUILTIN_FORMATS / PRESETS와 같다 (수정 불가).
  var BUILTIN = [
    { name: "webtob", label: "WebtoB", pattern: '%h %l %u %t "%r" %s %b' },
    { name: "webtob_combined", label: "WebtoB (combined)", pattern: '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"' },
    { name: "apache_common", label: "Apache (common)", pattern: '%h %l %u %t "%r" %>s %b' },
    { name: "apache_combined", label: "Apache (combined)", pattern: '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"' },
    { name: "nginx_combined", label: "nginx (combined)", pattern: '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"' }
  ];
  var CUSTOM = { name: "custom", label: "직접 지정 (필드 번호)" };
  var HELP = '%h %l %u %t "%r" %s %b 처럼 WebtoB/Apache LogFormat 문법을 그대로 적어요. %r(또는 %m+%U)과 날짜가 꼭 있어야 해요';
  var PATTERN_PLACEHOLDER = '%h %l %u %t "%r" %s %b';
  var SAMPLE_PLACEHOLDER = '10.1.2.3 - - [18/Nov/2026:10:00:00 +0900] "GET /v1/payments/123?x=1 HTTP/1.1" 200 512';
  var SAMPLE_NOTICE = "서버에서 로그를 가져오지 않아요. 직접 한 줄 붙여 넣어 주세요";

  var seq = 0;

  function trim(v) { return v === null || v === undefined ? "" : String(v).trim(); }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function isReserved(name) {
    if (name === CUSTOM.name) return true;
    for (var i = 0; i < BUILTIN.length; i++) if (BUILTIN[i].name === name) return true;
    return false;
  }

  // ------------------------------------------------------------ 초안

  function build(list) {
    return (list || []).map(function (f) {
      var name = (f && typeof f.name === "string") ? f.name : "";
      return { key: ++seq, name: name, pattern: (f && typeof f.pattern === "string") ? f.pattern : "", cur: name };
    });
  }

  function payload(entries) {
    return entries.map(function (f) { return { name: trim(f.name), pattern: trim(f.pattern) }; });
  }

  function snapshot(entries) { return JSON.stringify(payload(entries)); }

  function create(entries) {
    var taken = {};
    entries.forEach(function (f) { taken["#" + trim(f.name)] = true; taken["#" + f.cur] = true; });
    var name = "my_format", n = 2;
    while (taken["#" + name]) name = "my_format_" + n++;
    var f = { key: ++seq, name: name, pattern: "", cur: name };
    entries.push(f);
    return f;
  }

  // 이름 칸이 바뀌었을 때. eachServer(fn)은 초안의 모든 서버에 fn(sv)를 부른다.
  function rename(entries, f, text, eachServer) {
    f.name = text;
    var nn = trim(text);
    if (!nn || nn === f.cur || isReserved(nn)) return;
    for (var i = 0; i < entries.length; i++) {
      var o = entries[i];
      if (o !== f && (trim(o.name) === nn || o.cur === nn)) return; // 겹치면 서버는 그대로 둔다
    }
    var old = f.cur;
    if (old && !isReserved(old)) eachServer(function (sv) { if (sv.log_format === old) sv.log_format = nn; });
    f.cur = nn;
  }

  // 저장 직전: 편집 중에 겹쳐서 미뤄 둔 이름 바꾸기를 마저 반영한다.
  function settle(entries, eachServer) {
    entries.forEach(function (f) { rename(entries, f, f.name, eachServer); });
  }

  // 이 형식을 쓰는 서버의 표시 이름 목록. services: [{name, servers: [...]}]
  function usedBy(f, services) {
    if (!f.cur || isReserved(f.cur)) return [];
    var out = [];
    services.forEach(function (s) {
      (s.servers || []).forEach(function (sv) {
        if (sv.log_format === f.cur) out.push((trim(sv.name) || "(이름 없음)") + " (" + (trim(s.name) || "(이름 없음)") + ")");
      });
    });
    return out;
  }

  // 백엔드 검사(napi/config.py _validate_log_formats) 전에 미리 보는 것: 빈 칸, 이름 겹침. 패턴 문법은 백엔드가 본다.
  function validate(entries, add) {
    var count = 0, seen = {};
    entries.forEach(function (f) {
      var n = trim(f.name);
      if (!n) { add(f, "name", "형식 이름을 입력해 주세요."); count++; }
      else if (isReserved(n)) { add(f, "name", "기본 제공 형식과 같은 이름은 쓸 수 없어요."); count++; }
      else if (seen["#" + n]) { add(f, "name", "같은 이름의 형식이 이미 있어요."); count++; }
      seen["#" + n] = true;
      if (!trim(f.pattern)) { add(f, "pattern", "패턴을 입력해 주세요."); count++; }
    });
    return count;
  }

  // validate_config 문구 "로그 형식 'X': ..." / "로그 형식 이름이 비어 있습니다."를 해당 형식에 붙인다.
  function routeError(entries, msg, add) {
    var m = /^로그 형식 '([\s\S]*?)': ([\s\S]*)$/.exec(msg);
    var hits = [];
    if (m) {
      hits = entries.filter(function (f) { return trim(f.name) === m[1]; });
      var fieldName = /이름/.test(m[2]) ? "name" : "pattern";
      hits.forEach(function (f) { add(f, fieldName, m[2]); });
    } else if (/^로그 형식 이름이 비어/.test(msg)) {
      hits = entries.filter(function (f) { return !trim(f.name); });
      hits.forEach(function (f) { add(f, "name", msg); });
    }
    return hits.length > 0;
  }

  // ------------------------------------------------------------ 서버 카드의 형식 드롭다운

  function option(value, label, current) {
    var o = el("option", { value: value }, label);
    if (value === current) o.selected = true;
    return o;
  }

  function select(entries, value, onChange) {
    var s = el("select");
    var known = { custom: true };
    BUILTIN.forEach(function (b) { known[b.name] = true; });
    var users = entries.filter(function (f) { return f.cur && !isReserved(f.cur); });
    users.forEach(function (f) { known[f.cur] = true; });
    if (value && !known[value]) s.appendChild(option(value, value + " (없는 형식)", value));
    s.appendChild(el("optgroup", { label: "기본 제공" }, BUILTIN.map(function (b) { return option(b.name, b.label, value); })));
    if (users.length) {
      s.appendChild(el("optgroup", { label: "내 형식" }, users.map(function (f) { return option(f.cur, f.cur, value); })));
    }
    s.appendChild(option(CUSTOM.name, CUSTOM.label, value));
    s.addEventListener("change", function () { onChange(s.value); });
    return s;
  }

  // ------------------------------------------------------------ 샘플 줄 결과 (서버 카드와 형식 탭이 같이 쓴다)

  function sampleResult(res, extraLines) {
    if (!res || res.ok !== true) {
      return [el("p", { class: "sample-bad" }, "✕ " + ((res && res.error) || "이 형식으로 읽을 수 없는 줄이에요"))];
    }
    var status = res.status === null || res.status === undefined ? "상태 미상" : String(res.status);
    return [
      el("dl", { class: "sample-out" },
        el("dt", null, "메서드"), el("dd", null, res.method),
        el("dt", null, "경로 (원문)"), el("dd", null, res.path),
        el("dt", null, "정규화 경로"), el("dd", null, res.normalized),
        el("dt", null, "상태 코드"), el("dd", null, status),
        el("dt", null, "날짜"), el("dd", null, R.fmtDate(res.date))),
      extraLines ? el("p", { class: "muted small" }, "첫 줄만 테스트했어요") : null
    ];
  }

  // run(line) → Promise<res>. 패턴/형식 확인은 run 쪽이 미리 한다 (null을 돌려주면 그 문구를 보인다).
  function sampleBox(run) {
    var ta = el("textarea", { rows: "2", spellcheck: "false", "aria-label": "샘플 로그 한 줄", placeholder: SAMPLE_PLACEHOLDER });
    var out = el("div", { "aria-live": "polite" });
    function runTest() {
      clear(out);
      var lines = ta.value.split(/\r?\n/).filter(function (l) { return trim(l); });
      if (!lines.length) { out.appendChild(el("p", { class: "sample-bad" }, "로그 한 줄을 붙여 넣어 주세요")); return; }
      var p = run(lines[0]);
      if (typeof p === "string") { out.appendChild(el("p", { class: "sample-bad" }, p)); return; }
      p.then(function (res) {
        clear(out);
        sampleResult(res, lines.length > 1).forEach(function (n) { if (n) out.appendChild(n); });
      }, function () {
        clear(out);
        out.appendChild(el("p", { class: "sample-bad" }, "테스트하지 못했어요"));
      });
    }
    ta.addEventListener("paste", function () { setTimeout(runTest, 0); });
    return el("div", { class: "sample", hidden: true },
      el("p", { class: "notice" }, SAMPLE_NOTICE),
      ta,
      el("div", { class: "row-btns" }, el("button", { type: "button", class: "btn small", onclick: runTest }, "테스트")),
      out);
  }

  function toggleButton(label, box) {
    return el("button", { type: "button", class: "btn small", "aria-expanded": "false", onclick: function (e) {
      box.hidden = !box.hidden;
      e.currentTarget.setAttribute("aria-expanded", box.hidden ? "false" : "true");
      if (!box.hidden) box.querySelector("textarea").focus();
    } }, label);
  }

  // ------------------------------------------------------------ 형식 탭

  // ctx: { entries, services, errorsOf(f) → {name:[..], pattern:[..]}, onRename(f, text), onChange(),
  //        onAdd(), onDelete(f), input, field, api }
  function renderPanel(form, ctx) {
    var rows = BUILTIN.map(function (b) {
      return el("tr", null, el("td", { class: "mono" }, b.name), el("td", null, b.label), el("td", { class: "mono" }, b.pattern));
    });
    form.appendChild(el("fieldset", null, el("legend", null, "기본 제공 형식"),
      el("table", { class: "fmt-builtin" },
        el("thead", null, el("tr", null, el("th", null, "이름"), el("th", null, "표시"), el("th", null, "패턴"))),
        el("tbody", null, rows)),
      el("p", { class: "muted small", style: "margin:6px 0 0" },
        "직접 지정 (필드 번호): 줄을 공백으로 나눈 칸의 순서로 메서드·경로·상태·시간을 고르는 예전 방식이에요. 서버 카드에서 골라요.")));

    var box = el("fieldset", null, el("legend", null, "내 형식 (" + ctx.entries.length + "개)"),
      el("p", { class: "notice" }, HELP));
    if (!ctx.entries.length) box.appendChild(el("p", { class: "empty-note" }, "아직 만든 형식이 없어요. ＋ 형식 추가를 눌러 주세요"));
    ctx.entries.forEach(function (f) { box.appendChild(renderEntry(f, ctx)); });
    box.appendChild(el("div", { class: "row-btns" },
      el("button", { type: "button", class: "btn", onclick: ctx.onAdd }, "＋ 형식 추가")));
    form.appendChild(box);
  }

  function errText(bucket, name) { return bucket && bucket[name] ? bucket[name].join(" ") : null; }

  function renderEntry(f, ctx) {
    var E = ctx.errorsOf(f) || {};
    var used = usedBy(f, ctx.services);
    var legendName = el("span", null, trim(f.name) || "(이름 없음)");
    var nameInput = ctx.input("text", f.name, function (v) {
      ctx.onRename(f, v);
      legendName.textContent = trim(v) || "(이름 없음)";
    }, { placeholder: "my_webtob" });
    var patternInput = ctx.input("text", f.pattern, function (v) { f.pattern = v; ctx.onChange(); },
      { placeholder: PATTERN_PLACEHOLDER, class: "mono" });
    var usedText = used.length ? "사용 중인 서버: " + used.join(", ") : null;
    var del = el("button", { type: "button", class: "btn small danger", disabled: used.length > 0, title: usedText,
      onclick: function () { ctx.onDelete(f); } }, "형식 삭제");
    var sample = sampleBox(function (line) {
      var pattern = trim(f.pattern);
      if (!pattern) return "패턴을 먼저 입력해 주세요";
      return ctx.api().test_format(pattern, line);
    });
    return el("fieldset", { class: "server-card fmt-card" }, el("legend", null, legendName),
      el("div", { class: "fields" },
        ctx.field("이름", nameInput, errText(E, "name")),
        ctx.field("패턴 (LogFormat)", patternInput, errText(E, "pattern"), { full: true })),
      el("div", { class: "row-btns" },
        toggleButton("샘플 줄로 테스트", sample),
        del,
        usedText ? el("span", { class: "muted small fmt-used" }, usedText) : null),
      sample);
  }

  root.NapiFormats = {
    BUILTIN: BUILTIN,
    CUSTOM: CUSTOM,
    isReserved: isReserved,
    build: build,
    payload: payload,
    snapshot: snapshot,
    create: create,
    rename: rename,
    settle: settle,
    usedBy: usedBy,
    validate: validate,
    routeError: routeError,
    select: select,
    sampleBox: sampleBox,
    toggleButton: toggleButton,
    renderPanel: renderPanel
  };
})(window);

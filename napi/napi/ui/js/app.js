/* 메인 화면: 사이드바(서비스 목록), 냅냥 요약, 상태 탭, 표, 상세·메모 패널. 전역 NapiApp.
 * window.pywebview.api.* 만 쓴다 (?mock=1이면 mock-api.js가 같은 이름으로 대신한다).
 * onScan / onExport / onAddService / onSettings는 Task 13이 바꿔 끼우는 자리다.
 */
(function (root) {
  "use strict";
  var R = root.NapiRender;
  var el = R.el;
  var doc = root.document;
  var WINDOWS = [["90", "90일"], ["180", "180일"], ["365", "1년"], ["all", "전체"]];

  var state = {
    config: null,
    configPath: "",
    results: {},
    error: null,
    current: null,
    result: null,
    window: "365",
    view: { status: "all", query: "", method: "", tag: "", notedOnly: false, sort: null, fresh: true },
    detailKey: null
  };
  var api = null;
  var toastTimer = null;
  var tableBox = null;
  var rowCount = null;

  function $(id) { return doc.getElementById(id); }

  function toast(text) {
    var t = $("toast");
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 2600);
  }

  function isError(res) { return res && typeof res === "object" && res.ok === false && "error" in res; }

  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function svgNode(markup) {
    var t = doc.createElement("template");
    t.innerHTML = markup; // NapiCat 고정 SVG
    return t.content.firstChild;
  }

  function services() { return (state.config && state.config.services) || []; }

  // ------------------------------------------------------------ load

  function loadState() {
    return api.get_state().then(function (st) {
      if (isError(st)) {
        state.error = st.error;
        state.config = { services: [], settings: {} };
        state.results = {};
      } else {
        state.config = st.config || { services: [], settings: {} };
        state.configPath = st.config_path || "";
        state.results = st.results || {};
        state.error = st.error || null;
      }
      var settings = state.config.settings || {};
      if (!state.windowTouched && settings.default_window) state.window = String(settings.default_window);
      var names = services().map(function (s) { return s.name; });
      if (names.indexOf(state.current) < 0) state.current = names.length ? names[0] : null;
      renderSidebar();
    });
  }

  function loadResult() {
    if (!state.current) { state.result = null; return Promise.resolve(); }
    var name = state.current;
    return api.get_result(name).then(function (r) {
      if (name !== state.current) return;
      if (isError(r)) { state.result = null; toast(r.error); return; }
      state.result = r || null;
    });
  }

  function refresh() {
    return loadState().then(loadResult).then(function () { renderMain(); renderDetailFor(state.detailKey); });
  }

  function selectService(name) {
    if (name === state.current) return Promise.resolve();
    state.current = name;
    state.view = { status: "all", query: "", method: "", tag: "", notedOnly: false, sort: null, fresh: true };
    closeDetail();
    renderSidebar();
    return loadResult().then(renderMain);
  }

  // ------------------------------------------------------------ sidebar

  function renderSidebar() {
    var logo = $("logo");
    if (!logo.querySelector("svg")) logo.insertBefore(svgNode(root.NapiCat.face("asleep")), logo.firstChild);
    var list = $("svc-list");
    clear(list);
    services().forEach(function (s) {
      var r = state.results[s.name];
      var badge = r ? el("span", { class: "zz", title: "잠든 API " + r.sleeping + "개" }, R.fmtNum(r.sleeping))
        : el("small", null, "미스캔");
      var on = s.name === state.current;
      list.appendChild(el("button", {
        type: "button", class: "svc" + (on ? " on" : ""), "aria-current": on ? "true" : null, title: s.name,
        onclick: function () { selectService(s.name); }
      }, el("span", { class: "name" }, s.name), badge));
    });
    $("cfg-path").textContent = state.configPath || "—";
  }

  // ------------------------------------------------------------ main

  function windowSelect() {
    var sel = el("select", { class: "sel", "aria-label": "분석 기간", title: "분석 기간",
      onchange: function (e) { state.window = e.target.value; state.windowTouched = true; } });
    WINDOWS.forEach(function (w) {
      var o = el("option", { value: w[0] }, w[1]);
      if (w[0] === state.window) o.selected = true;
      sel.appendChild(o);
    });
    return sel;
  }

  function actionButtons(withExport) {
    var name = state.current;
    return el("div", { class: "btns" },
      windowSelect(),
      el("button", { type: "button", class: "btn pri", onclick: function () { NapiApp.onScan(name, state.window); } }, "▶ 스캔"),
      withExport ? el("button", { type: "button", class: "btn", onclick: function () { NapiApp.onExport(name, "html"); } }, "HTML") : null,
      withExport ? el("button", { type: "button", class: "btn", onclick: function () { NapiApp.onExport(name, "pdf"); } }, "PDF") : null);
  }

  function renderMain() {
    var content = $("content");
    var keepScroll = content.scrollTop;
    clear(content);
    tableBox = null;
    rowCount = null;
    if (state.error) content.appendChild(el("div", { class: "banner", role: "alert" }, "⚠ 설정 파일을 읽지 못했어요: " + state.error));

    if (!state.current) {
      content.appendChild(el("div", { class: "empty-state" },
        svgNode(root.NapiCat.full({ animated: true })),
        el("h2", null, "아직 등록된 서비스가 없어요"),
        el("p", null, "왼쪽의 ＋ 서비스 추가를 눌러 주세요")));
      return;
    }
    var r = state.result;
    if (!r) {
      content.appendChild(el("div", { class: "empty-state" },
        svgNode(root.NapiCat.full({ animated: true })),
        el("h2", null, state.current),
        el("p", null, "아직 스캔하지 않았어요. ▶ 스캔을 눌러 주세요"),
        actionButtons(false)));
      return;
    }

    var hero = el("section");
    content.appendChild(hero);
    var parts = R.renderSummary(hero, r, { print: false });
    parts.actions.appendChild(actionButtons(true));

    var v = state.view;
    if (v.fresh) { v.status = R.defaultTab(r.counts); delete v.fresh; }
    var tabs = el("div");
    content.appendChild(tabs);
    function onTab(k) { v.status = k; R.renderTabs(tabs, r.counts, v.status, onTab); drawTable(); }
    R.renderTabs(tabs, r.counts, v.status, onTab);

    var main = el("div", { class: "main" });
    main.appendChild(filterBar(r));
    tableBox = el("div");
    main.appendChild(tableBox);
    content.appendChild(main);
    drawTable();
    content.scrollTop = keepScroll;
  }

  function uniq(list) {
    var seen = {};
    return list.filter(function (x) { if (seen[x]) return false; seen[x] = true; return true; }).sort();
  }

  function filterBar(r) {
    var v = state.view;
    var apis = r.apis || [];
    var search = el("input", { class: "inp", type: "search", placeholder: "🔍 경로·summary·태그 검색", "aria-label": "검색" });
    search.value = v.query;
    search.addEventListener("input", function () { v.query = search.value; drawTable(); });

    function select(label, values, key) {
      var s = el("select", { class: "sel", "aria-label": label,
        onchange: function () { v[key] = s.value; drawTable(); } });
      s.appendChild(el("option", { value: "" }, label + " ▾"));
      values.forEach(function (x) {
        var o = el("option", { value: x }, x);
        if (x === v[key]) o.selected = true;
        s.appendChild(o);
      });
      return s;
    }
    var methods = uniq(apis.map(function (a) { return a.method; }));
    var tags = uniq([].concat.apply([], apis.map(function (a) { return a.tags || []; })));
    if (methods.indexOf(v.method) < 0) v.method = "";
    if (tags.indexOf(v.tag) < 0) v.tag = "";

    var noted = el("button", { type: "button", class: "btn" + (v.notedOnly ? " on" : ""), "aria-pressed": v.notedOnly ? "true" : "false" }, "메모 있는 것만");
    noted.addEventListener("click", function () {
      v.notedOnly = !v.notedOnly;
      noted.classList.toggle("on", v.notedOnly);
      noted.setAttribute("aria-pressed", v.notedOnly ? "true" : "false");
      drawTable();
    });
    rowCount = el("span", { class: "rowcount", "aria-live": "polite" });
    return el("div", { class: "filter" }, search, select("메서드", methods, "method"), select("태그", tags, "tag"), noted, rowCount);
  }

  function drawTable() {
    if (!tableBox || !state.result) return;
    var v = state.view;
    var n = R.renderTable(tableBox, state.result.apis || [], {
      status: v.status, query: v.query, method: v.method, tag: v.tag, notedOnly: v.notedOnly, sort: v.sort,
      probeRest: state.result.probe_rest,
      onSort: function (s) { v.sort = s; markSelected(); }
    }, function (a) { openDetail(R.opKey(a)); });
    if (rowCount) rowCount.textContent = R.fmtNum(n) + "개";
    markSelected();
  }

  function markSelected() {
    if (!tableBox) return;
    var rows = tableBox.querySelectorAll("tr.row");
    for (var i = 0; i < rows.length; i++) rows[i].classList.toggle("sel", rows[i].getAttribute("data-key") === state.detailKey);
  }

  // ------------------------------------------------------------ detail

  function findApi(key) {
    var apis = (state.result && state.result.apis) || [];
    for (var i = 0; i < apis.length; i++) if (R.opKey(apis[i]) === key) return apis[i];
    return null;
  }

  function openDetail(key) {
    state.detailKey = key;
    renderDetailFor(key);
    markSelected();
  }

  function renderDetailFor(key) {
    var a = key ? findApi(key) : null;
    if (!a) { closeDetail(); return; }
    var panel = $("detail");
    var service = state.current;
    R.renderDetail(panel, a,
      function (reason, author) {
        return api.save_note(service, key, reason, author).then(function (res) {
          if (isError(res)) return res;
          toast("메모를 저장했어요");
          return refresh().then(function () { return res; });
        });
      },
      function () {
        return api.delete_note(service, key).then(function (res) {
          if (isError(res)) return res;
          toast("메모를 지웠어요");
          return refresh().then(function () { return res; });
        });
      });
    var close = panel.querySelector(".detail-close");
    if (close) close.addEventListener("click", closeDetail);
    panel.classList.add("open");
    panel.setAttribute("aria-hidden", "false");
    $("backdrop").classList.add("open");
  }

  function closeDetail() {
    state.detailKey = null;
    var panel = $("detail");
    panel.classList.remove("open");
    panel.setAttribute("aria-hidden", "true");
    $("backdrop").classList.remove("open");
    markSelected();
  }

  // ------------------------------------------------------------ hooks (Task 13이 바꾼다)

  var NapiApp = {
    state: state,
    refresh: refresh,
    toast: toast,
    selectService: selectService,
    onScan: function (service, window) { toast("스캔 화면은 곧 연결돼요"); },
    onExport: function (service, kind) {
      return api.export(service, kind).then(function (res) {
        if (isError(res)) toast(res.error);
        else if (res && res.ok) toast("저장했어요: " + res.path);
      });
    },
    onAddService: function () { toast("설정 화면은 곧 연결돼요"); },
    onSettings: function () { toast("설정 화면은 곧 연결돼요"); }
  };
  root.NapiApp = NapiApp;

  // ------------------------------------------------------------ start

  var started = false;
  function start() {
    if (started) return;
    started = true;
    api = root.pywebview.api;
    $("add-service").addEventListener("click", function () { NapiApp.onAddService(); });
    $("open-settings").addEventListener("click", function () { NapiApp.onSettings(); });
    $("backdrop").addEventListener("click", closeDetail);
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape" && state.detailKey) closeDetail(); });
    refresh().catch(function () { toast("화면을 불러오지 못했어요"); });
  }

  root.addEventListener("pywebviewready", start);
  if (root.pywebview && root.pywebview.api && typeof root.pywebview.api.get_state === "function" && !root.pywebview.napiMock) start();
})(window);

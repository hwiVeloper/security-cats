/* 순수 렌더 함수 (메인 화면과 HTML/PDF 리포트가 같이 쓴다). 전역 NapiRender.
 * pywebview 없이도 동작해야 한다 (Task 10이 이 파일을 리포트에 인라인한다).
 * 로그·명세에서 온 문자열은 모두 textContent/createElement로 넣는다 (innerHTML 금지).
 * innerHTML은 NapiCat이 만든 고정 SVG와 숫자만 든 도넛 SVG에만 쓴다.
 */
(function (root) {
  "use strict";

  var STATUS_META = {
    awake: { label: "깨어있음", color: "#22a06b", desc: "최근에도 호출되고 있어요" },
    drowsy: { label: "꾸벅꾸벅", color: "#d9a400", desc: "기간 안에 호출됐지만 요즘은 뜸해요" },
    asleep: { label: "잠듦", color: "#7b6cf6", desc: "분석 기간 동안 한 번도 호출되지 않았어요" },
    rip: { label: "영면", color: "#8b8f98", desc: "deprecated이고 호출도 없어요. 지워도 될 것 같아요" },
    sleeptalk: { label: "잠꼬대", color: "#e8590c", desc: "deprecated인데 아직 호출되고 있어요" },
    ghost: { label: "유령", color: "#0ea5b7", desc: "명세에 없는데 호출되고 있어요" },
    probe: { label: "외부 탐색 시도", color: "#8a7a6e", desc: "명세에 없는 경로로 404만 났어요. 외부에서 찔러 본 요청으로 보여요" }
  };
  /* 상태 설명 (도움말 대화상자, 탭 아래 한 줄, 리포트 표지가 같이 쓴다).
   * criteria(n): n = 최근 기준 일수 (awake_days). 모르면 null → "최근 기준 기간(기본 30일)". */
  var RECENT_UNKNOWN = "최근 기준 기간(기본 30일)";
  var HELP = {
    awake: { mean: "지금도 쓰이는 API", action: "유지",
      criteria: function (n) { return (n ? "최근 " + n + "일" : RECENT_UNKNOWN) + " 안에 호출 기록이 있어요"; } },
    drowsy: { mean: "쓰이긴 했지만 요즘 뜸한 API", action: "사용처 확인",
      criteria: function (n) { return "분석 기간 안에 호출은 있지만, 마지막 호출이 " + (n ? n + "일" : RECENT_UNKNOWN) + "보다 오래됐어요"; } },
    asleep: { mean: "아무도 부르지 않는 API", action: "삭제 검토 (연 1회 배치처럼 드물게 쓰이면 메모로 유지 사유를 남겨 주세요)",
      criteria: function () { return "분석 기간 동안 호출이 한 번도 없어요"; } },
    rip: { mean: "이미 은퇴한 API", action: "바로 삭제 후보",
      criteria: function () { return "명세에 deprecated 표시가 있고, 호출도 없어요"; } },
    sleeptalk: { mean: "은퇴했는데 아직 불리는 API", action: "호출하는 쪽을 새 API로 옮긴 뒤 삭제",
      criteria: function () { return "명세에 deprecated 표시가 있는데, 아직 호출되고 있어요"; } },
    ghost: { mean: "명세에 없는 API", action: "명세에 추가하거나 차단 검토",
      criteria: function () { return "명세에 없는 경로인데 로그에 호출 기록이 있어요 (4xx 외 응답 포함)"; } },
    probe: { mean: "밖에서 찔러 본 요청", action: "판정 대상 아님 (참고용)",
      criteria: function () { return "명세에 없는 경로로 4xx(주로 404)만 받았어요. 스캐너나 봇으로 보여요"; } }
  };
  Object.keys(HELP).forEach(function (k) {
    STATUS_META[k].mean = HELP[k].mean;
    STATUS_META[k].criteria = HELP[k].criteria;
    STATUS_META[k].action = HELP[k].action;
  });
  var HELP_NOTES = [
    "분석 기간: 스캔할 때 고른 기간(90일·180일·1년·전체)과 서버에 남아 있는 로그 중 짧은 쪽이에요.",
    "응답 분포의 '상태 미상'은 상태코드가 없는 로그 형식(예: webtob_error)에서 온 호출이에요."
  ];
  var ALL_LINE = "모든 상태를 함께 보여 줘요. 상태가 헷갈리면 '? 상태 설명'을 눌러 보세요";
  var HELP_LABEL = "? 상태 설명";
  var DONUT_ORDER = ["awake", "drowsy", "asleep", "rip", "sleeptalk", "ghost"];
  var HELP_ORDER = DONUT_ORDER.concat(["probe"]);
  var STATUS_ORDER = ["asleep", "rip", "sleeptalk", "ghost", "drowsy", "awake", "probe"];
  var TAB_ORDER = ["all"].concat(STATUS_ORDER);
  // "???" = 상태 코드가 없는 형식으로 읽은 호출 (classifier가 상태 미상으로 집계한다)
  var BUCKETS = [["2xx", "#22a06b"], ["3xx", "#8b8f98"], ["4xx", "#f29a3f"], ["5xx", "#dc2626"], ["???", "#cfc6bc"]];
  var BUCKET_LABEL = { "???": "상태 미상" };
  var METHOD_CLASS = { GET: "get", POST: "post", PUT: "put", PATCH: "put", DELETE: "del" };
  var COLUMNS = [
    { key: "method", label: "메서드" },
    { key: "path", label: "경로" },
    { key: "status", label: "상태" },
    { key: "calls", label: "호출 수", num: true },
    { key: "last", label: "마지막 호출" },
    { key: "dist", label: "응답 분포", sortable: false },
    { key: "tags", label: "태그" },
    { key: "memo", label: "메모 (유지 사유)" }
  ];
  var AUTHOR_KEY = "napi.author";

  // ------------------------------------------------------------ helpers

  function el(tag, attrs) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = String(v);
        else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v === true ? "" : String(v));
      });
    }
    for (var i = 2; i < arguments.length; i++) append(n, arguments[i]);
    return n;
  }

  function append(parent, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) { child.forEach(function (c) { append(parent, c); }); return; }
    parent.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
  }

  /* 고정 SVG 문자열(NapiCat, renderDonut)을 노드로 바꾼다. 데이터 문자열에는 쓰지 않는다. */
  function svgNode(markup) {
    var t = document.createElement("template");
    t.innerHTML = markup;
    return t.content.firstChild;
  }

  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function fmtDate(ymd) {
    var s = ymd === null || ymd === undefined ? "" : String(ymd);
    return /^\d{8}$/.test(s) ? s.slice(0, 4) + "-" + s.slice(4, 6) + "-" + s.slice(6, 8) : "—";
  }

  function fmtDateTime(iso) {
    var s = iso ? String(iso) : "";
    var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(s);
    if (m) return m[1] + " " + m[2];
    return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s : "—";
  }

  function fmtNum(n) {
    var v = Number(n);
    return isFinite(v) ? v.toLocaleString("ko-KR") : "—";
  }

  function opKey(api) { return api.method + " " + api.path; }

  function face(status, cls) {
    var n = svgNode(root.NapiCat.face(status));
    if (cls) n.classList.add(cls);
    return n;
  }

  function metaOf(status) { return STATUS_META[status] || STATUS_META.asleep; }

  function countOf(counts, key) {
    counts = counts || {};
    if (key === "all") {
      return DONUT_ORDER.reduce(function (s, k) { return s + (Number(counts[k]) || 0); }, 0);
    }
    return Number(counts[key]) || 0;
  }

  function defaultTab(counts) { return countOf(counts, "asleep") > 0 ? "asleep" : "all"; }

  function lastAuthor() {
    try { return root.localStorage.getItem(AUTHOR_KEY) || ""; } catch (e) { return ""; }
  }

  function rememberAuthor(name) {
    try { root.localStorage.setItem(AUTHOR_KEY, name); } catch (e) { /* 저장소를 못 쓰면 무시 */ }
  }

  function statusPill(status) {
    var m = metaOf(status);
    return el("span", { class: "st s-" + (STATUS_META[status] ? status : "asleep") }, face(status, "st-face"), m.label);
  }

  function methodTag(method) {
    var m = String(method || "");
    return el("span", { class: "m " + (METHOD_CLASS[m.toUpperCase()] || "other") }, m);
  }

  function distText(byStatus) {
    byStatus = byStatus || {};
    var keys = Object.keys(byStatus).sort();
    if (!keys.length) return "—";
    return keys.map(function (k) { return (BUCKET_LABEL[k] || k) + " " + fmtNum(byStatus[k]); }).join(" · ");
  }

  function distBar(byStatus) {
    byStatus = byStatus || {};
    var total = 0;
    Object.keys(byStatus).forEach(function (k) { total += Number(byStatus[k]) || 0; });
    var bar = el("span", { class: "dist", title: distText(byStatus) });
    if (!total) { bar.classList.add("none"); return bar; }
    BUCKETS.forEach(function (b) {
      var c = Number(byStatus[b[0]]) || 0;
      if (!c) return;
      var seg = el("i");
      seg.style.width = (c / total * 100).toFixed(2) + "%";
      seg.style.background = b[1];
      bar.appendChild(seg);
    });
    return bar;
  }

  /* 경로를 "/" 뒤에서만 줄바꿈되도록 텍스트 노드와 <wbr>로 나눈다. */
  function pathParts(path) {
    var out = [];
    String(path || "").split("/").forEach(function (seg, i) {
      if (i > 0) { out.push("/"); }
      if (seg) out.push(seg);
      if (i > 0) out.push(el("wbr"));
    });
    return out;
  }

  function noteText(note) {
    if (!note) return "";
    return "📌 " + note.reason + (note.author ? " — " + note.author : "");
  }

  // ------------------------------------------------------------ filter/sort

  function filterApis(apis, f) {
    f = f || {};
    var status = f.status || "all";
    var q = String(f.query || "").trim().toLowerCase();
    return (apis || []).filter(function (a) {
      if (status === "all" ? a.status === "probe" : a.status !== status) return false;
      if (f.method && a.method !== f.method) return false;
      if (f.tag && (a.tags || []).indexOf(f.tag) < 0) return false;
      if (f.notedOnly && !a.note) return false;
      if (q) {
        var hay = [a.path, a.summary || "", (a.tags || []).join(" ")].join("\n").toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });
  }

  function sortValue(a, key) {
    switch (key) {
      case "method": return a.method || "";
      case "path": return a.path || "";
      case "status": return STATUS_ORDER.indexOf(a.status);
      case "calls": return Number(a.calls) || 0;
      case "last": return a.last || "";
      case "tags": return (a.tags || []).join(",");
      case "memo": return a.note ? a.note.reason : "";
      default: return 0;
    }
  }

  function sortApis(rows, sort) {
    if (!sort || !sort.key) return rows;
    var dir = sort.dir === "desc" ? -1 : 1;
    return rows
      .map(function (a, i) { return { a: a, i: i }; })
      .sort(function (x, y) {
        var vx = sortValue(x.a, sort.key), vy = sortValue(y.a, sort.key);
        if (vx < vy) return -dir;
        if (vx > vy) return dir;
        return x.i - y.i;
      })
      .map(function (x) { return x.a; });
  }

  // ------------------------------------------------------------ summary

  function renderDonut(counts) {
    var total = countOf(counts, "all");
    var out = '<svg class="donut-svg" width="96" height="96" viewBox="0 0 42 42" role="img" aria-label="상태 분포">' +
      '<circle cx="21" cy="21" r="15.9" fill="none" stroke="#f0f1f6" stroke-width="6"/>';
    var cum = 0;
    if (total > 0) {
      DONUT_ORDER.forEach(function (k) {
        var c = countOf(counts, k);
        if (!c) return;
        var pct = c / total * 100;
        out += '<circle cx="21" cy="21" r="15.9" fill="none" stroke="' + STATUS_META[k].color +
          '" stroke-width="6" stroke-dasharray="' + pct.toFixed(3) + " " + (100 - pct).toFixed(3) +
          '" stroke-dashoffset="' + (25 - cum).toFixed(3) + '"/>';
        cum += pct;
      });
    }
    return out + "</svg>";
  }

  /* 요약 문장의 첫 문장을 시안처럼 굵게: "API 161개 중 <b>12개가 1년째 자고 있어요.</b><br>그중 ..." */
  function summaryNodes(text) {
    var s = String(text || "");
    var m = /^(API [\d,]+개 중 )([^.]+\.)\s*(.*)$/.exec(s);
    if (!m) return [s];
    return [m[1], el("b", null, m[2]), m[3] ? [el("br"), m[3]] : null];
  }

  function logRange(result) {
    var oldest = null, newest = null;
    (result.servers || []).forEach(function (s) {
      if (s.oldest && (!oldest || s.oldest < oldest)) oldest = s.oldest;
      if (s.newest && (!newest || s.newest > newest)) newest = s.newest;
    });
    var start = result.since && (!oldest || result.since > oldest) ? result.since : oldest;
    var end = newest && result.until && newest > result.until ? result.until : (newest || result.until);
    return fmtDate(start) + " ~ " + fmtDate(end);
  }

  function renderSummary(container, result, opts) {
    opts = opts || {};
    clear(container);
    container.classList.add("hero");
    var mascot = svgNode(root.NapiCat.full({ animated: !opts.print, scanning: !!opts.scanning }));
    mascot.classList.add("mascot");

    var servers = result.servers || [];
    var metaParts = ["로그 " + logRange(result), "서버 " + servers.length + "대", "스캔 " + fmtDateTime(result.scanned_at)];
    var meta = el("div", { class: "meta" }, metaParts.join(" · "));
    if (result.spec_location) {
      meta.appendChild(el("div", { class: "spec-loc", title: result.spec_location }, "명세: " + result.spec_location));
    }
    if (Number(result.sleeping_noted) > 0) {
      append(meta, [el("br"), "메모를 남겨 둔 잠든 API " + fmtNum(result.sleeping_noted) + "개는 숫자에서 뺐어요"]);
    }
    var bubble = el("div", { class: "bubble" }, summaryNodes(result.summary), meta);

    var mid = el("div", { class: "hero-mid" }, bubble);
    var rel = result.reliability || {};
    if (rel.level === "low" && (rel.reasons || []).length) {
      mid.appendChild(el("ul", { class: "reasons warn", "aria-label": "결과를 그대로 믿기 어려운 이유" },
        rel.reasons.map(function (r) { return el("li", null, "⚠ " + r); })));
    }

    var counts = result.counts || {};
    var legend = el("div", { class: "legend" }, DONUT_ORDER.map(function (k) {
      var sw = el("i");
      sw.style.background = STATUS_META[k].color;
      return el("div", null, sw, STATUS_META[k].label + " " + fmtNum(countOf(counts, k)));
    }));
    var donut = el("div", { class: "donut" }, svgNode(renderDonut(counts)), legend);
    var actions = el("div", { class: "hero-actions" });
    container.appendChild(mascot);
    container.appendChild(mid);
    container.appendChild(el("div", { class: "hero-right" }, actions, donut));
    return { actions: actions, mascot: mascot };
  }

  // ------------------------------------------------------------ status help

  /* 최근 기준 일수. 숫자, {awake_days}, 결과 객체 모두 받는다. 모르면 null. */
  function awakeDays(th) {
    var v = th !== null && typeof th === "object" ? th.awake_days : th;
    var n = Number(v);
    return v !== null && v !== undefined && v !== "" && isFinite(n) && n > 0 && Math.floor(n) === n ? n : null;
  }

  /* 탭 title용: "<뜻>. <판정 기준>" */
  function statusTip(status, th) {
    var m = STATUS_META[status];
    return m ? m.mean + ". " + m.criteria(awakeDays(th)) : "";
  }

  /* 탭 아래 한 줄: "<label> — <뜻>. <판정 기준>. 권고: <권고>" (전체 탭은 안내 문장) */
  function statusLineText(status, th) {
    var m = STATUS_META[status];
    if (!m) return ALL_LINE;
    return m.label + " — " + m.mean + ". " + m.criteria(awakeDays(th)) + ". 권고: " + m.action;
  }

  function renderStatusLine(container, status, th) {
    clear(container);
    container.classList.add("tab-line");
    container.setAttribute("aria-live", "polite");
    if (STATUS_META[status]) container.appendChild(face(status, "ico-face"));
    container.appendChild(el("span", null, statusLineText(status, th)));
  }

  /* 상태 설명 표 + 각주. 도움말 대화상자와 리포트 표지가 같이 쓴다. */
  function statusHelpTable(th) {
    var n = awakeDays(th);
    var head = el("tr", null, ["상태", "뜻", "판정 기준", "권고"].map(function (t) { return el("th", { scope: "col" }, t); }));
    var rows = HELP_ORDER.map(function (k) {
      var m = STATUS_META[k];
      return el("tr", { "data-status": k },
        el("th", { scope: "row", class: "c-st" }, statusPill(k)),
        el("td", null, m.mean),
        el("td", null, m.criteria(n)),
        el("td", null, m.action));
    });
    return el("div", { class: "status-help" },
      el("table", { class: "help-table" }, el("thead", null, head), el("tbody", null, rows)),
      el("ul", { class: "help-notes" }, HELP_NOTES.map(function (t) { return el("li", null, t); })));
  }

  // ------------------------------------------------------------ tabs

  /* opts: { thresholds: awake_days 또는 결과 객체, onHelp: "? 상태 설명" 버튼을 누를 때 } */
  function renderTabs(container, counts, active, onChange, opts) {
    opts = opts || {};
    clear(container);
    container.classList.add("tabs");
    container.removeAttribute("role");
    var list = el("div", { class: "tab-list", role: "tablist", "aria-label": "상태" });
    container.appendChild(list);
    TAB_ORDER.forEach(function (key) {
      var n = countOf(counts, key);
      var label = key === "all" ? "전체 " + fmtNum(n) : STATUS_META[key].label + " " + fmtNum(n);
      var on = key === active;
      list.appendChild(el("button", {
        type: "button", class: "tab" + (on ? " on" : ""), role: "tab", "aria-selected": on ? "true" : "false",
        "data-status": key, title: key === "all" ? null : statusTip(key, opts.thresholds),
        onclick: function () { if (onChange) onChange(key); }
      }, key === "all" ? null : face(key, "ico-face"), label));
    });
    if (opts.onHelp) {
      container.appendChild(el("button", {
        type: "button", class: "tab-help", "aria-haspopup": "dialog",
        onclick: function () { opts.onHelp(); }
      }, HELP_LABEL));
    }
  }

  // ------------------------------------------------------------ table

  function renderTable(container, apis, opts, onRowClick) {
    opts = opts || {};
    clear(container);
    var rows = sortApis(filterApis(apis, opts), opts.sort);
    var sort = opts.sort || {};

    var headRow = el("tr");
    COLUMNS.forEach(function (c) {
      var th = el("th", { class: "c-" + c.key + (c.num ? " num" : ""), scope: "col" });
      if (c.sortable === false) { th.textContent = c.label; headRow.appendChild(th); return; }
      var on = sort.key === c.key;
      th.setAttribute("aria-sort", on ? (sort.dir === "desc" ? "descending" : "ascending") : "none");
      th.appendChild(el("button", {
        type: "button", class: "sort" + (on ? " on" : ""),
        onclick: function () {
          var next = { key: c.key, dir: on && sort.dir !== "desc" ? "desc" : "asc" };
          if (on && sort.dir === "desc") next = { key: null, dir: "asc" };
          opts.sort = next;
          if (opts.onSort) opts.onSort(next);
          renderTable(container, apis, opts, onRowClick);
        }
      }, c.label, el("span", { class: "arrow", "aria-hidden": "true" }, on ? (sort.dir === "desc" ? "▼" : "▲") : "")));
      headRow.appendChild(th);
    });

    var tbody = el("tbody");
    rows.forEach(function (a) {
      var pathCell = el("td", { class: "c-path" }, el("div", { class: "path" }, pathParts(a.path)));
      if (a.summary) pathCell.appendChild(el("div", { class: "sum" }, a.summary));
      var memo;
      if (a.note) memo = el("td", { class: "c-memo memo has", title: noteText(a.note) }, noteText(a.note));
      else memo = el("td", { class: "c-memo memo" }, opts.readOnly ? "" : "＋ 메모");
      var tr = el("tr", { "data-key": opKey(a) },
        el("td", { class: "c-method" }, methodTag(a.method)),
        pathCell,
        el("td", { class: "c-status" }, statusPill(a.status)),
        el("td", { class: "c-calls num" }, fmtNum(a.calls)),
        el("td", { class: "c-last" }, fmtDate(a.last)),
        el("td", { class: "c-dist" }, distBar(a.by_status)),
        el("td", { class: "c-tags" }, (a.tags || []).map(function (t) { return el("span", { class: "tag" }, t); })),
        memo);
      if (onRowClick) {
        tr.className = "row";
        tr.tabIndex = 0;
        tr.addEventListener("click", function () { onRowClick(a); });
        tr.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onRowClick(a); }
        });
      }
      tbody.appendChild(tr);
    });
    if (!rows.length) {
      tbody.appendChild(el("tr", null, el("td", { class: "empty", colspan: COLUMNS.length }, "조건에 맞는 API가 없어요")));
    }
    container.appendChild(el("table", { class: "api-table" }, el("thead", null, headRow), tbody));

    var rest = opts.probeRest;
    if (opts.status === "probe" && rest && Number(rest.distinct) > 0) {
      container.appendChild(el("p", { class: "foot" },
        "그 밖에 404만 난 경로 " + fmtNum(rest.distinct) + "개 (" + fmtNum(rest.count) + "회)는 개수만 셌어요"));
    }
    return rows.length;
  }

  // ------------------------------------------------------------ detail

  function renderDetail(container, api, onSaveNote, onDeleteNote) {
    clear(container);
    var m = metaOf(api.status);
    container.appendChild(el("div", { class: "detail-head" },
      methodTag(api.method),
      el("span", { class: "path" }, api.path),
      el("button", { type: "button", class: "detail-close", "aria-label": "닫기", title: "닫기 (Esc)" }, "✕")));

    container.appendChild(el("div", { class: "detail-status s-" + (STATUS_META[api.status] ? api.status : "asleep") },
      face(api.status, "detail-face"),
      el("div", null, el("b", null, m.label), el("div", { class: "muted" }, m.desc))));

    if (api.summary) container.appendChild(el("p", { class: "detail-sum" }, api.summary));

    var kv = [
      ["operationId", api.operation_id || "—"],
      ["태그", (api.tags || []).length ? api.tags.join(", ") : "—"],
      ["명세", api.in_spec ? (api.deprecated ? "있음 (deprecated)" : "있음") : "없음"],
      ["호출 수", fmtNum(api.calls)],
      ["최초 호출", fmtDate(api.first)],
      ["마지막 호출", fmtDate(api.last)],
      ["응답 분포", distText(api.by_status)]
    ];
    container.appendChild(el("dl", { class: "kv" }, kv.map(function (p) {
      return [el("dt", null, p[0]), el("dd", null, p[1])];
    })));
    var bar = distBar(api.by_status);
    bar.classList.add("wide");
    container.appendChild(bar);

    container.appendChild(el("h4", null, "서버별 호출 수"));
    var by = api.by_server || {};
    var names = Object.keys(by).sort();
    if (names.length) {
      container.appendChild(el("table", { class: "mini" }, el("tbody", null, names.map(function (n) {
        return el("tr", null, el("td", null, n), el("td", { class: "num" }, fmtNum(by[n])));
      }))));
    } else {
      container.appendChild(el("p", { class: "muted" }, "호출 기록이 없어요"));
    }

    container.appendChild(el("h4", null, "메모 (유지 사유)"));
    var note = api.note;
    if (!onSaveNote) {
      container.appendChild(el("p", { class: note ? "memo has" : "muted" }, note ? noteText(note) : "메모가 없어요"));
      return;
    }
    var reason = el("textarea", { id: "note-reason", rows: 3, placeholder: "예: 연 1회 정산 배치에서만 불러요" });
    reason.value = note ? note.reason : "";
    var author = el("input", { id: "note-author", type: "text", placeholder: "이름", autocomplete: "off" });
    author.value = note ? note.author : lastAuthor();
    var msg = el("p", { class: "form-msg", role: "status" });
    var saveBtn = el("button", { type: "submit", class: "btn pri" }, "저장");
    var delBtn = note ? el("button", { type: "button", class: "btn" }, "메모 삭제") : null;

    function busy(on) {
      saveBtn.disabled = on;
      if (delBtn) delBtn.disabled = on;
    }
    function handle(p) {
      busy(true);
      Promise.resolve(p).then(function (res) {
        busy(false);
        if (res && res.ok === false) msg.textContent = res.error || "저장하지 못했어요";
      }, function () {
        busy(false);
        msg.textContent = "저장하지 못했어요";
      });
    }
    var form = el("form", {
      class: "note-form",
      onsubmit: function (e) {
        e.preventDefault();
        var r = reason.value.trim();
        if (!r) { msg.textContent = "유지 사유를 입력해 주세요"; reason.focus(); return; }
        var who = author.value.trim();
        rememberAuthor(who);
        msg.textContent = "";
        handle(onSaveNote(r, who));
      }
    },
      el("label", { for: "note-reason" }, "유지 사유"), reason,
      el("label", { for: "note-author" }, "작성자"), author,
      note && note.date ? el("p", { class: "muted small" }, note.date + "에 작성") : null,
      el("div", { class: "form-btns" }, saveBtn, delBtn),
      msg);
    if (delBtn) {
      delBtn.addEventListener("click", function () {
        msg.textContent = "";
        if (onDeleteNote) handle(onDeleteNote());
      });
    }
    container.appendChild(form);
  }

  // ------------------------------------------------------------ report

  function serverRange(s) {
    if (!s.oldest && !s.newest) return "—";
    return fmtDate(s.oldest) + " ~ " + fmtDate(s.newest);
  }

  /* 형식이 맞지 않아 건너뛴 로그 줄 수. 없으면 null (예전 결과에는 parse_fail이 없다). */
  function skippedText(s) {
    var n = Number(s && s.parse_fail);
    return isFinite(n) && n > 0 ? "건너뛴 줄 " + fmtNum(n) : null;
  }

  /* 서버별 수집 결과 표 (이름, 성공/오류, 로그 기간, 걸린 시간). 리포트 표지에 둔다. */
  function renderServers(result) {
    var servers = result.servers || [];
    var rows = servers.map(function (s) {
      var skipped = skippedText(s);
      var state = s.ok
        ? el("td", { class: "c-ok" }, s.truncated ? "성공 (일부 잘림)" : "성공",
          skipped ? el("span", { class: "sv-skipped" }, " · " + skipped) : null)
        : el("td", { class: "c-err warn" }, "오류" + (s.error ? ": " + s.error : ""));
      var secs = Number(s.elapsed_sec);
      return el("tr", null,
        el("td", null, s.name),
        state,
        el("td", null, serverRange(s)),
        el("td", { class: "num" }, isFinite(secs) ? secs.toFixed(1) + "초" : "—"));
    });
    var rel = result.reliability || {};
    var reasons = rel.level === "low" ? [] : (rel.reasons || []);
    return el("section", { class: "report-servers" },
      el("h2", null, "서버별 수집 결과"),
      el("table", { class: "server-table" },
        el("thead", null, el("tr", null,
          el("th", { scope: "col" }, "서버"), el("th", { scope: "col" }, "상태"),
          el("th", { scope: "col" }, "로그 기간"), el("th", { scope: "col", class: "num" }, "걸린 시간"))),
        el("tbody", null, rows.length ? rows
          : el("tr", null, el("td", { class: "empty", colspan: 4 }, "서버 정보가 없어요")))),
      reasons.length ? el("ul", { class: "reasons", "aria-label": "참고 사항" },
        reasons.map(function (t) { return el("li", null, t); })) : null);
  }

  /* 상태별 건수는 result.counts 대신 apis 목록에서 직접 센다 (섹션이 조용히 빠지지 않게). */
  function withDerivedCounts(result) {
    var counts = {};
    (result.apis || []).forEach(function (a) { counts[a.status] = (counts[a.status] || 0) + 1; });
    var copy = {};
    Object.keys(result).forEach(function (k) { copy[k] = result[k]; });
    copy.counts = counts;
    return copy;
  }

  function renderReport(rootEl, result, opts) {
    opts = opts || {};
    var print = !!opts.print;
    result = withDerivedCounts(result || {});
    root.NapiCat.ensureDefs(rootEl.ownerDocument);
    clear(rootEl);
    rootEl.classList.add("napi-report");
    if (print) rootEl.classList.add("print");

    rootEl.appendChild(el("header", { class: "report-head" },
      el("h1", null, "napi 리포트 — " + (result.service || "")),
      el("p", { class: "muted" }, "스캔 " + fmtDateTime(result.scanned_at))));
    var hero = el("section", { class: "report-cover" });
    rootEl.appendChild(hero);
    renderSummary(hero, result, { print: print });
    rootEl.appendChild(renderServers(result));
    rootEl.appendChild(el("section", { class: "report-help" },
      el("h2", null, "상태 설명"), statusHelpTable(result)));

    var apis = result.apis || [];
    var counts = result.counts || {};
    if (print) {
      STATUS_ORDER.forEach(function (key) {
        var n = countOf(counts, key);
        if (!n) return;
        var m = STATUS_META[key];
        var body = el("div", { class: "main" });
        rootEl.appendChild(el("section", { class: "report-section" },
          el("h2", null, face(key, "ico-face"), m.label + " " + fmtNum(n)),
          el("p", { class: "muted" }, m.desc), body));
        renderTable(body, apis, { status: key, readOnly: true, probeRest: result.probe_rest }, null);
      });
      return;
    }
    var state = { status: defaultTab(counts), readOnly: true, probeRest: result.probe_rest, sort: null };
    var tabs = el("div");
    var line = el("p");
    var body = el("div", { class: "main" });
    rootEl.appendChild(tabs);
    rootEl.appendChild(line);
    rootEl.appendChild(body);
    function draw() {
      renderTabs(tabs, counts, state.status, function (k) { state.status = k; draw(); }, { thresholds: result });
      renderStatusLine(line, state.status, result);
      renderTable(body, apis, state, null);
    }
    draw();
  }

  root.NapiRender = {
    STATUS_META: STATUS_META,
    STATUS_ORDER: STATUS_ORDER,
    TAB_ORDER: TAB_ORDER,
    renderSummary: renderSummary,
    renderDonut: renderDonut,
    renderTabs: renderTabs,
    renderStatusLine: renderStatusLine,
    statusHelpTable: statusHelpTable,
    statusLineText: statusLineText,
    statusTip: statusTip,
    awakeDays: awakeDays,
    renderTable: renderTable,
    renderDetail: renderDetail,
    renderReport: renderReport,
    filterApis: filterApis,
    sortApis: sortApis,
    countOf: countOf,
    defaultTab: defaultTab,
    fmtDate: fmtDate,
    fmtDateTime: fmtDateTime,
    fmtNum: fmtNum,
    opKey: opKey,
    skippedText: skippedText,
    el: el
  };
})(window);

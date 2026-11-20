/* 브라우저 단독 미리보기용 가짜 api. 주소에 ?mock=1이 있을 때만 켜진다.
 * window.pywebview.api에 실제 api.py와 같은 이름의 Promise 함수를 둔다.
 * 수치는 시안 v3를 그대로 쓴다: 161개 (깨어있음 128, 꾸벅꾸벅 14, 잠듦 9, 영면 3, 잠꼬대 2, 유령 5)
 * + 외부 탐색 시도 6개, WAS-03 로그 부족(신뢰도 low).
 *
 * 설정·스캔 (Task 13):
 * - 비밀번호: 라운드페이 API의 WAS-02만 아직 입력되지 않은 상태로 시작한다.
 * - 가짜 스캔: 명세 1.2초 → 서버 수집 (동시에 max_parallel_servers대).
 *   서버 목록의 2번째 서버는 호스트 키 확인을 묻고 (수락하면 새로 고칠 때까지 다시 묻지 않는다),
 *   3번째 서버는 "인증 실패"로 끝난다.
 *   명세 출처가 "파일"인 서비스는 명세 단계에서 실패한다 (failed 화면 확인용).
 * - save_config는 napi/config.py validate_config와 같은 문구로 검사한다.
 *
 * 형식·경로·비밀번호 (Task 5):
 * - 사용자 형식 "webtob_vhost"를 정산 API의 SETTLE-01이 쓴다 (삭제 불가 확인용). SETTLE-01은 로그 경로가 2개다.
 * - test_format / test_log_line은 logformat.py를 줄인 JS 판이다 (리터럴, 공백, [시각], %{...}t, %r/%m/%U/%s).
 * - 비밀번호·키파일 암호는 "입력됨" 표시만 둔다 (값은 어디에도 두지 않는다). secret_status, clear_secret 흉내.
 * - pick_key_file은 고정 경로를 고른 것처럼 돌려준다.
 */
(function (root) {
  "use strict";
  if (!/[?&]mock=1(&|$)/.test(root.location.search)) return;

  var TODAY = "20261118";
  var SERVERS = ["WAS-01", "WAS-02", "WAS-03"];

  // 결정적 난수 (mulberry32)
  var seed = 20261118;
  function rand() {
    seed = (seed + 0x6d2b79f5) | 0;
    var t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function randInt(a, b) { return a + Math.floor(rand() * (b - a + 1)); }

  function daysBefore(ymd, days) {
    var d = new Date(Date.UTC(+ymd.slice(0, 4), +ymd.slice(4, 6) - 1, +ymd.slice(6, 8)));
    d.setUTCDate(d.getUTCDate() - days);
    return d.toISOString().slice(0, 10).replace(/-/g, "");
  }

  function traffic(calls, lastAgo, firstAgo, opts) {
    opts = opts || {};
    var bs = {};
    var c5 = opts.only4xx ? 0 : Math.floor(calls * rand() * 0.01);
    var c4 = opts.only4xx ? calls : Math.floor(calls * rand() * 0.06);
    var c3 = opts.only4xx ? 0 : (rand() < 0.15 ? Math.floor(calls * 0.05) : 0);
    var c2 = calls - c5 - c4 - c3;
    if (c2) bs["2xx"] = c2;
    if (c3) bs["3xx"] = c3;
    if (c4) bs["4xx"] = c4;
    if (c5) bs["5xx"] = c5;
    var w3 = Math.floor(calls * 0.18);
    var w1 = Math.floor((calls - w3) / 2);
    var by = {};
    by["WAS-01"] = w1;
    by["WAS-02"] = calls - w3 - w1;
    by["WAS-03"] = w3;
    Object.keys(by).forEach(function (k) { if (!by[k]) delete by[k]; });
    return { calls: calls, first: daysBefore(TODAY, firstAgo), last: daysBefore(TODAY, lastAgo), by_status: bs, by_server: by };
  }

  function api(method, path, status, t, extra) {
    extra = extra || {};
    return {
      method: method, path: path, status: status,
      calls: t ? t.calls : 0, first: t ? t.first : null, last: t ? t.last : null,
      by_status: t ? t.by_status : {}, by_server: t ? t.by_server : {},
      in_spec: extra.in_spec !== false, deprecated: !!extra.deprecated,
      tags: extra.tags || [], summary: extra.summary || null,
      operation_id: extra.operation_id || null, note: null
    };
  }

  function opId(method, path) {
    return method.toLowerCase() + path.replace(/[{}]/g, "").split(/[\/-]/).map(function (s) {
      return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
    }).join("");
  }

  function buildApis() {
    var apis = [];
    var resources = [
      ["payments", "결제"], ["merchants", "가맹점"], ["cards", "카드"], ["settlements", "정산"],
      ["coupons", "쿠폰"], ["notify", "알림"], ["reports", "리포트"], ["auth", "인증"],
      ["points", "포인트"], ["refunds", "환불"], ["terminals", "단말기"], ["receipts", "영수증"],
      ["banks", "은행"], ["webhooks", "웹훅"], ["qr", "QR결제"], ["subscriptions", "정기결제"]
    ];
    var ops = [
      ["GET", "", "목록 조회"], ["POST", "", "등록"], ["GET", "/{id}", "상세 조회"], ["PUT", "/{id}", "수정"],
      ["DELETE", "/{id}", "삭제"], ["GET", "/{id}/history", "이력 조회"], ["POST", "/{id}/approve", "승인"],
      ["GET", "/search", "검색"]
    ];
    // 깨어있음 128 = 16 × 8
    resources.forEach(function (r) {
      ops.forEach(function (o) {
        var path = "/v1/" + r[0] + o[1];
        var calls = randInt(40, 90000);
        apis.push(api(o[0], path, "awake", traffic(calls, randInt(0, 25), randInt(300, 363)),
          { tags: [r[0]], summary: r[1] + " " + o[2], operation_id: opId(o[0], path) }));
      });
    });
    // 꾸벅꾸벅 14
    resources.slice(0, 13).forEach(function (r) {
      var path = "/v1/" + r[0] + "/{id}/export";
      var lastAgo = randInt(40, 300);
      apis.push(api("GET", path, "drowsy", traffic(randInt(1, 60), lastAgo, randInt(lastAgo, 363)),
        { tags: [r[0]], summary: r[1] + " 엑셀 내보내기", operation_id: opId("GET", path) }));
    });
    apis.push(api("GET", "/v1/settlements/yearly", "drowsy", traffic(3, 322, 322),
      { tags: ["settlements"], summary: "연간 정산 내역", operation_id: "getYearlySettlements" }));
    // 잠듦 9
    [
      ["POST", "/v1/payments/{id}/partial-cancel", "부분 취소", ["payments"]],
      ["GET", "/v1/merchants/{mid}/legacy-info", "가맹점 구 정보 조회", ["merchants", "legacy"]],
      ["DELETE", "/v1/cards/{cardId}/alias", "카드 별칭 삭제", ["cards"]],
      ["PUT", "/v1/notify/test", "알림 테스트 발송 <b>테스트용</b>", ["notify"]],
      ["GET", "/v1/legacy/receipts/{rid}", "구 영수증 조회", ["legacy"]],
      ["POST", "/v1/coupons/{cid}/rollback", "쿠폰 사용 취소", ["coupons"]],
      ["GET", "/v1/reports/legacy-monthly", "구 월간 리포트", ["reports", "legacy"]],
      ["PATCH", "/v1/merchants/{mid}/fax", "가맹점 팩스 번호 변경", ["merchants"]],
      ["GET", "/v1/events/2019-promo", "2019 프로모션 조회", ["events"]]
    ].forEach(function (a) {
      apis.push(api(a[0], a[1], "asleep", null, { tags: a[3], summary: a[2], operation_id: opId(a[0], a[1]) }));
    });
    // 영면 3
    [
      ["GET", "/v1/legacy/payments/{id}", "구 결제 조회", ["legacy"]],
      ["POST", "/v1/old-auth/token", "구 인증 토큰 발급", ["auth"]],
      ["GET", "/v0/merchants", "구 가맹점 목록", ["merchants"]]
    ].forEach(function (a) {
      apis.push(api(a[0], a[1], "rip", null,
        { tags: a[3], summary: a[2], operation_id: opId(a[0], a[1]), deprecated: true }));
    });
    // 잠꼬대 2
    apis.push(api("GET", "/v1/legacy/balance", "sleeptalk", traffic(37, 3, 280),
      { tags: ["legacy"], summary: "구 잔액 조회", operation_id: "getLegacyBalance", deprecated: true }));
    apis.push(api("POST", "/v1/payments/{id}/cancel-old", "sleeptalk", traffic(4, 61, 200),
      { tags: ["payments"], summary: "구 결제 취소", operation_id: "cancelOld", deprecated: true }));
    // 유령 5
    [
      ["GET", "/internal/health", 52310], ["GET", "/v1/admin/dump", 5], ["POST", "/v1/payments/{*}/retry", 212],
      ["GET", "/v2/merchants/{*}", 1840], ["GET", "/v1/legacy/ping", 77]
    ].forEach(function (a) {
      apis.push(api(a[0], a[1], "ghost", traffic(a[2], randInt(0, 20), randInt(100, 363)), { in_spec: false }));
    });
    // 외부 탐색 시도 6 (404만)
    [
      ["GET", "/.env", 41], ["GET", "/wp-login.php", 133], ["GET", "/phpmyadmin/index.php", 18],
      ["POST", "/cgi-bin/luci", 9], ["GET", "/actuator/env", 27], ["GET", "/admin/<script>alert(1)</script>", 2]
    ].forEach(function (a) {
      apis.push(api(a[0], a[1], "probe", traffic(a[2], randInt(0, 60), randInt(60, 300), { only4xx: true }), { in_spec: false }));
    });
    return apis;
  }

  var config = {
    version: 1,
    settings: { max_parallel_servers: 2, default_window: "365", remote_timeout_sec: 3600, max_rows: 50000, top_404: 200 },
    log_formats: [{ name: "webtob_vhost", pattern: '%v %h %l %u %t "%r" %s %b' }],
    services: [
      {
        name: "라운드페이 API",
        spec: { type: "openapi", source: "url", location: "https://swagger.test.local/roundpay/v3/api-docs" },
        servers: SERVERS.map(function (n, i) {
          return { name: n, host: "10.0.12." + (11 + i), user: "napi", log_globs: ["/logs/webtob/access.log*"], port: 22,
            auth: "password", key_file: null, log_format: "webtob", custom_format: null };
        }),
        path_mapping: { strip_prefix: "", add_prefix: "" },
        thresholds: { awake_days: 30, min_coverage_days: 90 },
        exclude_paths: [],
        notes: {
          "GET /v1/settlements/yearly": { reason: "연 1회 정산용 (1월)", author: "홍길동", date: "2026-11-02" },
          "GET /internal/health": { reason: "L4 헬스체크용", author: "김운영", date: "2026-10-15" }
        }
      },
      {
        name: "정산 API",
        spec: { type: "openapi", source: "file", location: "C:\\specs\\settle.yaml" },
        servers: [{ name: "SETTLE-01", host: "10.0.20.5", user: "napi", log_globs: ["/logs/access.log*", "/logs/archive/access.log*.gz"],
          port: 22, auth: "key", key_file: "C:\\Users\\napi\\.ssh\\id_ed25519", log_format: "webtob_vhost", custom_format: null }],
        path_mapping: { strip_prefix: "", add_prefix: "" },
        thresholds: { awake_days: 30, min_coverage_days: 90 },
        exclude_paths: [], notes: {}
      },
      {
        name: "회원 API",
        spec: { type: "openapi", source: "url", location: "https://swagger.test.local/member/v3/api-docs" },
        servers: [], path_mapping: { strip_prefix: "", add_prefix: "" },
        thresholds: { awake_days: 30, min_coverage_days: 90 }, exclude_paths: [], notes: {}
      }
    ]
  };

  var results = {
    "라운드페이 API": {
      service: "라운드페이 API",
      scanned_at: "2026-11-18T14:02:11",
      window: "365",
      since: daysBefore(TODAY, 365),
      until: TODAY,
      spec_location: config.services[0].spec.location,
      summary: "",
      apis: buildApis(),
      counts: {},
      sleeping: 0,
      sleeping_noted: 0,
      reliability: { level: "low", coverage_days: 363, reasons: ["WAS-03은 로그가 85일치뿐이에요"] },
      servers: [
        { name: "WAS-01", ok: true, error: null, oldest: "20251120", newest: TODAY, elapsed_sec: 412.3, truncated: false, lines: 18240311, parse_fail: 42 },
        { name: "WAS-02", ok: true, error: null, oldest: "20251121", newest: TODAY, elapsed_sec: 398.9, truncated: false, lines: 17998102, parse_fail: 0 },
        { name: "WAS-03", ok: true, error: null, oldest: "20260825", newest: TODAY, elapsed_sec: 121.4, truncated: false, lines: 4120577, parse_fail: 0 }
      ],
      probe_rest: { distinct: 214, count: 1032 }
    }
  };

  // 백엔드(store.refresh_notes / classifier.summary_sentence)와 같은 방식으로 다시 계산한다.
  // 단, 전체 개수는 시안처럼 외부 탐색 시도를 뺀 개수(161)를 쓴다.
  function summarySentence(total, sleeping, rip, coverageDays) {
    if (sleeping === 0) return "API " + total + "개 모두 깨어 있어요! 🐾";
    var period = coverageDays >= 360 ? "1년째" : coverageDays >= 30 ? Math.floor(coverageDays / 30) + "개월째" : coverageDays + "일째";
    var text = "API " + total + "개 중 " + sleeping + "개가 " + period + " 자고 있어요.";
    if (rip > 0) text += " 그중 " + rip + "개는 deprecated라 바로 지워도 될 것 같아요 💤";
    return text;
  }

  function refresh(service) {
    var r = results[service];
    if (!r) return;
    var notes = config.services.filter(function (s) { return s.name === service; })[0].notes;
    var counts = { awake: 0, drowsy: 0, asleep: 0, rip: 0, sleeptalk: 0, ghost: 0, probe: 0 };
    var sleeping = 0, noted = 0, total = 0;
    r.apis.forEach(function (a) {
      a.note = notes[a.method + " " + a.path] || null;
      counts[a.status] += 1;
      if (a.status !== "probe") total += 1;
      if (a.status === "asleep" || a.status === "rip") {
        if (a.note) noted += 1; else sleeping += 1;
      }
    });
    r.counts = counts;
    r.sleeping = sleeping;
    r.sleeping_noted = noted;
    r.summary = summarySentence(total, sleeping, counts.rip, r.reliability.coverage_days);
  }
  refresh("라운드페이 API");

  function clone(v) { return v === undefined ? null : JSON.parse(JSON.stringify(v)); }
  function reply(v) {
    return new Promise(function (resolve) { setTimeout(function () { resolve(clone(v)); }, 30); });
  }
  function service(name) {
    return config.services.filter(function (s) { return s.name === name; })[0] || null;
  }
  function todayIso() { return TODAY.slice(0, 4) + "-" + TODAY.slice(4, 6) + "-" + TODAY.slice(6, 8); }

  // ------------------------------------------------------------ 설정 (Task 13)

  var configPath = "C:\\Users\\napi\\AppData\\Roaming\\napi\\napi-config.yaml";

  function validate(cfg) {
    var errors = [];
    var seen = {};
    var known = { custom: true };
    Object.keys(BUILTIN_PATTERNS).forEach(function (k) { known[k] = true; });
    var fseen = {};
    (cfg.log_formats || []).forEach(function (f) {
      var label = "로그 형식 '" + f.name + "'";
      if (!String(f.name || "").trim()) errors.push("로그 형식 이름이 비어 있습니다.");
      else if (known[f.name]) errors.push(label + ": 기본 제공 형식 이름과 같아서 쓸 수 없습니다.");
      else if (fseen[f.name]) errors.push(label + ": 형식 이름이 중복되었습니다.");
      fseen[f.name] = true;
      try { mockCompile(String(f.pattern || "")); } catch (e) { errors.push(label + ": " + e.message); }
    });
    Object.keys(fseen).forEach(function (k) { known[k] = true; });
    (cfg.services || []).forEach(function (svc) {
      var label = "서비스 '" + svc.name + "'";
      if (!String(svc.name || "").trim()) errors.push("서비스 이름이 비어 있습니다.");
      if (seen[svc.name]) errors.push(label + ": 서비스 이름이 중복되었습니다.");
      seen[svc.name] = true;
      (svc.servers || []).forEach(function (sv) {
        var sl = label + " / 서버 '" + sv.name + "'";
        if (!String(sv.host || "").trim()) errors.push(sl + ": 호스트가 비어 있습니다.");
        if (sv.auth === "key" && !sv.key_file) errors.push(sl + ": 키 인증인데 키 파일이 지정되지 않았습니다.");
        if (!known[sv.log_format]) errors.push(sl + ": 모르는 로그 형식이에요: " + sv.log_format);
        if (sv.log_format === "custom" && !sv.custom_format) errors.push(sl + ": 로그 형식이 custom인데 사용자 정의 형식이 없습니다.");
        var globs = sv.log_globs || [];
        if (!globs.length) errors.push(sl + ": 로그 경로를 하나 이상 지정해야 합니다.");
        globs.forEach(function (glob) {
          glob = String(glob);
          if (glob.charAt(0) !== "/") errors.push(sl + ": 로그 경로는 '/'로 시작해야 합니다: " + glob);
          else if (/[*?\[]/.test(glob.slice(0, glob.lastIndexOf("/")))) errors.push(sl + ": 로그 경로의 디렉터리 부분에는 * ? [ 를 쓸 수 없습니다: " + glob);
        });
      });
    });
    return errors;
  }

  // ------------------------------------------------------------ 형식 테스트 (logformat.py의 간이판)

  var MONTHS = { Jan: "01", Feb: "02", Mar: "03", Apr: "04", May: "05", Jun: "06",
    Jul: "07", Aug: "08", Sep: "09", Oct: "10", Nov: "11", Dec: "12" };
  var UNREADABLE = "이 형식으로 읽을 수 없는 줄이에요";

  function utf8Len(s) { return new TextEncoder().encode(s).length; }
  function isVariable(seg) {
    return /^[0-9]+$/.test(seg) ||
      /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(seg) ||
      /^[0-9a-fA-F]{16,}$/.test(seg) || /^[0-9][0-9-]{5,}$/.test(seg) || seg.indexOf("@") >= 0 ||
      (seg.indexOf("%") >= 0 && utf8Len(seg) >= 20) ||
      (/^[A-Za-z0-9_-]{20,}$/.test(seg) && /[0-9]/.test(seg) && /[A-Za-z]/.test(seg));
  }
  function normalizePath(path) {
    var segs = [];
    String(path).replace(/[?#].*$/, "").split("/").forEach(function (seg) {
      seg = seg.split(";")[0];
      if (seg) segs.push(isVariable(seg) ? "{*}" : seg);
    });
    return "/" + segs.join("/");
  }
  var BUILTIN_PATTERNS = {
    apache_common: '%h %l %u %t "%r" %>s %b',
    apache_combined: '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"',
    nginx_combined: '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"',
    webtob: '%h %l %u %t "%r" %s %b',
    webtob_combined: '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"'
  };
  var TIME_WIDTHS = { Y: 4, m: 2, d: 2, H: 2, M: 2, S: 2, b: 3, z: 5, y: 2 };
  var REQUIRED = "API 호출을 셀 수 없는 형식이에요 (요청/시각 필요)";

  // compile_format의 간이판 (글자는 1바이트로 친다). 잘못되면 Error(한국어 문구).
  function mockCompile(pattern) {
    if (/[\x00-\x08\x0a-\x1f]/.test(pattern)) throw new Error("형식에 쓸 수 없는 문자가 있어요");
    var tokens = [], lit = "", i = 0, n = pattern.length;
    function pushLit() {
      lit.split(/([ \t]+)/).forEach(function (part, k) { if (part) tokens.push({ kind: k % 2 ? "ws" : "lit", text: part }); });
      lit = "";
    }
    while (i < n) {
      var c = pattern.charAt(i);
      if (c !== "%") { lit += c; i++; continue; }
      var j = i + 1;
      if (pattern.charAt(j) === "%") { lit += "%"; i = j + 1; continue; }
      if (pattern.charAt(j) === "<" || pattern.charAt(j) === ">") j++;
      var arg = null;
      if (pattern.charAt(j) === "{") {
        var k = pattern.indexOf("}", j + 1);
        if (k < 0) throw new Error("닫히지 않은 %{ 가 있어요");
        arg = pattern.slice(j + 1, k);
        j = k + 1;
      }
      var letter = pattern.charAt(j);
      var text = pattern.slice(i, j + 1);
      i = j + 1;
      var last = tokens[tokens.length - 1];
      if (lit) pushLit();
      else if (last && last.kind === "field") throw new Error("필드 사이에 구분 문자가 필요해요: " + last.text + text);
      if (letter === "t" && arg !== null) tokens.push(timeToken(text, arg));
      else tokens.push({ kind: "field", text: text, d: letter && "hlutrmUqHsbBDTio".indexOf(letter) >= 0 ? letter : "skip" });
    }
    if (lit) pushLit();
    var dirs = {};
    tokens.forEach(function (t, idx) {
      if (t.kind !== "field") return;
      dirs[t.d === "tfmt" ? (t.layout.length ? "tdate" : "tfmt") : t.d] = true;
      var nx = tokens[idx + 1];
      if (t.d === "r" && nx && nx.kind === "ws") throw new Error('%r은 따옴표로 감싸 주세요 (예: "%r")');
    });
    if (!((dirs.r || (dirs.m && dirs.U)) && (dirs.t || dirs.tdate))) throw new Error(REQUIRED);
    var lastEssential = -1;
    tokens.forEach(function (t, idx) {
      if (t.kind === "field" && ("rmUst".indexOf(t.d) >= 0 || (t.d === "tfmt" && t.layout.length))) lastEssential = idx;
    });
    return { tokens: tokens, lastEssential: lastEssential };
  }

  function timeToken(text, arg) {
    var layout = [], off = 0, i = 0;
    while (i < arg.length) {
      if (arg.charAt(i) !== "%") { off++; i++; continue; }
      var d = arg.charAt(i + 1);
      i += 2;
      if (d === "%") { off++; continue; }
      if (!TIME_WIDTHS[d]) throw new Error("시각 형식에서 지원하지 않는 지시자예요: %" + d);
      if ("Ymdb".indexOf(d) >= 0) layout.push([d, off, TIME_WIDTHS[d]]);
      off += TIME_WIDTHS[d];
    }
    var keys = layout.map(function (x) { return x[0]; }).join("");
    var full = keys.indexOf("Y") >= 0 && keys.indexOf("d") >= 0 && (keys.indexOf("m") >= 0 || keys.indexOf("b") >= 0);
    return { kind: "field", text: text, d: "tfmt", layout: full ? layout : [], width: off };
  }

  // parse_with_spec의 간이판. 읽을 수 없으면 null.
  function mockParse(line, spec) {
    var toks = spec.tokens, pos = 0, method = null, path = null, status = null, date = null;
    for (var idx = 0; idx <= spec.lastEssential; idx++) {
      var t = toks[idx];
      if (t.kind === "lit") {
        if (line.slice(pos, pos + t.text.length) !== t.text) return null;
        pos += t.text.length;
        continue;
      }
      if (t.kind === "ws") {
        var w = /^[ \t]+/.exec(line.slice(pos));
        if (!w) return null;
        pos += w[0].length;
        continue;
      }
      if (t.d === "t") {
        if (line.charAt(pos) !== "[") return null;
        var close = line.indexOf("]", pos);
        var m = close < 0 ? null : /^\[([0-9]{2})\/([A-Za-z]{3})\/([0-9]{4})/.exec(line.slice(pos, close + 1));
        if (!m || !MONTHS[m[2]]) return null;
        date = m[3] + MONTHS[m[2]] + m[1];
        pos = close + 1;
        continue;
      }
      if (t.d === "tfmt") {
        var raw = line.slice(pos, pos + t.width);
        if (raw.length < t.width) return null;
        var parts = {};
        for (var q = 0; q < t.layout.length; q++) {
          var piece = raw.substr(t.layout[q][1], t.layout[q][2]);
          if (t.layout[q][0] === "b") { if (!MONTHS[piece]) return null; parts.m = MONTHS[piece]; }
          else { if (!/^[0-9]+$/.test(piece)) return null; parts[t.layout[q][0]] = piece; }
        }
        if (t.layout.length) date = parts.Y + parts.m + parts.d;
        pos += t.width;
        continue;
      }
      var nx = toks[idx + 1], end;
      if (!nx) end = line.length;
      else if (nx.kind === "ws") { end = line.slice(pos).search(/[ \t]/); end = end < 0 ? line.length : pos + end; if (end === pos) return null; }
      else { end = line.indexOf(nx.text, pos); if (end < 0) return null; }
      var value = line.slice(pos, end);
      pos = end;
      if (t.d === "r") {
        var rp = value.trim().split(/[ \t]+/);
        if (rp.length < 2 || !/^[A-Z]+$/.test(rp[0])) return null;
        method = rp[0]; path = rp[1];
      } else if (t.d === "m") {
        if (!/^[A-Z]+$/.test(value)) return null;
        method = value;
      } else if (t.d === "U") {
        path = value;
      } else if (t.d === "s") {
        if (!/^[0-9]{3}$/.test(value)) return null;
        status = +value;
      }
    }
    if (method === null || path === null || date === null) return null;
    return { method: method, path: path, status: status, date: date };
  }

  function sampleReply(p, error) {
    if (!p) return reply({ ok: false, method: null, path: null, normalized: null, status: null, status_class: null, date: null, error: error || UNREADABLE });
    return reply({ ok: true, method: p.method, path: p.path, normalized: normalizePath(p.path), status: p.status,
      status_class: p.status === null ? "???" : String(p.status).charAt(0) + "xx", date: p.date, error: null });
  }
  function stripEol(line) { return String(line || "").replace(/\n$/, "").replace(/\r$/, ""); }
  function parseCustom(line, c) {
    var f = line.trim().split(/[ \t]+/);
    function at(n) { return n >= 1 && n <= f.length ? f[n - 1] : null; }
    var m = at(c.method_field), p = at(c.path_field), st = at(c.status_field), tm = at(c.time_field);
    if (!m || !p || !st || !tm || !/^[A-Z]+$/.test(m) || !/^[0-9]{3}$/.test(st)) return null;
    p = p.replace(/^"/, "").replace(/"$/, "");
    if (!p) return null;
    var d;
    if (c.time_format === "iso8601") {
      var iso = /^([0-9]{4})-([0-9]{2})-([0-9]{2})/.exec(tm);
      if (!iso) return null;
      d = iso[1] + iso[2] + iso[3];
    } else {
      var clf = /([0-9]{2})\/([A-Za-z]{3})\/([0-9]{4})/.exec(tm);
      if (!clf || !MONTHS[clf[2]]) return null;
      d = clf[3] + MONTHS[clf[2]] + clf[1];
    }
    return { method: m, path: p, status: +st, date: d };
  }

  // ------------------------------------------------------------ 명령 미리보기 (모양만 흉내 낸다)

  function fakeCommand(sv, settings, window) {
    if (sv.log_format === "custom" && !sv.custom_format) return "설정 오류: 사용자 정의 형식이 없어요";
    var finds = (sv.log_globs || []).map(function (glob) {
      glob = String(glob);
      var cut = glob.lastIndexOf("/");
      var dir = cut > 0 ? glob.slice(0, cut) : "/";
      return "  find " + dir + " -maxdepth 1 -type f -name \"" + glob.slice(cut + 1) + "\" 2>/dev/null\n";
    }).join("");
    var since = window === "all" ? "-" : daysBefore(TODAY, +window);
    var script = "set -u; tok=\"$0\"\n" +
      "{\n" + finds + "} | LC_ALL=C sort -u | while IFS= read -r f; do\n" +
      "  printf \"\\n\\001F%s\\t%s\\n\" \"$tok\" \"$f\"\n" +
      "  case \"$f\" in *.gz) gzip -dc -- \"$f\" 2>/dev/null ;; *) cat -- \"$f\" 2>/dev/null ;; esac\n" +
      "done | LC_ALL=C awk -v since=" + since + " -v fmt=" + sv.log_format +
      " -v max_rows=" + settings.max_rows + " -v top404=" + settings.top_404 + " \"<집계 awk 프로그램 (미리보기에서는 생략)>\"";
    return "timeout " + settings.remote_timeout_sec + " nice -n 19 ionice -c3 sh -c '" + script + "' napi-scan-00000000";
  }

  // ------------------------------------------------------------ 비밀번호 (값은 어디에도 두지 않는다: 입력됐다는 표시만)

  var knownSecrets = { "napi@10.0.12.11:22": true, "napi@10.0.12.13:22": true };
  var knownPassphrases = {}; // key_file → true
  function secretKey(user, host, port) { return user + "@" + host + ":" + port; }

  // ------------------------------------------------------------ 가짜 스캔

  var scans = {};
  var activeScan = {};
  var scanSeq = 0;
  var knownHosts = {}; // 수락한 호스트 키 (known_hosts 흉내): 다음 스캔에서는 묻지 않는다

  function fakeFingerprint(host) {
    var abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    var h = 7, out = "";
    for (var i = 0; i < host.length; i++) h = (Math.imul(h, 31) + host.charCodeAt(i)) | 0;
    for (var j = 0; j < 43; j++) { h = (Math.imul(h, 1103515245) + 12345) | 0; out += abc.charAt((h >>> 8) & 63); }
    return "SHA256:" + out;
  }

  function later(scan, ms, fn) {
    var t = setTimeout(function () {
      var i = scan.timers.indexOf(t);
      if (i >= 0) scan.timers.splice(i, 1);
      if (!scan.stopped) fn();
    }, ms);
    scan.timers.push(t);
  }

  function nowIso() {
    var d = new Date();
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + "T" +
      p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function finishScan(scan, phase, error) {
    scan.stopped = true;
    scan.timers.forEach(clearTimeout);
    scan.timers = [];
    scan.prompts = [];
    scan.phase = phase;
    scan.error = error || null;
    if (activeScan[scan.service] === scan.id) delete activeScan[scan.service];
  }

  function saveFakeResult(scan, svc) {
    if (!service(svc.name)) return; // 스캔 중에 설정에서 지워진 서비스
    var r = results[svc.name];
    if (!r) {
      r = results[svc.name] = {
        service: svc.name, scanned_at: "", window: scan.window, since: null, until: TODAY,
        spec_location: svc.spec.location, summary: "", apis: buildApis(), counts: {}, sleeping: 0,
        sleeping_noted: 0, reliability: { level: "ok", coverage_days: 364, reasons: [] }, servers: [],
        probe_rest: { distinct: 0, count: 0 }
      };
    }
    var reasons = [];
    r.servers = svc.servers.map(function (sv) {
      var o = scan.outcomes[sv.name] || { ok: false, error: "취소됨", elapsed: 0 };
      if (!o.ok) reasons.push(sv.name + "에서 로그를 가져오지 못했어요 (" + o.error + ")");
      return { name: sv.name, ok: o.ok, error: o.ok ? null : o.error, oldest: o.ok ? daysBefore(TODAY, 364) : null,
        newest: o.ok ? TODAY : null, elapsed_sec: o.elapsed, truncated: false,
        lines: o.ok ? 1000000 : 0, parse_fail: o.ok ? 17 : 0 };
    });
    r.scanned_at = nowIso();
    r.window = scan.window;
    r.since = scan.window === "all" ? null : daysBefore(TODAY, +scan.window);
    r.spec_location = svc.spec.location;
    r.reliability = { level: reasons.length ? "low" : "ok", coverage_days: 364, reasons: reasons };
    refresh(svc.name);
  }

  function startFakeScan(svc, window) {
    var scan = { id: "mock-scan-" + (++scanSeq), service: svc.name, window: window, phase: "spec", servers: {},
      prompts: [], error: null, timers: [], stopped: false, outcomes: {} };
    svc.servers.forEach(function (sv) { scan.servers[sv.name] = "waiting"; });
    scans[scan.id] = scan;
    activeScan[svc.name] = scan.id;

    var queue = svc.servers.map(function (sv, i) {
      return { sv: sv, role: i === 1 ? "hostkey" : i === 2 ? "authfail" : "ok" };
    });
    var running = 0;

    function end(job, ok, error, t0) {
      scan.servers[job.sv.name] = ok ? "done" : "failed";
      scan.outcomes[job.sv.name] = { ok: ok, error: error, elapsed: Math.round((Date.now() - t0) / 100) / 10 };
      running -= 1;
      next();
    }
    function aggregate(job, t0) {
      scan.servers[job.sv.name] = "aggregating";
      later(scan, 3500, function () { end(job, true, null, t0); });
    }
    function run(job) {
      var t0 = Date.now();
      running += 1;
      scan.servers[job.sv.name] = "connecting";
      if (job.role === "authfail") {
        later(scan, 1500, function () { end(job, false, "인증 실패", t0); });
      } else if (job.role === "hostkey" && !knownHosts[job.sv.host + ":" + job.sv.port]) {
        later(scan, 700, function () {
          scan.prompts.push({ prompt_id: scan.id + "-" + job.sv.name, host: job.sv.host, port: job.sv.port,
            key_type: "ssh-ed25519", fingerprint: fakeFingerprint(job.sv.host),
            answer: function (accept) {
              if (accept) {
                knownHosts[job.sv.host + ":" + job.sv.port] = true;
                later(scan, 300, function () { aggregate(job, t0); });
              }
              else end(job, false, "호스트 키를 확인하지 않아 접속하지 않았어요", t0);
            } });
        });
      } else {
        later(scan, 1000, function () { aggregate(job, t0); });
      }
    }
    function next() {
      if (scan.stopped) return;
      var limit = Math.max(1, +config.settings.max_parallel_servers || 2);
      while (running < limit && queue.length) run(queue.shift());
      if (!running && !queue.length) {
        saveFakeResult(scan, svc);
        finishScan(scan, "done");
      }
    }

    later(scan, 1200, function () {
      if (svc.spec.source === "file") {
        finishScan(scan, "failed", "명세 파일을 찾을 수 없어요: " + svc.spec.location);
        return;
      }
      scan.phase = "collect";
      next();
    });
    return scan;
  }

  var mockApi = {
    get_state: function () {
      var out = {};
      config.services.forEach(function (s) {
        var r = results[s.name];
        out[s.name] = r ? { sleeping: r.sleeping, scanned_at: r.scanned_at } : null;
      });
      return reply({ config: config, config_path: configPath, results: out, error: null });
    },
    get_result: function (name) { return reply(results[name] || null); },
    save_note: function (name, opKey, reason, author) {
      var svc = service(name);
      if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + name });
      reason = String(reason || "").trim();
      if (!reason) return reply({ ok: false, error: "유지 사유를 입력해 주세요" });
      svc.notes[opKey] = { reason: reason, author: String(author || "").trim(), date: todayIso() };
      refresh(name);
      return reply({ ok: true });
    },
    delete_note: function (name, opKey) {
      var svc = service(name);
      if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + name });
      delete svc.notes[opKey];
      refresh(name);
      return reply({ ok: true });
    },
    export: function (name, kind) {
      if (kind !== "html" && kind !== "pdf") return reply({ ok: false, error: "내보내기 형식이 올바르지 않아요" });
      if (!results[name]) return reply({ ok: false, error: "아직 스캔 결과가 없어요" });
      return reply({ ok: true, path: "C:\\Users\\napi\\Downloads\\napi_" + name + "_20261119." + kind });
    },
    save_config: function (cfg) {
      var errors = validate(cfg);
      if (errors.length) return reply({ ok: false, errors: errors });
      config = clone(cfg);
      config.services.forEach(function (s) { if (results[s.name]) refresh(s.name); });
      return reply({ ok: true, errors: [] });
    },
    open_config_dialog: function () {
      configPath = "C:\\Users\\napi\\Desktop\\napi-config-team.yaml";
      return reply({ ok: true, path: configPath });
    },
    test_log_line: function (fmt, custom, line, formats) {
      if (fmt === "custom") {
        if (!custom) return reply({ ok: false, error: "사용자 정의 형식이 올바르지 않아요" });
        return sampleReply(parseCustom(stripEol(line), custom));
      }
      var pattern = BUILTIN_PATTERNS[fmt];
      if (!pattern) {
        var list = formats !== undefined && formats !== null ? formats : (config.log_formats || []);
        var hit = list.filter(function (f) { return f.name === fmt; })[0];
        if (!hit) return sampleReply(null, "모르는 로그 형식이에요: " + fmt);
        pattern = hit.pattern;
      }
      var spec;
      try { spec = mockCompile(String(pattern)); } catch (e) { return sampleReply(null, e.message); }
      return sampleReply(mockParse(stripEol(line), spec));
    },
    test_format: function (pattern, line) {
      var spec;
      try { spec = mockCompile(String(pattern || "")); } catch (e) { return sampleReply(null, e.message); }
      return sampleReply(mockParse(stripEol(line), spec));
    },
    preview_commands: function (name, window) {
      var svc = service(name);
      if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + name });
      var out = {};
      svc.servers.forEach(function (sv) { out[sv.name] = fakeCommand(sv, config.settings, String(window)); });
      return reply(out);
    },
    missing_secrets: function (name) {
      var svc = service(name);
      if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + name });
      return reply(svc.servers.filter(function (sv) {
        return sv.auth === "password" && !knownSecrets[secretKey(sv.user, sv.host, sv.port)];
      }).map(function (sv) { return { server: sv.name, user: sv.user, host: sv.host, port: sv.port, auth: sv.auth }; }));
    },
    set_password: function (user, host, port, password, applyTo) {
      var targets = [secretKey(user, host, +port)];
      if (applyTo) {
        var svc = service(applyTo);
        if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + applyTo });
        svc.servers.forEach(function (sv) { if (sv.auth === "password") targets.push(secretKey(sv.user, sv.host, sv.port)); });
      }
      targets.forEach(function (k) { knownSecrets[k] = true; });
      return reply({ ok: true });
    },
    set_passphrase: function (keyFile) {
      knownPassphrases[String(keyFile)] = true;
      return reply({ ok: true });
    },
    secret_status: function (name) {
      var svc = service(name);
      if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + name });
      var out = {};
      svc.servers.forEach(function (sv) {
        if (sv.auth === "key") out[sv.name] = sv.key_file && knownPassphrases[sv.key_file] ? "set" : "n/a";
        else out[sv.name] = knownSecrets[secretKey(sv.user, sv.host, sv.port)] ? "set" : "missing";
      });
      return reply(out);
    },
    clear_secret: function (name, server) {
      var svc = service(name);
      if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + name });
      var sv = svc.servers.filter(function (x) { return x.name === server; })[0];
      if (!sv) return reply({ ok: false, error: "서버를 찾을 수 없어요: " + server });
      if (sv.auth === "key") { if (sv.key_file) delete knownPassphrases[sv.key_file]; }
      else delete knownSecrets[secretKey(sv.user, sv.host, sv.port)];
      return reply({ ok: true });
    },
    pick_key_file: function () {
      return reply({ ok: true, path: "C:\\Users\\napi\\.ssh\\id_rsa_napi" });
    },
    start_scan: function (name, window) {
      if (["90", "180", "365", "all"].indexOf(String(window)) < 0) return reply({ ok: false, error: "분석 기간이 올바르지 않아요" });
      var svc = service(name);
      if (!svc) return reply({ ok: false, error: "서비스를 찾을 수 없어요: " + name });
      if (activeScan[name]) return reply({ ok: false, error: "이미 스캔 중이에요" });
      return reply({ scan_id: startFakeScan(clone(svc), String(window)).id });
    },
    get_scan_progress: function (id) {
      var scan = scans[id];
      if (!scan) return reply({ ok: false, error: "스캔을 찾을 수 없어요" });
      var p = scan.prompts[0];
      return reply({ phase: scan.phase, servers: scan.servers, error: scan.error,
        hostkey_pending: p ? { prompt_id: p.prompt_id, host: p.host, port: p.port, key_type: p.key_type,
          fingerprint: p.fingerprint } : null });
    },
    answer_hostkey: function (id, accept, promptId) {
      var scan = scans[id];
      if (!scan) return reply({ ok: false, error: "스캔을 찾을 수 없어요" });
      var p = scan.prompts.filter(function (x) { return promptId == null || x.prompt_id === promptId; })[0];
      if (!p) return reply({ ok: false, error: "확인할 호스트 키가 없어요" });
      scan.prompts.splice(scan.prompts.indexOf(p), 1);
      p.answer(!!accept);
      return reply({ ok: true });
    },
    cancel_scan: function (id) {
      var scan = scans[id];
      if (!scan) return reply({ ok: false, error: "스캔을 찾을 수 없어요" });
      scan.prompts = [];
      // 실제 백엔드처럼 바로 끝나지 않고 잠시 뒤에 멈춘다 (진행 중이던 서버는 "cancelled").
      setTimeout(function () {
        if (scan.stopped) return;
        Object.keys(scan.servers).forEach(function (k) {
          if (scan.servers[k] !== "done" && scan.servers[k] !== "failed") scan.servers[k] = "cancelled";
        });
        finishScan(scan, "cancelled");
      }, 600);
      return reply({ ok: true });
    }
  };

  root.pywebview = { api: mockApi, napiMock: true };
  function ready() { setTimeout(function () { root.dispatchEvent(new Event("pywebviewready")); }, 0); }
  if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", ready);
  else ready();
})(window);

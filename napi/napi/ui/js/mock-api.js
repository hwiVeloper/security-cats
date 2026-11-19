/* 브라우저 단독 미리보기용 가짜 api. 주소에 ?mock=1이 있을 때만 켜진다.
 * window.pywebview.api에 실제 api.py와 같은 이름의 Promise 함수를 둔다.
 * 수치는 시안 v3를 그대로 쓴다: 161개 (깨어있음 128, 꾸벅꾸벅 14, 잠듦 9, 영면 3, 잠꼬대 2, 유령 5)
 * + 외부 탐색 시도 6개, WAS-03 로그 부족(신뢰도 low).
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
    services: [
      {
        name: "라운드페이 API",
        spec: { type: "openapi", source: "url", location: "https://swagger.test.local/roundpay/v3/api-docs" },
        servers: SERVERS.map(function (n, i) {
          return { name: n, host: "10.0.12." + (11 + i), user: "napi", log_glob: "/logs/webtob/access.log*", port: 22,
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
        servers: [{ name: "SETTLE-01", host: "10.0.20.5", user: "napi", log_glob: "/logs/access.log*", port: 22,
          auth: "key", key_file: "C:\\Users\\napi\\.ssh\\id_ed25519", log_format: "webtob", custom_format: null }],
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
        { name: "WAS-01", ok: true, error: null, oldest: "20251120", newest: TODAY, elapsed_sec: 412.3, truncated: false },
        { name: "WAS-02", ok: true, error: null, oldest: "20251121", newest: TODAY, elapsed_sec: 398.9, truncated: false },
        { name: "WAS-03", ok: true, error: null, oldest: "20260825", newest: TODAY, elapsed_sec: 121.4, truncated: false }
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

  var mockApi = {
    get_state: function () {
      var out = {};
      config.services.forEach(function (s) {
        var r = results[s.name];
        out[s.name] = r ? { sleeping: r.sleeping, scanned_at: r.scanned_at } : null;
      });
      return reply({ config: config, config_path: "C:\\Users\\napi\\AppData\\Roaming\\napi\\napi-config.yaml", results: out, error: null });
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
    }
  };

  root.pywebview = { api: mockApi, napiMock: true };
  function ready() { setTimeout(function () { root.dispatchEvent(new Event("pywebviewready")); }, 0); }
  if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", ready);
  else ready();
})(window);

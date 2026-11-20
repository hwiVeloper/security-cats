/* 스캔 흐름: 비밀번호 입력 → start_scan → 500ms 폴링 (서버별 진행, 호스트 키 지문 확인) → 결과 새로 고침.
 * 전역 NapiScan.run(service, window). app.js의 NapiApp.onScan이 부른다.
 * 비밀번호는 입력란에서 바로 api.set_password로 넘기고, 끝나면 입력란을 비운다.
 * 어디에도 남기지 않는다 (변수·localStorage·로그 모두).
 */
(function (root) {
  "use strict";
  var R = root.NapiRender;
  var el = R.el;
  var doc = root.document;

  var POLL_MS = 500;
  var MAX_POLL_ERRORS = 20;
  var TERMINAL = { done: true, failed: true, cancelled: true };
  var SERVER_PHASE = {
    waiting: "대기 중", connecting: "접속 중", aggregating: "집계 중",
    done: "완료", failed: "실패", cancelled: "취소"
  };
  var PW_NOTICE = "비밀번호는 저장하지 않아요. 프로그램을 끄면 사라져요";
  var PP_NOTICE = "키파일 암호는 저장하지 않아요. 프로그램을 끄면 사라져요";
  var SAME_PW = "이 서비스의 모든 서버에 같은 비밀번호 사용";
  var HOSTKEY_TEXT = "처음 접속하는 서버예요. 지문이 맞는지 확인해 주세요";

  var current = null; // 패널에 보이는 스캔 (끝난 뒤에도 닫을 때까지 남는다)
  var starting = false; // 비밀번호 입력 ~ start_scan 사이
  var startingService = null; // starting 동안 스캔 버튼을 잠글 서비스
  var formSeq = 0;

  function api() { return root.pywebview.api; }
  function app() { return root.NapiApp; }
  function toast(t) { app().toast(t); }
  function isErr(res) { return res && typeof res === "object" && !Array.isArray(res) && res.ok === false && typeof res.error === "string"; }
  function $(id) { return doc.getElementById(id); }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  function svgNode(markup) {
    var t = doc.createElement("template");
    t.innerHTML = markup; // NapiCat 고정 SVG (외부 데이터 없음)
    return t.content.firstChild;
  }

  function serviceConfig(name) {
    var cfg = app().state.config;
    var list = (cfg && cfg.services) || [];
    for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i];
    return null;
  }

  // 이 서비스의 스캔이 시작 준비 중이거나 진행 중이면 true (스캔 버튼·기간 선택을 잠근다).
  function isRunning(service) {
    if (starting && startingService === service) return true;
    return !!(current && !current.finished && current.service === service);
  }

  function syncLock() {
    try {
      var a = app();
      if (a && a.syncScanLock) a.syncScanLock();
    } catch (e) { /* 잠금 표시를 못 고쳐도 스캔 흐름은 계속한다 */ }
  }

  function elapsedText(ms) {
    var sec = Math.max(0, Math.floor(ms / 1000));
    return "경과 " + Math.floor(sec / 60) + "분 " + (sec % 60) + "초";
  }

  function minutesText(sec) {
    var n = Number(sec);
    if (!isFinite(n) || n <= 0) return null;
    return "지난번 약 " + Math.max(1, Math.round(n / 60)) + "분";
  }

  // ------------------------------------------------------------ 시작

  function run(service, window) {
    if (starting) return Promise.resolve();
    if (current && !current.finished) {
      toast(current.service === service ? "이미 스캔 중이에요" : "다른 서비스를 스캔하고 있어요. 끝난 뒤에 시작해 주세요");
      return Promise.resolve();
    }
    starting = true;
    startingService = service;
    syncLock();
    function release() {
      starting = false;
      startingService = null;
      syncLock();
    }
    // Promise.resolve()로 시작해야 api()가 바로 throw해도 (pywebview 준비 전 등) 잠금이 풀린다.
    return Promise.resolve().then(function () {
      return api().missing_secrets(service);
    }).then(function (missing) {
      if (isErr(missing)) { toast(missing.error); return false; }
      if (!missing || !missing.length) return true;
      return askPasswords(service, missing.map(function (m) {
        return { server: m.server, user: m.user, host: m.host, port: m.port };
      }));
    }).then(function (go) {
      if (go) return begin(service, window);
    }).catch(function () {
      toast("스캔을 시작하지 못했어요");
    }).then(release, release);
  }

  function begin(service, window) {
    return api().start_scan(service, window).then(function (res) {
      if (isErr(res)) { toast(res.error); return; }
      if (current) hidePanel();
      var s = {
        id: res.scan_id, service: service, window: window, phase: "spec", servers: {},
        prev: {}, reasons: {}, skipped: {}, error: null, finished: false, cancelling: false,
        answered: {}, prompt: null, timer: null, pollErrors: 0, sig: null, catMode: null,
        startedAt: Date.now(), endedAt: null
      };
      current = s;
      try {
        showPanel(s);
      } catch (e) {
        // 패널을 못 그리면 진행을 보여 줄 수 없다: 끝난 것으로 처리해 잠금을 풀고, 서버 쪽 스캔도 취소해 둔다.
        finish(s, "failed", "스캔 화면을 그리지 못했어요");
        Promise.resolve().then(function () { return api().cancel_scan(s.id); }).catch(function () { /* 이미 끝났을 수 있다 */ });
        return;
      }
      Promise.resolve().then(function () { return api().get_result(service); }).then(function (r) {
        if (!r || isErr(r)) return;
        (r.servers || []).forEach(function (sv) { s.prev[sv.name] = sv.elapsed_sec; });
        renderPanel(s);
      }).catch(function () { /* 예상 시간은 없어도 된다 */ });
      poll(s);
    });
  }

  // ------------------------------------------------------------ 비밀번호 / 키파일 암호 모달

  // 비밀번호·키파일 암호 공용 입력 모달 → Promise<boolean> (true면 모두 전달됨).
  // opts: { title, notice, okLabel, emptyMsg, failMsg, rows: [labelNode...], extra(rowNodes) → node, plan }
  //   plan() → { idx: [보낼 입력 번호], send: function (i, value) → Promise<res> } (제출 순간에 한 번 부른다)
  // 전달 중(busy)에는 취소·✕·Esc가 아무것도 하지 않는다. 값은 제출 순간 지역 배열로 옮겨 두고,
  // 전달 중에는 DOM 입력을 다시 읽지 않는다. 끝나면 (성공·취소) 입력란을 비운다.
  function askSecrets(opts) {
    return new Promise(function (resolve) {
      var seq = ++formSeq;
      var inputs = [];
      var busyNow = false;
      var done = false;
      var msg = el("p", { class: "form-msg", role: "alert" });
      var rows = opts.rows.map(function (label, i) {
        var id = "napi-secret-" + seq + "-" + i;
        var inp = el("input", { id: id, class: "inp", type: "password", autocomplete: "off", spellcheck: "false" });
        inputs.push(inp);
        return el("div", { class: "pw-row" }, el("label", { for: id }, label), inp);
      });
      // 버튼은 모달 아래쪽(form 밖)에 있으므로 form 속성으로 묶는다 (Enter 제출도 된다).
      var formId = "napi-secret-form-" + seq;
      var ok = el("button", { type: "submit", class: "btn pri", form: formId }, opts.okLabel);
      var cancel = el("button", { type: "button", class: "btn" }, "취소");
      var form = el("form", { id: formId, autocomplete: "off" },
        el("p", { class: "notice" }, opts.notice), opts.extra ? opts.extra(rows) : null, rows);
      var handle;

      function finish(result) {
        if (done) return;
        done = true;
        inputs.forEach(function (inp) { inp.value = ""; });
        inputs = [];
        handle.close();
        resolve(result);
      }
      function busy(on) {
        busyNow = on;
        ok.disabled = on;
        cancel.disabled = on;
        var x = handle && handle.box.querySelector(".modal-x");
        if (x) x.disabled = on;
        form.querySelectorAll("input").forEach(function (inp) { inp.disabled = on; });
      }
      function dismiss() {
        if (busyNow) return; // 전달 중에는 닫지 않는다
        finish(false);
      }

      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (busyNow || done) return;
        var plan = opts.plan();
        for (var k = 0; k < plan.idx.length; k++) {
          if (!inputs[plan.idx[k]].value) {
            msg.textContent = opts.emptyMsg;
            inputs[plan.idx[k]].focus();
            return;
          }
        }
        var values = plan.idx.map(function (i) { return inputs[i].value; });
        msg.textContent = "";
        busy(true);
        var chain = Promise.resolve(null);
        plan.idx.forEach(function (i, k) {
          chain = chain.then(function (prevErr) {
            if (prevErr) return prevErr;
            return plan.send(i, values[k]).then(function (res) { return isErr(res) ? res.error : null; });
          });
        });
        chain.then(function (err) {
          values = null;
          busy(false);
          if (err) { msg.textContent = err; return; }
          finish(true);
        }, function () {
          values = null;
          busy(false);
          msg.textContent = opts.failMsg;
        });
      });
      cancel.addEventListener("click", dismiss);

      handle = app().openModal({
        title: opts.title, body: form, foot: [msg, cancel, ok], initialFocus: inputs[0],
        onDismiss: function () { dismiss(); return false; }
      });
    });
  }

  // entries: [{server, user, host, port}] → Promise<boolean>
  function askPasswords(service, entries) {
    var same = null;
    function extra(rows) {
      var sameId = "napi-pw-same-" + (++formSeq);
      same = el("input", { type: "checkbox", id: sameId });
      same.addEventListener("change", function () {
        for (var i = 1; i < rows.length; i++) rows[i].hidden = same.checked;
      });
      return el("label", { class: "check", for: sameId }, same, SAME_PW);
    }
    return askSecrets({
      title: service + " — 비밀번호 입력", notice: PW_NOTICE, okLabel: "스캔 시작",
      extra: entries.length > 1 ? extra : null,
      emptyMsg: "비밀번호를 입력해 주세요", failMsg: "비밀번호를 전달하지 못했어요",
      rows: entries.map(function (e) {
        return [e.server, el("small", { class: "muted" }, "  " + e.user + "@" + e.host + ":" + e.port)];
      }),
      plan: function () {
        var useSame = !!(same && same.checked);
        return {
          idx: useSame ? [0] : entries.map(function (_, i) { return i; }),
          send: function (i, value) {
            var e = entries[i];
            return api().set_password(e.user, e.host, e.port, value, useSame ? service : null);
          }
        };
      }
    });
  }

  // entries: [{server, key_file}] → Promise<boolean>
  function askPassphrases(service, entries) {
    return askSecrets({
      title: service + " — 키파일 암호 입력", notice: PP_NOTICE, okLabel: "다시 스캔",
      emptyMsg: "키파일 암호를 입력해 주세요", failMsg: "키파일 암호를 전달하지 못했어요",
      rows: entries.map(function (e) {
        return [e.server + " 키파일 암호", el("small", { class: "muted mono" }, "  " + e.key_file)];
      }),
      plan: function () {
        return {
          idx: entries.map(function (_, i) { return i; }),
          send: function (i, value) { return api().set_passphrase(entries[i].key_file, value); }
        };
      }
    });
  }

  // ------------------------------------------------------------ 호스트 키 확인

  function hostServerName(service, host, port) {
    var svc = serviceConfig(service);
    var hit = svc && (svc.servers || []).filter(function (sv) { return sv.host === host && Number(sv.port) === Number(port); })[0];
    return hit ? hit.name : null;
  }

  function syncPrompt(s, pending) {
    if (s.prompt && (!pending || pending.prompt_id !== s.prompt.id)) {
      // 기다리던 확인이 사라졌다 (시간 초과 또는 취소) → 모달을 닫는다.
      s.prompt.handle.close();
      s.prompt = null;
    }
    if (pending && !s.prompt && !s.answered[pending.prompt_id] && !s.finished) openHostkey(s, pending);
  }

  function openHostkey(s, pk) {
    var entry = { id: pk.prompt_id, handle: null };
    function answer(accept) {
      if (s.prompt !== entry) return;
      s.answered[pk.prompt_id] = true;
      s.prompt = null;
      entry.handle.close();
      api().answer_hostkey(s.id, accept, pk.prompt_id).then(function (res) {
        if (isErr(res)) toast(res.error);
      }, function () { toast("호스트 키 응답을 보내지 못했어요"); });
    }
    var name = hostServerName(s.service, pk.host, pk.port);
    var reject = el("button", { type: "button", class: "btn", onclick: function () { answer(false); } }, "거절");
    var accept = el("button", { type: "button", class: "btn pri", onclick: function () { answer(true); } }, "수락");
    var body = [
      el("p", null, HOSTKEY_TEXT),
      el("dl", { class: "fp-kv" },
        el("dt", null, "서버"), el("dd", null, (name ? name + " " : ""), el("span", { class: "mono" }, pk.host + ":" + pk.port)),
        el("dt", null, "키 종류"), el("dd", { class: "mono" }, pk.key_type),
        el("dt", null, "SHA256 지문"), el("dd", null, el("code", { class: "fp" }, pk.fingerprint))),
      el("p", { class: "muted small", style: "margin-top:10px" }, "서버 관리자에게 받은 지문과 다르면 거절해 주세요.")
    ];
    s.prompt = entry;
    entry.handle = app().openModal({
      title: "호스트 키 확인", body: body, foot: [reject, accept], initialFocus: reject,
      onDismiss: function () { answer(false); return false; }
    });
  }

  // ------------------------------------------------------------ 폴링

  // 진행 조회 실패와 응답 처리 중 예외(throw)를 똑같이 폴링 오류로 센다.
  // 어느 쪽이든 다음 폴링을 잡거나 MAX_POLL_ERRORS에서 끝내야 한다 (안 그러면 스캔 버튼이 계속 잠긴다).
  function poll(s) {
    if (s !== current || s.finished) return;
    Promise.resolve().then(function () {
      return api().get_scan_progress(s.id);
    }).then(function (p) {
      if (s !== current || s.finished) return;
      if (isErr(p)) { finish(s, "failed", p.error); return; }
      s.phase = p.phase;
      s.servers = p.servers || {};
      syncPrompt(s, p.hostkey_pending || null);
      if (TERMINAL[p.phase]) { finish(s, p.phase, p.error); return; }
      renderPanel(s);
      s.pollErrors = 0; // 처리까지 끝난 뒤에만 오류 수를 되돌린다
      s.timer = setTimeout(function () { poll(s); }, POLL_MS);
    }).catch(function () {
      if (s !== current || s.finished) return;
      s.pollErrors += 1;
      if (s.pollErrors >= MAX_POLL_ERRORS) { finish(s, "failed", "진행 상황을 받아 오지 못했어요"); return; }
      s.timer = setTimeout(function () { poll(s); }, POLL_MS);
    });
  }

  function finish(s, phase, error) {
    s.finished = true;
    s.phase = phase;
    s.endedAt = Date.now();
    clearTimeout(s.timer);
    syncLock();
    if (phase === "failed") s.error = error || "스캔하지 못했어요";
    try {
      syncPrompt(s, null);
      renderPanel(s);
    } catch (e) { /* 패널을 못 그려도 잠금은 이미 풀렸고, 결과 새로 고침은 계속한다 */ }
    if (phase !== "done") return;
    app().refresh().catch(function () { toast("결과를 다시 불러오지 못했어요"); });
    api().get_result(s.service).then(function (r) {
      if (!r || isErr(r)) return;
      (r.servers || []).forEach(function (sv) {
        if (!sv.ok && sv.error) s.reasons[sv.name] = sv.error;
        var skipped = R.skippedText(sv);
        if (sv.ok && skipped) s.skipped[sv.name] = skipped;
      });
      renderPanel(s);
    }, function () { /* 사유는 결과 화면의 신뢰도에서도 보인다 */ });
  }

  function cancel(s) {
    if (s.finished || s.cancelling) return;
    s.cancelling = true;
    renderPanel(s);
    api().cancel_scan(s.id).then(function (res) {
      if (isErr(res)) { s.cancelling = false; toast(res.error); renderPanel(s); }
    }, function () { s.cancelling = false; toast("취소하지 못했어요"); renderPanel(s); });
  }

  // ------------------------------------------------------------ 패널

  function showPanel(s) {
    var panel = $("scan-panel");
    clear(panel);
    s.catBox = el("div", { class: "scan-cat" });
    s.status = el("p", { class: "scan-status", role: "status" });
    s.prog = buildProgress();
    s.body = el("div");
    panel.appendChild(s.catBox);
    panel.appendChild(el("div", { class: "scan-body" }, s.status, s.body));
    panel.hidden = false;
    renderPanel(s);
  }

  function hidePanel() {
    var s = current;
    if (s && !s.finished) return;
    current = null;
    var panel = $("scan-panel");
    clear(panel);
    panel.hidden = true;
  }

  function statusText(s) {
    if (s.phase === "done") return "스캔을 마쳤어요";
    if (s.phase === "failed") return "스캔하지 못했어요. 이전 결과는 그대로예요";
    if (s.phase === "cancelled") return "스캔을 취소했어요. 이전 결과는 그대로예요";
    if (s.cancelling) return "취소하는 중이에요…";
    if (s.phase === "spec") return "명세를 읽고 있어요";
    return "서버에서 로그를 집계하고 있어요 (원본 로그는 가져오지 않아요)";
  }

  // 진행 막대·경과 시간은 고정 노드를 제자리에서 고친다 (매 폴링마다 패널 전체를 다시 그리면 취소 버튼 포커스가 사라진다).
  function buildProgress() {
    var p = { text: el("span", { class: "prog-text" }), pct: el("span", { class: "prog-pct" }), fill: el("i"), phase: el("p", { class: "prog-phase" }) };
    p.bar = el("div", { class: "prog-bar", role: "progressbar", "aria-label": "스캔 진행", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" }, p.fill);
    p.box = el("div", { class: "scan-prog" }, el("div", { class: "prog-row" }, p.text, p.pct), p.bar, p.phase);
    return p;
  }

  function setText(node, text) { if (node.textContent !== text) node.textContent = text; }

  // 지난 결과의 서버별 걸린 시간 중 가장 긴 것 (이번 스캔 서버만). 없으면 0.
  function prevMax(s, names) {
    var max = 0;
    (names.length ? names : Object.keys(s.prev)).forEach(function (n) {
      var v = Number(s.prev[n]);
      if (isFinite(v) && v > max) max = v;
    });
    return max;
  }

  function updateProgress(s) {
    var p = s.prog;
    var names = Object.keys(s.servers);
    var total = names.length;
    var completed = names.filter(function (n) { return TERMINAL[s.servers[n]]; }).length;
    var elapsedMs = (s.endedAt || Date.now()) - s.startedAt;
    var expect = prevMax(s, names);
    var frac = total ? completed / total : 0;
    var pct = null;
    if (s.phase === "done") {
      frac = 1;
      pct = "100%";
    } else {
      // 지난번 가장 오래 걸린 서버 시간 대비 경과 비율과 끝난 서버 비율 중 큰 쪽. 끝나기 전에는 99%를 넘지 않는다.
      if (!s.finished && expect > 0) frac = Math.max(frac, elapsedMs / 1000 / expect);
      frac = Math.min(0.99, Math.max(0, frac));
      if (!s.finished && expect > 0) pct = "예상 약 " + Math.round(frac * 100) + "%";
    }
    var now = String(Math.round(frac * 100));
    var progText = total ? "서버 " + completed + "/" + total + " 완료" : "서버 확인 중";
    setText(p.text, progText);
    setText(p.pct, pct || "");
    p.pct.hidden = !pct;
    p.fill.style.width = now + "%";
    if (p.bar.getAttribute("aria-valuenow") !== now) p.bar.setAttribute("aria-valuenow", now);
    p.bar.setAttribute("aria-valuetext", progText + (pct ? ", " + pct : ""));
    p.bar.classList.toggle("done", s.phase === "done");
    p.bar.classList.toggle("stopped", s.finished && s.phase !== "done");
    var phase = s.finished ? null : s.phase === "spec" ? "명세를 읽는 중" : "서버에서 집계 중";
    setText(p.phase, (phase ? phase + " · " : "") + elapsedText(elapsedMs));
  }

  function renderPanel(s) {
    if (s !== current || !s.body) return;
    updateProgress(s);
    var sig = JSON.stringify([s.phase, s.servers, s.prev, s.reasons, s.skipped, s.error, s.cancelling, s.finished]);
    if (sig === s.sig) return;
    s.sig = sig;

    var mode = s.finished ? "rest" : "scan";
    if (mode !== s.catMode) {
      s.catMode = mode;
      clear(s.catBox);
      s.catBox.appendChild(svgNode(root.NapiCat.full({ animated: true, scanning: mode === "scan" })));
    }
    var text = statusText(s);
    if (s.status.textContent !== text) s.status.textContent = text;

    var body = s.body;
    clear(body);
    body.appendChild(el("h3", null, s.service + (s.finished ? " 스캔" : " 스캔 중")));
    if (s.error) body.appendChild(el("p", { class: "scan-error", role: "alert" }, "⚠ " + s.error));
    body.appendChild(s.prog.box);

    var names = Object.keys(s.servers);
    if (names.length) {
      body.appendChild(el("ul", { class: "scan-servers" }, names.map(function (n) {
        var ph = s.servers[n];
        var eta = !TERMINAL[ph] && !s.finished ? minutesText(s.prev[n]) : null;
        return el("li", null,
          el("span", { class: "sv-name" }, n),
          el("span", { class: "ph ph-" + ph }, SERVER_PHASE[ph] || ph),
          eta ? el("span", { class: "sv-eta" }, eta) : null,
          ph === "failed" && s.reasons[n] ? el("span", { class: "sv-reason" }, s.reasons[n]) : null,
          s.skipped[n] ? el("span", { class: "sv-skipped" }, s.skipped[n]) : null);
      })));
    }

    var btns = el("div", { class: "btns", style: "justify-content:flex-start" });
    if (!s.finished) {
      btns.appendChild(el("button", { type: "button", class: "btn", disabled: s.cancelling,
        onclick: function () { cancel(s); } }, s.cancelling ? "취소하는 중…" : "취소"));
    } else {
      retryButtons(s).forEach(function (b) { btns.appendChild(b); });
      btns.appendChild(el("button", { type: "button", class: "btn", onclick: hidePanel }, "닫기"));
    }
    body.appendChild(btns);
  }

  // 끝난 뒤: 인증 실패 → 비밀번호 다시 입력, 키파일 암호 문제 → 암호 입력. 그다음 같은 기간으로 다시 스캔한다.
  function retryButtons(s) {
    if (s.phase !== "done") return [];
    var svc = serviceConfig(s.service);
    if (!svc) return [];
    var pw = [], pp = [], seenKey = {};
    (svc.servers || []).forEach(function (sv) {
      var reason = s.reasons[sv.name];
      if (!reason) return;
      if (sv.auth === "password" && reason === "인증 실패") {
        pw.push({ server: sv.name, user: sv.user, host: sv.host, port: sv.port });
      } else if (sv.auth === "key" && sv.key_file && reason.indexOf("키파일 암호") >= 0 && !seenKey[sv.key_file]) {
        seenKey[sv.key_file] = true;
        pp.push({ server: sv.name, key_file: sv.key_file });
      }
    });
    var out = [];
    if (pw.length) {
      out.push(el("button", { type: "button", class: "btn pri", onclick: function () {
        askPasswords(s.service, pw).then(function (ok) { if (ok) rescan(s); });
      } }, "비밀번호 다시 입력하고 스캔"));
    }
    if (pp.length) {
      out.push(el("button", { type: "button", class: "btn pri", onclick: function () {
        askPassphrases(s.service, pp).then(function (ok) { if (ok) rescan(s); });
      } }, "키파일 암호 입력하고 스캔"));
    }
    return out;
  }

  function rescan(s) {
    if (current === s) hidePanel();
    run(s.service, s.window);
  }

  root.NapiScan = { run: run, isRunning: isRunning };
})(window);

"""HTML 보고서: 브라우저로 여는 파일 하나 (CSS·스크립트 모두 안에 들어 있어 내부망에서도 그대로 열림).

구성: 요약 카드 → 점검 정보 → 서버별 결과 → IP별 결과(누르면 그 IP의 로그·AI 분석 펼침) → 분석 파일 목록 → 경고·오류
"""
import html
import os
from datetime import datetime

from .. import __version__
from .detector import ATTACK_TAGS, TAG_LABELS

GRADE_TEXT = {"CRITICAL": "CRITICAL", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW", "NONE": "미발견"}


def _e(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _t(t, with_date=True) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%m-%d %H:%M:%S") if t else ""


def _n(v) -> str:
    return f"{v:,}" if isinstance(v, int) else _e(v)


CSS = """
:root{--bg:#f4f6f8;--panel:#fff;--text:#1f2933;--sub:#6b7785;--line:#e3e7ec;--accent:#0f766e;--accent-l:#e6f2f1;
--crit-bg:#fde2e2;--crit:#991b1b;--high-bg:#ffedd5;--high:#9a3412;--med-bg:#fef6c7;--med:#854d0e;--low-bg:#eef1f4;--low:#374151;
--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--blue:#2563eb;--purple:#7c3aed;--row:#f8fafc;--code:#f3f5f7}
@media (prefers-color-scheme:dark){:root{--bg:#0f141a;--panel:#161c24;--text:#e6edf3;--sub:#93a1b0;--line:#283241;
--accent:#2dd4bf;--accent-l:#163634;--crit-bg:#4a1d1d;--crit:#fecaca;--high-bg:#4a2b14;--high:#fed7aa;--med-bg:#45390f;
--med:#fde68a;--low-bg:#2a3442;--low:#cbd5e1;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--blue:#60a5fa;--purple:#a78bfa;
--row:#1b2330;--code:#1b2330}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 "맑은 고딕","Malgun Gothic",system-ui,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:24px 20px 60px}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:8px;margin-bottom:18px}
h1{margin:0;font-size:24px;color:var(--accent)}
h2{font-size:17px;margin:30px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--accent);color:var(--accent)}
.meta{color:var(--sub);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-top:4px solid var(--c);border-radius:8px;padding:12px 14px}
.card .k{color:var(--sub);font-size:12px}.card .v{font-size:28px;font-weight:700;color:var(--c)}
.card .v small{font-size:13px;color:var(--sub);font-weight:400;margin-left:3px}
.note{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--warn);border-radius:6px;
padding:10px 14px;margin:14px 0 0;font-size:13px}
.note.bad{border-left-color:var(--bad)}
.tw{overflow-x:auto;border-radius:8px}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th{background:var(--accent);color:#fff;font-weight:600;font-size:13px;padding:8px 8px;text-align:left;white-space:nowrap;
position:sticky;top:0;z-index:1}
@media (prefers-color-scheme:dark){th{color:#062420}}
td{padding:7px 8px;border-top:1px solid var(--line);vertical-align:top;font-size:13px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
td.c,th.c{text-align:center}
.kv td:first-child{width:200px;background:var(--accent-l);font-weight:600}
.badge{display:inline-block;min-width:74px;text-align:center;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700}
.g-CRITICAL{background:var(--crit-bg);color:var(--crit)}.g-HIGH{background:var(--high-bg);color:var(--high)}
.g-MEDIUM{background:var(--med-bg);color:var(--med)}.g-LOW{background:var(--low-bg);color:var(--low)}
.g-NONE{background:transparent;color:var(--sub);border:1px solid var(--line)}
tr.ip{cursor:pointer}tr.ip:hover td{background:var(--accent-l)!important}
tr.ip.r-CRITICAL td{background:color-mix(in srgb,var(--crit-bg) 55%,var(--panel))}
tr.ip.r-HIGH td{background:color-mix(in srgb,var(--high-bg) 55%,var(--panel))}
tr.ip td.ipc{font-family:Consolas,monospace;font-weight:700;white-space:nowrap}
tr.ip td.ipc::before{content:"▸ ";color:var(--sub)}tr.ip.open td.ipc::before{content:"▾ "}
tr.detail{display:none}tr.detail.open{display:table-row}
tr.detail>td{background:var(--bg);padding:12px 14px 16px}
.ai{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--purple);border-radius:6px;
padding:10px 14px;margin-bottom:12px;white-space:pre-wrap}
.ai .h{font-weight:700;color:var(--purple);margin-bottom:4px;white-space:normal}
.ai .h span{font-weight:400;color:var(--sub);font-size:12px;margin-left:6px}
.logs{max-height:520px;overflow:auto;border-radius:6px}
.logs table{border-radius:0}.logs td{font-size:12px}
.logs td.url{font-family:Consolas,monospace;word-break:break-all;min-width:260px}
.raw{display:none;font-family:Consolas,monospace;font-size:11.5px;color:var(--sub);word-break:break-all;background:var(--code)}
body.showraw .raw{display:table-row}
.s2{color:var(--ok);font-weight:700}.s3{color:var(--blue);font-weight:700}.s4{color:var(--warn);font-weight:700}
.s5{color:var(--bad);font-weight:700}.atk{color:var(--bad);font-weight:700}.unp{color:var(--warn)}
.st-완료{color:var(--ok);font-weight:700}.st-부분{color:var(--warn);font-weight:700}.st-실패{color:var(--bad);font-weight:700}
.tools{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 10px}
.tools input[type=search]{padding:6px 10px;border:1px solid var(--line);border-radius:6px;min-width:220px;
background:var(--panel);color:var(--text)}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:999px;padding:4px 12px;
cursor:pointer;font:inherit;font-size:13px}
.chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
@media (prefers-color-scheme:dark){.chip.on{color:#062420}}
.tools label{font-size:13px;color:var(--sub);cursor:pointer}
.more{color:var(--sub);font-size:12px;padding:6px 2px}
details{margin-top:8px}summary{cursor:pointer;color:var(--sub);font-size:13px}
.mono{font-family:Consolas,monospace;word-break:break-all}
footer{margin-top:36px;color:var(--sub);font-size:12px;border-top:1px solid var(--line);padding-top:10px}
@media print{body{background:#fff}.tools{display:none}th{position:static}.logs{max-height:none;overflow:visible}
tr.detail{display:table-row}h2{break-after:avoid}tr{break-inside:avoid}}
"""

JS = """
(function(){
  var grade='all';
  function apply(){
    var q=document.getElementById('q').value.trim().toLowerCase();
    var hide=document.getElementById('hidenone').checked;
    document.querySelectorAll('tr.ip').forEach(function(r){
      var ok=(grade==='all'||r.dataset.g===grade)&&(!hide||r.dataset.g!=='NONE')&&(!q||r.dataset.s.indexOf(q)>=0);
      r.style.display=ok?'':'none';
      var d=document.getElementById(r.dataset.d); if(d&&!ok){d.classList.remove('open');r.classList.remove('open');}
    });
  }
  document.querySelectorAll('tr.ip').forEach(function(r){
    r.addEventListener('click',function(){var d=document.getElementById(r.dataset.d);if(!d)return;
      d.classList.toggle('open');r.classList.toggle('open');});
  });
  document.querySelectorAll('.chip[data-g]').forEach(function(c){
    c.addEventListener('click',function(){grade=c.dataset.g;
      document.querySelectorAll('.chip[data-g]').forEach(function(x){x.classList.toggle('on',x===c);});apply();});
  });
  document.getElementById('q').addEventListener('input',apply);
  document.getElementById('hidenone').addEventListener('change',apply);
  document.getElementById('raw').addEventListener('change',function(e){document.body.classList.toggle('showraw',e.target.checked);});
  document.getElementById('openall').addEventListener('click',function(){
    var rows=[].slice.call(document.querySelectorAll('tr.ip')).filter(function(r){return r.style.display!=='none'&&r.dataset.d;});
    var open=rows.some(function(r){return !r.classList.contains('open');});
    rows.forEach(function(r){r.classList.toggle('open',open);document.getElementById(r.dataset.d).classList.toggle('open',open);});
    this.textContent=open?'모두 접기':'모두 펼치기';
  });
})();
"""


def _status_class(st) -> str:
    return f"s{str(st)[0]}" if isinstance(st, int) and 200 <= st < 600 else ""


def _state_class(state) -> str:
    return "st-완료" if state == "완료" else "st-부분" if state == "부분 분석" else "st-실패"


def build_html(result, mask=False, ioc_result=None) -> str:
    from .report import _period, mask_query, status_text
    c = result.summary_counts()
    req = result.request
    out = []
    w = out.append
    w(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
      f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
      f"<title>IPtrace 점검 결과 {result.started:%Y-%m-%d %H:%M}</title><style>{CSS}</style></head><body><div class='wrap'>")
    w("<header><div><h1>침해의심 IP 로그 점검 결과</h1>"
      f"<div class='meta'>점검 {_t(result.started)} · 조회 기간 {_e(_period(result))} · 규칙 {_e(result.rule_version or '-')}"
      f" · IPtrace v{_e(__version__)}</div></div>"
      f"<div class='meta'>서버 원본 로그는 서버에 그대로 있고, 이 파일에는 의심 IP가 든 줄만 들어 있습니다.</div></header>")
    cards = (("점검한 IP", c["iocs"], "#475569"), ("로그 발견", c["accessed"], "var(--blue)"),
             ("조회 범위 내 미발견", c["not_accessed"], "var(--sub)"), ("위험 (CRITICAL·HIGH)", c["danger"], "var(--bad)"),
             ("404 있는 IP", c["with_404"], "var(--warn)"), ("500 있는 IP", c["with_500"], "var(--purple)"))
    w("<div class='cards'>" + "".join(f"<div class='card' style='--c:{col}'><div class='k'>{_e(k)}</div>"
                                      f"<div class='v'>{v:,}<small>개</small></div></div>" for k, v, col in cards) + "</div>")
    failed = result.failed_servers
    if failed:
        w("<div class='note bad'><b>확인하지 못한 서버</b>: " + ", ".join(f"{_e(s.name)} ({_e(s.reason)})" for s in failed)
          + " — 이 서버는 다시 점검이 필요합니다.</div>")
    if result.parse_errors:
        w(f"<div class='note'><b>해석 실패 {result.parse_errors:,}줄</b>: 로그 형식과 맞지 않는 줄이 있었습니다. "
          "의심 IP와 정확히 맞는 줄은 결과에 '해석 실패'로 넣었고, 나머지는 아래 '경고·오류'에 있습니다.</div>")
    if mask:
        w("<div class='note'>URL의 쿼리 값은 *** 로 가렸습니다.</div>")

    # ---------------------------------------------------------------- 점검 정보
    w("<h2>점검 정보</h2><table class='kv'><tbody>")
    for k, v in (("점검 일시", _t(result.started)), ("조회 기간", _period(result)), ("분석 범위", result.coverage_text()),
                 ("위험도별 IP", " · ".join(f"{GRADE_TEXT[g]} {n}" for g, n in c["grades"].items())),
                 ("규칙 버전", result.rule_version), ("입력 IP 목록 해시", req.ioc_text_hash)):
        w(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>")
    w("</tbody></table>")

    # ---------------------------------------------------------------- 서버별 결과
    w("<h2>서버별 결과</h2><table><thead><tr><th>서버</th><th>상태</th><th class='n'>파일</th>"
      "<th class='n'>서버에서 받은 줄</th><th class='n'>매칭 줄</th><th>비고</th></tr></thead><tbody>")
    for s in result.servers:
        note = []
        if s.ok:
            if s.parse_errors:
                note.append(f"해석 실패 {s.parse_errors:,}줄")
            if s.limited_files:
                note.append(f"끝까지 못 본 파일 {len(s.limited_files)}개 ({', '.join(sorted({f.limit for f in s.limited_files}))})")
        else:
            note.append(s.reason)
        w(f"<tr><td class='mono'>{_e(s.name)}</td><td class='{_state_class(s.state)}'>{_e(s.state)}</td>"
          f"<td class='n'>{_n(len(s.files)) if s.ok else '-'}</td><td class='n'>{_n(s.candidate_lines) if s.ok else '-'}</td>"
          f"<td class='n'>{_n(s.matched_lines) if s.ok else '-'}</td><td>{_e(', '.join(note))}</td></tr>")
    w("</tbody></table>")

    # ---------------------------------------------------------------- IP별 결과
    w("<h2>IP별 결과</h2><div class='tools'><input id='q' type='search' placeholder='IP·판정 근거 검색'>")
    w("<button class='chip on' data-g='all'>전체</button>" + "".join(
        f"<button class='chip' data-g='{g}'>{GRADE_TEXT[g]} {n}</button>" for g, n in c["grades"].items() if n))
    w("<label><input type='checkbox' id='hidenone'> 미발견 숨기기</label>"
      "<label><input type='checkbox' id='raw'> 로그 원문 줄 보기</label>"
      "<button class='chip' id='openall'>모두 펼치기</button></div>")
    w("<table><thead><tr><th>위험도</th><th>의심 IP</th><th class='n'>요청</th><th class='n'>2xx</th><th class='n'>3xx</th>"
      "<th class='n'>4xx</th><th class='n'>404</th><th class='n'>5xx</th><th class='n'>500</th><th>판정 근거</th>"
      "<th>서버별 요청</th><th>최초 · 최종</th></tr></thead><tbody>")
    recs_by_ip = {}
    for r in sorted(result.records, key=lambda x: (x.time is None, x.time)):
        recs_by_ip.setdefault(r.ioc_key, []).append(r)
    for i, s in enumerate(result.summaries):
        g = s.grade if s.accessed else "NONE"
        did = f"d{i}" if s.accessed else ""
        search = " ".join([s.ioc_key, s.verdict, *s.matched_ips]).lower()
        servers = ", ".join(f"{k} {v:,}" for k, v in s.server_counts.items())
        seen = f"{_t(s.first_seen)}<br>{_t(s.last_seen)}" if s.first_seen else ""
        ipcell = _e(s.ioc_key) + (f"<div class='meta'>발견 IP {_e(', '.join(s.matched_ips[:5]))}</div>"
                                  if s.is_range and s.matched_ips else "")
        w(f"<tr class='ip r-{g}' data-g='{g}' data-d='{did}' data-s='{_e(search)}'>"
          f"<td><span class='badge g-{g}'>{GRADE_TEXT[g]}</span></td><td class='ipc'>{ipcell}</td>"
          + "".join(f"<td class='n'>{_n(v)}</td>" for v in (s.requests, s.c2xx, s.c3xx, s.c4xx, s.c404, s.c5xx, s.c500))
          + f"<td>{_e(s.verdict)}{(' · ' + _e(s.confidence)) if s.confidence else ''}</td><td>{_e(servers)}</td>"
          f"<td class='meta'>{seen}</td></tr>")
        if not s.accessed:
            continue
        w(f"<tr class='detail' id='{did}'><td colspan='12'>")
        note = result.ai_notes.get(s.ioc_key)
        if note:
            w(f"<div class='ai'><div class='h'>AI 분석 (참고)<span>{_e(note.model or '기본 모델')} · "
              f"{_t(note.time)} · {note.lines_sent}/{note.lines_total}줄 보냄 · "
              f"민감값 {'가림' if note.masked else '안 가림'} · 위험도는 규칙 판정을 따름</span></div>{_e(note.text)}</div>")
        w(f"<div class='meta' style='margin-bottom:6px'>{_e(status_text(s))}"
          + (f" · User-Agent: {_e(' | '.join(s.user_agents[:3]))}" if s.user_agents else "") + "</div>")
        w("<div class='logs'><table><thead><tr><th>시각</th><th>서버</th><th>원본 위치</th><th class='c'>메서드</th>"
          "<th>URL</th><th class='c'>응답</th><th class='n'>크기</th><th>태그</th><th>User-Agent</th></tr></thead><tbody>")
        for r in recs_by_ip.get(s.ioc_key, []):
            url = mask_query(r.url) if mask else r.url
            raw = r.raw.replace(r.url, url) if mask else r.raw
            tags = ", ".join(TAG_LABELS.get(t, t) for t in r.tags)
            tcls = "atk" if any(t in ATTACK_TAGS for t in r.tags) else ("unp" if r.unparsed else "")
            when = _t(r.time) or ("시간 모름" if r.unparsed else "")
            via = " (XFF)" if r.matched_field == "xff" else ""
            w(f"<tr><td style='white-space:nowrap'>{_e(when)}</td><td class='mono'>{_e(r.server)}</td>"
              f"<td class='mono'>{_e(os.path.basename(r.file))}:{r.line_no}{_e(via)}</td><td class='c'>{_e(r.method)}</td>"
              f"<td class='url'>{_e(url)}</td><td class='c {_status_class(r.status)}'>{_e(r.status or '-')}</td>"
              f"<td class='n'>{_n(r.bytes)}</td><td class='{tcls}'>{_e(tags)}</td><td>{_e(r.user_agent)}</td></tr>"
              f"<tr class='raw'><td colspan='9'>{_e(raw)}</td></tr>")
        w("</tbody></table></div></td></tr>")
    w("</tbody></table>")

    # ---------------------------------------------------------------- 분석 파일 목록 / 경고·오류
    files = [(s, f) for s in result.servers for f in s.files]
    w(f"<h2>분석 파일 목록</h2><details><summary>파일 {len(files):,}개 보기</summary><table><thead><tr><th>서버</th>"
      "<th>파일</th><th class='n'>크기</th><th>수정 시각</th><th class='n'>서버에서 받은 줄</th><th>끝까지 확인</th>"
      "<th>SHA256</th></tr></thead><tbody>")
    for s, f in files:
        w(f"<tr><td class='mono'>{_e(s.name)}</td><td class='mono'>{_e(f.path)}</td><td class='n'>{_n(f.size)}</td>"
          f"<td>{datetime.fromtimestamp(f.mtime):%Y-%m-%d %H:%M:%S}</td>"
          f"<td class='n'>{_n(f.matched) if s.server_filter else '-'}</td>"
          f"<td class='{'st-부분' if f.limit else ''}'>{_e(f.limit or '예')}</td><td class='mono'>{_e(f.sha256)}</td></tr>")
    w("</tbody></table></details>")
    warns = [("수집 실패", f"{s.name}: {s.reason}", "bad") for s in failed]
    if ioc_result:
        warns += [("제외된 IP", f"{ln}번째 줄 {v}: {why}", "") for ln, v, why in ioc_result.excluded]
        warns += [("입력 오류", f"{ln}번째 줄: {tok}", "bad") for ln, _, tok in ioc_result.errors]
    for s in result.servers:
        for f, ln, text, has_ip in s.parse_error_lines[:20]:
            warns.append(("해석 실패 줄", f"{s.name} {f}:{ln}{' [의심 IP 포함]' if has_ip else ''}  {text[:300]}", ""))
    w(f"<h2>경고·오류</h2><details{' open' if warns else ''}><summary>{len(warns)}건</summary>")
    if warns:
        w("<table><thead><tr><th>구분</th><th>내용</th></tr></thead><tbody>" + "".join(
            f"<tr><td class='{'st-실패' if k == 'bad' else 'st-부분'}' style='white-space:nowrap'>{_e(a)}</td>"
            f"<td class='mono'>{_e(b)}</td></tr>" for a, b, k in warns) + "</tbody></table>")
    w("</details>")
    w("<footer>참고: 접근로그에는 POST 본문이 남지 않아 POST 공격은 경로·빈도·응답코드로만 추정합니다. "
      "위험도는 규칙(rules/signatures.json) 기준이며 AI 분석은 참고용입니다. "
      f"IP 행을 누르면 그 IP의 로그가 펼쳐집니다. 인쇄하면 모든 로그가 펼쳐진 상태로 나옵니다.</footer>")
    w(f"</div><script>{JS}</script></body></html>")
    # 표가 창보다 넓으면 표만 가로로 스크롤 (페이지 전체가 옆으로 밀리지 않게)
    return "".join(out).replace("<table", "<div class='tw'><table").replace("</table>", "</table></div>")


def save_html(result, path, mask=False, ioc_result=None) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_html(result, mask, ioc_result))
    result.saved_path = str(path)
    return str(path)

"""AI 분석: 보낼 내용 만들기, 민감값 가리기, API 방식별 호출 (가짜 게이트웨이), 키 암호화."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import kst
from iptrace.core import ai
from iptrace.core.models import IpSummary, LogRecord


def rec(path="/", status=200, query="", tags=(), t=None, ua="Mozilla/5.0", raw="", method="GET"):
    return LogRecord(server="M-T-WEBAPIPROD01-api", file="/data/webserver/APP/log/access.log", line_no=1,
                     raw=raw or f"203.0.113.45 - - [..] \"{method} {path}\" {status} 10", remote_ip="203.0.113.45",
                     xff=[], time=t or kst(2026, 10, 21, 10, 0), method=method, path=path, query=query,
                     protocol="HTTP/1.1", status=status, bytes=10, referer="-", user_agent=ua, ioc_key="203.0.113.45",
                     client_ip="203.0.113.45", matched_field="remote", tags=list(tags))


def summary(n=3):
    return IpSummary(ioc_key="203.0.113.45", original="203.0.113.45", is_range=False, requests=n, c2xx=1, c4xx=n - 1,
                     c404=n - 1, first_seen=kst(2026, 10, 21, 10, 0), last_seen=kst(2026, 10, 21, 10, 5),
                     grade="HIGH", reasons=["경로 조작 공격 200 응답 (성공 가능)"],
                     server_counts={"M-T-WEBAPIPROD01-api": n}, user_agents=["sqlmap/1.7"])


# ------------------------------------------------------------------ 가리기
def test_mask_keeps_attack_strings_but_hides_secrets():
    m = ai.mask_text
    assert m("/a?token=abcdef123456&q=1") == "/a?token=***&q=1"
    assert m("/login;jsessionid=ABCDEF0123?id=x&pw=hello123") == "/login;jsessionid=***?id=x&pw=***"
    assert m("/x?key=../../etc/passwd") == "/x?key=../../etc/passwd"           # 공격 문자열은 근거라 남김
    assert m("/s?code=%27%20or%201=1--") == "/s?code=%27%20or%201=1--"
    assert m("Authorization: Bearer abcdefghijkl") == "Authorization: Bearer ***"
    assert m("901010-1234567 010-1234-5678 a.b@c.co.kr 1234-5678-9012-3456") == \
        "******-******* 010-****-**** ***@*** ****-****-****-****"
    assert m("eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4") == "***JWT***"
    assert m("203.0.113.45 GET /index.html") == "203.0.113.45 GET /index.html"   # IP는 남김


# ------------------------------------------------------------------ 보낼 내용
def test_prompt_groups_same_requests_and_puts_attacks_first():
    recs = [rec("/missing", 404, t=kst(2026, 10, 21, 10, 0, i)) for i in range(30)]
    recs += [rec("/a", 404, t=kst(2026, 10, 21, 10, 1, i)) for i in range(5)]
    recs.append(rec("/../../etc/passwd", 200, tags=["TRAVERSAL"], t=kst(2026, 10, 21, 10, 5)))
    recs.append(rec("/login", 200, query="id=a&pw=secret99", t=kst(2026, 10, 21, 10, 6)))
    p = ai.build_prompt(summary(len(recs)), recs, max_lines=2)
    assert p.groups_sent == 2 and p.lines_total == 37
    assert "/../../etc/passwd" in p.user and "경로 조작" in p.user           # 공격 태그 + 200 이 먼저
    assert "x30" in p.user and p.lines_sent == 31                            # 다음은 많이 반복된 요청 (30줄을 한 줄로)
    assert "/login" not in p.user and "나머지 6줄은 생략" in p.user
    p2 = ai.build_prompt(summary(len(recs)), recs, max_lines=10)
    assert "pw=***" in p2.user and "secret99" not in p2.user
    p3 = ai.build_prompt(summary(len(recs)), recs, max_lines=10, mask=False)
    assert "secret99" in p3.user
    assert "규칙 판정: HIGH" in p.user and "sqlmap" in p.user


def test_prompt_includes_unparsed_raw_lines():
    r = rec(raw="[2026-10-23T02:22:37] [CLIENT(203.0.113.45)] [E] GET /containers/json HTTP/1.1", tags=["UNPARSED"])
    p = ai.build_prompt(summary(1), [r])
    assert "형식 해석 실패 원문" in p.user and "/containers/json" in p.user


def test_endpoint_rules():
    assert ai.endpoint({"api": "openai", "url": "https://gw.corp/v1"}) == "https://gw.corp/v1/chat/completions"
    assert ai.endpoint({"api": "openai", "url": "http://h:8000"}) == "http://h:8000/v1/chat/completions"
    assert ai.endpoint({"api": "openai", "url": "https://gw.corp/llm/chat"}) == "https://gw.corp/llm/chat"
    assert ai.endpoint({"api": "anthropic", "url": "https://gw.corp/claude/v1"}) == "https://gw.corp/claude/v1/messages"
    for bad in ("", "gw.corp/v1"):
        with pytest.raises(ai.AiError):
            ai.endpoint({"api": "openai", "url": bad})


# ------------------------------------------------------------------ 가짜 게이트웨이
class Gateway(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Gateway.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path == "/slow":
            time.sleep(1.5)
        if self.path == "/v1/chat/completions":
            if self.headers.get("Authorization") != "Bearer k1":
                return self._send(401, {"error": "invalid key"})
            return self._send(200, {"choices": [{"message": {"content": "<think>생각</think>\n[요약] 자동 스캔"}}]})
        if self.path == "/v1/messages":
            if self.headers.get("x-api-key") != "ka" or not self.headers.get("anthropic-version"):
                return self._send(401, {"type": "error", "error": {"type": "authentication_error"}})
            if "max_tokens" not in body or "temperature" in body or body["messages"][0]["role"] != "user":
                return self._send(400, {"type": "error", "error": {"type": "invalid_request_error"}})
            if "거절" in body["messages"][0]["content"]:
                return self._send(200, {"content": [], "stop_reason": "refusal",
                                        "stop_details": {"type": "refusal", "category": "cyber"}})
            return self._send(200, {"content": [{"type": "thinking", "thinking": ""},
                                                {"type": "text", "text": "[요약] Claude 답"}],
                                    "stop_reason": "end_turn"})
        if self.path == "/gw/chat":       # 헤더를 더 요구하는 게이트웨이 (OpenAI 호환)
            if self.headers.get("x-api-key") != "k2" or self.headers.get("x-client") != "iptrace":
                return self._send(403, {"error": "forbidden"})
            if body.get("model") == "empty":
                return self._send(200, {"result": "형식이 다름"})
            return self._send(200, {"choices": [{"text": "게이트웨이 답 " + body["model"]}]})
        if self.path == "/notjson":
            self.send_response(200)
            self.end_headers()
            return self.wfile.write(b"<html>gateway login</html>")
        self._send(404, {"error": "no route"})

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def gateway():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Gateway.seen = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def cfg_ai(url, **kw):
    from iptrace.core import config as cfgmod
    a = cfgmod.normalize({})["ai"]
    a.update(url=url, enabled=True, **kw)
    return a


def test_openai_call_strips_think_and_sends_key(gateway):
    a = cfg_ai(gateway + "/v1", model="qwen")
    note = ai.analyze(a, "k1", summary(), [rec("/../../etc/passwd", 200, tags=["TRAVERSAL"])])
    assert note.text == "[요약] 자동 스캔" and note.model == "qwen" and note.lines_sent == 1
    path, headers, body = Gateway.seen[-1]
    assert body["model"] == "qwen" and body["messages"][0]["role"] == "system"
    assert "/../../etc/passwd" in body["messages"][1]["content"] and "[system]" in note.prompt
    with pytest.raises(ai.AiError, match="인증 실패"):
        ai.analyze(a, "wrong", summary(), [rec()])
    assert "연결 성공" in ai.test_connection(a, "k1")


def test_anthropic_mode(gateway):
    a = cfg_ai(gateway + "/v1", api="anthropic", key_header="x-api-key", key_prefix="")
    assert ai.endpoint(a) == gateway + "/v1/messages"
    sent = len(Gateway.seen)
    with pytest.raises(ai.AiError, match="모델 이름이 꼭 있어야"):              # 기본 모델은 없다
        ai.analyze(a, "ka", summary(), [rec()])
    assert len(Gateway.seen) == sent                                         # 보내지도 않는다
    a["model"] = "claude-opus-5-5"
    note = ai.analyze(a, "ka", summary(), [rec("/../../etc/passwd", 200, tags=["TRAVERSAL"])])
    assert note.text == "[요약] Claude 답"                                   # thinking 블록은 빼고 text만
    path, headers, body = Gateway.seen[-1]
    assert body["model"] == "claude-opus-5-5" and body["system"] == ai.SYSTEM_PROMPT
    assert headers["anthropic-version"] == ai.ANTHROPIC_VERSION and "fallbacks" not in body
    with pytest.raises(ai.AiError, match="거절했습니다.*cyber"):
        ai.analyze(a, "ka", summary(), [rec("/거절")])
    fb = dict(a, anthropic_fallback=True, model="claude-sonnet-5-5")
    ai.analyze(fb, "ka", summary(), [rec()])
    path, headers, body = Gateway.seen[-1]
    assert body["fallbacks"] == "default" and headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert body["model"] == "claude-sonnet-5-5"
    with pytest.raises(ai.AiError, match="인증 실패"):
        ai.analyze(a, "wrong", summary(), [rec()])


def test_gateway_key_header_and_extra_headers(gateway):
    c = cfg_ai(gateway + "/gw/chat", model="m9", key_header="x-api-key", key_prefix="", extra_headers="x-client: iptrace")
    assert ai.analyze(c, "k2", summary(), [rec()]).text == "게이트웨이 답 m9"
    assert Gateway.seen[-1][2]["stream"] is False
    with pytest.raises(ai.AiError, match="권한 없음"):
        ai.analyze(dict(c, extra_headers=""), "k2", summary(), [rec()])
    with pytest.raises(ai.AiError, match="답을 찾지 못했습니다"):
        ai.analyze(dict(c, model="empty"), "k2", summary(), [rec()])


def test_old_ai_modes_fall_back_to_openai():
    from iptrace.core import config as cfgmod
    c = cfgmod.normalize({"ai": {"api": "custom", "custom_body": "{}", "custom_answer": "a"}})
    assert c["ai"]["api"] == "openai" and "custom_body" not in c["ai"] and not cfgmod.validate(c)


def test_errors_are_explained(gateway):
    with pytest.raises(ai.AiError, match="주소를 찾을 수 없음"):
        ai.analyze(cfg_ai(gateway + "/nope"), "k1", summary(), [rec()])
    with pytest.raises(ai.AiError, match="JSON이 아닙니다"):
        ai.analyze(cfg_ai(gateway + "/notjson"), "k1", summary(), [rec()])
    with pytest.raises(ai.AiError, match="시간 초과"):
        ai.analyze(cfg_ai(gateway + "/slow", timeout=0.5), "k1", summary(), [rec()])
    with pytest.raises(ai.AiError, match="접속 실패"):
        ai.analyze(cfg_ai("http://127.0.0.1:9"), "k1", summary(), [rec()])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
def test_saved_key_roundtrip():
    blob = ai.protect("비밀-key-123")
    assert "비밀" not in blob and ai.unprotect(blob) == "비밀-key-123"
    with pytest.raises(ai.AiError):
        ai.unprotect("aGVsbG8=")

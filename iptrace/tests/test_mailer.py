"""이메일 보내기: 연동 규격(POST /email/send)대로 요청을 만들고, 응답 코드·처리 상태를 판단한다 (가짜 메일 서버)."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import kst
from iptrace.core import mailer


class FakeMailApi(BaseHTTPRequestHandler):
    """사내 메일 API 흉내. 토큰이 맞으면 규격의 응답을 돌려준다."""
    seen = []
    mode = "ok"          # ok / dcode / badcode

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        FakeMailApi.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        if self.path != "/api/email/send":
            return self._send(404, {"error": "not found"})
        if self.headers.get("Authorization") != "t-123":
            return self._send(401, {"resCode": "9001", "resMessage": "인증 실패"})
        if self.headers.get("appServiceSeq") != "S001" or self.headers.get("apiVersion") != "1.0":
            return self._send(400, {"resCode": "9002", "resMessage": "공통 헤더 오류"})
        status = {"ok": "O", "dcode": "D", "badcode": "O"}[FakeMailApi.mode]
        code = "1001" if FakeMailApi.mode == "badcode" else 0          # 실제 API는 성공을 숫자 0으로 준다
        rd = {k: body.get(k, "") for k in ("msgFrom", "msgTo", "msgCc", "msgBcc", "ordDay", "ordTime", "ordNo",
                                           "param1", "param2")}
        rd.update(transStatus=status, trDay="20261123", trTime="101500", trNo="TR0001")
        self._send(200, {"resCode": code, "resMessage": "정상" if code == 0 else "수신자 오류", "resData": rd})

    def _send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def noti():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeMailApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeMailApi.seen, FakeMailApi.mode = [], "ok"
    yield f"http://127.0.0.1:{srv.server_address[1]}/api"
    srv.shutdown()


def cfg_em(url="https://mail.corp", **kw):
    from iptrace.core import config as cfgmod
    em = cfgmod.normalize({})["email"]
    em.update(url=url, enabled=True, sender="secu@corp.co.kr;보안팀", service_seq="S001", **kw)
    return em


def test_endpoint_and_addresses():
    assert mailer.endpoint({"url": "https://mail.corp"}) == "https://mail.corp/email/send"
    assert mailer.endpoint({"url": "https://mail.corp/gw/"}) == "https://mail.corp/gw/email/send"
    assert mailer.endpoint({"url": "https://mail.corp/email/send"}) == "https://mail.corp/email/send"
    with pytest.raises(mailer.MailError, match="http"):
        mailer.endpoint({"url": "mail.corp"})
    assert mailer.split_addrs("a@x.com, b@x.com;c@x.com\nA@x.com") == ["a@x.com", "b@x.com", "c@x.com"]
    assert mailer.sender_addr("secu@corp.co.kr;보안팀") == "secu@corp.co.kr"
    with pytest.raises(mailer.MailError, match="받는 사람 주소"):
        mailer.check_addrs(["a@x.com", "not-mail"], "받는 사람")


def test_request_follows_spec_and_masks_title_and_contents():
    now = kst(2026, 11, 23, 9, 5, 7)
    body = mailer.build_request(cfg_em(), ["a@x.com", "b@x.com"], ["c@x.com"], "결과 010-1234-5678",
                                "토큰 /a?token=abcdef123456 주민 901010-1234567",
                                [("IPtrace_결과.html", "<html>보고서</html>".encode("utf-8"))], now=now)
    assert body["msgFrom"] == "secu@corp.co.kr;보안팀" and body["msgTo"] == "a@x.com,b@x.com" and body["msgCc"] == "c@x.com"
    assert "msgBcc" not in body and body["contentType"] == "text/plain"
    assert (body["ordDay"], body["ordTime"]) == ("20261123", "090507") and body["ordNo"].startswith("IPTRACE20261123090507")
    assert body["title"] == "결과 010-****-****"                              # 규격: 제목·내용 개인정보 마스킹 필수
    assert "abcdef123456" not in body["contents"] and "1234567" not in body["contents"]
    f = body["fileList"][0]
    assert (f["fileName"], f["fileEncoding"]) == ("IPtrace_결과.html", "UTF-8")
    assert base64.b64decode(f["fileData"]).decode("utf-8") == "<html>보고서</html>"
    assert "fileList" not in mailer.build_request(cfg_em(), ["a@x.com"], [], "t", "c", now=now)
    assert mailer.build_request(cfg_em(), ["a@x.com"], [], "t", "c", now=now)["ordNo"] != body["ordNo"]


def test_request_limits():
    em = cfg_em()
    with pytest.raises(mailer.MailError, match="받는 사람을 넣으세요"):
        mailer.build_request(em, [], [], "t", "c")
    with pytest.raises(mailer.MailError, match="발신자"):
        mailer.build_request(dict(em, sender=""), ["a@x.com"], [], "t", "c")
    with pytest.raises(mailer.MailError, match="10MB"):
        mailer.build_request(em, ["a@x.com"], [], "t", "c", [("big.html", b"x" * (mailer.MAX_FILE + 1))])
    with pytest.raises(mailer.MailError, match="20MB"):
        mailer.build_request(em, ["a@x.com"], [], "t", "c", [(f"{i}.html", b"x" * (8 * 1024 * 1024)) for i in range(3)])
    many = [f"user{i:03d}@example.co.kr" for i in range(60)]
    with pytest.raises(mailer.MailError, match="msgTo가 너무"):
        mailer.build_request(em, many, [], "t", "c")


def test_send_success_and_failures(noti):
    em = cfg_em(noti, extra_headers="x-client: iptrace")
    body = mailer.build_request(em, ["a@x.com"], [], "제목", "본문", [("r.html", b"<html/>")])
    r = mailer.send(em, "t-123", body)
    path, headers, sent = FakeMailApi.seen[-1]
    assert path == "/api/email/send" and headers["authorization"] == "t-123" and headers["x-client"] == "iptrace"
    assert headers["appserviceseq"] == "S001" and headers["apiversion"] == "1.0"          # 규격 공통 헤더
    assert sent["fileList"][0]["fileName"] == "r.html"
    assert (r.status, r.tr_no, r.res_code, r.ord_no) == ("O", "TR0001", "0", body["ordNo"])
    assert mailer.send(dict(em, success_code="0000"), "t-123", body).status == "O"   # 0 = 0000 (숫자는 값으로 비교)
    assert mailer.same_code("0", "0000") and mailer.same_code(0, "0") and not mailer.same_code("1001", "0")
    with pytest.raises(mailer.MailError, match="인증 실패"):
        mailer.send(em, "wrong", body)
    FakeMailApi.mode = "dcode"
    with pytest.raises(mailer.MailError, match="처리 상태 D: 사업자측 오류"):
        mailer.send(em, "t-123", body)
    FakeMailApi.mode = "badcode"
    with pytest.raises(mailer.MailError, match="응답 코드 1001: 수신자 오류"):
        mailer.send(em, "t-123", body)
    assert mailer.send(dict(em, success_code=""), "t-123", body).status == "O"   # 성공 코드를 비우면 처리 상태만 본다
    with pytest.raises(mailer.MailError, match="서비스코드"):
        mailer.send(dict(em, service_seq=""), "t-123", body)
    with pytest.raises(mailer.MailError, match="HTTP 400"):
        mailer.send(dict(em, service_seq="WRONG"), "t-123", body)
    with pytest.raises(mailer.MailError, match="주소를 찾을 수 없음"):
        mailer.send(dict(em, url=noti + "/wrong/email/send"), "t-123", body)
    with pytest.raises(mailer.MailError, match="접속 실패"):
        mailer.send(dict(em, url="http://127.0.0.1:1/email/send", timeout=5), "t-123", body)


def test_config_validates_email_section():
    from iptrace.core import config as cfgmod
    c = cfgmod.normalize({})
    assert c["email"]["enabled"] is False and c["email"]["success_code"] == "0" and not cfgmod.validate(c)
    assert c["email"]["url"] == "https://internal.mailapi.com" and c["email"]["to"] == "security@foo.bar"
    assert c["email"]["token_prefix"] == ""                          # 규격: Authorization = 발급받은 토큰
    c["email"].update(enabled=True, url="mail.corp", sender="보안팀")
    errs = " ".join(cfgmod.validate(c))
    assert "발송 주소" in errs and "발신자 주소" in errs and "서비스코드" in errs
    c["email"].update(url="https://mail.corp", sender="secu@corp.co.kr;보안팀", service_seq="S001")
    assert not cfgmod.validate(c)
    # 이메일을 켜면 발신자·받는 사람이 비었거나 형식이 틀리면 저장하지 못한다
    c["email"].update(sender="", to="")
    errs = " ".join(cfgmod.validate(c))
    assert "발신자를 넣으세요" in errs and "기본 받는 사람을 넣으세요" in errs
    c["email"].update(sender="secu@corp.co.kr", to="a@x.com; 이상한주소", cc="b@x")
    errs = " ".join(cfgmod.validate(c))
    assert "기본 받는 사람 주소 형식" in errs and "이상한주소" in errs and "기본 참조 주소 형식" in errs
    c["email"].update(enabled=False)
    assert not cfgmod.validate(c)                                    # 꺼 두면 검사하지 않음

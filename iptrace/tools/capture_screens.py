"""문서용 화면 이미지 만들기 (개발 PC 전용).

실제 프로그램 화면을 띄워 예시 데이터로 점검을 실행하고 screens\\ 폴더에 캡처한다.
원격 서버는 테스트용 가짜 SSH 서버(tests/test_ssh_mock.py)를 써서 '서버에서 거르기' 흐름을 그대로 보여준다.

    .venv\\Scripts\\python tools\\capture_screens.py
"""
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = ROOT / "screens"

from iptrace import paths  # noqa: E402

TMP = Path(tempfile.mkdtemp())
for k, v in (("BASE", TMP), ("CONFIG_DIR", TMP / "config"), ("CONFIG_FILE", TMP / "config" / "config.json"),
             ("RULES_DIR", TMP / "rules"), ("LOGS_DIR", TMP / "logs")):
    setattr(paths, k, v)
(TMP / "rules").mkdir()
shutil.copy(ROOT / "rules" / "signatures.json", TMP / "rules" / "signatures.json")

import paramiko  # noqa: E402
import test_ssh_mock  # noqa: E402
from test_ssh_mock import FakeServer  # noqa: E402

# 예시 로그(8~10월)를 한 주(10-15 ~ 10-21) 안으로 옮긴다. 서버 점검은 최대 7일까지만 조회한다
_sample = (ROOT / "tests" / "fixtures" / "access_sample.log").read_text(encoding="utf-8")
for _old, _new in (("02/Aug/2026", "15/Oct/2026"), ("11/Sep/2026", "17/Oct/2026"), ("03/Oct/2026", "19/Oct/2026"),
                   ("04/Oct/2026", "20/Oct/2026"), ("05/Oct/2026", "21/Oct/2026")):
    _sample = _sample.replace(_old, _new)
(TMP / "access_sample.log").write_text(_sample, encoding="utf-8")
test_ssh_mock.LOG = str(TMP / "access_sample.log")
from winshot import capture_png  # noqa: E402

# 화면 예시는 api/fe 계정을 쓰므로 가짜 서버가 계정과 상관없이 비밀번호 'pw'를 받게 한다
FakeServer.check_auth_password = lambda self, u, p: paramiko.AUTH_SUCCESSFUL if p == "pw" else paramiko.AUTH_FAILED

from iptrace.core import config as cfgmod, history  # noqa: E402
from iptrace.gui import dialogs, main_window, settings_page, theme  # noqa: E402


def fake_ssh():
    key = paramiko.RSAKey.generate(2048)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)

    def serve():
        while True:
            client, _ = sock.accept()
            t = paramiko.Transport(client)
            t.add_server_key(key)
            t.start_server(server=FakeServer([]))

    threading.Thread(target=serve, daemon=True).start()
    return sock.getsockname()[1]


XFF = '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" "%{X-Forwarded-For}i"'
p1, p2 = fake_ssh(), fake_ssh()
cfg = cfgmod.normalize({
    "general": {"own_networks": ["10.0.0.0/8", "198.51.100.80/32"], "trusted_proxies": ["10.1.1.10"]},
    "detect": {"scan_404_threshold": 3},
    "ssh": {"user": "logviewer", "connect_timeout": 2, "workers": 3},
    "log_formats": {"webtob_xff": XFF},
    "servers": [
        {"name": "web01-api", "host": "127.0.0.1", "port": p1, "user": "api", "format": "webtob_xff",
         "logs": [{"path": "/data/webserver/APP/log/*_ssl/access*.log*", "format": "webtob_xff"},
                  {"path": "/data/webserver/APP/log/*_ssl/error*.log*", "format": "webtob_error"}]},
        {"name": "web01-fe", "host": "127.0.0.1", "port": p2, "user": "fe", "format": "webtob_xff",
         "log_paths": ["/data/webserver/WEB/log/*_ssl/*.log*"]},
        {"name": "web03", "host": "127.0.0.1", "port": 1, "format": "webtob_xff",
         "log_paths": ["/data/webserver/APP/log/*_ssl/*.log*"]},
    ],
})
cfg["ai"].update(enabled=True, url="https://ai-gw.corp.local/v1/chat/completions", model="gpt-oss-120b")
cfg["email"].update(enabled=True, url="https://mail-api.corp.local", sender="secu@corp.local;보안팀", service_seq="S0001",
                    to="soc@corp.local; cert@corp.local", cc="infra@corp.local")
cfgmod.save(cfg, paths.CONFIG_FILE)

dialogs.DoneDialog.show = lambda self: self.close()
main_window.DoneDialog.show = dialogs.DoneDialog.show

root = tk.Tk()
theme.setup(root, "light")
mw = main_window.MainWindow(root, cfg)
mw.ask_host_key = lambda *a: True
mw.worker.ask_host_key = lambda *a: True
mw.passwords = {cfgmod.password_key(cfg, s): "pw" for s in cfg["servers"]}
root.geometry("1280x820+10+10")


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def cap(name, win=None):
    pump(0.5)
    capture_png(win or root, str(OUT / f"{name}.png"))
    print("captured", name)


for old in OUT.glob("*.png"):
    old.unlink()

mw.goto("check")
mw.txt.insert("1.0", (ROOT / "tests" / "fixtures" / "ioc_typed.list").read_text(encoding="utf-8"))
mw.period.set("custom")
mw.custom_start.set("2026-10-15 00:00")
mw.custom_end.set("2026-10-21 23:59")
mw.render("empty")
cap("01_로그점검_처음")

# 조회 기간 [직접]의 달력
import datetime as _dt  # noqa: E402
from iptrace.gui.widgets import DatePicker  # noqa: E402
dp = DatePicker(root, _dt.date(2026, 10, 21), lambda d: None)
cap("01b_조회기간_달력", dp.win)
dp.win.grab_release()
dp.win.destroy()

# 점검 중 화면 (진행 상태를 고정해서 캡처)
mw.progress = {"web01-api": {"pct": 60, "state": "run", "text": "access_20261021.log (3/5)", "matched": 9},
               "web01-fe": {"pct": 100, "state": "done", "text": "파일 5개, 매칭 12줄", "matched": 12},
               "web03": {"pct": 100, "state": "fail", "text": "실패: 접속 실패: timed out", "matched": 0}}
mw.run_started = time.time() - 95
mw.run_meta = "IP 6개 · 2026-10-15 00:00 ~ 2026-10-21 23:59 · 서버 3대"
mw.render("running")
cap("02_로그점검_점검중")
mw.render("empty")

mw.start_check()
t0 = time.time()
while mw.state != "result" and time.time() - t0 < 60:
    pump(0.1)
print("state:", mw.state)
mw.tree.selection_set("198.51.100.7")
pump(0.3)
cap("03_로그점검_결과")
mw.status_filter.set("404")
mw.fill_logs()
mw.set_theme("dark")
mw.tree.selection_set("203.0.113.45")
cap("04_로그점검_결과_어두운테마")
mw.set_theme("light")

# 점검 이력: 지난 점검 하나를 '확인 완료'로 검토해 둔다
history.update_review(mw.result.history_id, "조치 필요", "203.0.113.45 웹셸 업로드 의심. 서버 담당자에게 x.jsp 파일 확인 요청 (10/22).")
mw.goto("history")
cap("05_점검이력")

mw.goto("settings")
page = mw.page_obj
page.test_result = {"name": "web01-api", "ok": True, "text": "✔ 접속   ✔ 로그 폴더   ✔ 파일 8개   ✖ 형식 안 맞는 경로 1개",
                    "paths": [{"path": "/data/webserver/APP/log/*_ssl/access*.log*", "format": "webtob_xff", "files": 5,
                               "fmt_ok": (0, 5), "detect": [("COMBINED+xff", XFF, 5, 5)]},
                              {"path": "/data/webserver/APP/log/*_ssl/error*.log*", "format": "webtob_error", "files": 3,
                               "fmt_ok": (5, 5), "detect": []}]}
page.render_test()
cap("06_서버설정_서버")
mw.set_theme("dark")
mw.goto("settings")
cap("06b_서버설정_서버_어두운테마")
mw.set_theme("light")
mw.goto("settings")
page = mw.page_obj
page.page.set("rule")
page.show()
cap("07_서버설정_판정기준")
page.page.set("general")
page.show()
cap("08_서버설정_일반")
page.page.set("ai")
page.show()
cap("13_서버설정_AI분석")
page.page.set("email")
page.show()
cap("16_서버설정_이메일")

# 로그 형식 편집 (업무 로그 예시를 넣고 샘플로 시험)
from iptrace.core.parser import EXAMPLES  # noqa: E402
page.page.set("server")
page.show()
fd = settings_page.FormatDialog(page, "")
fd.use_example(EXAMPLES[2][0], EXAMPLES[2][2])
fd.sample.insert("1.0", "[2026-10-21 13:00:00] [INFO] login ok user=kim ip=203.0.113.45\n"
                        "[2026-10-21 13:00:05] [WARN] login fail user=admin ip=198.51.100.7, agent=curl\n")
fd.test()
cap("12_서버설정_로그형식_편집", fd.win)
fd.win.destroy()


def dlg(d, name, size):
    d.win.geometry(size + "+60+60")
    d.win.deiconify()
    cap(name, d.win)
    d.win.destroy()


dlg(dialogs.PasswordDialog(root, [(cfgmod.password_key(cfg, s), f"{s['name']} ({cfgmod.effective_ssh(cfg, s)['user']})")
                                for s in cfg["servers"]]), "09_대화상자_SSH비밀번호", "520x270")
dlg(dialogs.HostKeyDialog(root, "web03", "10.2.0.13", "ssh-ed25519", "SHA256:Xb3kq0Lr7vN2Tz9fQ..."), "10_대화상자_호스트키", "560x300")
d = dialogs.DoneDialog.__new__(dialogs.DoneDialog)
dialogs.DoneDialog.__init__(d, root, mw.result)
dlg(d, "11_대화상자_점검완료", "560x360")
import datetime as _dt2  # noqa: E402
from iptrace.core.ai import AiNote  # noqa: E402
note = AiNote("198.51.100.7", "[요약] 짧은 시간에 관리자 페이지와 설정 파일 경로를 차례로 요청한 자동 스캔으로 보입니다.\n\n"
              "[공격 의도] 취약점 탐색. sqlmap User-Agent와 /admin, /.env, /WEB-INF/web.xml 요청이 이어집니다.\n\n"
              "[성공 가능성] 중간. 2026-10-21 13:02:11 GET /WEB-INF/web.xml 이 200(3,412B)으로 응답해 설정 파일이 노출됐을 수 있습니다. "
              "나머지는 404입니다.\n\n"
              "[추가 확인] web.xml에 DB 접속 정보가 있는지, 같은 시간대 WAS 로그에 같은 IP의 오류가 있는지 확인하세요.\n\n"
              "[권고 조치] 해당 IP 차단, WEB-INF 경로 외부 접근 차단 설정 점검, 노출된 계정 비밀번호 변경.",
              "gpt-oss-120b", _dt2.datetime(2026, 10, 27, 14, 5), 21, 21, 9, True, "[system]\n...\n\n[user]\n...", 6.2)
dlg(dialogs.AiDialog(root, note, "HIGH"), "14_대화상자_AI분석", "860x620")
d = dialogs.ProgressDialog(root, "보고서 저장", "AI 분석 후 저장 · IP 5개",
                           "다 받으면 IPtrace_20261027_1405.html 파일로 저장합니다. IP 하나에 최대 120초 걸릴 수 있습니다.",
                           5, lambda: None, stop_text="중지 (받은 것까지 저장)")
d.step(3, "198.51.100.7 분석 중…")
d.clock.configure(text="경과 0:42")
dlg(d, "15_대화상자_AI분석후저장", "540x250")
from iptrace.core import report as _report  # noqa: E402
_name = _report.attachment_filename(mw.result, "[IPtrace]")
dlg(dialogs.EmailDialog(root, "soc@corp.local; cert@corp.local", "infra@corp.local",
                        _report.email_title(mw.result, "[IPtrace]"), _report.MEMO,
                        lambda memo, nm: _report.email_text(mw.result, memo, nm), _name, "86KB"),
    "17_대화상자_이메일보내기", "800x640")
dlg(dialogs.AskDialog(root, "시험 메일 보내기", "시험 메일을 보낼 주소를 넣으세요",
                      [("sender", "발신자", "secu@corp.local;보안팀", "'주소;이름' 가능 (예: secu@회사.co.kr;보안팀)"),
                       ("to", "받는 사람", "soc@corp.local; cert@corp.local", "여러 명은 , 또는 ; 로 구분")],
                      note="발송 주소: mail-api.corp.local", ok_text="보내기"), "18_대화상자_시험메일", "580x300")
mw.unsaved = False
root.destroy()
shutil.rmtree(TMP, ignore_errors=True)
print("done")

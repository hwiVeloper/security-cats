"""[서버 설정] 탭에서 서버를 추가하고 [저장]하는 흐름 (화면 포함)."""
import json
import traceback

import pytest

from iptrace import paths
from iptrace.core import config as cfgmod

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app(tmp_path, monkeypatch):
    for k, v in (("BASE", tmp_path), ("CONFIG_DIR", tmp_path / "config"),
                 ("CONFIG_FILE", tmp_path / "config" / "config.json")):
        monkeypatch.setattr(paths, k, v)
    from iptrace.gui import main_window, settings_page, theme
    warnings = []
    monkeypatch.setattr(settings_page.messagebox, "showwarning", lambda *a, **k: warnings.append(a))
    cfgmod.save(cfgmod.normalize({}), paths.CONFIG_FILE)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("화면을 띄울 수 없는 환경")
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *a: errors.append("".join(traceback.format_exception(*a)))
    theme.setup(root, "light")
    mw = main_window.MainWindow(root, cfgmod.load(paths.CONFIG_FILE))
    yield root, mw, errors, warnings
    root.destroy()


def fill(page, **values):
    for k, v in values.items():
        e = page.form[f"srv.{k}"]
        e.delete(0, "end")
        e.insert(0, v)


def test_add_servers_and_save(app):
    root, mw, errors, warnings = app
    mw.goto("settings")
    page = mw.page_obj
    page.add_server()
    fill(page, host="10.2.0.11", user="api")
    page.add_account()
    fill(page, name="web01-fe", user="fe")
    page.log_rows[0][0].delete(0, "end")
    page.log_rows[0][0].insert(0, "/data/webserver/WEB/log/*_ssl/*.log*")
    page.save()  # 저장 후 화면을 다시 만들 때 지난 페이지의 입력칸을 읽다가 오류가 나던 문제
    root.update()
    assert not errors and not warnings, errors or warnings
    saved = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))["servers"]
    assert [(s["name"], s["user"], s["log_paths"]) for s in saved] == [
        ("web01", "api", ["/data/webserver/APP/log/*_ssl/*.log*"]),
        ("web01-fe", "fe", ["/data/webserver/WEB/log/*_ssl/*.log*"]),
    ]
    assert "저장했습니다" in mw.page_obj.lbl_msg.cget("text")
    # 저장한 뒤 다른 탭으로 갔다가 돌아와도 오류가 없어야 한다
    mw.goto("check")
    mw.goto("settings")
    root.update()
    assert not errors, errors


def test_start_button_explains_why_disabled(app):
    """사설 대역 IP만 넣으면 [점검 시작]이 꺼지고 이유가 보이며, '사설·자사 대역 IP도 점검'을 켜면 켜진다."""
    root, mw, errors, _ = app
    mw.goto("settings")
    page = mw.page_obj
    page.add_server()
    fill(page, host="10.2.0.11", user="api")
    page.save()
    mw.goto("check")

    def typed(text):
        mw.txt.delete("1.0", "end")
        mw.txt.insert("1.0", text)
        mw.validate()
        root.update()
        return mw.btn_start.instate(["disabled"]), mw.lbl_start_hint.cget("text")

    off, why = typed("")
    assert off and "IP를 입력" in why
    off, why = typed("10.1.2.3\n192.168.0.10")
    assert off and "2개가 모두 사설·자사 대역" in why
    mw.include_private.set(True)
    off, why = typed("10.1.2.3\n192.168.0.10")
    assert not off and why == "" and len(mw.ioc_res.entries) == 2
    mw.include_private.set(False)
    off, why = typed("203.0.113.45")
    assert not off
    mw.server_vars["web01"].set(False)
    mw.refresh_start_state()
    assert mw.btn_start.instate(["disabled"]) and "서버를 하나 이상" in mw.lbl_start_hint.cget("text")
    assert not errors, errors


def test_general_page_saves_load_limits(app):
    """[서버 설정] > 일반의 서버 부하 항목(최대 줄 수, 시간 제한, 증적 해시)이 저장된다."""
    root, mw, errors, warnings = app
    mw.goto("settings")
    page = mw.page_obj
    page.add_server()
    fill(page, host="10.2.0.11", user="api")
    page.page.set("general")
    page.show()
    for k, v in (("max_lines_per_file", "50000"), ("server_timeout", "900")):
        page.form[k].delete(0, "end")
        page.form[k].insert(0, v)
    assert page.form["evidence_hash"].get() is False   # 기본은 꺼짐
    page.form["evidence_hash"].set(True)
    page.save()
    root.update()
    assert not errors and not warnings, errors or warnings
    ssh = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))["ssh"]
    assert (ssh["max_lines_per_file"], ssh["server_timeout"], ssh["evidence_hash"]) == (50000, 900, True)


def test_left_panel_scrolls_when_it_overflows(app):
    """서버가 많거나 창이 작아 왼쪽 1·2·3 구역이 넘치면 스크롤바가 생기고, 넉넉하면 없다."""
    root, mw, errors, _ = app
    root.deiconify()
    mw.cfg["servers"] = [dict(cfgmod.SERVER_DEFAULTS, name=f"web{i:02d}", host=f"10.2.0.{i}") for i in range(1, 13)]
    mw.period.set("custom")
    mw.goto("check")
    root.geometry("1280x700")
    for _ in range(5):
        root.update()
    sb = mw.ioc_tools.master.scroll_bar
    assert sb.winfo_ismapped()
    mw.cfg["servers"] = mw.cfg["servers"][:1]
    mw.period.set("7")
    root.geometry("1280x1000")
    mw.goto("check")
    for _ in range(5):
        root.update()
    assert not mw.ioc_tools.master.scroll_bar.winfo_ismapped()
    assert not errors, errors


def test_failed_password_is_not_reused(app, monkeypatch):
    """틀린 비밀번호는 다시 쓰지 않고, 같은 비밀번호를 다시 넣으면 접속하지 않고 알린다."""
    from iptrace.gui import main_window
    root, mw, errors, _ = app
    srv = dict(cfgmod.SERVER_DEFAULTS, name="web01", host="10.2.0.11", user="api")
    key = cfgmod.password_key(mw.cfg, srv)
    mw.passwords[key] = "wrong"
    assert mw.pw_for(srv) == "wrong"
    mw.mark_auth_failed(srv, "wrong")
    assert mw.pw_for(srv) == "" and key not in mw.passwords
    mw.passwords[key] = "wrong"                      # [서버 설정] 칸에 틀린 값이 남아 다시 들어와도
    assert mw.pw_for(srv) == ""                      # 쓰지 않는다
    shown = []
    monkeypatch.setattr(main_window.messagebox, "showwarning", lambda *a, **k: shown.append(a[1]))
    monkeypatch.setattr(main_window.PasswordDialog, "show", lambda self: {key: "wrong"})
    assert mw.ask_passwords([srv]) is False and "방금 인증에 실패한 비밀번호" in shown[-1]
    monkeypatch.setattr(main_window.PasswordDialog, "show", lambda self: {key: ""})
    assert mw.ask_passwords([srv]) is False and "비밀번호를 입력" in shown[-1]
    monkeypatch.setattr(main_window.PasswordDialog, "show", lambda self: {key: "new-pw"})
    assert mw.ask_passwords([srv]) is True and mw.pw_for(srv) == "new-pw"
    mw.goto("check")
    root.update()
    assert not hasattr(mw, "shared_pw")              # [로그 점검] 공통 비밀번호 칸은 없앴다
    assert not errors, errors



def test_popups_open_centered_on_main_window(app):
    """대화상자와 형식 편집 창이 화면 왼쪽 위가 아니라 메인 창 가운데에 뜬다."""
    from iptrace.gui import dialogs, settings_page
    root, mw, errors, _ = app
    root.deiconify()
    root.geometry("1280x820+300+100")
    root.update()
    cx, cy = root.winfo_rootx() + root.winfo_width() // 2, root.winfo_rooty() + root.winfo_height() // 2

    def center_of(win):
        win.update()
        return win.winfo_rootx() + win.winfo_width() // 2, win.winfo_rooty() + win.winfo_height() // 2

    d = dialogs.ListDialog(root, "목록", "목록", [("a", 100)], [("x",)])
    root.after(300, lambda: (got.append(center_of(d.win)), d.close()))
    got = []
    d.show()
    mw.goto("settings")
    fd = settings_page.FormatDialog(mw.page_obj, "")
    got.append(center_of(fd.win))
    fd.win.destroy()
    for x, y in got:
        assert abs(x - cx) < 40 and abs(y - cy) < 60, (got, (cx, cy))
    assert not errors, errors


def test_format_dialog_adds_new_format(app):
    """[+ 새 형식]: 예시를 넣고 시험한 뒤 저장하면 서버의 로그 형식 목록에 추가된다."""
    from iptrace.core.parser import EXAMPLES
    from iptrace.gui import settings_page
    root, mw, errors, warnings = app
    mw.goto("settings")
    page = mw.page_obj
    page.add_server()
    fd = settings_page.FormatDialog(page, "")
    key, _, fmt = EXAMPLES[2]
    fd.use_example(key, fmt)
    fd.sample.insert("1.0", "[2026-10-21 13:00:00] [INFO] login ok user=kim ip=203.0.113.45\n")
    fd.test()
    assert "1/1줄 일치" in fd.out.cget("text") and "IP=203.0.113.45" in fd.out.cget("text")
    fd.save()
    assert page.cfg["log_formats"][key] == fmt and page.log_rows[0][1].get() == key
    assert not errors and not warnings, errors or warnings


def test_add_account_keeps_full_name(app):
    """[+ 같은 서버에 계정 추가]는 이름 전체 뒤에 번호를 붙인다 (M-web01-api -> M-web01-api-2, 그다음 -3)."""
    root, mw, errors, _ = app
    mw.goto("settings")
    page = mw.page_obj
    page.add_server()
    fill(page, name="M-web01-api", host="10.2.0.11", user="api")
    page.add_account()
    assert page.cfg["servers"][page.cur_server]["name"] == "M-web01-api-2"
    page.add_account()   # 번호가 붙은 항목에서 다시 추가하면 다음 번호
    assert [s["name"] for s in page.cfg["servers"]] == ["M-web01-api", "M-web01-api-2", "M-web01-api-3"]
    assert page.cfg["servers"][page.cur_server]["host"] == "10.2.0.11"
    assert not errors, errors


def test_select_all_servers_toggle(app):
    """[로그 점검] 대상 서버의 [전체 해제]/[전체 선택]."""
    root, mw, errors, _ = app
    mw.cfg["servers"] = [dict(cfgmod.SERVER_DEFAULTS, name=f"web{i}", host=f"10.2.0.{i}") for i in range(1, 4)]
    mw.goto("check")
    mw.txt.insert("1.0", "203.0.113.45")
    mw.validate()
    root.update()
    assert mw.btn_all.cget("text") == "전체 해제" and len(mw.selected_servers()) == 3
    mw.toggle_all_servers()
    assert mw.selected_servers() == [] and mw.btn_all.cget("text") == "전체 선택"
    assert mw.btn_start.instate(["disabled"]) and "서버를 하나 이상" in mw.lbl_start_hint.cget("text")
    mw.server_vars["web2"].set(True)
    mw.on_server_check()
    assert mw.btn_all.cget("text") == "전체 선택"           # 일부만 선택 -> 누르면 전체 선택
    mw.toggle_all_servers()
    assert len(mw.selected_servers()) == 3 and mw.btn_all.cget("text") == "전체 해제"
    assert not mw.btn_start.instate(["disabled"])
    assert not errors, errors



def test_parse_failure_warning_and_long_names(app, tmp_path, monkeypatch):
    """해석 실패가 있으면 결과 위에 경고와 [읽지 못한 줄 보기]가 나오고, 긴 서버 이름은 한 줄에 하나씩 보인다."""
    import os
    import time
    from conftest import FIX
    from iptrace.gui import main_window
    root, mw, errors, _ = app
    names = ["M-T-WEBAPI01", "M-T-WEBAPI02", "M-T-WASAPP01"]
    mw.cfg["servers"] = [dict(cfgmod.SERVER_DEFAULTS, name=n, host=f"10.2.0.{i}") for i, n in enumerate(names, 1)]
    mw.goto("check")
    root.update()
    cells = [w for w in mw.ioc_tools.master.winfo_children() if isinstance(w, tk.Frame)]
    cols = {int(c.grid_info()["column"]) for g in cells for c in g.winfo_children() if c.grid_info()}
    assert cols == {0}                                    # 이름이 길면 한 줄에 하나
    for n in names[:2]:
        os.makedirs(tmp_path / "logs" / n)
        open(tmp_path / "logs" / n / "access.log", "w", encoding="utf-8").write(
            open(os.path.join(FIX, "access_sample.log"), encoding="utf-8").read() + "broken 203.0.113.45 line\n")
    mw.cfg["log_formats"]["x"] = '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" "%{X-Forwarded-For}i"'
    mw.cfg["general"]["local_log_format"] = "x"
    mw.local_folder = str(tmp_path / "logs")
    mw.txt.insert("1.0", "203.0.113.45")
    mw.period.set("custom")
    mw.custom_start.set("2026-01-01 00:00")
    mw.custom_end.set("2026-12-31 23:59")
    monkeypatch.setattr(main_window.DoneDialog, "show", lambda self: self.close())
    monkeypatch.setattr(main_window.messagebox, "showerror", lambda *a, **k: errors.append(a))   # 막히지 않게
    import shutil
    (tmp_path / "rules").mkdir(exist_ok=True)   # 공격 규칙 파일 (기준 폴더가 tmp_path로 바뀌어 있다)
    shutil.copy(os.path.join(os.path.dirname(FIX), "..", "rules", "signatures.json"), tmp_path / "rules" / "signatures.json")
    mw.validate()
    mw.start_check()
    t0 = time.time()
    while mw.state != "result" and time.time() - t0 < 30:
        root.update()
        time.sleep(0.05)
    assert sum(s.parse_errors for s in mw.result.servers) >= 2
    shown = {}
    monkeypatch.setattr(main_window.ListDialog, "__init__",
                        lambda self, parent, title, heading, columns, rows, **kw: shown.update(rows=rows, kw=kw))
    monkeypatch.setattr(main_window.ListDialog, "show", lambda self: None)
    mw.show_parse_errors()
    assert any(r[0] == "M-T-WEBAPI01" and "broken 203.0.113.45" in r[2] and r[3] == "bad" for r in shown["rows"])
    assert "broken 203.0.113.45 line" in shown["kw"]["copy_text"]
    assert not errors, errors


def test_log_paths_with_their_own_formats(app):
    """로그 위치 표: 경로마다 형식을 고르고 저장하면 서버에 [경로, 형식]으로 남는다 (접근 로그 + 에러 로그)."""
    root, mw, errors, warnings = app
    mw.goto("settings")
    page = mw.page_obj
    page.add_server()
    fill(page, host="10.2.0.11", user="api")
    assert len(page.log_rows) == 1 and page.log_rows[0][1].get() == "webtob_combined"
    page.log_rows[0][0].delete(0, "end")
    page.log_rows[0][0].insert(0, "/data/webserver/APP/log/*_ssl/access*.log*")
    e, c, _ = page._add_log_row("/data/webserver/APP/log/*_ssl/error*.log*", None)
    assert c.get() == "webtob_combined"           # 새 줄은 위 줄 형식으로 시작
    c.set("webtob_error")
    page._add_log_row("", None)                    # 빈 줄은 저장하지 않는다
    page.save()
    root.update()
    assert not errors and not warnings, errors or warnings
    s = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))["servers"][0]
    assert [(x["path"], x["format"]) for x in s["logs"]] == [
        ("/data/webserver/APP/log/*_ssl/access*.log*", "webtob_combined"),
        ("/data/webserver/APP/log/*_ssl/error*.log*", "webtob_error")]
    assert s["log_paths"] == [x["path"] for x in s["logs"]]
    # 다시 열면 표에 그대로 나온다
    mw.goto("settings")
    page = mw.page_obj
    assert [(r[0].get(), r[1].get()) for r in page.log_rows] == [(x["path"], x["format"]) for x in s["logs"]]
    page._remove_log_row(page.log_rows[1])
    assert len(page.log_rows) == 1
    assert not errors, errors


def test_long_server_names_are_not_cut(app):
    """20자 서버 이름: 비밀번호 창 이름 칸, 진행 막대 이름, 서버 상태 칸(넘치면 요약), 목록 창 칸이 이름 길이에 맞는다."""
    import time
    import tkinter.font as tkfont
    from iptrace.core.models import ServerStatus
    from iptrace.gui import dialogs
    from iptrace.gui.theme import F
    root, mw, errors, _ = app
    names = ["M-T-WEBAPIPROD01-api", "M-T-WEBAPIPROD01-fe", "M-T-WASAPPPROD02-err"]
    # 비밀번호 창: 이름 칸이 글자 폭만큼 넓어진다
    pd = dialogs.PasswordDialog(root, [(n, f"{n} · 계정 api") for n in names])
    pd.same.set(False)
    pd._layout()
    root.update()
    labels = [w for w in pd.box.winfo_children() if isinstance(w, tk.Label)]
    fnt = tkfont.Font(family=F["ui"], size=9)
    assert len(labels) == 3 and all(lb.winfo_reqwidth() >= fnt.measure(lb.cget("text")) for lb in labels)
    pd.win.destroy()
    # 진행 막대: 이름 칸 너비(글자 수)가 가장 긴 이름 이상
    mw.goto("check")
    mw.progress = {n: {"pct": 50, "state": "run", "text": "", "matched": 0} for n in names}
    mw.run_started = time.time()
    mw.run_meta = ""
    mw.render("running")
    root.update()
    widths = {int(w.cget("width")) for row in mw.body.winfo_children() for w in _all(row)
              if isinstance(w, tk.Label) and w.cget("text") in names}
    assert widths and min(widths) >= max(len(n) for n in names)
    mw.render("empty")
    # 서버 상태 칸: 긴 이름 3대는 한 줄에 안 들어가 요약으로
    sts = [ServerStatus(name=n) for n in names[:2]] + [ServerStatus(name=names[2], ok=False, reason="접속 실패: timed out")]
    assert not mw._chips_fit(sts)
    assert mw._chips_fit([ServerStatus(name="web01"), ServerStatus(name="web02")])
    assert not errors, errors


def _all(w):
    for c in w.winfo_children():
        yield c
        yield from _all(c)


def test_progress_text_does_not_repeat_server_name(app):
    from iptrace.core.engine import Event
    root, mw, errors, _ = app
    name = "M-T-WEBAPIPROD01-api"
    mw.on_event(Event("server_done", name, matched=3, message=f"{name} 3줄 · 파일 2개"))
    assert mw.progress[name]["text"] == "3줄 · 파일 2개"
    assert not errors, errors


def test_custom_period_prefill_tidy_and_calendar(app, monkeypatch):
    from datetime import date, datetime
    from iptrace.core import engine
    from iptrace.gui import main_window, widgets
    root, mw, errors, _ = app
    mw.goto("check")
    today = datetime.now(engine.KST)
    # [7일]을 보다가 [직접]으로 바꾸면 그 기간(시작 00:00 ~ 오늘 23:59)으로 채운다
    mw.period.set("7")
    mw._on_period_pick("7")
    mw.period.set("custom")
    mw._on_period_pick("custom")
    s7, _ = engine.period_range("7")
    assert mw.custom_start.get() == f"{s7:%Y-%m-%d} 00:00"
    assert mw.custom_end.get() == f"{today:%Y-%m-%d} 23:59"
    s, e = mw.period_range()
    assert (e.hour, e.minute, e.second) == (23, 59, 59)
    # 짧게 쓴 입력은 칸을 떠날 때 정리된다
    mw.custom_start.set("어제")
    mw._tidy_when(mw.custom_start, False)
    assert mw.custom_start.get() == (today - engine.timedelta(days=1)).strftime("%Y-%m-%d 00:00")
    mw.custom_start.set("엉터리")
    mw._tidy_when(mw.custom_start, False)
    assert mw.custom_start.get() == "엉터리"            # 못 읽으면 그대로, 기간 줄에 빨간 안내
    # 달력에서 고르면 날짜만 바뀌고 시각은 유지
    mw.custom_end.set("2026-10-21 18:30")
    opened = []
    monkeypatch.setattr(widgets, "DatePicker", lambda anchor, cur, cb: opened.append((cur, cb)))
    mw._open_calendar(mw.body, mw.custom_end, True)
    cur, cb = opened[0]
    assert cur == date(2026, 10, 21)
    cb(date(2026, 10, 19))
    assert mw.custom_end.get() == "2026-10-19 18:30"
    # 서버 점검은 최대 7일: 넘으면 기간 줄에 빨간 안내, 로컬 폴더면 그대로 받는다
    mw.custom_start.set("2026-10-01 00:00")
    mw.update_period()
    assert "최대 7일" in mw.lbl_range.cget("text")
    assert [k for k, _ in main_window.PERIOD_ITEMS] == ["today", "yesterday", "7", "custom"]
    mw.local_folder = "C:/logs"
    assert mw.period_range()[0].day == 1
    mw.local_folder = ""
    assert not errors, errors


def test_date_picker_moves_and_picks(app):
    from datetime import date
    from iptrace.gui.widgets import DatePicker
    root, mw, errors, _ = app
    root.deiconify()
    got = []
    dp = DatePicker(root, date(2026, 1, 15), got.append)
    dp.move(-1)
    assert (dp.year, dp.month) == (2025, 12)
    dp.move(2)
    assert (dp.year, dp.month) == (2026, 2)
    dp.pick(date(2026, 2, 3))
    assert got == [date(2026, 2, 3)] and not dp.win.winfo_exists()
    assert not errors, errors


def test_ai_settings_tab_saves_and_encrypts_key(app):
    import sys
    from iptrace.core import ai
    root, mw, errors, warnings = app
    mw.goto("settings")
    page = mw.page_obj
    page.page.set("ai")
    page.show()
    root.update()
    f = page.form
    f["ai.enabled"].set(True)
    f["ai.api"].set("anthropic")
    page._ai_api_changed("anthropic")
    assert (f["ai.key_header"].get(), f["ai.key_prefix"].get()) == ("x-api-key", "")   # 방식에 맞춰 헤더 기본값
    for k, v in (("ai.url", "https://ai-gw.corp/llm/v2/ask"), ("ai.model", "claude-opus-5-5"),
                 ("ai.max_lines", "40"), ("ai.key", "k-123")):
        f[k].delete(0, "end")
        f[k].insert(0, v)
    f["ai.extra_headers"].insert("1.0", "x-client: iptrace")
    f["ai.save_key"].set(sys.platform == "win32")
    page.save()
    root.update()
    assert not errors and not warnings, errors or warnings
    saved = json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))["ai"]
    assert (saved["enabled"], saved["api"], saved["url"], saved["key_header"], saved["key_prefix"], saved["max_lines"],
            saved["extra_headers"]) == (True, "anthropic", "https://ai-gw.corp/llm/v2/ask", "x-api-key", "", 40,
                                        "x-client: iptrace")
    assert "k-123" not in paths.CONFIG_FILE.read_text(encoding="utf-8")     # 키는 평문으로 남지 않는다
    if sys.platform == "win32":
        assert ai.unprotect(saved["key_saved"]) == "k-123"
    assert mw.ai_key == "k-123"
    # Anthropic은 기본 모델이 없으므로 모델 이름을 비우면 저장 전에 알려 준다
    page = mw.page_obj
    page.form["ai.model"].delete(0, "end")
    page.save()
    assert warnings and "모델 이름" in warnings[-1][1]
    page.form["ai.model"].insert(0, "claude-opus-5-5")
    # 잘못된 주소는 저장 전에 알려 준다
    page.form["ai.url"].delete(0, "end")
    page.form["ai.url"].insert(0, "ai-gw.corp")
    page.save()
    assert warnings and "http://" in warnings[-1][1]


def test_ai_button_sends_ip_logs_and_saves_opinion(app, tmp_path, monkeypatch):
    import os
    import shutil
    import threading
    import time
    from http.server import ThreadingHTTPServer
    from conftest import FIX
    from openpyxl import load_workbook
    from test_ai import Gateway
    from iptrace.core import report
    from iptrace.gui import main_window
    root, mw, errors, _ = app
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Gateway.seen = []
    try:
        os.makedirs(tmp_path / "srvlogs" / "web01")
        shutil.copy(os.path.join(FIX, "access_sample.log"), tmp_path / "srvlogs" / "web01" / "access.log")
        (tmp_path / "rules").mkdir(exist_ok=True)
        shutil.copy(os.path.join(os.path.dirname(FIX), "..", "rules", "signatures.json"), tmp_path / "rules")
        mw.cfg["log_formats"]["x"] = '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" "%{X-Forwarded-For}i"'
        mw.cfg["general"]["local_log_format"] = "x"
        mw.cfg["ai"].update(enabled=True, url=f"http://127.0.0.1:{srv.server_address[1]}/v1", model="qwen")
        mw.goto("check")
        mw.local_folder = str(tmp_path / "srvlogs")
        mw.txt.insert("1.0", "203.0.113.45\n198.51.100.7")
        mw.period.set("custom")
        mw.custom_start.set("2026-01-01 00:00")
        mw.custom_end.set("2026-12-31 23:59")
        monkeypatch.setattr(main_window.DoneDialog, "show", lambda self: self.close())
        monkeypatch.setattr(main_window.messagebox, "showerror", lambda *a, **k: errors.append(a))
        asked, shown, keys = [], [], []
        monkeypatch.setattr(main_window.messagebox, "askyesno", lambda *a, **k: asked.append(a[1]) or True)
        monkeypatch.setattr(main_window.AiDialog, "show", lambda self: shown.append(self.note) or None)

        class FakeKey:
            def __init__(self, parent, items, **kw):
                keys.append(kw.get("title"))

            def show(self):
                return {"ai": "k1"}
        monkeypatch.setattr(main_window, "PasswordDialog", FakeKey)
        mw.validate()
        mw.start_check()
        t0 = time.time()
        while mw.state != "result" and time.time() - t0 < 30:
            root.update()
            time.sleep(0.05)
        key = "203.0.113.45"
        mw.tree.selection_set(key)
        root.update()
        assert mw.btn_ai.cget("text") == "AI 분석"
        mw.run_ai(key)
        t0 = time.time()
        while key not in mw.result.ai_notes and time.time() - t0 < 15:
            root.update()
            time.sleep(0.05)
        root.update()
        assert keys == ["AI API 키"] and mw.ai_key == "k1"                  # 키가 없으면 물어본다
        assert len(asked) == 1 and "AI 서버로 보냅니다" in asked[0] and "127.0.0.1" in asked[0]
        assert shown and shown[0].text == "[요약] 자동 스캔"
        sent = Gateway.seen[-1][2]["messages"][1]["content"]
        assert key in sent and "203.0.113.45" in sent
        assert mw.btn_ai.cget("text") == "AI 결과 보기"
        mw.run_ai(key)                                                       # 이미 받은 의견은 다시 보내지 않고 보여 준다
        assert len(Gateway.seen) == 1 and len(shown) == 2
        kind, path = report.save(mw.result, tmp_path / "out.xlsx")
        ws = load_workbook(path)["AI 분석"]
        assert ws.cell(2, 1).value == key and ws.cell(2, 3).value == "[요약] 자동 스캔"
        audit = (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8")
        assert '"action": "ai"' in audit and "[요약]" not in audit          # 감사 로그에는 내용 없이 기록만
        # 보고서 저장: 아직 분석하지 않은 IP만 AI로 분석해서 같이 넣는다
        other = "198.51.100.7"
        assert [s.ioc_key for s in mw.ai_pending()] == [other]
        asked3, saved = [], []
        monkeypatch.setattr(main_window.filedialog, "asksaveasfilename", lambda **k: str(tmp_path / "rep.html"))
        monkeypatch.setattr(main_window.messagebox, "askyesnocancel", lambda *a, **k: asked3.append(a[1]) or True)
        monkeypatch.setattr(main_window.SavedDialog, "show", lambda self: saved.append(1))
        mw.save_report()
        assert mw._save_progress.win.winfo_exists()                          # 분석하는 동안 진행 창이 뜬다
        t0 = time.time()
        while not saved and time.time() - t0 < 15:
            root.update()
            time.sleep(0.05)
        assert not mw._save_progress.win.winfo_exists()
        assert len(asked3) == 1 and other in asked3[0] and "이미 분석한 1개" in asked3[0]
        assert len(Gateway.seen) == 2 and other in Gateway.seen[-1][2]["messages"][1]["content"]
        assert set(mw.result.ai_notes) == {key, other} and not mw.unsaved
        html = (tmp_path / "rep.html").read_text(encoding="utf-8")
        assert html.count("AI 분석 (참고)") >= 2
        mw.save_report()                                                     # 모두 분석했으면 묻지 않고 바로 저장
        root.update()
        assert len(asked3) == 1 and len(saved) == 2 and len(Gateway.seen) == 2
        # [중지]: 지금 보낸 IP까지만 받고 저장한다
        mw.result.ai_notes.clear()
        warned = []
        monkeypatch.setattr(main_window.messagebox, "showwarning", lambda *a, **k: warned.append(a[1]))
        mw.save_report()
        mw._save_progress.stop()
        t0 = time.time()
        while len(saved) < 3 and time.time() - t0 < 15:
            root.update()
            time.sleep(0.05)
        assert len(saved) == 3 and len(Gateway.seen) <= 3 and len(mw.result.ai_notes) <= 1
        assert warned and "중지했습니다" in warned[0]
        # 받지 못한 IP가 있으면 [다시 시도]를 물어보고, 다시 보내서 넣는다
        mw.result.ai_notes.clear()
        real, calls = main_window.aimod.analyze, []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise main_window.aimod.AiError("시간 초과 (120초) - 시간 제한을 늘리거나 보낼 줄 수를 줄이세요")
            return real(*a, **k)
        monkeypatch.setattr(main_window.aimod, "analyze", flaky)
        mw.save_report()
        t0 = time.time()
        while len(saved) < 4 and time.time() - t0 < 15:
            root.update()
            time.sleep(0.05)
        assert len(saved) == 4 and len(calls) == 3 and set(mw.result.ai_notes) == {key, other}
        assert "다시 시도" in asked3[-1] and "시간 제한을 늘리세요" in asked3[-1]
        monkeypatch.setattr(main_window.aimod, "analyze", real)
        # 단건 분석 중에 다른 탭에 갔다 와도 오류 없이 끝나고, 돌아오면 진행 중인 것이 보인다
        mw.result.ai_notes.clear()
        mw.tree.selection_set(key)
        root.update()
        mw.run_ai(key)
        mw.goto("history")
        mw.goto("check")
        assert mw.btn_ai.cget("text") == "AI 분석 중…" and "AI 분석 중" in mw.lbl_toast.cget("text")
        mw.goto("history")
        t0 = time.time()
        while key not in mw.result.ai_notes and time.time() - t0 < 15:
            root.update()
            time.sleep(0.05)
        root.update()
        mw.goto("check")
        assert key in mw.result.ai_notes and not mw._ai_running
        assert not errors, errors
    finally:
        srv.shutdown()


def test_email_tab_and_send_report(app, tmp_path, monkeypatch):
    """[서버 설정] > 이메일을 켜면 결과 화면 버튼이 [이메일 보내기]로 바뀌고, 회신 요약 + HTML 보고서 첨부로 보낸다."""
    import base64
    import os
    import shutil
    import sys
    import threading
    import time
    from http.server import ThreadingHTTPServer
    from tkinter import ttk
    from conftest import FIX
    from test_mailer import FakeMailApi
    from iptrace.core import ai
    from iptrace.gui import main_window
    root, mw, errors, warnings = app
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeMailApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    FakeMailApi.seen, FakeMailApi.mode = [], "ok"
    try:
        mw.goto("settings")
        page = mw.page_obj
        page.page.set("email")
        page.show()
        root.update()
        f = page.form
        f["em.enabled"].set(True)
        for k, v in (("em.url", f"http://127.0.0.1:{srv.server_address[1]}/api"), ("em.sender", "secu@corp.co.kr;보안팀"),
                     ("em.to", "a@x.com; b@x.com"), ("em.token", "t-123"), ("em.service_seq", "S001")):
            f[k].delete(0, "end")
            f[k].insert(0, v)
        f["em.save_token"].set(sys.platform == "win32")
        # 받는 사람을 비우면 저장하지 않고 알려 준다
        f["em.to"].delete(0, "end")
        page.save()
        assert warnings and "기본 받는 사람을 넣으세요" in warnings[-1][1]
        assert not json.loads(paths.CONFIG_FILE.read_text(encoding="utf-8"))["email"]["enabled"]   # 저장 안 됨
        warnings.clear()
        f["em.to"].insert(0, "a@x.com; b@x.com")
        page.save()
        root.update()
        assert not errors and not warnings, errors or warnings
        text = paths.CONFIG_FILE.read_text(encoding="utf-8")
        saved = json.loads(text)["email"]
        assert saved["enabled"] and saved["to"] == "a@x.com; b@x.com" and "t-123" not in text   # 토큰은 평문으로 남지 않는다
        if sys.platform == "win32":
            assert ai.unprotect(saved["token_saved"]) == "t-123"
        # [시험 메일 보내기]: 발신자·받는 사람을 물어보고 그 주소로 보낸다 (설정 값이 기본으로 채워짐)
        from iptrace.gui import settings_page
        asked = []

        class FakeAsk:
            def __init__(self, parent, title, heading, fields, **kw):
                asked.append({k: v for k, _, v, _ in fields})

            def show(self):
                return {"sender": "tester@corp.co.kr;시험", "to": "me@x.com"}
        monkeypatch.setattr(settings_page, "AskDialog", FakeAsk)
        mw.page_obj.test_mail()
        t0 = time.time()
        while "보냈습니다" not in mw.page_obj.lbl_mail_test.cget("text") and time.time() - t0 < 15:
            root.update()
            time.sleep(0.05)
        root.update()
        assert asked == [{"sender": "secu@corp.co.kr;보안팀", "to": "a@x.com; b@x.com"}]
        _, _, sent = FakeMailApi.seen[-1]
        assert sent["msgFrom"] == "tester@corp.co.kr;시험" and sent["msgTo"] == "me@x.com" and "시험 메일" in sent["title"]
        assert "보냈습니다" in mw.page_obj.lbl_mail_test.cget("text")
        # 로컬 폴더로 점검해 결과 화면을 만든다
        os.makedirs(tmp_path / "srvlogs" / "web01")
        shutil.copy(os.path.join(FIX, "access_sample.log"), tmp_path / "srvlogs" / "web01" / "access.log")
        (tmp_path / "rules").mkdir(exist_ok=True)
        shutil.copy(os.path.join(os.path.dirname(FIX), "..", "rules", "signatures.json"), tmp_path / "rules")
        mw.cfg["log_formats"]["x"] = '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" "%{X-Forwarded-For}i"'
        mw.cfg["general"]["local_log_format"] = "x"
        mw.goto("check")
        mw.local_folder = str(tmp_path / "srvlogs")
        mw.txt.insert("1.0", "203.0.113.45")
        mw.period.set("custom")
        mw.custom_start.set("2026-01-01 00:00")
        mw.custom_end.set("2026-12-31 23:59")
        monkeypatch.setattr(main_window.DoneDialog, "show", lambda self: self.close())
        mw.validate()
        mw.start_check()
        t0 = time.time()
        while mw.state != "result" and time.time() - t0 < 30:
            root.update()
            time.sleep(0.05)
        texts = []

        def walk(w):
            for ch in w.winfo_children():
                if isinstance(ch, ttk.Button):
                    texts.append(ch.cget("text"))
                walk(ch)
        walk(root)
        assert "이메일 보내기" in texts and "회신 요약 복사" not in texts
        opened, infos = [], []

        class FakeDialog:
            def __init__(self, parent, to, cc, title, memo, preview, name, size, err, note):
                opened.append((to, title, memo, preview, name, err))

            def show(self):
                to, title, memo, preview, name, err = opened[-1]
                return {"to": to, "cc": "c@x.com", "title": title, "memo": memo + " 담당 010-1234-5678", "attach": True,
                        "name": "결과보고서.html"}
        monkeypatch.setattr(main_window, "EmailDialog", FakeDialog)
        monkeypatch.setattr(main_window.messagebox, "showinfo", lambda *a, **k: infos.append(a[1]))
        # AI 분석을 켰고 분석하지 않은 IP가 있으면, 보내기 전에 먼저 묻는다 ([취소]면 보내기 창도 안 뜬다)
        asked_ai = []
        mw.cfg["ai"]["enabled"] = True
        monkeypatch.setattr(main_window.messagebox, "askyesnocancel", lambda *a, **k: asked_ai.append(a[1]) or None)
        mw.email_report()
        root.update()
        assert len(asked_ai) == 1 and "보내기 전에 AI로 분석" in asked_ai[0] and "[아니요] AI 분석 없이 보내기" in asked_ai[0]
        assert not opened
        monkeypatch.setattr(main_window.messagebox, "askyesnocancel", lambda *a, **k: asked_ai.append(a[1]) or False)
        mw.email_report()                                                    # [아니요]: AI 없이 보내기 창으로
        t0 = time.time()
        while not infos and time.time() - t0 < 15:
            root.update()
            time.sleep(0.05)
        to, title, memo, preview, name, err = opened[0]
        assert to == "a@x.com; b@x.com" and title.startswith("[IPtrace] 침해의심 IP 점검 결과 [") and not err
        assert len(asked_ai) == 2 and name.startswith("[IPtrace]점검결과_") and name.endswith(".html")   # 제목 앞말
        assert name in preview(memo, name) and name not in preview(memo, "")
        assert "■ 로그가 발견된 IP" in preview(memo, name) and "203.0.113.45" in preview(memo, name)
        name = "결과보고서.html"                                               # 보내기 창에서 바꾼 첨부 파일 이름
        path, headers, sent = FakeMailApi.seen[-1]
        assert headers["authorization"] == "t-123" and sent["msgTo"] == "a@x.com,b@x.com" and sent["msgCc"] == "c@x.com"
        assert headers["appserviceseq"] == "S001" and headers["apiversion"] == "1.0"
        assert sent["contentType"] == "text/html" and "<table" in sent["contents"] and "203.0.113.45" in sent["contents"]
        assert "010-****-****" in sent["contents"] and f"<b>{name}</b>" in sent["contents"]   # 개인정보는 가리고 보낸다
        html = base64.b64decode(sent["fileList"][0]["fileData"]).decode("utf-8")
        assert sent["fileList"][0]["fileName"] == name and "203.0.113.45" in html
        assert infos and "TR0001" in infos[0]
        audit = (tmp_path / "logs" / "audit.log").read_text(encoding="utf-8")
        assert '"action": "email"' in audit and "a@x.com" not in audit                        # 감사 로그에는 주소 없이 건수만
        assert not errors, errors
    finally:
        srv.shutdown()


def test_password_fields_take_ascii_only(app):
    """비밀번호·키 칸은 영문·숫자·기호만 받는다 (한글은 막고 안내)."""
    from iptrace.gui.dialogs import PasswordDialog
    root, mw, errors, warnings = app
    d = PasswordDialog(root, [("a", "web01 (api)")])
    e = d.entries["*"]
    e.insert(0, "Pa$$w0rd!~ ")
    e.insert("end", "한글")                                   # 막힘
    assert e.get() == "Pa$$w0rd!~ " and "영문" in d.warn.cget("text")
    d.close()
    mw.goto("settings")
    page = mw.page_obj
    page.add_server()
    texts = []

    def walk(w):
        for ch in w.winfo_children():
            if isinstance(ch, tk.Label):
                texts.append(ch.cget("text"))
            walk(ch)
    walk(page.body)
    assert any("서비스 계정은 절대 사용하지 마세요" in t and "개인 계정" in t for t in texts)      # 서버 정보 위에 계정 주의 문구
    pw = page.form["srv.password"]
    pw.insert(0, "abc가")
    assert pw.get() == "" and "영문" in page.lbl_pw.cget("text")
    pw.insert(0, "abc123")
    assert pw.get() == "abc123"
    for tab, key in (("ai", "ai.key"), ("email", "em.token")):
        page.page.set(tab)
        page.show()
        f = page.form[key]
        f.insert(0, "키k")
        assert f.get() == ""
        f.insert(0, "k-1")
        assert f.get() == "k-1"
    assert not errors, errors

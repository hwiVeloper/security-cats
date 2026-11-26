"""메인 창: 상단 바 + 탭 [로그 점검] [점검 이력] [서버 설정]. 새 창을 띄우지 않고 탭 안에서 처리한다.

로그 점검 탭: 왼쪽 입력 + 오른쪽(처음 / 점검 중 / 결과)
"""
import gc
import hashlib
import os
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, APP_TITLE, __version__, paths
from ..core import aggregator, audit, config as cfgmod, engine, history, ioc, report
from ..core import ai as aimod
from ..core import mailer, report_html
from ..core.detector import ATTACK_TAGS, TAG_LABELS
from ..core.engine import KST
from ..core.models import NOT_FOUND, CheckRequest
from . import theme
from .dialogs import (AiDialog, DoneDialog, EmailDialog, HostKeyDialog, ListDialog, PasswordDialog, ProgressDialog,
                      SavedDialog)
from .theme import C, F
from .widgets import (badge, buttons_right, card, check, divider, grade_tags, label, scroll_area, scrolled_tree,
                      seg, stat_box, step_title, var)
from .worker import Worker

PERIOD_ITEMS = (("today", "오늘"), ("yesterday", "어제"), ("7", "7일"), ("custom", "직접"))
STATUS_FILTERS = (("all", "전체"), ("2", "2xx"), ("3", "3xx"), ("4", "4xx"), ("404", "404"),
                  ("5", "5xx"), ("500", "500"), ("atk", "공격 태그"), ("unp", "해석 실패"))
TABS = (("check", "  로그 점검  "), ("history", "  점검 이력  "), ("settings", "  서버 설정  "))
ALL_SERVERS = "전체 서버"
MAX_DETAIL_ROWS = 500
STATE_COLOR = {"완료": "green", "부분 분석": "amber", "연결 실패": "red", "수집 실패": "red"}


# 보고서 저장·이메일 보내기 전에 AI 분석을 할 때 쓰는 문구
SAVE_MODE = {"act": "저장", "title": "보고서 저장", "before": "저장하기 전에", "obj": "보고서",
             "next": "끝나면 저장합니다", "none": "저장하지 않았습니다", "sub": "", "go": "저장합니다"}
MAIL_MODE = {"act": "보내기", "title": "이메일 보내기", "before": "보내기 전에", "obj": "첨부 보고서",
             "next": "끝나면 이메일 보내기 창을 엽니다", "none": "보내지 않았습니다",
             "sub": "다 받으면 이메일 보내기 창을 엽니다.", "go": "이메일 보내기 창을 엽니다"}


class MainWindow:
    def __init__(self, root: tk.Tk, cfg: dict):
        self.root = root
        self.cfg = cfg
        root.title(f"{APP_NAME} - {APP_TITLE} v{__version__}")
        # 화면 배율(125%·150%)에서는 글자가 커지므로 창과 왼쪽 패널도 같은 비율로 키운다 (화면 크기 안에서)
        self.ui_scale = max(1.0, float(root.tk.call("tk", "scaling")) * 72 / 96)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        # 기본 높이 900 (화면이 작으면 화면에 맞춤). 결과 화면의 로그 표가 몇 줄 더 보이게
        w, h = min(self.px(1280), sw - 40), min(self.px(900), sh - 80)
        root.geometry(f"{w}x{h}")
        root.minsize(min(self.px(1100), w), min(self.px(800), h))
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # 탭을 바꾸거나 다시 그려도 유지할 상태
        self.tab = var("settings" if not cfg["servers"] else "check")
        self.ioc_text = ""
        self.ioc_res = ioc.parse_text("")
        self.include_private = var(False)  # 사설·자사 대역 IP도 점검 (내부 테스트용, 저장 안 함)
        self.period = var(str(cfg["general"].get("default_period", "7")))
        self.custom_start = var(datetime.now(KST).strftime("%Y-%m-%d 00:00"))
        self.custom_end = var(datetime.now(KST).strftime("%Y-%m-%d 23:59"))   # 로그에 미래 시각은 없으니 오늘 끝까지
        self._last_period = None
        self.server_vars = {}
        self.local_folder = ""
        self.passwords = {}        # {계정@주소:포트: 비밀번호} 메모리에만 둔다 (서버 설정·대화상자에서 입력)
        self.ai_key = ""           # AI API 키 (메모리에만. [키 저장]을 켜면 설정에 암호화해 둔다)
        self.mail_token = ""       # 메일 발송 API 인증 토큰 (AI 키와 같은 방식으로 보관)
        self._ai_confirmed = set()  # 이번 실행에서 '보낼까요?'에 답한 AI 주소
        self._ai_running = False
        self._ai_status = ""        # AI 분석 중 안내 (탭을 바꿔 결과 화면을 다시 만들어도 다시 보여 준다)
        self._save_progress = None  # 보고서 저장 전 AI 분석 진행 창
        self.failed_pw = {}        # {계정@주소:포트: 인증에 실패한 비밀번호의 해시} 같은 비밀번호로 다시 접속하지 않는다
        self.run_passwords = {}    # 진행 중인 점검에 쓴 {서버 이름: 비밀번호}
        self.state = "empty"
        self.result = None
        self.unsaved = False
        self.run_started = 0.0
        self.progress = {}
        self.result_filter = None
        self.server_filter = var(ALL_SERVERS)
        self.search = var("")
        self.hide_none = var(False)
        self.status_filter = var("all")
        self.selected_ip = None
        self.sort_key = None
        self.worker = Worker(root, self.ask_host_key)
        self._validate_job = None
        self.settings_state = {}   # 서버 설정 탭의 작업 중 값 (탭을 오가도 유지)
        self.history_state = {}
        self.build()

    # ------------------------------------------------------------ 틀
    def build(self):
        self._save_text()
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self.root.configure(bg=C["bg"])
        top = tk.Frame(self.root, bg=C["panel"], height=52)
        top.pack(fill="x")
        top.pack_propagate(False)
        label(top, APP_NAME, 16, True, fg=C["accent"]).pack(side="left", padx=(18, 8))
        label(top, APP_TITLE, 10, fg=C["sub"]).pack(side="left")
        dark = self.cfg["general"].get("theme") == "dark"
        self.btn_theme = ttk.Button(top, text="☀ 밝게" if dark else "☾ 어둡게", command=self.toggle_theme)
        self.btn_theme.pack(side="right", padx=(6, 16))
        label(top, "서버 원본 로그는 PC로 내려받지 않습니다 (서버에서 걸러 분석)", 9, fg=C["sub"]).pack(side="right", padx=10)

        nav = tk.Frame(self.root, bg=C["panel"])
        nav.pack(fill="x")
        tabs = tk.Frame(nav, bg=C["panel"])
        tabs.pack(side="left", padx=14, pady=(0, 8))
        self.tab_buttons = {}
        for key, text in TABS:
            b = ttk.Radiobutton(tabs, text=text, value=key, variable=self.tab, style="Tab.Toolbutton",
                                command=self.show_tab)
            b.pack(side="left", padx=(0, 4))
            self.tab_buttons[key] = b
        n = len(self.cfg["servers"])
        label(nav, f"서버 {n}대 등록", 9, fg=C["sub"]).pack(side="right", padx=(0, 18))
        label(nav, "●", 9, fg=C["green"] if n else C["amber"]).pack(side="right", padx=(0, 4))
        tk.Frame(self.root, bg=C["line"], height=1).pack(fill="x")
        self.content = tk.Frame(self.root, bg=C["bg"])
        self.content.pack(fill="both", expand=True)
        self.show_tab()

    def show_tab(self):
        self._save_text()
        page = getattr(self, "page_obj", None)
        if page is not None and hasattr(page, "save_state"):
            page.save_state()
        self.page_obj = None
        for w in self.content.winfo_children():
            w.destroy()
        # 버린 화면의 tk 변수를 여기(메인 스레드)서 치운다. AI 분석 스레드에서 치워지면 Tcl 오류가 난다
        gc.collect()
        t = self.tab.get()
        if t == "settings":
            from .settings_page import SettingsPage
            self.page_obj = SettingsPage(self.content, self)
        elif t == "history":
            from .history_page import HistoryPage
            self.page_obj = HistoryPage(self.content, self)
        else:
            self.body = tk.Frame(self.content, bg=C["bg"])
            self.body.pack(fill="both", expand=True, padx=14, pady=12)
            self.render(self.state)
        self._lock_tabs()

    def _lock_tabs(self):
        running = self.state == "running"
        for key, b in self.tab_buttons.items():
            b.state(["disabled"] if running and key != "check" else ["!disabled"])
        self.btn_theme.state(["disabled"] if running else ["!disabled"])

    def goto(self, tab):
        self.tab.set(tab)
        self.show_tab()

    def render(self, state):
        self._save_text()
        self.state = state
        if self.tab.get() != "check":
            self._lock_tabs()
            return
        for w in self.body.winfo_children():
            w.destroy()
        self.build_left()
        right = tk.Frame(self.body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        {"empty": self.build_empty, "running": self.build_running, "result": self.build_result}[state](right)
        self._lock_tabs()

    def _save_text(self):
        t = getattr(self, "txt", None)
        try:
            if t is not None and t.winfo_exists():
                self.ioc_text = t.get("1.0", "end-1c")
        except tk.TclError:
            pass

    def toggle_theme(self):
        self.set_theme("light" if self.cfg["general"].get("theme") == "dark" else "dark")

    def set_theme(self, name):
        page = getattr(self, "page_obj", None)
        if page is not None and hasattr(page, "save_state"):
            page.save_state()
            self.page_obj = None
        self.cfg["general"]["theme"] = name
        cfgmod.save(self.cfg)
        theme.setup(self.root, name)
        self.build()

    def reload_config(self, cfg):
        # 저장 직후 화면을 다시 만들 때, 이미 저장한 설정 페이지의 입력칸을 다시 읽지 않게 한다
        self.page_obj = None
        self.cfg = cfg
        theme.setup(self.root, cfg["general"]["theme"])
        self.build()

    def px(self, n) -> int:
        """100% 화면 기준 픽셀을 지금 화면 배율에 맞춘다."""
        return int(n * self.ui_scale)

    # ------------------------------------------------------------ 왼쪽 입력
    def build_left(self):
        running = self.state == "running"
        left = card(self.body, width=self.px(370))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        inner = tk.Frame(left, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=16, pady=14)

        btns = tk.Frame(inner, bg=C["panel"])
        btns.pack(side="bottom", fill="x")
        if running:
            self.btn_cancel = ttk.Button(btns, text="■  점검 중지", style="Danger.TButton", command=self.cancel_check)
            self.btn_cancel.pack(fill="x")
        else:
            ttk.Button(btns, text="연결 테스트", command=self.connection_test).pack(fill="x", pady=(0, 8))
            self.btn_start = ttk.Button(btns, text="▶  점검 시작", style="Accent.TButton", command=self.start_check)
            self.btn_start.pack(fill="x")
            # [점검 시작]을 누를 수 없을 때 이유를 버튼 아래에 보여준다
            self.lbl_start_hint = label(btns, "", 9, fg=C["amber"], anchor="w", justify="left", wraplength=self.px(330))

        # 1·2·3 구역은 창이 작거나 화면 배율이 커서 넘치면 스크롤된다 (아래 버튼은 항상 보인다)
        inner = scroll_area(inner, pady=(0, 10))
        # 1·2·3 구역은 가는 선으로 나누고, 구역 제목 아래에 여백을 둔다
        h1 = tk.Frame(inner, bg=C["panel"])
        h1.pack(fill="x", pady=(0, 8))
        step_title(h1, "1", "침해의심 IP 입력").pack(side="left")
        label(h1, "한 줄에 하나", 8, fg=C["sub"]).pack(side="right", pady=(3, 0))
        tf = tk.Frame(inner, bg=C["panel"])
        tf.pack(fill="x")
        self.txt = tk.Text(tf, height=7 if self.state != "result" else 6, font=(F["mono"], 10), bg=C["input"],
                           fg=C["text"], insertbackground=C["text"], relief="flat", highlightthickness=1,
                           highlightbackground=C["line"], highlightcolor=C["accent"], padx=8, pady=6, undo=True,
                           wrap="none")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="x", expand=True)
        self.txt.insert("1.0", self.ioc_text)
        self.txt.tag_configure("bad", background=C["crit"][0], foreground=C["crit"][1])
        self.txt.bind("<KeyRelease>", lambda e: self.schedule_validate())
        self.txt.bind("<<Paste>>", lambda e: self.root.after(50, self.schedule_validate))
        if running:
            self.txt.configure(state="disabled", fg=C["faint"])
        st = tk.Frame(inner, bg=C["panel"])
        st.pack(fill="x", pady=(6, 0))
        self.lbl_count = label(st, "0", 16, True, fg=C["accent"])
        self.lbl_count.pack(side="left")
        label(st, "개 IP", 10).pack(side="left", pady=(5, 0))
        self.lbl_stats = label(st, "", 9, fg=C["sub"])
        self.lbl_stats.pack(side="left", padx=(10, 0), pady=(5, 0))
        # 입력 오류 안내는 오류가 있을 때만 자리를 차지한다 (validate에서 보이고 숨김)
        self.lbl_error = label(inner, "", 9, fg=C["red"], anchor="w", justify="left", wraplength=self.px(300))
        tools = self.ioc_tools = tk.Frame(inner, bg=C["panel"])
        tools.pack(fill="x", pady=(6, 0))
        for t, cmd in (("파일 불러오기", self.load_ioc_file), ("지우기", self.clear_iocs), ("제외·오류 목록", self.show_ioc_issues)):
            b = ttk.Button(tools, text=t, style="Small.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            if running:
                b.state(["disabled"])
        cp = check(inner, "사설·자사 대역 IP도 점검 (내부 테스트용)", self.include_private, command=self.validate)
        cp.configure(font=(F["ui"], 9))
        cp.pack(anchor="w", pady=(4, 0))
        if running:
            cp.configure(state="disabled")

        divider(inner)
        step_title(inner, "2", "조회 기간").pack(anchor="w", pady=(0, 8))
        sg = seg(inner, PERIOD_ITEMS, self.period, command=self._on_period_pick)
        sg.pack(anchor="w")
        # 직접 입력: [입력칸][달력]. 10/21, 1021, 어제 같은 짧은 입력도 받고, 칸을 떠나면 2026-10-21 13:00 모양으로 고친다
        self.custom_box = tk.Frame(inner, bg=C["panel"])
        for v, tip, is_end in ((self.custom_start, "시작", False), (self.custom_end, "끝", True)):
            row = tk.Frame(self.custom_box, bg=C["panel"])
            row.pack(anchor="w", pady=1)
            label(row, tip, 9, fg=C["sub"], width=4, anchor="w").pack(side="left")
            e = ttk.Entry(row, textvariable=v, width=17, font=(F["mono"], 10))
            e.pack(side="left")
            e.bind("<KeyRelease>", lambda ev: self.update_period())
            e.bind("<FocusOut>", lambda ev, v_=v, end_=is_end: self._tidy_when(v_, end_))
            e.bind("<Return>", lambda ev, v_=v, end_=is_end: self._tidy_when(v_, end_))
            cb = ttk.Button(row, text="달력", style="Small.TButton", width=5,
                            command=lambda e_=e, v_=v, end_=is_end: self._open_calendar(e_, v_, end_))
            cb.pack(side="left", padx=(4, 0))
            if running:
                e.state(["disabled"])
                cb.state(["disabled"])
        label(self.custom_box, "예) 10/21 · 1021 · 어제 · 2026-10-21 13:00\n날짜만 쓰면 시작 00:00, 끝 23:59 · 서버 점검은 최대 7일", 8,
              fg=C["sub"], justify="left").pack(anchor="w", pady=(2, 0))
        self.lbl_range = label(inner, "", 9, fg=C["sub"], wraplength=self.px(300), justify="left")
        self.lbl_range.pack(anchor="w", pady=(4, 0))
        if running:
            for w in sg.winfo_children():
                w.state(["disabled"])

        divider(inner)
        hdr = tk.Frame(inner, bg=C["panel"])
        hdr.pack(fill="x", pady=(0, 6))
        step_title(hdr, "3", "대상 서버").pack(side="left")
        lb = ttk.Button(hdr, text="로컬 폴더..." if not self.local_folder else "서버로 전환", style="Small.TButton",
                        command=self.toggle_local)
        lb.pack(side="right")
        if running:
            lb.state(["disabled"])
        self.btn_all = None
        if not self.local_folder and len(self.cfg["servers"]) > 1:
            # 대상 서버 전체 선택·해제
            self.btn_all = ttk.Button(hdr, style="Small.TButton", command=self.toggle_all_servers)
            self.btn_all.pack(side="right", padx=(0, 6))
            if running:
                self.btn_all.state(["disabled"])
        grid = tk.Frame(inner, bg=C["panel"])
        grid.pack(anchor="w", fill="x")
        if self.local_folder:
            names = ", ".join(n for n, _ in self.local_sources()) or "(로그 파일 없음)"
            label(grid, f"로컬 폴더: {self.local_folder}", 9, wraplength=self.px(300), justify="left").pack(anchor="w")
            label(grid, f"서버: {names}", 9, fg=C["sub"], wraplength=self.px(300), justify="left").pack(anchor="w")
        elif not self.cfg["servers"]:
            label(grid, "등록된 서버가 없습니다. [서버 설정] 탭에서 추가하세요.", 9, fg=C["amber"]).pack(anchor="w")
        last = {s.name: s for s in self.result.servers} if self.result else {}
        # 서버 이름이 길면(M-T-********01 등) 한 줄에 하나씩, 짧으면 두 개씩
        ncol = 1 if max((len(s["name"]) for s in self.cfg["servers"]), default=0) > 11 else 2
        for i, s in enumerate(self.cfg["servers"] if not self.local_folder else []):
            v = self.server_vars.setdefault(s["name"], var(True))
            cell = tk.Frame(grid, bg=C["panel"])
            cell.grid(row=i // ncol, column=i % ncol, sticky="w", padx=(0, 10), pady=1)
            cb = check(cell, s["name"], v, command=self.on_server_check)
            cb.pack(side="left")
            if s["name"] in last:
                stt = last[s["name"]].state
                label(cell, stt, 8, fg=C[STATE_COLOR.get(stt, "sub")]).pack(side="left", padx=(2, 0))
            if running:
                cb.configure(state="disabled")
        self.update_period()
        self.validate()
        self.on_server_check()

    def schedule_validate(self):
        if self._validate_job:
            self.root.after_cancel(self._validate_job)
        self._validate_job = self.root.after(500, self.validate)

    def validate(self):
        self._validate_job = None
        self._save_text()
        self.ioc_res = ioc.parse_text(self.ioc_text, self.cfg["general"]["own_networks"],
                                      include_private=self.include_private.get())
        r = self.ioc_res
        if not getattr(self, "lbl_count", None) or not self.lbl_count.winfo_exists():
            return
        self.lbl_count.configure(text=str(len(r.entries)))
        self.lbl_stats.configure(text=f"중복 {r.duplicates} · 제외 {len(r.excluded)} · 오류 {len(r.error_lines)}")
        lines = r.error_lines
        if lines:
            more = f" 외 {len(lines) - 1}줄" if len(lines) > 1 else ""
            self.lbl_error.configure(text=f"⚠ {lines[0]}번째 줄{more}: IP 형식 오류 (점검에서 빠짐)", fg=C["red"])
        elif r.warnings:
            self.lbl_error.configure(text="⚠ " + r.warnings[0], fg=C["amber"])
        else:
            self.lbl_error.configure(text="")
        if self.lbl_error.cget("text"):
            self.lbl_error.pack(anchor="w", fill="x", pady=(2, 0), before=self.ioc_tools)
        else:
            self.lbl_error.pack_forget()
        self.txt.tag_remove("bad", "1.0", "end")
        for ln in lines:
            self.txt.tag_add("bad", f"{ln}.0", f"{ln}.end")
        self.refresh_start_state()

    def refresh_start_state(self):
        b = getattr(self, "btn_start", None)
        if not b or not b.winfo_exists():
            return
        r = self.ioc_res
        if not r.entries:
            if r.excluded and not self.include_private.get():
                why = f"IP {len(r.excluded)}개가 모두 사설·자사 대역이라 제외됨\n→ 점검하려면 '사설·자사 대역 IP도 점검'을 켜세요"
            elif r.errors:
                why = "점검할 IP가 없습니다. IP 형식을 확인하세요."
            else:
                why = "침해의심 IP를 입력하세요."
        elif not (self.selected_servers() or self.local_sources()):
            why = ("[서버 설정] 탭에서 서버를 먼저 추가하세요." if not self.cfg["servers"] and not self.local_folder
                   else "대상 서버를 하나 이상 선택하세요.")
        else:
            why = ""
        b.state(["disabled"] if why else ["!disabled"])
        hint = self.lbl_start_hint
        hint.configure(text=why)
        if why and not hint.winfo_ismapped():
            hint.pack(fill="x", pady=(6, 0))
        elif not why:
            hint.pack_forget()

    def period_range(self):
        kind = self.period.get()
        limit = not self.local_folder        # 서버를 읽을 때만 최대 7일 (로컬 폴더는 서버 부하가 없다)
        if kind == "custom":
            # 시각을 안 쓰면 시작 00:00 / 끝 23:59:59, 끝 시각은 적은 분의 59초까지 포함
            return engine.period_range("custom", start=engine.parse_when(self.custom_start.get()),
                                       end=engine.parse_when(self.custom_end.get(), end=True), limit=limit)
        return engine.period_range(kind, limit=limit)

    def _tidy_when(self, v, end):
        """짧게 쓴 날짜(10/21, 1021, 어제 …)를 2026-10-21 00:00 모양으로 고친다. 못 읽으면 그대로 둔다."""
        try:
            t = engine.parse_when(v.get(), end=end)
        except ValueError:
            return
        v.set(t.strftime("%Y-%m-%d %H:%M"))
        self.update_period()

    def _open_calendar(self, entry, v, end):
        from .widgets import DatePicker
        try:
            cur = engine.parse_when(v.get(), end=end)
        except ValueError:
            cur = None

        def picked(d):
            # 시각은 지금 적힌 것을 유지 (없으면 시작 00:00, 끝 23:59)
            hm = cur.strftime("%H:%M") if cur else ("23:59" if end else "00:00")
            v.set(f"{d:%Y-%m-%d} {hm}")
            self.update_period()
        DatePicker(entry, cur.date() if cur else None, picked)

    def _on_period_pick(self, k):
        # [직접]을 누르면 보고 있던 기간(시작 00:00 ~ 그날 23:59)으로 채워, 고칠 곳만 고치게 한다
        if k == "custom" and self._last_period not in (None, "custom"):
            try:
                s, e = engine.period_range(self._last_period)
                self.custom_start.set(s.strftime("%Y-%m-%d %H:%M"))
                self.custom_end.set(e.strftime("%Y-%m-%d") + " 23:59")
            except ValueError:
                pass
        self.update_period()

    def update_period(self):
        self._last_period = self.period.get()
        if self.period.get() == "custom":
            self.custom_box.pack(anchor="w", pady=(4, 0), before=self.lbl_range)
        else:
            self.custom_box.pack_forget()
        try:
            s, e = self.period_range()
            k = self.period.get()
            txt = (f"{s:%Y-%m-%d} 00:00 ~ 지금" if k == "today" else f"{s:%Y-%m-%d} 00:00 ~ 23:59" if k == "yesterday"
                   else f"{s:%Y-%m-%d %H:%M} ~ {e:%Y-%m-%d %H:%M}" if k == "custom" else f"{s:%Y-%m-%d} ~ {e:%Y-%m-%d}")
            self.lbl_range.configure(text=txt, fg=C["sub"])
        except ValueError as e:
            msg = str(e) if "최대" in str(e) or "앞이어야" in str(e) else "날짜 형식: 2026-10-21 13:00 (시작이 끝보다 앞이어야 함)"
            self.lbl_range.configure(text=msg, fg=C["red"])

    def toggle_all_servers(self):
        """모두 선택돼 있으면 전체 해제, 하나라도 빠져 있으면 전체 선택."""
        on = len(self.selected_servers()) < len(self.cfg["servers"])
        for s in self.cfg["servers"]:
            self.server_vars.setdefault(s["name"], var(True)).set(on)
        self.on_server_check()

    def on_server_check(self):
        if getattr(self, "btn_all", None) is not None and self.btn_all.winfo_exists():
            all_on = len(self.selected_servers()) == len(self.cfg["servers"])
            self.btn_all.configure(text="전체 해제" if all_on else "전체 선택")
        self.refresh_start_state()

    def selected_servers(self):
        if self.local_folder:
            return []
        return [s for s in self.cfg["servers"] if self.server_vars.setdefault(s["name"], var(True)).get()]

    def local_sources(self):
        """로컬 폴더: 하위 폴더 이름을 서버 이름으로 쓴다. 하위 폴더가 없으면 'local' 하나."""
        if not self.local_folder or not os.path.isdir(self.local_folder):
            return []
        subs = sorted(d for d in os.listdir(self.local_folder) if os.path.isdir(os.path.join(self.local_folder, d)))
        if subs:
            return [(d, os.path.join(self.local_folder, d, "*")) for d in subs]
        return [("local", os.path.join(self.local_folder, "*"))]

    def toggle_local(self):
        if self.local_folder:
            self.local_folder = ""
        else:
            d = filedialog.askdirectory(parent=self.root, title="이미 받아 둔 로그 폴더 선택 (하위 폴더 이름 = 서버 이름)")
            if not d:
                return
            self.local_folder = d
        self.render(self.state)

    def load_ioc_file(self):
        p = filedialog.askopenfilename(parent=self.root, title="IP 목록 파일",
                                       filetypes=[("텍스트", "*.txt *.csv *.list"), ("모든 파일", "*.*")])
        if not p:
            return
        try:
            text = ioc.read_file(p)
        except (ioc.EncryptedFileError, OSError) as e:
            messagebox.showwarning(APP_NAME, str(e), parent=self.root)
            return
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", text)
        self.validate()

    def set_iocs(self, text):
        """다른 탭(이력)에서 IP 목록을 넘겨받아 입력칸에 넣는다."""
        self.ioc_text = text
        self.goto("check")
        self.validate()

    def clear_iocs(self):
        self.txt.delete("1.0", "end")
        self.validate()

    def show_ioc_issues(self):
        r = self.ioc_res
        rows = [(f"{ln}", v, why) for ln, v, why in r.excluded]
        rows += [(f"{ln}", tok, "IP 형식 오류", "bad") for ln, _, tok in r.errors]
        rows += [("", w, "경고") for w in r.warnings]
        ListDialog(self.root, "제외·오류 목록", "점검에서 빠지는 입력", [("줄", 60), ("값", 220), ("사유", 300)], rows).show()

    # ------------------------------------------------------------ 오른쪽: 처음
    def build_empty(self, right):
        c = card(right)
        c.pack(fill="both", expand=True)
        box = tk.Frame(c, bg=C["panel"])
        box.place(relx=0.5, rely=0.45, anchor="center")
        label(box, "점검 결과가 여기에 표시됩니다", 16, True).pack()
        label(box, "아래 순서대로 진행하세요.", 10, fg=C["sub"]).pack(pady=(4, 18))
        for n, t in (("1", "왼쪽 칸에 보안팀이 알려준 침해의심 IP 입력 (한 줄에 하나)"),
                     ("2", "조회 기간과 대상 서버 확인"), ("3", "[점검 시작] 누르기")):
            r = tk.Frame(box, bg=C["panel"])
            r.pack(anchor="w", pady=4)
            tk.Label(r, text=n, bg=C["accent"], fg=C["accent_fg"], font=(F["ui"], 10, "bold"), width=2).pack(side="left")
            label(r, t, 11).pack(side="left", padx=10)
        note = tk.Frame(box, bg=C["panel2"])
        note.pack(pady=(22, 0), fill="x")
        label(note, "· 서버의 원본 로그는 PC로 내려받지 않습니다. 서버에서 의심 IP가 든 줄만 걸러 받습니다.\n"
                    "· 결과 파일은 [보고서 저장]을 눌러야 만들어집니다. 점검 요약은 [점검 이력]에 남습니다.",
              9, fg=C["sub"], bg=C["panel2"], justify="left").pack(padx=14, pady=10, anchor="w")

    # ------------------------------------------------------------ 점검 실행
    def start_check(self, retry_servers=None):
        if self.worker.busy:
            return
        self.validate()
        if not self.ioc_res.entries and retry_servers is None:
            messagebox.showwarning(APP_NAME, "점검할 IP가 없습니다.", parent=self.root)
            return
        if self.unsaved and retry_servers is None and not messagebox.askyesno(
                APP_NAME, "이전 결과를 보고서로 저장하지 않았습니다 (요약은 점검 이력에 있습니다).\n새로 점검할까요?",
                parent=self.root):
            return
        try:
            start, end = (self.result.request.start, self.result.request.end) if retry_servers is not None \
                else self.period_range()
        except ValueError as e:
            messagebox.showwarning(APP_NAME, str(e), parent=self.root)
            return
        if retry_servers is not None and self.local_folder:
            local, servers = [x for x in self.local_sources() if x[0] in set(retry_servers)], []
        elif retry_servers is not None:
            local, servers = [], retry_servers
        else:
            local, servers = self.local_sources(), self.selected_servers()
        if not self.ask_passwords(servers):
            return
        iocs = self.result.request.iocs if retry_servers is not None else self.ioc_res.entries
        req = CheckRequest(iocs=iocs, start=start, end=end, servers=servers, local_logs=local,
                           log_format=self.cfg["general"].get("local_log_format", "webtob_combined"),
                           passwords={s["name"]: self.pw_for(s) for s in servers})
        self.run_passwords = dict(req.passwords)
        self.retrying = retry_servers is not None
        names = [s["name"] for s in servers] + [n for n, _ in local]
        self.progress = {n: {"pct": 0, "text": "대기", "state": "wait", "matched": 0} for n in names}
        self.run_started = time.time()
        self.run_meta = f"IP {len(iocs)}개 · {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} · 서버 {len(names)}대"
        self.render("running")
        self.worker.start(engine.run_check, on_event=self.on_event, on_done=self.on_done, on_error=self.on_error,
                          req=req, cfg=self.cfg)
        self._tick()

    @staticmethod
    def _pw_hash(pw) -> str:
        return hashlib.sha256((pw or "").encode("utf-8")).hexdigest()

    def pw_for(self, server, cfg=None) -> str:
        """그 계정(계정@주소:포트)에 입력한 비밀번호. 인증에 실패한 비밀번호면 빈 값(다시 묻는다)."""
        key = cfgmod.password_key(cfg or self.cfg, server)
        pw = self.passwords.get(key, "")
        return "" if pw and self.failed_pw.get(key) == self._pw_hash(pw) else pw

    def mark_auth_failed(self, server, password, cfg=None):
        """비밀번호가 틀린 계정: 그 비밀번호를 지우고, 같은 비밀번호로는 다시 접속하지 않게 기억한다."""
        key = cfgmod.password_key(cfg or self.cfg, server)
        if password:
            self.failed_pw[key] = self._pw_hash(password)
        self.passwords.pop(key, None)

    def ask_passwords(self, servers, cfg=None) -> bool:
        """비밀번호가 하나도 없는 서버만 대화상자로 묻는다 (보통은 화면 안의 비밀번호 칸으로 충분)."""
        cfg = cfg or self.cfg
        need, seen = [], set()
        for s in servers:
            ssh = cfgmod.effective_ssh(cfg, s)
            key = cfgmod.password_key(cfg, s)
            if ssh["auth"] == "key" or self.pw_for(s, cfg) or key in seen:
                continue
            seen.add(key)
            need.append((key, f"{s['name']} · 계정 {ssh.get('user') or '(없음)'}"))
        if not need:
            return True
        got = PasswordDialog(self.root, need).show()
        if got is None:
            return False
        if any(not v for v in got.values()):
            messagebox.showwarning(APP_NAME, "비밀번호를 입력하세요.", parent=self.root)
            return False
        same = [k for k, v in got.items() if self.failed_pw.get(k) == self._pw_hash(v)]
        if same:
            messagebox.showwarning(APP_NAME, "방금 인증에 실패한 비밀번호와 같습니다.\n"
                                             "여러 번 틀리면 계정이 잠기므로 같은 비밀번호로는 다시 접속하지 않습니다.\n\n"
                                             + "\n".join(same), parent=self.root)
            return False
        self.passwords.update(got)
        return True

    def ask_host_key(self, server, host, key_type, fp) -> bool:
        return HostKeyDialog(self.root, server, host, key_type, fp).show() == "trust"

    def cancel_check(self):
        self.worker.cancel.set()
        self.btn_cancel.configure(text="중지하는 중...")
        self.btn_cancel.state(["disabled"])

    def on_event(self, e):
        p = self.progress.setdefault(e.server, {"pct": 0, "text": "", "state": "run", "matched": 0})
        if e.kind == "server_started":
            p.update(state="run", text=f"파일 {e.files}개 확인 중")
        elif e.kind == "file_progress":
            p.update(pct=min(99, int(e.file_index - 1) * 100 // max(1, e.files)) if e.files else 0, state="run",
                     text=f"{e.file_name} ({e.file_index}/{e.files})", matched=e.matched)
        elif e.kind == "server_done":
            msg = e.message[len(e.server):].strip() if e.message.startswith(e.server) else e.message.split(": ", 1)[-1]
            p.update(pct=100, state="done", text=msg, matched=e.matched)
        elif e.kind == "server_failed":
            p.update(pct=100, state="fail", text=f"실패: {e.message}")
            if "인증 실패" in e.message:
                srv = next((s for s in self.cfg["servers"] if s["name"] == e.server), None)
                if srv:
                    self.mark_auth_failed(srv, self.run_passwords.get(e.server))
        self._update_running()

    def _tick(self):
        if self.state != "running":
            return
        self._update_running()
        self.root.after(1000, self._tick)

    def on_done(self, result):
        if getattr(self, "retrying", False) and self.result:
            result.history_id = self.result.history_id
            result = engine.merge_retry(self.result, result, self.cfg)
        self.result = result
        try:
            history.save_result(result, audit._user())
        except Exception as e:  # noqa: BLE001 - 이력 저장 실패가 결과 표시를 막지 않게 한다
            messagebox.showwarning(APP_NAME, f"점검 이력을 저장하지 못했습니다: {e}", parent=self.root)
        self.unsaved = True
        self.selected_ip = next((s.ioc_key for s in result.summaries if s.accessed), None)
        self.result_filter = None
        self.server_filter.set(ALL_SERVERS)
        self.status_filter.set("all")
        self.render("result")
        DoneDialog(self.root, result).show()

    def on_error(self, exc):
        back = "result" if self.result and getattr(self, "retrying", False) else ("result" if self.result else "empty")
        if isinstance(exc, engine.Cancelled):
            self.render("result" if self.result and getattr(self, "retrying", False) else "empty")
            return
        self.render(back)
        messagebox.showerror(APP_NAME, f"점검하지 못했습니다.\n{exc}", parent=self.root)

    # ------------------------------------------------------------ 오른쪽: 점검 중
    def build_running(self, right):
        head = tk.Frame(right, bg=C["bg"])
        head.pack(fill="x")
        label(head, "점검 중...", 15, True, bg=C["bg"]).pack(side="left")
        label(head, getattr(self, "run_meta", ""), 9, fg=C["sub"], bg=C["bg"]).pack(side="left", padx=10, pady=(6, 0))
        c = card(right)
        c.pack(fill="x", pady=(8, 8))
        top = tk.Frame(c, bg=C["panel"])
        top.pack(fill="x", padx=18, pady=(14, 6))
        self.lbl_total = label(top, "0%", 26, True, fg=C["accent"])
        self.lbl_total.pack(side="left")
        self.bar_total = ttk.Progressbar(top, style="Big.Horizontal.TProgressbar")
        self.bar_total.pack(side="left", fill="x", expand=True, padx=16)
        self.lbl_time = label(top, "", 10, fg=C["sub"])
        self.lbl_time.pack(side="right")
        self.run_rows = {}
        name_w = max([16] + [min(32, len(n)) for n in self.progress])   # 긴 서버 이름도 끝까지 (api·fe 구분)
        for name in self.progress:
            r = tk.Frame(c, bg=C["panel"])
            r.pack(fill="x", padx=18, pady=3)
            label(r, name, 10, True, mono=True, width=name_w, anchor="w").pack(side="left")
            bar = ttk.Progressbar(r, length=380)
            bar.pack(side="left", padx=10)
            lb = label(r, "대기", 10, fg=C["sub"], anchor="w")
            lb.pack(side="left", fill="x")
            self.run_rows[name] = (bar, lb)
        tk.Frame(c, bg=C["panel"], height=10).pack()
        f = card(right)
        f.pack(fill="both", expand=True)
        label(f, "진행 방식", 11, True).pack(anchor="w", padx=14, pady=(12, 4))
        label(f, "서버마다 로그 파일을 차례로 열어, 서버 안에서 의심 IP가 든 줄만 골라 받습니다.\n"
                 "원본 로그는 서버에 그대로 있고 PC로 내려받지 않습니다.", 10, fg=C["sub"], justify="left"
              ).pack(anchor="w", padx=14)
        self.lbl_found = label(f, "", 10, fg=C["text"], anchor="w", justify="left")
        self.lbl_found.pack(fill="x", padx=14, pady=(12, 14))
        self._update_running()

    def _update_running(self):
        if self.state != "running" or not getattr(self, "run_rows", None):
            return
        try:
            if not self.lbl_total.winfo_exists():
                return
        except tk.TclError:
            return
        vals = []
        for name, (bar, lb) in self.run_rows.items():
            p = self.progress.get(name, {})
            pct, st = p.get("pct", 0), p.get("state", "wait")
            vals.append(100 if st in ("done", "fail") else pct)
            bar.configure(value=pct, style="Fail.Horizontal.TProgressbar" if st == "fail" else "Horizontal.TProgressbar")
            lb.configure(text=p.get("text", ""), fg=C["red"] if st == "fail" else C["green"] if st == "done" else C["sub"])
        total = int(sum(vals) / max(1, len(vals)))
        self.lbl_total.configure(text=f"{total}%")
        self.bar_total.configure(value=total)
        el = int(time.time() - self.run_started)
        remain = f" · 남은 시간 약 {_mmss(el * (100 - total) / total)}" if 3 < total < 100 else ""
        self.lbl_time.configure(text=f"경과 {_mmss(el)}{remain}")
        matched = sum(p.get("matched", 0) for p in self.progress.values())
        self.lbl_found.configure(text=f"지금까지 의심 IP가 기록된 로그 {matched}줄" if matched else "")

    # ------------------------------------------------------------ 오른쪽: 결과
    def current_summaries(self):
        """서버 필터가 있으면 그 서버의 로그로 다시 집계한다."""
        srv = self.server_filter.get()
        if srv == ALL_SERVERS:
            return self.result.summaries
        recs = [r for r in self.result.records if r.server == srv]
        return aggregator.summarize(self.result.request.iocs, recs, int(self.cfg["detect"]["scan_404_threshold"]))

    def build_result(self, right):
        res = self.result
        c = res.summary_counts()
        bar = tk.Frame(right, bg=C["bg"])
        bar.pack(side="bottom", fill="x", pady=(8, 0))
        self.lbl_toast = label(bar, "파일은 [보고서 저장]을 눌러야 만들어집니다. 요약은 [점검 이력]에 저장됐습니다."
                               if self.unsaved else f"저장됨: {res.saved_path}", 9, fg=C["sub"], bg=C["bg"])
        self.lbl_toast.pack(side="left")
        if self._ai_running:                     # 다른 탭에 갔다 와도 AI 분석 중인 것이 보이게
            self.toast(text=self._ai_status, fg=C["sub"])
        # 이메일 보내기를 켜면 [회신 요약 복사] 자리에 [이메일 보내기] (본문 복사는 보내기 창에서)
        reply = (("이메일 보내기", "TButton", self.email_report) if self.cfg["email"].get("enabled")
                 else ("회신 요약 복사", "TButton", self.copy_reply))
        buttons_right(bar, [("선택 로그 복사", "TButton", self.copy_logs), reply,
                            ("보고서 저장...", "Accent.TButton", self.save_report)])[0].pack(side="right")

        head = tk.Frame(right, bg=C["bg"])
        head.pack(fill="x")
        label(head, "점검 결과", 15, True, bg=C["bg"]).pack(side="left")
        r = res.request
        label(head, f"{res.started:%m-%d %H:%M} 점검 · {r.start:%Y-%m-%d %H:%M} ~ {r.end:%Y-%m-%d %H:%M} · 규칙 {res.rule_version}",
              9, fg=C["sub"], bg=C["bg"]).pack(side="left", padx=10, pady=(6, 0))

        stats = tk.Frame(right, bg=C["bg"])
        stats.pack(fill="x", pady=(8, 6))
        cards = (("점검한 IP", c["iocs"], "개", C["card_total"], None),
                 ("로그 발견", c["accessed"], "개 IP", C["card_found"], "accessed"),
                 ("404 있음", c["with_404"], "개 IP", C["card_404"], "404"),
                 ("500 있음", c["with_500"], "개 IP", C["card_500"], "500"),
                 ("위험 (CRITICAL·HIGH)", c["danger"], "개 IP", C["card_danger"], "danger"))
        for i, (t, v, u, col, flt) in enumerate(cards):
            title = t + ("  ▼" if flt and self.result_filter == flt else "")
            stat_box(stats, title, v, u, col, command=lambda f=flt: self.set_result_filter(f)).grid(
                row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            stats.columnconfigure(i, weight=1)

        # 서버별 상태 (완료 / 부분 분석 / 연결 실패)
        sv = tk.Frame(right, bg=C["bg"])
        sv.pack(fill="x", pady=(0, 6))
        summary = len(res.servers) > 3 or not self._chips_fit(res.servers)
        if summary:
            # 서버가 많거나 이름이 길어 한 줄에 다 안 들어가면 요약 + [서버별 보기]. 실패한 서버는 한 번에 다시 점검
            self._server_summary_chip(sv, res.servers)
        for s in (res.servers if not summary else []):
            col = C[STATE_COLOR.get(s.state, "sub")]
            chip = tk.Frame(sv, bg=C["panel"], highlightthickness=1, highlightbackground=col if not s.ok else C["line"])
            chip.pack(side="left", padx=(0, 6))
            label(chip, "●", 9, fg=col).pack(side="left", padx=(8, 2), pady=5)
            label(chip, s.name, 9, True).pack(side="left")
            # 실패 이유는 짧게 ('연결 실패: 접속 실패: ...'처럼 겹치는 말은 뺀다). 전체 이유는 점검 완료 창·엑셀에 있다
            detail = self._chip_detail(s)
            label(chip, detail, 9, fg=C["sub"]).pack(side="left", padx=(6, 8))
            if not s.ok:
                ttk.Button(chip, text="다시 점검", style="Small.TButton",
                           command=lambda n=s.name: self.retry(n)).pack(side="left", padx=(0, 6), pady=3)

        # 해석 실패(로그 형식과 맞지 않는 줄): 결과에 들어가지 않으므로 눈에 띄게 알리고 바로 확인할 수 있게 한다
        unread = [x for x in res.servers if x.ok and x.parse_errors]
        if unread:
            n = sum(x.parse_errors for x in unread)
            n_ip = sum(x.parse_errors_ip for x in unread)
            wb = tk.Frame(right, bg=C["warn_bg"])
            wb.pack(fill="x", pady=(0, 6))
            n_in = sum(x.unparsed_matched for x in unread)
            msg = f"⚠ 해석 실패 {n}줄: 로그 형식과 맞지 않는 줄"
            msg += (f" · 의심 IP가 든 {n_in}줄은 결과에 '해석 실패'로 넣었습니다 (원문 확인 필요)" if n_in
                    else " · 의심 IP와 맞는 줄은 없습니다")
            tk.Label(wb, text=msg, bg=C["warn_bg"], fg=C["warn_fg"], font=(F["ui"], 9)).pack(side="left", padx=10, pady=2)
            ttk.Button(wb, text="읽지 못한 줄 보기", style="Small.TButton",
                       command=self.show_parse_errors).pack(side="right", padx=6, pady=1)

        rc = card(right)
        rc.pack(fill="both", expand=True)
        rh = tk.Frame(rc, bg=C["panel"])
        rh.pack(fill="x", padx=12, pady=(10, 6))
        label(rh, "IP별 결과", 11, True).pack(side="left")
        # [AI 분석]: 표에서 고른 IP를 분석한다. IP를 고르기 전·로그가 없는 IP·분석 중에는 누를 수 없다
        self.btn_ai = None
        if self.cfg["ai"].get("enabled"):
            self.btn_ai = ttk.Button(rh, text="AI 분석", style="Small.TButton",
                                     command=lambda: self.selected_ip and self.run_ai(self.selected_ip))
            self.btn_ai.pack(side="left", padx=(12, 0))
            self._update_ai_button()
        if self.result_filter:
            ttk.Button(rh, text="필터 해제", style="Small.TButton", command=lambda: self.set_result_filter(None)).pack(side="left", padx=8)
        check(rh, "미발견 숨기기", self.hide_none, command=self.fill_results).pack(side="right")
        e = ttk.Entry(rh, width=16, textvariable=self.search)
        e.pack(side="right", padx=(4, 10))
        e.bind("<KeyRelease>", lambda ev: self.fill_results())
        label(rh, "IP 검색", 9, fg=C["sub"]).pack(side="right")
        names = [ALL_SERVERS] + [s.name for s in res.servers]
        # 서버 이름 길이에 맞춘다 (M-T-********01 같은 긴 이름이 잘리지 않게)
        cb = ttk.Combobox(rh, values=names, textvariable=self.server_filter, state="readonly",
                          width=max(12, min(28, max(len(n) for n in names) + 2)))
        cb.pack(side="right", padx=(4, 12))
        cb.bind("<<ComboboxSelected>>", lambda ev: self.fill_results())
        label(rh, "서버", 9, fg=C["sub"]).pack(side="right")
        cols = ("sev", "ip", "req", "c2", "c3", "c4", "c404", "c5", "c500", "why")
        heads = ("위험도", "의심 IP", "요청", "2xx", "3xx", "4xx", "404", "5xx", "500", "판정")
        widths = [self.px(w) for w in (84, 130, 48, 42, 42, 42, 42, 42, 42, 300)]   # 판정 전체 글은 아래 '확인 근거'
        # 해석 실패 경고 줄이 있으면 IP 표를 한 줄 줄여 아래 로그 표 자리를 남긴다
        fr, self.tree = scrolled_tree(rc, columns=cols, show="headings", height=3 if unread else 4)
        for col, h, w in zip(cols, heads, widths):
            self.tree.heading(col, text=h, command=lambda c_=col: self.sort_results(c_))
            self.tree.column(col, width=w, anchor="w" if col in ("sev", "ip", "why") else "center", stretch=(col == "why"))
        grade_tags(self.tree)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select_ip())
        fr.pack(fill="both", expand=True, padx=12)
        label(rc, f"분석 범위: {res.coverage_text()}", 9, fg=C["sub"], anchor="w").pack(fill="x", padx=12, pady=(4, 8))

        self.detail = card(right)
        self.detail.pack(fill="both", expand=True, pady=(8, 0))
        self.fill_results()

    def set_result_filter(self, f):
        self.result_filter = None if f == self.result_filter else f
        self.render("result")

    def filtered_summaries(self):
        out = []
        q = self.search.get().strip()
        for s in self.current_summaries():
            f = self.result_filter
            if f == "accessed" and not s.accessed or f == "404" and not s.c404 or f == "500" and not s.c500 \
                    or f == "danger" and s.grade not in ("CRITICAL", "HIGH"):
                continue
            if self.hide_none.get() and not s.accessed:
                continue
            if q and q not in s.ioc_key and not any(q in ip for ip in s.matched_ips):
                continue
            out.append(s)
        if self.sort_key:
            key, rev = self.sort_key
            out.sort(key=key, reverse=rev)
        return out

    def sort_results(self, col):
        getters = {"sev": lambda s: ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE").index(s.grade),
                   "ip": lambda s: s.ioc_key, "req": lambda s: s.requests, "c2": lambda s: s.c2xx,
                   "c3": lambda s: s.c3xx, "c4": lambda s: s.c4xx, "c404": lambda s: s.c404,
                   "c5": lambda s: s.c5xx, "c500": lambda s: s.c500, "why": lambda s: s.verdict}
        prev_col, prev_rev = getattr(self, "_sort_state", (None, False))
        # 같은 열을 다시 누르면 순서를 뒤집는다. 숫자 열은 큰 값부터 시작한다.
        rev = (not prev_rev) if prev_col == col else (col not in ("sev", "ip", "why"))
        self._sort_state = (col, rev)
        self.sort_key = (getters[col], rev)
        self.fill_results()

    def fill_results(self):
        tv = self.tree
        tv.delete(*tv.get_children())
        rows = self.filtered_summaries()
        for s in rows:
            vals = (s.grade if s.accessed else "-", s.ioc_key, s.requests,
                    *(("", "", "", "", "", "") if not s.accessed else (s.c2xx, s.c3xx, s.c4xx, s.c404, s.c5xx, s.c500)),
                    s.verdict)
            tv.insert("", "end", iid=s.ioc_key, values=vals, tags=(s.grade if s.accessed else "-",))
        if self.selected_ip in [s.ioc_key for s in rows]:
            tv.selection_set(self.selected_ip)
        else:
            self.selected_ip = None
            self.fill_detail()

    def on_select_ip(self):
        sel = self.tree.selection()
        if sel:
            if sel[0] != self.selected_ip:
                self.status_filter.set("all")
            self.selected_ip = sel[0]
        self.fill_detail()

    def ip_records(self, key):
        srv = self.server_filter.get()
        return [r for r in self.result.records if r.ioc_key == key and (srv == ALL_SERVERS or r.server == srv)]

    def detail_records(self):
        recs = sorted(self.ip_records(self.selected_ip), key=lambda r: (r.time is None, r.time))
        f = self.status_filter.get()
        if f == "atk":
            return [r for r in recs if any(t in ATTACK_TAGS for t in r.tags)]
        if f == "unp":
            return [r for r in recs if r.unparsed]
        if f in ("404", "500"):
            return [r for r in recs if r.status == int(f)]
        if f in ("2", "3", "4", "5"):
            return [r for r in recs if r.status and str(r.status).startswith(f)]
        return recs

    def _update_ai_button(self):
        b = getattr(self, "btn_ai", None)
        if not b or not b.winfo_exists():
            return
        if self._ai_running:
            b.configure(text="AI 분석 중…")
            return b.state(["disabled"])
        s = next((x for x in self.result.summaries if x.ioc_key == self.selected_ip), None)
        # 이미 분석한 IP는 [AI 결과 보기]. 누르면 받은 결과를 다시 연다 (창 안의 [다시 분석]으로 새로 보냄)
        b.configure(text="AI 결과 보기" if s and s.ioc_key in self.result.ai_notes else "AI 분석")
        b.state(["!disabled"] if s and s.accessed else ["disabled"])

    def fill_detail(self):
        self._update_ai_button()
        d = self.detail
        for w in d.winfo_children():
            w.destroy()
        s = next((x for x in self.current_summaries() if x.ioc_key == self.selected_ip), None)
        if not s:
            label(d, "위 표에서 IP를 고르면 그 IP의 로그와 확인 근거가 여기에 표시됩니다.", 10, fg=C["sub"]).pack(padx=14, pady=20)
            return
        dh = tk.Frame(d, bg=C["panel"])
        dh.pack(fill="x", padx=12, pady=(10, 2))
        label(dh, s.ioc_key, 12, True, mono=True).pack(side="left")
        badge(dh, s.grade if s.accessed else "미발견", grade=s.grade).pack(side="left", padx=8)
        info = []
        if s.server_counts:
            # 서버가 많으면 건수가 많은 2대만 쓰고 나머지는 'N대 더' (전체는 아래 로그 표의 서버 칸)
            top = sorted(s.server_counts.items(), key=lambda kv: -kv[1])
            more = f" 외 {len(top) - 2}대" if len(top) > 2 else ""
            info.append(", ".join(f"{k} {v}건" for k, v in top[:2]) + more)
        if s.first_seen:
            info.append(f"관찰 {s.first_seen:%m-%d %H:%M:%S} ~ {s.last_seen:%m-%d %H:%M:%S}")
        if s.is_range and s.matched_ips:
            info.append("발견 IP " + ", ".join(s.matched_ips[:3]) + (" …" if len(s.matched_ips) > 3 else ""))
        label(dh, " · ".join(info), 9, fg=C["sub"]).pack(side="left")
        label(d, "확인 근거: " + s.verdict, 9, fg=C["sub"], anchor="w", wraplength=820, justify="left").pack(fill="x", padx=12)
        if not s.accessed:
            label(d, f"{NOT_FOUND}. 분석하지 못한 서버·구간은 위 '분석 범위'를 확인하세요.", 10, fg=C["sub"]
                  ).pack(anchor="w", padx=12, pady=12)
            return
        recs = self.ip_records(s.ioc_key)
        counts = {"all": len(recs)}
        for k in ("2", "3", "4", "5"):
            counts[k] = sum(1 for r in recs if r.status and str(r.status).startswith(k))
        counts["404"] = sum(1 for r in recs if r.status == 404)
        counts["500"] = sum(1 for r in recs if r.status == 500)
        counts["atk"] = sum(1 for r in recs if any(t in ATTACK_TAGS for t in r.tags))
        counts["unp"] = sum(1 for r in recs if r.unparsed)
        # '해석 실패' 칸은 그런 줄이 있을 때만 보인다
        filters = [x for x in STATUS_FILTERS if x[0] != "unp" or counts["unp"]]
        seg(d, [(k, f"{t} {counts[k]}") for k, t in filters], self.status_filter,
            command=lambda v: self.fill_logs()).pack(fill="x", padx=12, pady=(4, 6))
        # 원본 위치·태그처럼 짧고 중요한 칸은 앞쪽에 다 보이게 두고, 긴 URL은 맨 뒤에서 남는 폭을 쓴다.
        # 잘린 URL·원문은 줄을 고르면 표 아래에 전체가 나온다
        cols = ("t", "srv", "loc", "m", "code", "size", "tag", "url")
        heads = ("시간", "서버", "원본 위치", "메서드", "응답", "크기", "태그", "URL")
        longest = max((len(x.name) for x in self.result.servers), default=8)
        widths = [self.px(w) for w in (112, max(90, min(200, 9 * longest + 16)), 190, 56, 46, 64, 112, 160)]
        fr, self.log_tv = scrolled_tree(d, xscroll=True, columns=cols, show="headings", height=6, style="Mono.Treeview")
        for col, h, w in zip(cols, heads, widths):
            self.log_tv.heading(col, text=h)
            self.log_tv.column(col, width=w, minwidth=min(w, 60) if col != "url" else 160,
                               anchor="center" if col in ("code", "m") else "e" if col == "size" else "w",
                               stretch=(col == "url"))
        self.log_tv.bind("<<TreeviewSelect>>", lambda e: self.show_log_line())
        # 아래쪽 안내 줄(고른 줄 원문, 'N줄 더 있음')은 표보다 먼저 아래에 붙여 항상 보이게 한다
        bottom = tk.Frame(d, bg=C["panel"])
        bottom.pack(side="bottom", fill="x", padx=12, pady=(0, 6))
        self.lbl_more = label(bottom, "", 9, fg=C["amber"])
        self.lbl_more.pack(side="right", anchor="n")
        self.lbl_line = label(bottom, "줄을 고르면 원문 전체가 여기에 나옵니다.", 9, fg=C["sub"], mono=True, anchor="w",
                              justify="left", wraplength=self.px(800))
        self.lbl_line.pack(side="left", fill="x", expand=True)
        self.log_tv.tag_configure("c2", foreground=C["green"])
        self.log_tv.tag_configure("c4", foreground=C["amber"])
        self.log_tv.tag_configure("c5", foreground=C["red"], font=(F["mono"], 10, "bold"))
        self.log_tv.tag_configure("unp", foreground=C["sub"])
        fr.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.fill_logs()

    def fill_logs(self):
        tv = self.log_tv
        tv.delete(*tv.get_children())
        recs = self.detail_records()
        self._log_rows = recs[:MAX_DETAIL_ROWS]
        for i, r in enumerate(self._log_rows):
            code = str(r.status or "-")
            loc = f"{os.path.basename(r.file.replace(chr(92), '/'))}:{r.line_no:,}"
            # 해석 실패 기록은 URL 칸에 원문을 보여 준다 (요청·응답을 읽지 못했으므로)
            url = r.raw[:300] if r.unparsed else r.url
            when = r.time.strftime("%m-%d %H:%M:%S") if r.time else ("시간 모름" if r.unparsed else "")
            tv.insert("", "end", iid=str(i), values=(when, r.server, loc, r.method or "-", code,
                                                     f"{r.bytes:,}" if not r.unparsed else "-",
                                                     ", ".join(TAG_LABELS.get(t, t) for t in r.tags), url),
                      tags=("unp" if r.unparsed else "c" + code[0],))
        more = len(recs) - MAX_DETAIL_ROWS
        self.lbl_more.configure(text=f"{more}줄 더 있음 (전체는 보고서)" if more > 0 else "")

    def show_log_line(self):
        """고른 로그 줄의 원문 전체 (표에서 잘린 URL·원문 확인용)."""
        sel = self.log_tv.selection()
        lbl = getattr(self, "lbl_line", None)
        if not sel or lbl is None or not lbl.winfo_exists():
            return
        r = self._log_rows[int(sel[0])]
        loc = f"{r.server} {os.path.basename(r.file.replace(chr(92), '/'))}:{r.line_no:,}"
        raw = r.raw if len(r.raw) <= 260 else r.raw[:260] + " …"   # 두 줄 안쪽 (전체는 [선택 로그 복사]·보고서)
        lbl.configure(text=f"{loc}  {raw}", fg=C["text"])

    @staticmethod
    def _chip_detail(s):
        # 실패 이유는 짧게 ('연결 실패: 접속 실패: ...'처럼 겹치는 말은 뺀다). 전체 이유는 점검 완료 창·엑셀에 있다
        return s.detail_text() if s.ok else f"{s.state}: {s.reason.replace('접속 실패: ', '')[:28]}"

    def _chips_fit(self, servers) -> bool:
        """서버 상태 칸(서버마다 칩)이 결과 화면 폭 한 줄에 들어가는지 글자 폭을 재서 본다."""
        import tkinter.font as tkfont
        f = tkfont.Font(family=F["ui"], size=9)
        fb = tkfont.Font(family=F["ui"], size=9, weight="bold")
        need = sum(fb.measure(s.name) + f.measure(self._chip_detail(s)) + 46 + (0 if s.ok else f.measure("다시 점검") + 40)
                   for s in servers)
        width = self.root.winfo_width()
        if width < 300:   # 아직 창이 그려지지 않았으면 기본 크기로 본다
            width = self.px(1280)
        return need <= width - self.px(370) - 60

    def _server_summary_chip(self, parent, servers):
        """서버가 많을 때 서버 상태를 한 줄로: '서버 6대 · 완료 4 · 부분 분석 1 · 연결 실패 1 [서버별 보기]'."""
        counts = {}
        for x in servers:
            counts[x.state] = counts.get(x.state, 0) + 1
        failed = [x.name for x in servers if not x.ok]
        col = C["red"] if failed else C["amber"] if any(x.state == "부분 분석" for x in servers) else C["green"]
        chip = tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=col if failed else C["line"])
        chip.pack(side="left")
        label(chip, "●", 9, fg=col).pack(side="left", padx=(8, 2), pady=5)
        label(chip, f"서버 {len(servers)}대", 9, True).pack(side="left")
        label(chip, " · ".join(f"{k} {v}" for k, v in counts.items()), 9, fg=C["sub"]).pack(side="left", padx=(6, 8))
        ttk.Button(chip, text="서버별 보기", style="Small.TButton",
                   command=self.show_server_status).pack(side="left", padx=(0, 6), pady=3)
        if failed:
            ttk.Button(chip, text=f"실패 {len(failed)}대 다시 점검", style="Small.TButton",
                       command=lambda: self.retry_many(failed)).pack(side="left", padx=(0, 6), pady=3)

    def show_parse_errors(self):
        rows, texts = [], []
        for x in self.result.servers:
            for f, ln, text, has_ip in x.parse_error_lines:
                rows.append((x.name, f"{os.path.basename(str(f).replace(chr(92), '/'))}:{ln}", text[:300],
                             "bad" if has_ip else ""))
                texts.append(text)
        total = sum(x.parse_errors for x in self.result.servers)
        sub = (f"모두 {total}줄 중 {len(rows)}줄 (서버마다 최대 50줄). 빨간 줄은 의심 IP가 든 줄입니다."
               if total > len(rows) else f"모두 {total}줄. 빨간 줄은 의심 IP가 든 줄입니다.")
        note = ("로그 형식과 맞지 않아 읽지 못한 줄입니다. 의심 IP가 든 줄이면 결과에서 빠진 기록일 수 있습니다.\n"
                "형식을 맞추려면 [원문 복사] 후 [서버 설정] > 그 서버 > [형식 편집]의 샘플 칸에 붙여넣고 [자동 확인]·[시험]을 누르세요. "
                "스택트레이스나 잘린 줄처럼 원래 IP 기록이 아닌 줄이면 무시해도 됩니다.")
        longest = max((len(r[0]) for r in rows), default=8)
        ListDialog(self.root, "읽지 못한 줄", "해석 실패: 로그 형식과 맞지 않는 줄",
                   [("서버", max(90, 9 * longest)), ("위치", 170), ("원문", 560)], rows, sub=sub, note=note,
                   copy_text="\n".join(texts), width=self.px(900)).show()

    def show_server_status(self):
        rows = [(x.name, x.state, x.detail_text().split(" · ", 1)[-1] if x.ok else x.reason, "good" if x.ok else "bad")
                for x in self.result.servers]
        ListDialog(self.root, "서버별 상태", f"서버 {len(rows)}대 점검 상태", [("서버", 160), ("상태", 90), ("내용", 420)],
                   rows).show()

    def retry_many(self, names):
        if self.local_folder:
            self.start_check(retry_servers=list(names))
            return
        servers = [s for s in self.cfg["servers"] if s["name"] in set(names)]
        if servers:
            self.start_check(retry_servers=servers)

    def retry(self, name):
        if self.local_folder:
            self.start_check(retry_servers=[name])
            return
        servers = [s for s in self.cfg["servers"] if s["name"] == name]
        if servers:
            self.start_check(retry_servers=servers)

    # ------------------------------------------------------------ 저장·복사
    # ------------------------------------------------------------ AI 분석
    def current_ai_key(self, ai=None):
        """이번 실행에서 입력한 키, 없으면 [키 저장]으로 암호화해 둔 키."""
        ai = ai or self.cfg["ai"]
        if not self.ai_key and ai.get("key_saved"):
            try:
                self.ai_key = aimod.unprotect(ai["key_saved"])
            except (OSError, ValueError, AttributeError) as e:
                raise aimod.AiError("저장된 API 키를 풀지 못했습니다. [서버 설정] > AI 분석에서 키를 다시 입력하세요.") from e
        return self.ai_key

    def _ai_ready(self, ai):
        """AI 주소와 키를 준비한다. 키가 없으면 입력받는다. 쓸 수 없으면 None."""
        try:
            url = aimod.endpoint(ai)
            if ai.get("api") == "anthropic" and not (ai.get("model") or "").strip():
                raise aimod.AiError(aimod.NEED_MODEL)
            api_key = self.current_ai_key(ai)
        except aimod.AiError as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return None
        if not api_key and ai.get("key_header", "").strip():
            got = PasswordDialog(self.root, [("ai", f"AI API 키 · {url}")], title="AI API 키", heading="AI API 키를 입력하세요",
                                 note="키는 프로그램을 끄면 지워집니다. 계속 쓰려면 [서버 설정] > AI 분석에서 [키 저장]을 켜세요.").show()
            if got is None:
                return None
            api_key = self.ai_key = got["ai"].strip()
        return url, api_key

    def _ai_busy(self, text):
        self._ai_running = True
        self._ai_status = text
        self._update_ai_button()
        self.toast(text=text, fg=C["sub"])

    def toast(self, text, fg):
        """결과 화면 아래 안내 줄. 다른 탭에 가 있어 줄이 없으면 건너뛴다."""
        lbl = getattr(self, "lbl_toast", None)
        if lbl is not None and lbl.winfo_exists():
            lbl.configure(text=text, fg=fg)

    def ai_pending(self):
        """AI 분석을 아직 받지 않은, 접속 기록이 있는 IP (AI 분석을 켰을 때만)."""
        if not self.cfg["ai"].get("enabled"):
            return []
        return [s for s in self.result.summaries if s.accessed and s.ioc_key not in self.result.ai_notes]

    def run_ai(self, key, force=False):
        """고른 IP의 로그를 AI에 보내 분석을 받는다. 이미 받은 분석이 있으면 그것을 보여준다."""
        if key in self.result.ai_notes and not force:
            return self.show_ai(key)
        if self.worker.busy:
            return
        ai = dict(self.cfg["ai"])
        s = next(x for x in self.result.summaries if x.ioc_key == key)
        recs = [r for r in self.result.records if r.ioc_key == key]
        ready = self._ai_ready(ai)
        if not ready:
            return
        url, api_key = ready
        host = url.split("/")[2]
        if host not in self._ai_confirmed:
            p = aimod.build_prompt(s, recs, int(ai.get("max_lines") or 60), bool(ai.get("mask", True)))
            if not messagebox.askyesno(
                    APP_NAME, f"{key}의 로그 {p.lines_sent}줄(같은 요청은 묶어 {p.groups_sent}줄)과 규칙 판정을 AI 서버로 보냅니다.\n\n"
                              f"주소: {host}\n민감값 가림: {'켜짐' if p.masked else '꺼짐'}\n\n"
                              "보낼까요? (이번 실행 동안 이 주소는 다시 묻지 않습니다)", parent=self.root):
                return
            self._ai_confirmed.add(host)
        self._ai_busy(f"{key} AI 분석 중… (최대 {ai.get('timeout')}초)")

        def done(note):
            self._ai_running = False
            self.result.ai_notes[key] = note
            audit.log_ai(key, url, note.lines_sent, note.masked, True)
            self.toast(text=f"{key} AI 분석을 받았습니다. [보고서 저장]하면 보고서에도 들어갑니다.", fg=C["green"])
            self._update_ai_button()
            self.show_ai(key)

        def fail(e):
            self._ai_running = False
            audit.log_ai(key, url, 0, bool(ai.get("mask", True)), False, str(e))
            self.toast(text="AI 분석 실패", fg=C["red"])
            self._update_ai_button()
            if isinstance(e, aimod.AiError):
                messagebox.showerror(APP_NAME, f"AI 분석에 실패했습니다.\n\n{e}", parent=self.root)
            else:
                messagebox.showerror(APP_NAME, f"AI 분석 중 오류: {type(e).__name__}: {e}", parent=self.root)
        self.worker.start(lambda **kw: aimod.analyze(ai, api_key, s, recs), on_done=done, on_error=fail)

    def show_ai(self, key):
        s = next((x for x in self.result.summaries if x.ioc_key == key), None)
        if AiDialog(self.root, self.result.ai_notes[key], s.grade if s else "-").show() == "retry":
            self.run_ai(key, force=True)

    # ------------------------------------------------------------ 이메일
    def current_mail_token(self, em=None):
        """이번 실행에서 입력한 토큰, 없으면 [토큰 저장]으로 암호화해 둔 토큰."""
        em = em or self.cfg["email"]
        if not self.mail_token and em.get("token_saved"):
            try:
                self.mail_token = aimod.unprotect(em["token_saved"])
            except (OSError, ValueError, AttributeError) as e:
                raise aimod.AiError("저장된 인증 토큰을 풀지 못했습니다. [서버 설정] > 이메일에서 토큰을 다시 입력하세요.") from e
        return self.mail_token

    def email_report(self):
        """회신 요약을 본문으로, HTML 보고서를 첨부해 사내 메일 API로 보낸다.

        AI 분석을 켰으면 저장할 때처럼, 아직 분석하지 않은 IP를 보내기 전에 분석할지 먼저 묻는다.
        """
        if self.worker.busy:
            self.toast(text="진행 중인 작업이 끝나면 보낼 수 있습니다.", fg=C["amber"])
            return
        self._ai_before(MAIL_MODE, self._email_compose)

    def _email_compose(self):
        em = dict(self.cfg["email"])
        res = self.result
        name = report.attachment_filename(res, em.get("title_prefix", ""))     # 첨부 파일 이름에도 제목 앞말
        try:
            html = report_html.build_html(res, mask=bool(em.get("mask_report", True)), ioc_result=self.ioc_res).encode("utf-8")
            attach_err = "" if len(html) <= mailer.MAX_FILE else (
                f"보고서가 10MB를 넘어 첨부할 수 없습니다 ({len(html) / 1e6:.1f}MB). [보고서 저장]으로 저장해 따로 전달하세요.")
        except Exception as e:  # noqa: BLE001 - 첨부만 빠지고 본문은 보낼 수 있게
            html, attach_err = b"", f"보고서를 만들지 못했습니다: {type(e).__name__}: {e}"
        size = f"{len(html) / 1024:.0f}KB" if len(html) < 1024 * 1024 else f"{len(html) / 1e6:.1f}MB"
        title = report.email_title(res, em.get("title_prefix", ""))
        pending = self.ai_pending()
        note = f"AI 분석을 받지 않은 IP {len(pending)}개는 AI 내용 없이 첨부됩니다." if pending else ""
        got = EmailDialog(self.root, em.get("to", ""), em.get("cc", ""), title, report.MEMO,
                          lambda memo, attach_name: report.email_text(res, memo, attach_name),
                          name, size, attach_err, note).show()
        if not got:
            return
        name = got["name"] or name                     # 보내기 창에서 첨부 파일 이름을 바꿀 수 있다
        # 본문은 요약 카드·IP 표가 든 HTML (규격상 text/html 허용). 전할 말만 사용자가 고친다
        body = report.email_html(res, got["memo"], name if got["attach"] else "")
        try:
            url = mailer.endpoint(em)
            token = self.current_mail_token(em)
            if not token and em.get("token_header", "").strip():
                pw = PasswordDialog(self.root, [("mail", f"메일 발송 토큰 · {url}")], title="이메일 인증 토큰",
                                    heading="메일 발송 API 인증 토큰을 입력하세요",
                                    note="토큰은 프로그램을 끄면 지워집니다. 계속 쓰려면 [서버 설정] > 이메일에서 [토큰 저장]을 켜세요.").show()
                if pw is None:
                    return
                token = self.mail_token = pw["mail"].strip()
            to, cc = mailer.split_addrs(got["to"]), mailer.split_addrs(got["cc"])
            req = mailer.build_request(em, to, cc, got["title"], body, [(name, html)] if got["attach"] else [],
                                       content_type="text/html")
        except (mailer.MailError, aimod.AiError) as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.root)
            return
        self.toast(text=f"이메일 보내는 중… (받는 사람 {len(to)}명, 최대 {em.get('timeout')}초)", fg=C["sub"])
        self.root.configure(cursor="watch")

        def done(r):
            self.root.configure(cursor="")
            audit.log_email(url, r.ord_no, len(to), len(cc), got["attach"], True, r.tr_no)
            self.toast(text=f"이메일을 보냈습니다 (받는 사람 {len(to)}명{', 보고서 첨부' if got['attach'] else ''}).", fg=C["green"])
            messagebox.showinfo(APP_NAME, f"이메일을 보냈습니다.\n\n받는 사람: {', '.join(to)}"
                                + (f"\n참조: {', '.join(cc)}" if cc else "")
                                + f"\n첨부: {name if got['attach'] else '없음'}\n처리 상태: {r.status or '-'} · "
                                  f"거래번호: {r.tr_no or '-'} · 주문번호: {r.ord_no}", parent=self.root)

        def fail(e):
            self.root.configure(cursor="")
            audit.log_email(url, req.get("ordNo", ""), len(to), len(cc), got["attach"], False, error=str(e))
            self.toast(text="이메일을 보내지 못했습니다.", fg=C["red"])
            messagebox.showerror(APP_NAME, f"이메일을 보내지 못했습니다.\n\n{e}" if isinstance(e, mailer.MailError)
                                 else f"이메일 보내는 중 오류: {type(e).__name__}: {e}", parent=self.root)
        self.worker.start(lambda **kw: mailer.send(em, token, req), on_done=done, on_error=fail)

    def copy_reply(self):
        self._clip(report.reply_text(self.result))
        self.toast(text="회신 요약을 복사했습니다. 메일이나 메신저에 붙여넣으세요.", fg=C["green"])

    def copy_logs(self):
        if not self.selected_ip:
            self.toast(text="먼저 IP를 고르세요.", fg=C["amber"])
            return
        rows = self.detail_records()
        self._clip("\n".join("\t".join([r.ioc_key, r.time.strftime("%Y-%m-%d %H:%M:%S") if r.time else "", r.server,
                                        r.method, r.url, str(r.status or ""), str(r.bytes),
                                        f"{os.path.basename(r.file)}:{r.line_no}"]) for r in rows))
        self.toast(text=f"로그 {len(rows)}줄을 복사했습니다.", fg=C["green"])

    def _clip(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def save_report(self):
        """보고서 저장: HTML(기본, 브라우저로 열림) 또는 엑셀. 파일 형식 목록에서 고른다.

        AI 분석을 켰으면, 아직 분석하지 않은 IP를 저장 전에 AI로 분석해 같이 넣는다 (이미 받은 분석은 그대로 쓴다).
        """
        if self.worker.busy:
            self.toast(text="AI 분석이 끝나면 저장할 수 있습니다.", fg=C["amber"])
            return
        folder = str(paths.resolve(self.cfg["general"].get("save_dir", "out")))
        os.makedirs(folder, exist_ok=True)
        p = filedialog.asksaveasfilename(parent=self.root, title="보고서 저장", initialdir=folder,
                                         initialfile=report.default_filename(self.result), defaultextension=".html",
                                         filetypes=[("HTML 보고서 (브라우저로 열기)", "*.html"), ("Excel", "*.xlsx")])
        if not p:
            return
        mode = dict(SAVE_MODE, sub=f"다 받으면 {os.path.basename(p)} 파일로 저장합니다.")
        self._ai_before(mode, lambda: self._write_report(p))

    def _ai_before(self, mode, after):
        """AI 분석을 켰고 아직 분석하지 않은 IP가 있으면 먼저 분석할지 묻고, 끝나면 after()를 부른다 (저장·이메일 공통).

        [예] 분석한 뒤 계속 / [아니요] AI 분석 없이 계속 / [취소] 그만둠
        """
        todo = self.ai_pending()
        if not todo:
            return after()
        ai = dict(self.cfg["ai"])
        try:
            host = aimod.endpoint(ai).split("/")[2]
        except aimod.AiError:
            host = "(주소 설정 확인 필요)"
        names = ", ".join(s.ioc_key for s in todo[:5]) + (f" 외 {len(todo) - 5}개" if len(todo) > 5 else "")
        done_n = len(self.result.ai_notes)
        act = mode["act"]
        ans = messagebox.askyesnocancel(
            APP_NAME, f"AI 분석을 아직 하지 않은 IP가 {len(todo)}개 있습니다.\n{names}\n\n"
                      f"{mode['before']} AI로 분석해서 {mode['obj']}에 넣을까요?"
                      + (f" (이미 분석한 {done_n}개는 받은 결과를 그대로 넣습니다)" if done_n else "") + "\n\n"
                      f"주소: {host}\n보내는 로그: IP마다 최대 {ai.get('max_lines')}줄 · "
                      f"민감값 가림 {'켜짐' if ai.get('mask', True) else '꺼짐'}\n\n"
                      f"[예] 분석한 뒤 {act}    [아니요] AI 분석 없이 {act}    [취소] {act} 안 함", parent=self.root)
        if ans is None:
            return
        if not ans:
            return after()
        ready = self._ai_ready(ai)
        if ready:
            self._ai_then(todo, ai, *ready, mode=mode, after=after)

    def _ai_then(self, todo, ai, url, api_key, mode, after):
        """todo IP를 차례로 AI에 보내고, 받은 만큼 넣은 뒤 after()를 부른다. 실패한 IP는 AI 내용 없이 넘어간다."""
        self._ai_confirmed.add(url.split("/")[2])                 # 확인 창에서 이미 물었다
        act = mode["act"]
        recs = {}
        for r in self.result.records:
            recs.setdefault(r.ioc_key, []).append(r)
        jobs = [(s, recs.get(s.ioc_key, [])) for s in todo]
        self._ai_busy(f"{mode['obj']}에 넣을 AI 분석 중… 0/{len(jobs)} ({mode['next']})")
        # 진행 창: 떠 있는 동안 다른 탭으로 가지 못하게 하고, [중지]하면 받은 것까지만 넣고 계속한다
        dlg = self._save_progress = ProgressDialog(
            self.root, mode["title"], f"AI 분석 후 {act} · IP {len(jobs)}개",
            f"{mode['sub']} IP 하나에 최대 {ai.get('timeout')}초 걸릴 수 있습니다.",
            len(jobs), lambda: self.worker.cancel.set(), stop_text=f"중지 (받은 것까지 넣고 {act})").open()

        def work(on_event, cancel, **kw):
            notes, errs = {}, []
            for i, (s, rs) in enumerate(jobs, 1):
                if cancel.is_set():
                    break
                on_event((i, s.ioc_key))
                try:
                    notes[s.ioc_key] = aimod.analyze(ai, api_key, s, rs)
                except Exception as e:  # noqa: BLE001 - 한 IP가 실패해도 나머지는 계속 보낸다
                    errs.append((s.ioc_key, str(e) if isinstance(e, aimod.AiError) else f"{type(e).__name__}: {e}"))
                    if len(errs) >= 2 and not notes:             # 처음부터 연달아 실패하면 설정 문제로 보고 멈춘다
                        break
            return notes, errs

        def progress(ev):
            i, key = ev
            self._ai_status = f"{mode['obj']}에 넣을 AI 분석 중… {i}/{len(jobs)} · {key} ({mode['next']})"
            self.toast(text=self._ai_status, fg=C["sub"])
            dlg.step(i, f"{key} 분석 중…")

        def done(res):
            notes, errs = res
            self._ai_running = False
            dlg.close()
            for k, n in notes.items():
                self.result.ai_notes[k] = n
                audit.log_ai(k, url, n.lines_sent, n.masked, True)
            for k, e in errs:
                audit.log_ai(k, url, 0, bool(ai.get("mask", True)), False, e)
            self._update_ai_button()
            missed = [s for s, _ in jobs if s.ioc_key not in notes]
            why = "\n\n" + "\n".join(f"{k}: {e}" for k, e in errs[:3]) if errs else ""
            if missed and dlg.stopped:
                messagebox.showwarning(
                    APP_NAME, f"AI 분석을 중지했습니다. {len(jobs)}개 중 {len(notes)}개를 받았습니다. "
                              f"받지 못한 {len(missed)}개 IP는 AI 내용 없이 {mode['go']}." + why, parent=self.root)
            elif missed:
                slow = any("시간 초과" in e for _, e in errs)
                ans = messagebox.askyesnocancel(
                    APP_NAME, f"AI 분석 {len(jobs)}개 중 {len(notes)}개를 받았습니다. 받지 못한 IP가 {len(missed)}개 있습니다." + why
                    + ("\n\nAI 서버가 바쁘거나 그 요청에 처음 답할 때는 느릴 수 있어, 다시 시도하면 될 때가 많습니다. "
                       "자주 그러면 [서버 설정] > AI 분석에서 시간 제한을 늘리세요." if slow else "")
                    + f"\n\n[예] 받지 못한 IP 다시 시도    [아니요] 그 IP는 AI 내용 없이 {act}    [취소] {act} 안 함 (받은 분석은 남습니다)",
                    parent=self.root)
                if ans is None:
                    self.toast(text=f"{mode['none']}. 받은 AI 분석은 남아 있습니다.", fg=C["amber"])
                    return
                if ans:
                    return self._ai_then(missed, ai, url, api_key, mode, after)
            after()

        def fail(e):
            self._ai_running = False
            dlg.close()
            self._update_ai_button()
            messagebox.showerror(APP_NAME, f"AI 분석 중 오류: {type(e).__name__}: {e}\n\nAI 내용 없이 {mode['go']}.", parent=self.root)
            after()
        self.worker.start(work, on_event=progress, on_done=done, on_error=fail)

    def _write_report(self, p):
        if p.lower().endswith(".xlsx") and not report.has_xlsx():
            messagebox.showinfo(APP_NAME, "엑셀 저장 모듈(openpyxl)이 설치되지 않아 CSV 파일(엑셀에서 열림)로 저장합니다.\n"
                                          f"저장 폴더: {os.path.splitext(p)[0]}_csv", parent=self.root)
        try:
            kind, saved = report.save(self.result, p, mask=self.cfg["report"].get("mask_query", False),
                                      ioc_result=self.ioc_res)
        except PermissionError:
            messagebox.showerror(APP_NAME, "파일을 저장할 수 없습니다. 같은 파일이 엑셀에서 열려 있으면 닫고 다시 시도하세요.",
                                 parent=self.root)
            return
        shown = os.path.dirname(saved[0]) if kind == "csv" else saved
        audit.log_save(shown)
        self.unsaved = False
        self.toast(text=f"저장됨: {shown}", fg=C["green"])
        SavedDialog(self.root, saved[0] if kind == "csv" else saved).show()

    # ------------------------------------------------------------ 연결 테스트·종료
    def connection_test(self, servers=None):
        servers = servers or self.selected_servers()
        if not servers:
            messagebox.showinfo(APP_NAME, "연결 테스트할 서버를 고르세요 (로컬 폴더 모드에서는 필요 없습니다).", parent=self.root)
            return
        if self.worker.busy or not self.ask_passwords(servers):
            return
        from .settings_page import run_connection_test
        self.root.configure(cursor="watch")

        def done(rows):
            self.root.configure(cursor="")
            ListDialog(self.root, "연결 테스트", "연결 테스트 결과", [("서버", 90), ("주소", 120), ("결과", 420)],
                       [(r["name"], r["host"], r["text"], "good" if r["ok"] else "bad") for r in rows]).show()

        def err(e):
            self.root.configure(cursor="")
            messagebox.showerror(APP_NAME, str(e), parent=self.root)

        self.worker.start(run_connection_test, on_done=done, on_error=err, servers=servers, main=self)

    def on_close(self):
        if self.worker.busy:
            if not messagebox.askyesno(APP_NAME, "진행 중인 작업(점검·AI 분석)을 중지하고 종료할까요?", parent=self.root):
                return
            self.worker.cancel.set()
        elif self.unsaved and not messagebox.askyesno(
                APP_NAME, "결과를 보고서로 저장하지 않았습니다 (요약은 점검 이력에 있습니다). 종료할까요?", parent=self.root):
            return
        self.root.destroy()


def _mmss(sec) -> str:
    sec = int(sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"

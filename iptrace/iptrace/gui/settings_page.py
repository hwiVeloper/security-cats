"""[서버 설정] 탭: 서버 / 판정 기준 / 일반 / AI 분석. [저장]해야 config.json에 반영된다."""
import copy
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, paths
from ..core import ai as aimod
from ..core import config as cfgmod
from ..core.collector import AuthFailed, AuthGuard, CollectError, SshSource
from ..core.detector import Signatures
from ..core.parser import EXAMPLES, FORMAT_HELP, LogFormatError, LogParser, detect_format, resolve_format
from .dialogs import AskDialog
from .theme import C, F
from .widgets import ascii_only, buttons_right, card, center_window, check, label, scroll_area, seg, var

PERIOD_ITEMS = (("today", "오늘"), ("yesterday", "어제"), ("7", "7일"))
SAMPLE_LINES = 5


def run_connection_test(on_event, cancel, on_unknown_host, servers, main, cfg=None):
    """(작업 스레드) 서버마다 접속 → 로그 폴더 → 파일 목록 → 형식 확인(앞 5줄만 받음)."""
    cfg = cfg or main.cfg
    known = str(paths.resolve(cfg["ssh"]["known_hosts"]))
    rows = []
    guard = AuthGuard()
    for s in servers:
        row = {"name": s["name"], "host": s["host"], "ok": False, "text": "", "detect": [], "fmt_ok": None}
        pw = main.pw_for(s, cfg)
        src = SshSource(s, cfgmod.effective_ssh(cfg, s), pw, known, on_unknown_host, guard=guard)
        try:
            src.connect()
            src.check()
            # 로그 경로마다: 파일 개수, 가장 최근 파일 앞 5줄로 그 경로의 형식이 맞는지
            row["paths"] = []
            total = 0
            for path, fmt_name in src.logs:
                files = src.list_files_for(path, fmt_name)
                total += len(files)
                pr = {"path": path, "format": fmt_name, "files": len(files), "fmt_ok": None, "detect": []}
                if files:
                    sample = src.sample_lines(files[-1], SAMPLE_LINES)
                    p = LogParser(resolve_format(cfg, fmt_name))
                    pr["fmt_ok"] = (sum(1 for x in sample if p.match(x)), len(sample))
                    pr["detect"] = detect_format(sample, cfg)
                row["paths"].append(pr)
            bad = [x for x in row["paths"] if x["fmt_ok"] and x["fmt_ok"][0] < x["fmt_ok"][1]]
            row["ok"] = True
            row["text"] = "   ".join(["✔ 접속", "✔ 로그 폴더", f"✔ 파일 {total}개"]
                                     + ([f"✖ 형식 안 맞는 경로 {len(bad)}개"] if bad else []))
        except (CollectError, LogFormatError) as e:
            row["text"] = f"✖ {e}"
            if isinstance(e, AuthFailed):
                # 틀린 비밀번호는 다시 쓰지 않는다 (여러 번 틀리면 계정이 잠김)
                main.mark_auth_failed(s, pw, cfg)
                row["auth_failed"] = True
                row["text"] += " · 새 비밀번호를 입력할 때까지 이 계정으로 다시 접속하지 않습니다"
        finally:
            src.close()
        rows.append(row)
    return rows


class SettingsPage:
    def __init__(self, parent, main):
        self.main = main
        st = main.settings_state
        self.cfg = st.get("cfg") or copy.deepcopy(main.cfg)
        self.cur_server = st.get("cur_server", 0)
        self.page = var(st.get("page", "server"))
        self.test_result = st.get("test_result")
        self.form = {}
        w = self.frame = tk.Frame(parent, bg=C["bg"])
        w.pack(fill="both", expand=True, padx=18, pady=(12, 12))
        head = tk.Frame(w, bg=C["bg"])
        head.pack(fill="x")
        label(head, "서버 설정", 15, True, bg=C["bg"]).pack(side="left")
        first = not main.cfg["servers"]
        msg = ("먼저 점검할 서버를 추가하세요. 이미 받아 둔 로그 파일만 볼 때는 [로그 점검] 탭의 [로컬 폴더...]를 쓰면 됩니다."
               if first else "서버를 등록하면 [로그 점검] 탭에서 고를 수 있습니다. 비밀번호는 저장하지 않습니다.")
        label(head, msg, 9, fg=C["amber"] if first else C["sub"], bg=C["bg"]).pack(side="left", padx=10, pady=(6, 0))
        seg(w, (("server", "서버"), ("rule", "판정 기준"), ("general", "일반"), ("ai", "AI 분석"), ("email", "이메일")),
            self.page, command=lambda v: self.show(), bg=C["bg"]).pack(anchor="w", pady=(8, 0))
        bb = tk.Frame(w, bg=C["bg"])
        bb.pack(side="bottom", fill="x", pady=(10, 0))
        self.lbl_msg = label(bb, "", 9, fg=C["green"], bg=C["bg"])
        self.lbl_msg.pack(side="left")
        buttons_right(bb, [("되돌리기", "TButton", self.revert), ("저장", "Accent.TButton", self.save)])[0].pack(side="right")
        self.body = tk.Frame(w, bg=C["bg"])
        self.body.pack(fill="both", expand=True, pady=(10, 0))
        if st.get("saved_msg"):
            self.lbl_msg.configure(text=st.pop("saved_msg"))
        self.show()

    def save_state(self):
        self.collect()
        self.main.settings_state = {"cfg": self.cfg, "cur_server": self.cur_server, "page": self.page.get(),
                                    "test_result": self.test_result}

    # ------------------------------------------------------------ 공통
    def show(self):
        self.collect()
        for c in self.body.winfo_children():
            c.destroy()
        self.form = {}
        {"server": self.server_page, "rule": self.rule_page, "general": self.general_page,
         "ai": self.ai_page, "email": self.email_page}[self.page.get()]()

    def entry(self, parent, row, key, name, value, width=30, hint=None, mono=False):
        label(parent, name, 10, True).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        e = ttk.Entry(parent, width=width, font=(F["mono"] if mono else F["ui"], 10))
        e.insert(0, str(value))
        e.grid(row=row, column=1, sticky="w", pady=4)
        if hint:
            label(parent, hint, 9, fg=C["sub"]).grid(row=row, column=2, sticky="w", padx=10)
        self.form[key] = e
        return e

    def collect(self):
        """화면의 입력값을 작업용 설정(self.cfg)에 옮긴다."""
        f = self.form
        if not f:
            return
        g, d, ssh = self.cfg["general"], self.cfg["detect"], self.cfg["ssh"]
        if "srv.name" in f and self.cfg["servers"]:
            s = self.cfg["servers"][self.cur_server]
            for k in ("name", "host", "port", "user", "key_file"):
                s[k] = f[f"srv.{k}"].get().strip()
            cfgmod.set_server_logs(s, [(e.get().strip(), c.get().strip() or s.get("format", "webtob_combined"))
                                       for e, c, _ in self.log_rows if e.winfo_exists() and e.get().strip()])
            s["auth"] = f["srv.auth"].get()
            try:
                s["port"] = int(s["port"])
            except ValueError:
                pass
            # 비밀번호는 설정 파일에 저장하지 않고, 프로그램이 켜져 있는 동안만 '계정@주소:포트' 단위로 기억한다
            pw = f["srv.password"].get()
            if pw:
                self.main.passwords[cfgmod.password_key(self.cfg, s)] = pw
        if "own_networks" in f:
            g["own_networks"] = _split(f["own_networks"].get())
            g["trusted_proxies"] = _split(f["trusted_proxies"].get())
            d["signature_file"] = f["signature_file"].get().strip()
            try:
                d["scan_404_threshold"] = int(f["scan_404"].get())
            except ValueError:
                d["scan_404_threshold"] = f["scan_404"].get()
        if "save_dir" in f:
            g["save_dir"] = f["save_dir"].get().strip()
            g["default_period"] = f["default_period"].get()
            g["local_log_format"] = f["local_log_format"].get().strip()
            ssh["user"] = f["ssh_user"].get().strip()
            for k in ("connect_timeout", "workers", "max_lines_per_file", "server_timeout"):
                try:
                    ssh[k] = int(f[k].get())
                except ValueError:
                    ssh[k] = f[k].get()     # 숫자가 아니면 [저장] 때 검사에서 알려 준다
            ssh["evidence_hash"] = bool(f["evidence_hash"].get())
        if "ai.url" in f:
            ai = self.cfg["ai"]
            for k in ("url", "model", "key_header"):
                ai[k] = f[f"ai.{k}"].get().strip()
            ai["key_prefix"] = f["ai.key_prefix"].get()       # 'Bearer ' 뒤 빈칸도 그대로
            ai["api"] = f["ai.api"].get()
            for k in ("enabled", "mask", "verify_tls", "use_proxy", "anthropic_fallback"):
                ai[k] = bool(f[f"ai.{k}"].get())
            ai["extra_headers"] = f["ai.extra_headers"].get("1.0", "end").strip()
            for k in ("max_lines", "timeout"):
                try:
                    ai[k] = int(f[f"ai.{k}"].get())
                except ValueError:
                    ai[k] = f[f"ai.{k}"].get()
            # API 키: 기본은 프로그램이 켜져 있는 동안만 기억. [키 저장]을 켜면 Windows 계정으로 암호화해 설정에 둔다
            key = f["ai.key"].get().strip()
            if key:
                self.main.ai_key = key
            if f["ai.save_key"].get():
                if key or not ai.get("key_saved"):
                    try:
                        ai["key_saved"] = aimod.protect(key or self.main.ai_key) if (key or self.main.ai_key) else ""
                    except (OSError, AttributeError, aimod.AiError):
                        ai["key_saved"] = ""
            else:
                ai["key_saved"] = ""
        if "em.url" in f:
            em = self.cfg["email"]
            for k in ("url", "service_seq", "token_header", "sender", "to", "cc", "title_prefix", "success_code"):
                em[k] = f[f"em.{k}"].get().strip()
            em["token_prefix"] = f["em.token_prefix"].get()      # 'Bearer ' 뒤 빈칸도 그대로
            for k in ("enabled", "mask_report", "verify_tls", "use_proxy"):
                em[k] = bool(f[f"em.{k}"].get())
            em["extra_headers"] = f["em.extra_headers"].get("1.0", "end").strip()
            try:
                em["timeout"] = int(f["em.timeout"].get())
            except ValueError:
                em["timeout"] = f["em.timeout"].get()
            # 인증 토큰: API 키와 같이 기본은 메모리에만, [토큰 저장]을 켜면 Windows 계정으로 암호화해 둔다
            token = f["em.token"].get().strip()
            if token:
                self.main.mail_token = token
            if f["em.save_token"].get():
                if token or not em.get("token_saved"):
                    try:
                        em["token_saved"] = aimod.protect(token or self.main.mail_token) \
                            if (token or self.main.mail_token) else ""
                    except (OSError, AttributeError, aimod.AiError):
                        em["token_saved"] = ""
            else:
                em["token_saved"] = ""

    def save(self):
        self.collect()
        errs = cfgmod.validate(self.cfg)
        ai = self.cfg["ai"]
        if ai.get("enabled") and ai.get("api") == "anthropic" and not str(ai.get("model", "")).strip():
            errs.append("AI 분석: " + aimod.NEED_MODEL)
        if errs:
            messagebox.showwarning(APP_NAME, "저장하지 못했습니다.\n\n" + "\n".join(errs[:10]), parent=self.main.root)
            return
        cfgmod.save(self.cfg)
        self.main.settings_state = {"page": self.page.get(), "cur_server": self.cur_server,
                                    "saved_msg": "저장했습니다. [로그 점검] 탭에서 서버를 고를 수 있습니다."}
        self.main.reload_config(copy.deepcopy(self.cfg))

    def revert(self):
        self.main.settings_state = {"page": self.page.get()}
        self.cfg = copy.deepcopy(self.main.cfg)
        self.cur_server = 0
        self.test_result = None
        self.form = {}
        self.show()
        self.lbl_msg.configure(text="저장된 값으로 되돌렸습니다.")

    # ------------------------------------------------------------ 서버
    def server_page(self):
        px = self.main.px
        # 서버 이름 칸은 가장 긴 이름의 글자 폭에 맞추고, 목록 폭도 그만큼 넓힌다 (380~520)
        import tkinter.font as tkfont
        fnt = tkfont.Font(family=F["ui"], size=10)
        name_w = max(px(150), min(px(300), max((fnt.measure(x["name"]) for x in self.cfg["servers"]), default=0) + 24))
        user_w = max(px(70), min(px(140), max((fnt.measure(cfgmod.effective_ssh(self.cfg, x).get("user", ""))
                                                for x in self.cfg["servers"]), default=0) + 24))
        left = card(self.body, width=max(px(380), min(px(520), name_w + user_w + px(150))))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        lb = tk.Frame(left, bg=C["panel"])
        lb.pack(side="bottom", fill="x", padx=10, pady=10)
        ttk.Button(lb, text="+ 서버 추가", command=self.add_server).pack(fill="x")
        ttk.Button(lb, text="+ 같은 서버에 계정 추가", command=self.add_account).pack(fill="x", pady=(6, 0))
        self.srv_tv = ttk.Treeview(left, columns=("n", "u", "a"), show="headings", height=10, selectmode="browse")
        for c, h, w in (("n", "이름", name_w), ("u", "계정", user_w), ("a", "SSH 주소", px(110))):
            self.srv_tv.heading(c, text=h, anchor="w")
            self.srv_tv.column(c, width=w, minwidth=px(50), stretch=(c == "n"), anchor="w")
        for i, s in enumerate(self.cfg["servers"]):
            self.srv_tv.insert("", "end", iid=str(i),
                               values=(s["name"], cfgmod.effective_ssh(self.cfg, s).get("user", ""), s["host"]))
        self.srv_tv.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        if self.cfg["servers"]:
            self.cur_server = min(self.cur_server, len(self.cfg["servers"]) - 1)
            self.srv_tv.selection_set(str(self.cur_server))
        self.srv_tv.bind("<<TreeviewSelect>>", self.on_server_select)

        right = card(self.body)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        inner = tk.Frame(right, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=20, pady=14)
        if not self.cfg["servers"]:
            label(inner, "등록된 서버가 없습니다. 왼쪽 아래 [+ 서버 추가]를 누르세요.", 11, fg=C["sub"]).pack(anchor="w")
            return
        s = self.cfg["servers"][self.cur_server]
        top = tk.Frame(inner, bg=C["panel"])
        top.pack(fill="x")
        label(top, s["name"] or "(새 서버)", 14, True).pack(side="left")
        ttk.Button(top, text="삭제", style="Small.TButton", command=self.delete_server).pack(side="right")
        # 계정 주의: 서비스 계정으로 접속하면 실수·유출 때 서비스에 영향이 가고, 누가 조회했는지 남지 않는다
        warn = tk.Frame(inner, bg=C["warn_bg"])
        warn.pack(fill="x", pady=(8, 0))
        tk.Label(warn, text="⚠  서비스 계정은 절대 사용하지 마세요. 인프라팀을 통해 개인 계정으로 로그에 접근할 수 있도록 "
                            "권한을 받아 사용하세요.",
                 bg=C["warn_bg"], fg=C["warn_fg"], font=(F["ui"], 9, "bold"), wraplength=self.main.px(760),
                 justify="left").pack(anchor="w", padx=10, pady=6)

        cols = tk.Frame(inner, bg=C["panel"])
        cols.pack(fill="x", pady=(8, 0))
        a = tk.Frame(cols, bg=C["panel"])
        a.pack(side="left", anchor="n")
        b = tk.Frame(cols, bg=C["panel"])
        b.pack(side="left", anchor="n", padx=(30, 0))
        label(a, "접속 정보", 9, True, fg=C["accent"]).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.entry(a, 1, "srv.name", "이름", s["name"], width=24)
        self.entry(a, 2, "srv.host", "SSH 주소", s["host"], width=24)
        self.entry(a, 3, "srv.port", "포트", s.get("port", 22), width=8)
        self.entry(a, 4, "srv.user", "계정", s.get("user", ""), width=24)
        label(a, f"비우면 공통 계정({self.cfg['ssh'].get('user') or '미설정'})", 8, fg=C["sub"]).grid(row=5, column=1, sticky="w")
        label(a, "인증 방식", 10, True).grid(row=6, column=0, sticky="w", pady=4)
        auth = tk.StringVar(value=s.get("auth") or self.cfg["ssh"].get("auth", "password"))
        self.form["srv.auth"] = auth
        # 밝게/어둡게 모두에서 선택 상태가 잘 보이도록 구간 버튼으로 표시한다
        seg(a, (("password", "비밀번호"), ("key", "키 파일")), auth,
            command=lambda v: self._auth_state()).grid(row=6, column=1, sticky="w")
        key = cfgmod.password_key(self.cfg, s)
        label(a, "비밀번호", 10, True).grid(row=7, column=0, sticky="w", pady=4, padx=(0, 12))
        pw = ttk.Entry(a, width=24, show="●")
        pw.insert(0, self.main.passwords.get(key, ""))
        ascii_only(pw, lambda: (self.lbl_pw.configure(text="영문·숫자·기호만 입력할 수 있습니다. 한/영 키를 확인하세요.",
                                                      fg=C["red"]), pw.bell()))
        pw.grid(row=7, column=1, sticky="w", pady=4)
        self.form["srv.password"] = pw
        self.lbl_pw = label(a, "저장 안 함 · 프로그램이 켜져 있는 동안만 사용", 8, fg=C["sub"])
        self.lbl_pw.grid(row=8, column=1, sticky="w")
        kf = self.entry(a, 9, "srv.key_file", "키 파일", s.get("key_file", ""), width=24, mono=True)
        self.btn_kf = ttk.Button(a, text="...", style="Small.TButton", width=3,
                                 command=lambda: self._browse(kf, file=True))
        self.btn_kf.grid(row=9, column=2, sticky="w", padx=6)
        self._auth_state()

        # 로그 위치: 경로마다 로그 형식을 고른다 (접근 로그·에러 로그·WAS 로그를 한 서버에서 함께)
        label(b, "로그 위치", 9, True, fg=C["accent"]).grid(row=0, column=0, columnspan=2, sticky="nw", pady=(0, 4))
        label(b, "로그 경로 (* 사용 가능)", 9, fg=C["sub"]).grid(row=1, column=0, sticky="w")
        label(b, "로그 형식", 9, fg=C["sub"]).grid(row=1, column=1, sticky="w", padx=(6, 0))
        self.log_box = tk.Frame(b, bg=C["panel"])
        self.log_box.grid(row=2, column=0, columnspan=2, sticky="w")
        self.log_rows, self.fmt_row = [], None
        for path, fmt_name in (cfgmod.server_logs(s) or [("", s.get("format", "webtob_combined"))]):
            self._add_log_row(path, fmt_name)
        self.form["srv.logs"] = True
        fb = tk.Frame(b, bg=C["panel"])
        fb.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(fb, text="+ 경로 추가", style="Small.TButton", command=lambda: self._add_log_row("", None, focus=True)
                   ).pack(side="left")
        ttk.Button(fb, text="형식 편집", style="Small.TButton",
                   command=lambda: FormatDialog(self, self._target_row()[1].get())).pack(side="left", padx=(6, 0))
        ttk.Button(fb, text="+ 새 형식", style="Small.TButton",
                   command=lambda: FormatDialog(self, "")).pack(side="left", padx=(6, 0))
        label(b, "예) /data/webserver/APP/log/*_ssl/access*.log*\n[형식 편집]·[+ 새 형식]은 마지막으로 고른 줄에 적용됩니다",
              8, fg=C["sub"], justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))

        box = tk.Frame(inner, bg=C["panel2"])
        box.pack(fill="x", pady=(14, 0))
        self.test_box = tk.Frame(box, bg=C["panel2"])
        self.test_box.pack(fill="x", padx=12, pady=10)
        self.render_test()
        bar = tk.Frame(inner, bg=C["panel"])
        bar.pack(fill="x", pady=(10, 0))
        label(bar, "연결 테스트는 접속·로그 폴더·파일 목록을 확인하고, 형식 확인을 위해 최근 로그 앞 5줄만 받습니다.",
              9, fg=C["sub"]).pack(side="left")
        ttk.Button(bar, text="연결 테스트", command=self.test_server).pack(side="right")

    def render_test(self, running=False):
        for c in self.test_box.winfo_children():
            c.destroy()
        r = self.test_result
        s = self.cfg["servers"][self.cur_server] if self.cfg["servers"] else None
        if running:
            label(self.test_box, "연결 테스트 중...", 10, fg=C["sub"], bg=C["panel2"]).pack(anchor="w")
            return
        if not r or not s or r["name"] != s["name"]:
            label(self.test_box, "연결 테스트 결과가 여기에 표시됩니다.", 10, fg=C["sub"], bg=C["panel2"]).pack(anchor="w")
            return
        label(self.test_box, r["text"], 10, True, fg=C["green"] if r["ok"] else C["red"], bg=C["panel2"],
              wraplength=self.main.px(640), justify="left").pack(anchor="w")
        # 경로별 결과 (이전 결과 모양이면 첫 경로 하나로 본다)
        paths = r.get("paths")
        if paths is None and r.get("fmt_ok") is not None:
            first = cfgmod.server_logs(s)[:1] or [("", s.get("format", ""))]
            paths = [{"path": first[0][0], "format": first[0][1], "files": None, "fmt_ok": r["fmt_ok"],
                      "detect": r.get("detect", [])}]
        for pr in paths or []:
            row = tk.Frame(self.test_box, bg=C["panel2"])
            row.pack(anchor="w", fill="x", pady=(4, 0))
            short = pr["path"] if len(pr["path"]) <= 42 else "…" + pr["path"][-41:]
            files = "" if pr["files"] is None else f"파일 {pr['files']}개"
            if pr["fmt_ok"]:
                ok, total = pr["fmt_ok"]
                good = total and ok == total
                fmt_txt = f"{'✔' if good else '✖'} {pr['format']} {ok}/{total}줄 일치"
            else:
                good, fmt_txt = pr["files"] != 0, ("파일 없음 (기간·경로 확인)" if pr["files"] == 0 else "")
            label(row, f"· {short}", 9, mono=True, bg=C["panel2"]).pack(side="left")
            label(row, "  ".join(x for x in (files, fmt_txt) if x), 9, fg=C["green"] if good else C["amber"],
                  bg=C["panel2"]).pack(side="left", padx=(8, 0))
            best = pr["detect"][0] if pr.get("detect") else None
            if pr["fmt_ok"] and best and pr["fmt_ok"][0] < pr["fmt_ok"][1] and best[2] > pr["fmt_ok"][0]:
                label(row, f"추천: {best[0]} ({best[2]}/{best[3]})", 9, fg=C["amber"], bg=C["panel2"]).pack(side="left", padx=(8, 0))
                ttk.Button(row, text="이 형식 적용", style="Small.TButton",
                           command=lambda b_=best, p_=pr["path"]: self.apply_format(b_[0], b_[1], p_)).pack(side="left", padx=6)

    def _add_log_row(self, path, fmt_name, focus=False):
        """로그 위치 표에 '경로 | 형식 | ×' 한 줄을 더한다."""
        names = list(self.cfg["log_formats"])
        if fmt_name is None:   # [+ 경로 추가]: 바로 위 줄과 같은 형식으로 시작
            fmt_name = self.log_rows[-1][1].get() if self.log_rows else self.cfg["servers"][self.cur_server].get("format", "")
        fr = tk.Frame(self.log_box, bg=C["panel"])
        fr.pack(fill="x", pady=2)
        e = ttk.Entry(fr, width=27, font=(F["mono"], 10))
        e.insert(0, path)
        e.pack(side="left")
        c = ttk.Combobox(fr, values=names, width=16, state="readonly")
        c.set(fmt_name if fmt_name in names or not names else names[0])
        c.pack(side="left", padx=(6, 0))
        row = (e, c, fr)
        ttk.Button(fr, text="×", style="Small.TButton", width=2,
                   command=lambda: self._remove_log_row(row)).pack(side="left", padx=(4, 0))
        for w in (e, c):
            w.bind("<FocusIn>", lambda ev: setattr(self, "fmt_row", row))
        self.log_rows.append(row)
        if focus:
            e.focus_set()
        return row

    def _remove_log_row(self, row):
        if len(self.log_rows) <= 1:   # 한 줄은 남긴다 (경로를 지우면 됨)
            row[0].delete(0, "end")
            return
        self.log_rows.remove(row)
        row[2].destroy()
        if self.fmt_row is row:
            self.fmt_row = None

    def _target_row(self):
        """[형식 편집]·[+ 새 형식]·[이 형식 적용]이 바꿀 줄: 마지막으로 고른 줄, 없으면 첫 줄."""
        return self.fmt_row if self.fmt_row in self.log_rows else self.log_rows[0]

    def on_format_saved(self, name):
        """형식 편집 창에서 저장하면 모든 줄의 목록을 새로 고치고, 대상 줄에 그 형식을 고른다."""
        if not getattr(self, "log_rows", None):
            return
        names = list(self.cfg["log_formats"])
        for _, c, _ in self.log_rows:
            if c.winfo_exists():
                c.configure(values=names)
        self._target_row()[1].set(name)

    def _auth_state(self):
        """인증 방식에 맞는 입력칸만 쓸 수 있게 한다."""
        is_key = self.form["srv.auth"].get() == "key"
        self.form["srv.password"].state(["disabled"] if is_key else ["!disabled"])
        self.form["srv.key_file"].state(["!disabled"] if is_key else ["disabled"])
        self.btn_kf.state(["!disabled"] if is_key else ["disabled"])

    def apply_format(self, name, fmt, path=None):
        """추천 형식을 적용한다. path가 있으면 그 로그 경로만, 없으면 모든 경로."""
        self.collect()
        self.cfg["log_formats"].setdefault(name, fmt)
        s = self.cfg["servers"][self.cur_server]
        cfgmod.set_server_logs(s, [(p, name if path in (None, p) else f) for p, f in cfgmod.server_logs(s)])
        self.form = {}
        self.show()
        where = f"'{path}'의 " if path else ""
        self.lbl_msg.configure(text=f"{where}로그 형식을 '{name}'(으)로 바꿨습니다. [저장]해야 반영됩니다.", fg=C["amber"])

    def on_server_select(self, _e=None):
        sel = self.srv_tv.selection()
        if not sel or int(sel[0]) == self.cur_server:
            return
        self.collect()
        self.cur_server = int(sel[0])
        self.form = {}
        self.show()

    def add_server(self):
        self.collect()
        names = {s["name"] for s in self.cfg["servers"]}
        n = len(self.cfg["servers"]) + 1
        name = next(f"web{i:02d}" for i in range(n, n + 100) if f"web{i:02d}" not in names)
        self.cfg["servers"].append(dict(copy.deepcopy(cfgmod.SERVER_DEFAULTS), name=name))
        self.cur_server = len(self.cfg["servers"]) - 1
        self.form = {}
        self.show()

    def add_account(self):
        """같은 서버에 다른 계정(예: api, fe)을 추가한다. 주소·포트·형식은 복사하고 계정·로그 경로를 바꾼다."""
        if not self.cfg["servers"]:
            return self.add_server()
        self.collect()
        src = self.cfg["servers"][self.cur_server]
        names = {s["name"] for s in self.cfg["servers"]}
        # 이름 전체 뒤에 번호를 붙인다 (M-web01-api -> M-web01-api-2). 이미 번호가 있으면 다음 번호로
        base = re.sub(r"-\d+$", "", src["name"]) or src["name"]
        name = next(f"{base}-{i}" for i in range(2, 1000) if f"{base}-{i}" not in names)
        new = dict(copy.deepcopy(src), name=name, user="")
        self.cfg["servers"].insert(self.cur_server + 1, new)
        self.cur_server += 1
        self.form = {}
        self.show()
        self.lbl_msg.configure(text=f"'{name}'을 추가했습니다. 필요하면 이름을 바꾸고, 계정·로그 경로를 입력한 뒤 [저장]하세요.",
                               fg=C["amber"])

    def delete_server(self):
        s = self.cfg["servers"][self.cur_server]
        if messagebox.askyesno(APP_NAME, f"{s['name']} 서버를 삭제할까요? ([저장]해야 반영됩니다)", parent=self.main.root):
            self.form = {}
            del self.cfg["servers"][self.cur_server]
            self.cur_server = max(0, self.cur_server - 1)
            self.show()

    def test_server(self):
        self.collect()
        s = dict(self.cfg["servers"][self.cur_server])
        ssh = cfgmod.effective_ssh(self.cfg, s)
        if not s["host"] or not ssh.get("user"):
            self.test_result = {"name": s["name"], "ok": False, "text": "SSH 주소와 계정을 먼저 입력하세요.", "detect": []}
            self.render_test()
            return
        main = self.main
        if main.worker.busy or not main.ask_passwords([s], cfg=self.cfg):
            return
        self.render_test(running=True)

        def done(rows):
            self.test_result = rows[0]
            if self.frame.winfo_exists():
                pw = self.form.get("srv.password")
                if rows[0].get("auth_failed") and pw is not None and pw.winfo_exists():
                    pw.delete(0, "end")   # 틀린 비밀번호가 칸에 남아 다시 쓰이지 않게 지운다
                self.render_test()

        main.worker.start(run_connection_test, on_done=done,
                          on_error=lambda e: done([{"name": s["name"], "ok": False, "text": f"✖ {e}", "detect": []}]),
                          servers=[s], main=main, cfg=copy.deepcopy(self.cfg))

    # ------------------------------------------------------------ 판정 기준
    def rule_page(self):
        g, d = self.cfg["general"], self.cfg["detect"]
        c1 = card(self.body)
        c1.pack(fill="x")
        i1 = tk.Frame(c1, bg=C["panel"])
        i1.pack(fill="x", padx=20, pady=14)
        label(i1, "IP 대역", 9, True, fg=C["accent"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.entry(i1, 1, "own_networks", "자사 대역", ", ".join(g["own_networks"]), width=64, mono=True)
        label(i1, "입력한 IP 중 이 대역은 자동으로 뺍니다. 자사 공인 대역도 넣어 두세요.", 9,
              fg=C["sub"]).grid(row=2, column=1, sticky="w")
        self.entry(i1, 3, "trusted_proxies", "L4/프록시 IP", ", ".join(g["trusted_proxies"]), width=64, mono=True)
        label(i1, "로그에서 이 IP는 매칭하지 않습니다 (헬스체크 등).", 9, fg=C["sub"]).grid(row=4, column=1, sticky="w")

        c2 = card(self.body)
        c2.pack(fill="x", pady=12)
        i2 = tk.Frame(c2, bg=C["panel"])
        i2.pack(fill="x", padx=20, pady=14)
        label(i2, "판정 기준", 9, True, fg=C["accent"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        label(i2, "경로 스캔 기준", 10, True).grid(row=1, column=0, sticky="w", padx=(0, 12))
        sp = tk.Frame(i2, bg=C["panel"])
        sp.grid(row=1, column=1, sticky="w", pady=5)
        label(sp, "404 응답이 ").pack(side="left")
        s = ttk.Spinbox(sp, from_=1, to=10000, width=6)
        s.set(d["scan_404_threshold"])
        s.pack(side="left")
        self.form["scan_404"] = s
        label(sp, " 건 이상이면 '경로 스캔'으로 판정").pack(side="left")
        e = self.entry(i2, 2, "signature_file", "공격 시그니처", d["signature_file"], width=46, mono=True)
        try:
            sig = Signatures.load(paths.resolve(d["signature_file"]))
            info, col = f"● 규칙 {sig.rule_count}개 사용 중 · 버전 {sig.version}", C["green"]
        except (OSError, ValueError) as ex:
            info, col = f"● 시그니처 파일을 읽을 수 없습니다: {ex}", C["red"]
        label(i2, info, 9, fg=col).grid(row=3, column=1, sticky="w")
        ttk.Button(i2, text="...", style="Small.TButton", width=3,
                   command=lambda: self._browse(e, file=True)).grid(row=2, column=2, padx=10)

        c3 = tk.Frame(self.body, bg=C["panel2"], highlightthickness=1, highlightbackground=C["line"])
        c3.pack(fill="x")
        i3 = tk.Frame(c3, bg=C["panel2"])
        i3.pack(fill="x", padx=20, pady=12)
        label(i3, "위험도는 이렇게 정합니다", 9, True, fg=C["accent"], bg=C["panel2"]).pack(anchor="w", pady=(0, 6))
        for name, key, desc in (("CRITICAL", "crit", "공격 요청이 200으로 성공 (웹셸·명령 실행·SQLi)"),
                                ("HIGH", "high", "그 밖의 공격 200 성공, 민감 파일 노출"),
                                ("MEDIUM", "med", "500 유발, 공격 시도(4xx), 404 다수(경로 스캔), 스캐너 도구"),
                                ("LOW", "lowbadge", "일반 접속")):
            r = tk.Frame(i3, bg=C["panel2"])
            r.pack(anchor="w", pady=2)
            tk.Label(r, text=f" {name} ", bg=C[key][0], fg=C[key][1], font=(F["ui"], 9, "bold"), width=10).pack(side="left")
            label(r, desc, 10, bg=C["panel2"]).pack(side="left", padx=10)

    # ------------------------------------------------------------ 일반
    def general_page(self):
        g, ssh = self.cfg["general"], self.cfg["ssh"]
        c = card(self.body)
        c.pack(fill="x")
        i = tk.Frame(c, bg=C["panel"])
        i.pack(fill="x", padx=20, pady=14)
        label(i, "화면", 9, True, fg=C["accent"]).grid(row=0, column=0, sticky="w", pady=(0, 6))
        label(i, "화면 테마", 10, True).grid(row=1, column=0, sticky="w", padx=(0, 12))
        tv = tk.StringVar(value=self.main.cfg["general"].get("theme", "light"))
        seg(i, (("light", "☀ 밝게"), ("dark", "☾ 어둡게")), tv, command=self.apply_theme).grid(row=1, column=1, sticky="w", pady=5)
        label(i, "바로 적용되고 다음 실행 때도 유지됩니다.", 9, fg=C["sub"]).grid(row=2, column=1, columnspan=2, sticky="w")
        label(i, "점검", 9, True, fg=C["accent"]).grid(row=3, column=0, sticky="w", pady=(16, 6))
        label(i, "기본 조회 기간", 10, True).grid(row=4, column=0, sticky="w", pady=5, padx=(0, 12))
        pv = tk.StringVar(value=str(g.get("default_period", "7")))
        seg(i, PERIOD_ITEMS, pv).grid(row=4, column=1, sticky="w", pady=5)
        self.form["default_period"] = pv
        e = self.entry(i, 5, "save_dir", "기본 저장 폴더", g.get("save_dir", "out"), width=40, mono=True)
        ttk.Button(i, text="...", style="Small.TButton", width=3, command=lambda: self._browse(e)).grid(row=5, column=2, sticky="w", padx=10)
        self.entry(i, 6, "ssh_user", "SSH 공통 계정", ssh.get("user", ""),
                   hint="서버별 계정이 비어 있을 때 사용 · 서비스 계정 금지, 로그 접근 권한을 받은 개인 계정")
        self.entry(i, 7, "connect_timeout", "SSH 시간 제한", ssh.get("connect_timeout", 10), width=8, hint="초")
        self.entry(i, 8, "workers", "동시 점검 서버 수", ssh.get("workers", 2), width=8,
                   hint="SSH 주소가 같은 항목(api·fe 등)은 이 값과 상관없이 한 대씩 차례로 점검")
        label(i, "로컬 로그 형식", 10, True).grid(row=9, column=0, sticky="w", pady=5, padx=(0, 12))
        cb = ttk.Combobox(i, values=list(self.cfg["log_formats"]), width=28)
        cb.set(g.get("local_log_format", "webtob_combined"))
        cb.grid(row=9, column=1, sticky="w")
        self.form["local_log_format"] = cb
        label(i, "[로컬 폴더...]로 점검할 때 쓰는 형식", 9, fg=C["sub"]).grid(row=9, column=2, sticky="w", padx=10)
        label(i, "서버 부하", 9, True, fg=C["accent"]).grid(row=10, column=0, sticky="w", pady=(16, 6))
        self.entry(i, 11, "max_lines_per_file", "파일당 최대 줄 수", ssh.get("max_lines_per_file", 100000), width=10,
                   hint="의심 IP가 든 줄을 파일마다 이만큼만 받음. 넘으면 '부분 분석'으로 표시")
        self.entry(i, 12, "server_timeout", "서버 작업 시간 제한", ssh.get("server_timeout", 180), width=10,
                   hint="초. 파일 하나를 읽다 이 시간이 지나면 서버가 스스로 멈추고 그 파일은 '부분 분석'으로 표시")
        eh = var(bool(ssh.get("evidence_hash", False)))
        self.form["evidence_hash"] = eh
        check(i, "증적 해시(sha256) 계산", eh).grid(row=13, column=1, sticky="w", pady=5)
        label(i, "켜면 파일을 한 번 더 끝까지 읽음 (디스크 읽기 2배)", 9, fg=C["sub"]).grid(row=13, column=2, sticky="w", padx=10)

    # ------------------------------------------------------------ AI 분석
    def ai_page(self):
        ai, px = self.cfg["ai"], self.main.px
        c = card(self.body)
        c.pack(fill="both", expand=True)
        i = scroll_area(c, padx=20, pady=14)
        label(i, "AI 분석 (선택)", 9, True, fg=C["accent"]).grid(row=0, column=0, sticky="w", pady=(0, 2))
        label(i, "결과 화면에서 IP를 고르고 [AI 분석]을 누르면, 그 IP의 로그 줄(같은 요청은 묶음)과 규칙 판정을 사내 AI에 보내 "
                 "분석 결과를 받습니다. 위험도 판정은 규칙 기준 그대로이고 AI 분석은 참고용입니다. 서버의 원본 로그 파일은 보내지 않습니다.",
              9, fg=C["sub"], wraplength=px(860), justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        en = var(bool(ai.get("enabled")))
        self.form["ai.enabled"] = en
        check(i, "AI 분석 사용 (결과 화면에 [AI 분석] 버튼 표시)", en).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        label(i, "API 방식", 10, True).grid(row=3, column=0, sticky="w", pady=5, padx=(0, 12))
        api = var(ai.get("api", "openai"))
        self.form["ai.api"] = api
        seg(i, aimod.APIS, api, command=self._ai_api_changed).grid(row=3, column=1, sticky="w", pady=5)
        label(i, "게이트웨이가 알려준 방식 (Claude는 Anthropic)", 9, fg=C["sub"]).grid(row=3, column=2, sticky="w", padx=10)
        self.entry(i, 4, "ai.url", "주소 (URL)", ai.get("url", ""), width=56, mono=True)
        label(i, "예: https://ai-gw.회사/v1  ·  /v1로 끝나면 OpenAI는 /chat/completions, Anthropic은 /messages를 붙임",
              9, fg=C["sub"]).grid(row=5, column=1, columnspan=2, sticky="w")
        self.entry(i, 6, "ai.model", "모델 이름", ai.get("model", ""), width=32, mono=True,
                   hint="OpenAI 호환은 비워도 됨. Anthropic은 꼭 넣어야 함")
        self.entry(i, 7, "ai.key_header", "키 헤더 이름", ai.get("key_header", "Authorization"), width=20, mono=True,
                   hint="예: Authorization, x-api-key, apikey")
        self.entry(i, 8, "ai.key_prefix", "키 앞 글자", ai.get("key_prefix", "Bearer "), width=12, mono=True,
                   hint="Authorization이면 'Bearer ' (뒤 빈칸 포함), x-api-key면 비움")
        k = self.entry(i, 9, "ai.key", "API 키", "", width=40, mono=True)
        ascii_only(k).configure(show="●")
        has = bool(self.main.ai_key or ai.get("key_saved"))
        label(i, "입력됨 (바꿀 때만 다시 입력)" if has else "프로그램을 끄면 지워짐 (저장하려면 아래 체크)", 9,
              fg=C["green"] if has else C["sub"]).grid(row=9, column=2, sticky="w", padx=10)
        sk = var(bool(ai.get("key_saved")))
        self.form["ai.save_key"] = sk
        check(i, "이 PC에 키 저장 (Windows 계정으로 암호화, 다른 PC·계정에서는 못 씀)", sk).grid(
            row=10, column=1, columnspan=2, sticky="w", pady=(0, 4))
        label(i, "추가 헤더", 10, True).grid(row=11, column=0, sticky="nw", pady=5, padx=(0, 12))
        self.form["ai.extra_headers"] = self._text(i, 11, ai.get("extra_headers", ""), 2,
                                                   "게이트웨이가 더 요구하는 헤더. 한 줄에 '이름: 값'")
        label(i, "보낼 양·연결", 9, True, fg=C["accent"]).grid(row=15, column=0, sticky="w", pady=(14, 4))
        self.entry(i, 16, "ai.max_lines", "보낼 최대 줄 수", ai.get("max_lines", 60), width=8,
                   hint="IP 하나당. 같은 요청은 한 줄로 묶고 공격 태그, 많이 반복된 요청부터 보냄")
        self.entry(i, 17, "ai.timeout", "시간 제한", ai.get("timeout", 120), width=8, hint="초")
        for row, key, text, hint in ((18, "mask", "민감값 가리고 보내기", "토큰·세션·비밀번호 값, 주민번호, 전화번호, 카드번호, 이메일 → ***"),
                                     (19, "verify_tls", "TLS 인증서 확인", "사내 인증서 오류가 나면 끄기"),
                                     (20, "use_proxy", "시스템 프록시 사용", "보통 끔 (사내 주소는 바로 접속)"),
                                     (21, "anthropic_fallback", "Anthropic: 거절되면 대체 모델 사용",
                                      "Claude API에 바로 연결할 때만. 게이트웨이는 지원하지 않을 수 있음")):
            v = var(bool(ai.get(key, key in ("mask", "verify_tls"))))
            self.form[f"ai.{key}"] = v
            check(i, text, v).grid(row=row, column=1, sticky="w", pady=3)
            label(i, hint, 9, fg=C["sub"]).grid(row=row, column=2, sticky="w", padx=10)
        tb = tk.Frame(i, bg=C["panel"])
        tb.grid(row=22, column=0, columnspan=3, sticky="w", pady=(14, 0))
        self.btn_ai_test = ttk.Button(tb, text="연결 테스트", command=self.test_ai)
        self.btn_ai_test.pack(side="left")
        self.lbl_ai_test = label(tb, "저장하지 않아도 지금 입력한 값으로 시험합니다.", 9, fg=C["sub"],
                                 wraplength=px(700), justify="left")
        self.lbl_ai_test.pack(side="left", padx=10)

    # ------------------------------------------------------------ 이메일
    def email_page(self):
        em, px = self.cfg["email"], self.main.px
        c = card(self.body)
        c.pack(fill="both", expand=True)
        i = scroll_area(c, padx=20, pady=14)
        label(i, "이메일로 결과 보내기 (선택)", 9, True, fg=C["accent"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        label(i, "켜면 결과 화면의 [회신 요약 복사]가 [이메일 보내기]로 바뀝니다. 회신 요약을 본문으로, HTML 보고서를 첨부해 "
                 "사내 메일 API(POST /email/send)로 보냅니다. 제목·본문의 개인정보는 가리고 보냅니다.",
              9, fg=C["sub"], wraplength=px(860), justify="left").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        en = var(bool(em.get("enabled")))
        self.form["em.enabled"] = en
        check(i, "이메일 보내기 사용", en).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        self.entry(i, 3, "em.url", "발송 주소 (URL)", em.get("url", ""), width=56, mono=True)
        label(i, "예: https://mail-api.회사  ·  /email/send로 끝나지 않으면 자동으로 붙임", 9, fg=C["sub"]).grid(
            row=4, column=1, columnspan=2, sticky="w")
        self.entry(i, 5, "em.service_seq", "서비스코드", em.get("service_seq", ""), width=20, mono=True,
                   hint="필수. 헤더 appServiceSeq (관리자에게 발급받은 기준정보)")
        self.entry(i, 6, "em.token_header", "토큰 헤더 이름", em.get("token_header", "Authorization"), width=20, mono=True,
                   hint="규격: Authorization. apiVersion(1.0)은 자동으로 넣음")
        self.entry(i, 7, "em.token_prefix", "토큰 앞 글자", em.get("token_prefix", ""), width=12, mono=True,
                   hint="규격은 토큰만 보냄 (비움). 'Bearer '가 필요하면 넣음")
        t = self.entry(i, 8, "em.token", "인증 토큰", "", width=40, mono=True)
        ascii_only(t).configure(show="●")
        has = bool(self.main.mail_token or em.get("token_saved"))
        label(i, "입력됨 (바꿀 때만 다시 입력)" if has else "프로그램을 끄면 지워짐 (저장하려면 아래 체크)", 9,
              fg=C["green"] if has else C["sub"]).grid(row=8, column=2, sticky="w", padx=10)
        st = var(bool(em.get("token_saved")))
        self.form["em.save_token"] = st
        check(i, "이 PC에 토큰 저장 (Windows 계정으로 암호화, 다른 PC·계정에서는 못 씀)", st).grid(
            row=9, column=1, columnspan=2, sticky="w", pady=(0, 4))
        label(i, "추가 헤더", 10, True).grid(row=10, column=0, sticky="nw", pady=5, padx=(0, 12))
        self.form["em.extra_headers"] = self._text(i, 10, em.get("extra_headers", ""), 2,
                                                   "발송 API가 더 요구하는 헤더. 한 줄에 '이름: 값'")
        label(i, "보내는 사람·받는 사람", 9, True, fg=C["accent"]).grid(row=11, column=0, sticky="w", pady=(14, 4))
        self.entry(i, 12, "em.sender", "발신자", em.get("sender", ""), width=40, mono=True,
                   hint="필수. '주소;이름' 가능 (예: secu@회사.co.kr;보안팀)")
        self.entry(i, 13, "em.to", "기본 받는 사람", em.get("to", ""), width=56, mono=True,
                   hint="필수. 여러 명은 , 또는 ; 로 구분. 보낼 때 바꿀 수 있음")
        self.entry(i, 14, "em.cc", "기본 참조", em.get("cc", ""), width=56, mono=True)
        self.entry(i, 15, "em.title_prefix", "제목 앞말", em.get("title_prefix", "[IPtrace]"), width=20,
                   hint="메일 제목과 첨부 파일 이름 맨 앞에 붙음")
        label(i, "응답·연결", 9, True, fg=C["accent"]).grid(row=16, column=0, sticky="w", pady=(14, 4))
        self.entry(i, 17, "em.success_code", "성공 응답 코드", em.get("success_code", "0"), width=10, mono=True,
                   hint="응답 resCode가 이 값이면 성공. 비우면 처리 상태(transStatus = O)만 봄")
        self.entry(i, 18, "em.timeout", "시간 제한", em.get("timeout", 30), width=8, hint="초")
        for row, key, text, hint in ((19, "mask_report", "첨부 보고서의 URL 민감값 가리기", "토큰·세션 같은 쿼리 값을 *** 로"),
                                     (20, "verify_tls", "TLS 인증서 확인", "사내 인증서 오류가 나면 끄기"),
                                     (21, "use_proxy", "시스템 프록시 사용", "보통 끔 (사내 주소는 바로 접속)")):
            v = var(bool(em.get(key, key in ("mask_report", "verify_tls"))))
            self.form[f"em.{key}"] = v
            check(i, text, v).grid(row=row, column=1, sticky="w", pady=3)
            label(i, hint, 9, fg=C["sub"]).grid(row=row, column=2, sticky="w", padx=10)
        tb = tk.Frame(i, bg=C["panel"])
        tb.grid(row=22, column=0, columnspan=3, sticky="w", pady=(14, 0))
        self.btn_mail_test = ttk.Button(tb, text="시험 메일 보내기", command=self.test_mail)
        self.btn_mail_test.pack(side="left")
        self.lbl_mail_test = label(tb, "저장하지 않아도 지금 입력한 값으로 보냅니다. 보낼 때 발신자·받는 사람을 묻습니다.", 9,
                                   fg=C["sub"], wraplength=px(700), justify="left")
        self.lbl_mail_test.pack(side="left", padx=10)

    def test_mail(self):
        from ..core import mailer
        if self.main.worker.busy:
            return
        self.collect()
        em = dict(self.cfg["email"])
        try:
            url = mailer.endpoint(em)
            token = self.main.current_mail_token(em)
        except (mailer.MailError, aimod.AiError) as e:
            self.lbl_mail_test.configure(text=str(e), fg=C["red"])
            return
        # 시험 메일은 발신자·받는 사람을 그때그때 묻는다 (설정의 값은 기본값으로만 채움)
        got = AskDialog(self.main.root, "시험 메일 보내기", "시험 메일을 보낼 주소를 넣으세요",
                        [("sender", "발신자", em.get("sender", ""), "'주소;이름' 가능 (예: secu@회사.co.kr;보안팀)"),
                         ("to", "받는 사람", em.get("to", ""), "여러 명은 , 또는 ; 로 구분")],
                        note=f"발송 주소: {url.split('/')[2]}", ok_text="보내기").show()
        if not got:
            return
        to = mailer.split_addrs(got["to"])
        try:
            body = mailer.build_request(dict(em, sender=got["sender"]), to, [],
                                        f"{em.get('title_prefix', '')} 시험 메일".strip(),
                                        "IPtrace 메일 발송 설정 확인용 시험 메일입니다.")
        except mailer.MailError as e:
            self.lbl_mail_test.configure(text=str(e), fg=C["red"])
            return
        self.lbl_mail_test.configure(text="보내는 중… (최대 %s초)" % em.get("timeout"), fg=C["sub"])
        self.btn_mail_test.state(["disabled"])

        def done(msg, ok):
            if self.lbl_mail_test.winfo_exists():
                self.lbl_mail_test.configure(text=msg, fg=C["green"] if ok else C["red"])
                self.btn_mail_test.state(["!disabled"])

        def ok(r):
            from ..core import audit
            audit.log_email(url, r.ord_no, len(to), 0, False, True, r.tr_no)
            done(f"보냈습니다 ({r.elapsed:.1f}초, 받는 사람 {len(to)}명) · 처리 상태 {r.status or '-'} · "
                 f"거래번호 {r.tr_no or '-'}. 받은편지함을 확인하세요.", True)
        self.main.worker.start(lambda **kw: mailer.send(em, token, body), on_done=ok,
                               on_error=lambda e: done(f"보내기 실패: {e}", False))

    def _ai_api_changed(self, api):
        """방식을 바꾸면 키 헤더 기본값도 맞춘다 (사용자가 직접 바꾼 값은 그대로 둔다)."""
        h, p = self.form["ai.key_header"], self.form["ai.key_prefix"]
        now = (h.get().strip(), p.get())
        new = (("x-api-key", "") if api == "anthropic" and now == ("Authorization", "Bearer ")
               else ("Authorization", "Bearer ") if api != "anthropic" and now == ("x-api-key", "") else None)
        if new:
            h.delete(0, "end")
            h.insert(0, new[0])
            p.delete(0, "end")
            p.insert(0, new[1])

    def _text(self, parent, row, value, height, hint):
        t = tk.Text(parent, height=height, width=56, font=(F["mono"], 10), bg=C["input"], fg=C["text"], relief="flat",
                    highlightthickness=1, highlightbackground=C["line"], insertbackground=C["text"], wrap="none")
        t.insert("1.0", value)
        t.grid(row=row, column=1, sticky="w", pady=4)
        label(parent, hint, 9, fg=C["sub"], wraplength=self.main.px(300), justify="left").grid(
            row=row, column=2, sticky="nw", padx=10, pady=4)
        return t

    def test_ai(self):
        if self.main.worker.busy:
            return
        self.collect()
        ai = dict(self.cfg["ai"])
        try:
            key = self.main.current_ai_key(ai)
        except aimod.AiError as e:
            self.lbl_ai_test.configure(text=str(e), fg=C["red"])
            return
        self.lbl_ai_test.configure(text="연결 중… (최대 %s초)" % ai.get("timeout"), fg=C["sub"])
        self.btn_ai_test.state(["disabled"])

        def done(msg, ok):
            if self.lbl_ai_test.winfo_exists():
                self.lbl_ai_test.configure(text=msg, fg=C["green"] if ok else C["red"])
                self.btn_ai_test.state(["!disabled"])
        self.main.worker.start(lambda **kw: aimod.test_connection(ai, key),
                               on_done=lambda m: done(m, True), on_error=lambda e: done(f"연결 실패: {e}", False))

    def apply_theme(self, name):
        self.cfg["general"]["theme"] = name
        self.main.set_theme(name)   # 화면 전체를 다시 그린다 (작업 중인 설정 값은 save_state로 유지)

    def _browse(self, entry, file=False):
        p = (filedialog.askopenfilename(parent=self.main.root) if file else filedialog.askdirectory(parent=self.main.root))
        if p:
            entry.delete(0, "end")
            entry.insert(0, p)


class FormatDialog:
    """로그 형식(Format 문자열) 추가·수정. 샘플 줄로 바로 시험해 볼 수 있다.

    웹·WAS 접근 로그는 설정 값을 옮겨 적고, 그 밖의 로그는 %{시간}t·%* 등으로 한 줄 모양을 적는다.
    """

    def __init__(self, settings, name):
        self.s = settings
        root = settings.main.root
        px = settings.main.px
        w = self.win = tk.Toplevel(root)
        w.title("로그 형식 편집" if name else "새 로그 형식")
        w.configure(bg=C["panel"])
        w.transient(root)
        center_window(w, root, px(920), px(660))   # 메인 창 가운데
        f = tk.Frame(w, bg=C["panel"])
        f.pack(fill="both", expand=True, padx=20, pady=16)
        label(f, "로그 한 줄의 모양을 % 지시자로 적습니다. WebtoB는 http.m의 Format 값, Tomcat·JEUS 접근 로그는 pattern 값을 "
                 "그대로 옮기고, 그 밖의 로그는 아래 안내대로 적으세요.", 10, fg=C["sub"], wraplength=px(860),
              justify="left").pack(anchor="w")
        g = tk.Frame(f, bg=C["panel"])
        g.pack(fill="x", pady=8)
        label(g, "형식 이름", 10, True).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.name = ttk.Entry(g, width=30)
        self.name.insert(0, name)
        self.name.grid(row=0, column=1, sticky="w")
        ex = ttk.Menubutton(g, text="예시 넣기 ▾", style="Small.TButton")
        menu = tk.Menu(ex, tearoff=0)
        for key, desc, fmt in EXAMPLES:
            menu.add_command(label=desc, command=lambda k=key, v=fmt: self.use_example(k, v))
        ex["menu"] = menu
        ex.grid(row=0, column=1, sticky="e")
        label(g, "Format", 10, True).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.fmt = ttk.Entry(g, width=90, font=(F["mono"], 10))
        self.fmt.insert(0, settings.cfg["log_formats"].get(name, '%h %l %u %t "%r" %s %b'))
        self.fmt.grid(row=1, column=1, sticky="w")
        hb = tk.Frame(f, bg=C["panel2"])
        hb.pack(fill="x", pady=(2, 4))
        for i, (code, desc) in enumerate(FORMAT_HELP):
            label(hb, code, 9, True, mono=True, bg=C["panel2"], fg=C["accent"]).grid(
                row=i, column=0, sticky="w", padx=(12, 14), pady=(8 if i == 0 else 1, 8 if i == len(FORMAT_HELP) - 1 else 1))
            label(hb, desc, 9, bg=C["panel2"]).grid(row=i, column=1, sticky="w")
        label(f, "IP와 시간만 읽혀도 점검됩니다. 요청·응답 코드가 없는 로그는 404·500·공격 패턴 판정만 빠집니다.",
              9, fg=C["sub"]).pack(anchor="w")
        label(f, "샘플 로그 (마스킹한 실제 로그 1~5줄)", 10, True).pack(anchor="w", pady=(8, 2))
        self.sample = tk.Text(f, height=4, font=(F["mono"], 10), bg=C["input"], fg=C["text"], relief="flat",
                              highlightthickness=1, highlightbackground=C["line"], insertbackground=C["text"])
        self.sample.pack(fill="x")
        self.out = label(f, "", 10, mono=True, justify="left", anchor="w", wraplength=px(860))
        self.out.pack(fill="x", pady=8)
        buttons_right(f, [("자동 확인", "TButton", self.auto), ("시험", "TButton", self.test), ("닫기", "TButton", w.destroy),
                          ("저장", "Accent.TButton", self.save)])[0].pack(side="bottom", anchor="e")

    def _lines(self):
        return [x for x in self.sample.get("1.0", "end").splitlines() if x.strip()]

    def use_example(self, key, fmt):
        self.fmt.delete(0, "end")
        self.fmt.insert(0, fmt)
        if not self.name.get().strip():
            self.name.delete(0, "end")
            self.name.insert(0, key)
        if self._lines():
            self.test()

    def auto(self):
        lines = self._lines()
        if not lines:
            self.out.configure(text="샘플 줄을 넣으세요.", fg=C["amber"])
            return
        best = detect_format(lines, self.s.cfg)[0]
        if not best[2]:
            self.out.configure(text="✖ 맞는 기본 형식이 없습니다. [예시 넣기]에서 비슷한 형식을 고른 뒤 고쳐 보세요.", fg=C["red"])
            return
        self.fmt.delete(0, "end")
        self.fmt.insert(0, best[1])
        if best[0] not in self.s.cfg["log_formats"]:
            self.name.delete(0, "end")
            self.name.insert(0, best[0])
        self.test()

    def test(self):
        try:
            p = LogParser(self.fmt.get())
        except Exception as e:  # noqa: BLE001
            self.out.configure(text=f"형식 오류: {e}", fg=C["red"])
            return
        lines = self._lines()
        if not lines:
            self.out.configure(text="샘플 줄을 넣으세요.", fg=C["amber"])
            return
        ok = [p.parse(x) for x in lines]
        r = next((x for x in ok if x), None)
        n = sum(1 for x in ok if x)
        if not r:
            self.out.configure(text=f"✖ 이 형식으로 샘플 줄을 읽지 못했습니다 (0/{len(lines)}줄).", fg=C["red"])
            return
        # 읽은 값만 보여 준다 (웹 로그가 아니면 요청·응답 칸이 없다)
        got = [f"IP={r.remote_ip}", f"시간={r.time:%Y-%m-%d %H:%M:%S}" if r.time else "",
               f"{r.method} {r.url[:50]}".strip(), f"응답={r.status}" if r.status is not None else "",
               f"크기={r.bytes}" if r.bytes else "", f"UA={r.user_agent[:30]}" if r.user_agent else "",
               f"XFF={', '.join(r.xff)}" if r.xff else ""]
        text = f"{'✔' if n == len(lines) else '△'} {n}/{len(lines)}줄 일치 · 예: " + "  ".join(x for x in got if x)
        warn = n != len(lines)
        if r.time is None:
            text += "\n△ 시간을 읽지 못했습니다. 기간 필터가 적용되지 않으니 %t 또는 %{...}t를 확인하세요."
            warn = True
        if not r.method and r.status is None:
            text += "\n· 요청·응답 코드가 없는 로그입니다 (웹 로그가 아니면 정상). 404·500·공격 판정은 하지 않습니다."
        self.out.configure(fg=C["amber"] if warn else C["green"], text=text)

    def save(self):
        name = self.name.get().strip()
        if not name:
            return
        try:
            LogParser(self.fmt.get())
        except Exception as e:  # noqa: BLE001
            messagebox.showwarning(APP_NAME, f"형식 오류: {e}", parent=self.win)
            return
        self.s.cfg["log_formats"][name] = self.fmt.get()
        self.s.on_format_saved(name)   # 로그 위치 표의 대상 줄에 이 형식을 고른다
        self.win.destroy()


def _split(text: str) -> list:
    return [x.strip() for x in text.replace("\n", ",").split(",") if x.strip()]

"""대화상자: SSH 비밀번호, 호스트 키 확인, 점검 완료, 저장 완료, 목록 보기, AI 분석 (PLAN.md 6.5)."""
import os
import re
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .theme import C, F
from .widgets import ascii_only, buttons_right, center_window, check, label, stat_box, var


class Dialog:
    """모달 대화상자 기본형. show()는 닫힐 때까지 기다린 뒤 self.result를 돌려준다."""

    def __init__(self, parent, title, icon, icon_colors, heading, sub=None, width=480):
        self.result = None
        w = self.win = tk.Toplevel(parent)
        w.withdraw()
        w.title(title)
        w.configure(bg=C["panel"])
        w.resizable(False, False)
        if parent.winfo_viewable():
            w.transient(parent)
        w.minsize(width, 10)
        top = tk.Frame(w, bg=C["panel"])
        top.pack(fill="x", padx=22, pady=(18, 8))
        tk.Label(top, text=icon, bg=icon_colors[0], fg=icon_colors[1], font=(F["ui"], 16, "bold"),
                 width=2, height=1).pack(side="left", anchor="n")
        t = tk.Frame(top, bg=C["panel"])
        t.pack(side="left", padx=12)
        label(t, heading, 13, True).pack(anchor="w")
        if sub:
            label(t, sub, 9, fg=C["sub"], wraplength=width - 120, justify="left").pack(anchor="w")
        self.body = tk.Frame(w, bg=C["panel"])
        self.body.pack(fill="both", expand=True, padx=22)
        self.foot = tk.Frame(w, bg=C["panel"])
        self.foot.pack(fill="x", padx=22, pady=16)
        w.protocol("WM_DELETE_WINDOW", self.close)
        w.bind("<Escape>", lambda e: self.close())

    def close(self, result=None):
        self.result = result
        self.win.grab_release()
        self.win.destroy()

    def show(self):
        w = self.win
        center_window(w, w.master)   # 메인 창 가운데
        w.deiconify()
        w.grab_set()
        w.focus_force()
        w.wait_window()
        return self.result


class PasswordDialog(Dialog):
    """계정별 비밀번호를 받는다. items: [(키, 표시 이름)]. 결과: {키: 비밀번호} 또는 None(취소)."""

    def __init__(self, parent, items, title="SSH 비밀번호", heading="서버 접속 비밀번호를 입력하세요",
                 note="비밀번호는 저장되지 않으며 프로그램을 종료하면 지워집니다."):
        self.items = items
        self.servers = [k for k, _ in items]
        super().__init__(parent, title, "i", (C["accent_light"], C["accent"]),
                         heading, " · ".join(lbl for _, lbl in items[:6]))
        self.same = var(True)
        self.entries = {}
        self.box = tk.Frame(self.body, bg=C["panel"])
        self.box.pack(fill="x", pady=(6, 4))
        if len(items) > 1 or title == "SSH 비밀번호":
            check(self.body, "모든 서버에 같은 비밀번호 사용", self.same, command=self._layout).pack(anchor="w")
        label(self.body, note, 9, fg=C["sub"]).pack(anchor="w", pady=(4, 0))
        self.warn = label(self.body, "", 9, fg=C["red"])
        self.warn.pack(anchor="w")
        buttons_right(self.foot, [("취소", "TButton", self.close), ("확인", "Accent.TButton", self.ok)])[0].pack(side="right")
        self.win.bind("<Return>", lambda e: self.ok())
        self._layout()

    def _layout(self):
        for c in self.box.winfo_children():
            c.destroy()
        self.entries = {}
        labels = dict(self.items)
        names = ["*"] if self.same.get() or len(self.servers) == 1 else self.servers
        # 서버별 입력: 이름 칸은 가장 긴 '서버 · 계정'에 맞추고(잘리지 않게), 입력칸은 남은 폭
        self.box.columnconfigure(1, weight=1)
        for i, n in enumerate(names):
            if n != "*":
                label(self.box, labels.get(n, n), 9, anchor="w").grid(row=i, column=0, sticky="w", padx=(0, 10), pady=2)
            e = ascii_only(ttk.Entry(self.box, show="●", font=(F["ui"], 12), width=18), self._reject)
            e.grid(row=i, column=1 if n != "*" else 0, columnspan=1 if n != "*" else 2, sticky="ew", pady=2)
            self.entries[n] = e
        next(iter(self.entries.values())).focus_set()

    def _reject(self):
        self.warn.configure(text="영문·숫자·기호만 입력할 수 있습니다. 한/영 키를 확인하세요.")
        self.win.bell()

    def ok(self):
        if "*" in self.entries:
            pw = self.entries["*"].get()
            self.close({s: pw for s in self.servers})
        else:
            self.close({s: e.get() for s, e in self.entries.items()})


class HostKeyDialog(Dialog):
    """결과: 'trust' / 'skip' / None(취소)"""

    def __init__(self, parent, server, host, key_type, fingerprint):
        super().__init__(parent, "호스트 키 확인", "!", (C["warn_bg"], C["amber"]),
                         "처음 접속하는 서버입니다", f"{server} ({host})", width=540)
        box = tk.Frame(self.body, bg=C["panel2"])
        box.pack(fill="x", pady=(6, 10))
        label(box, f"{key_type}\n{fingerprint}", 10, mono=True, bg=C["panel2"], justify="left").pack(anchor="w", padx=12, pady=8)
        label(self.body, "서버 담당자에게 위 지문이 맞는지 확인한 뒤 신뢰하세요.", 10).pack(anchor="w")
        label(self.body, "신뢰하면 config\\known_hosts에 저장되어 다음부터 묻지 않습니다.", 9, fg=C["sub"]).pack(anchor="w")
        buttons_right(self.foot, [("취소", "TButton", self.close),
                                  ("이 서버 건너뛰기", "TButton", lambda: self.close("skip")),
                                  ("신뢰하고 계속", "Accent.TButton", lambda: self.close("trust"))])[0].pack(side="right")


class DoneDialog(Dialog):
    """결과: 'show' 또는 None"""

    def __init__(self, parent, result):
        c = result.summary_counts()
        took = (result.finished - result.started) if result.finished else None
        sub = f"소요 시간 {str(took).split('.')[0]}" if took else ""
        super().__init__(parent, "점검 완료", "✔", (C["ok_bg"], C["green"]),
                         f"IP {c['iocs']}개 점검을 마쳤습니다", sub, width=540)
        row = tk.Frame(self.body, bg=C["panel"])
        row.pack(fill="x", pady=(6, 8))
        for i, (t, v, col) in enumerate((("로그 발견", c["accessed"], C["card_found"]), ("404 있음", c["with_404"], C["card_404"]),
                                         ("500 있음", c["with_500"], C["card_500"]), ("위험", c["danger"], C["card_danger"]))):
            stat_box(row, t, v, "개", col).grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
            row.columnconfigure(i, weight=1)
        for s in result.failed_servers:
            wb = tk.Frame(self.body, bg=C["warn_bg"])
            wb.pack(fill="x", pady=(0, 4))
            tk.Label(wb, text=f"⚠  {s.name} 서버는 수집에 실패해 확인되지 않았습니다. ({s.reason[:60]})",
                     bg=C["warn_bg"], fg=C["warn_fg"], font=(F["ui"], 9), wraplength=480, justify="left"
                     ).pack(anchor="w", padx=10, pady=6)
        unread = sum(s.parse_errors for s in result.servers if s.ok)
        if unread:
            wb = tk.Frame(self.body, bg=C["warn_bg"])
            wb.pack(fill="x", pady=(0, 4))
            n_in = sum(s.unparsed_matched for s in result.servers if s.ok)
            tk.Label(wb, text=f"⚠  로그 형식과 맞지 않는 줄이 {unread}줄 있습니다."
                              + (f" 의심 IP가 든 {n_in}줄은 결과에 '해석 실패'로 넣었으니 원문을 확인하세요." if n_in else
                                 " 결과 화면의 [읽지 못한 줄 보기]에서 확인하세요."),
                     bg=C["warn_bg"], fg=C["warn_fg"], font=(F["ui"], 9), wraplength=480, justify="left"
                     ).pack(anchor="w", padx=10, pady=6)
        label(self.foot, "결과는 아직 파일로 저장되지 않았습니다.", 9, fg=C["sub"]).pack(side="left")
        buttons_right(self.foot, [("닫기", "TButton", self.close),
                                  ("결과 보기", "Accent.TButton", lambda: self.close("show"))])[0].pack(side="right")
        self.win.bind("<Return>", lambda e: self.close("show"))


class SavedDialog(Dialog):
    def __init__(self, parent, path):
        super().__init__(parent, "저장 완료", "✔", (C["ok_bg"], C["green"]), "저장했습니다", path, width=520)
        buttons_right(self.foot, [("닫기", "TButton", self.close),
                                  ("폴더 열기", "TButton", lambda: (_open(os.path.dirname(path)), self.close())),
                                  ("파일 열기", "Accent.TButton", lambda: (_open(path), self.close()))])[0].pack(side="right")


class ListDialog(Dialog):
    """제외·오류 목록, 연결 테스트 결과 같은 표를 보여준다."""

    def __init__(self, parent, title, heading, columns, rows, sub=None, note=None, copy_text=None, width=640):
        super().__init__(parent, title, "i", (C["accent_light"], C["accent"]), heading, sub, width=width)
        tv = ttk.Treeview(self.body, columns=[c for c, _ in columns], show="headings", height=min(12, max(3, len(rows))))
        fnt = tkfont.Font(family=F["ui"], size=10)
        for i, (c, w) in enumerate(columns):
            longest = max([fnt.measure(str(r[i])) for r in rows if i < len(r) and r[i] not in ("bad", "good")]
                          + [fnt.measure(c)], default=0)
            tv.heading(c, text=c)
            tv.column(c, width=max(w, min(longest + 24, 640)), anchor="w")
        tv.tag_configure("bad", foreground=C["red"])
        tv.tag_configure("good", foreground=C["green"])
        for r in rows:
            tag = r[-1] if r and r[-1] in ("bad", "good") else ""
            tv.insert("", "end", values=r[:-1] if tag else r, tags=(tag,) if tag else ())
        tv.pack(fill="both", expand=True, pady=(6, 0))
        if not rows:
            label(self.body, "항목이 없습니다.", 10, fg=C["sub"]).pack(anchor="w", pady=6)
        if note:
            label(self.body, note, 9, fg=C["sub"], wraplength=width - 60, justify="left").pack(anchor="w", pady=(8, 0))
        btns = [("닫기", "Accent.TButton", self.close)]
        if copy_text:
            def copy():
                self.win.clipboard_clear()
                self.win.clipboard_append(copy_text)
                self.copied.configure(text="복사했습니다.")
            btns.insert(0, ("원문 복사", "TButton", copy))
            self.copied = label(self.foot, "", 9, fg=C["green"])
            self.copied.pack(side="left")
        buttons_right(self.foot, btns)[0].pack(side="right")


class AiDialog(Dialog):
    """AI 분석 결과 보기. [보낸 내용 보기]로 AI에 실제로 보낸 글을 확인할 수 있다. 결과: "retry"(다시 분석) 또는 None"""

    def __init__(self, parent, note, grade):
        sub = (f"참고용 분석입니다. 위험도는 규칙 판정({grade})을 따르세요.  ·  모델 {note.model or '기본'}  ·  "
               f"{note.lines_sent}/{note.lines_total}줄 보냄  ·  민감값 {'가림' if note.masked else '안 가림'}  ·  "
               f"{note.elapsed:.0f}초  ·  {note.time:%m-%d %H:%M}")
        super().__init__(parent, "AI 분석", "AI", (C["accent_light"], C["accent"]), f"AI 분석 · {note.ioc_key}", sub,
                         width=820)
        self.note, self.showing = note, "answer"
        self.win.resizable(True, True)
        box = tk.Frame(self.body, bg=C["panel"])
        box.pack(fill="both", expand=True, pady=(6, 0))
        self.text = tk.Text(box, height=22, width=96, wrap="word", font=(F["ui"], 10), bg=C["input"], fg=C["text"],
                            relief="flat", highlightthickness=1, highlightbackground=C["line"], padx=10, pady=8)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.copied = label(self.foot, "", 9, fg=C["green"])
        self.copied.pack(side="left")
        bar, btns = buttons_right(self.foot, [("보낸 내용 보기", "TButton", self.toggle), ("복사", "TButton", self.copy),
                                              ("다시 분석", "TButton", lambda: self.close("retry")),
                                              ("닫기", "Accent.TButton", self.close)])
        bar.pack(side="right")
        self.btn_toggle = btns[0]
        self._fill()

    def _fill(self):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", self.note.text if self.showing == "answer" else self.note.prompt)
        t.configure(state="disabled", font=(F["ui"], 10) if self.showing == "answer" else (F["mono"], 9))

    def toggle(self):
        self.showing = "prompt" if self.showing == "answer" else "answer"
        self.btn_toggle.configure(text="분석 결과 보기" if self.showing == "prompt" else "보낸 내용 보기")
        self._fill()

    def copy(self):
        self.win.clipboard_clear()
        self.win.clipboard_append(self.note.text if self.showing == "answer" else self.note.prompt)
        self.copied.configure(text="복사했습니다.")


class AskDialog(Dialog):
    """여러 칸을 입력받는다. fields: [(키, 이름, 기본값, 안내)]. 결과: {키: 입력값} 또는 None(취소). 빈 칸은 받지 않는다."""

    def __init__(self, parent, title, heading, fields, note="", ok_text="확인"):
        super().__init__(parent, title, "@", (C["accent_light"], C["accent"]), heading, note or None, width=560)
        g = tk.Frame(self.body, bg=C["panel"])
        g.pack(fill="x", pady=(6, 4))
        g.columnconfigure(1, weight=1)
        self.entries, self.names = {}, {}
        for row, (key, name, value, hint) in enumerate(fields):
            label(g, name, 10, True).grid(row=row * 2, column=0, sticky="w", padx=(0, 10), pady=(4, 0))
            e = ttk.Entry(g, font=(F["ui"], 10))
            e.insert(0, value)
            e.grid(row=row * 2, column=1, sticky="ew", pady=(4, 0))
            if hint:
                label(g, hint, 9, fg=C["sub"]).grid(row=row * 2 + 1, column=1, sticky="w")
            self.entries[key], self.names[key] = e, name
        first = next(iter(self.entries.values()))
        first.focus_set()
        first.select_range(0, "end")
        self.msg = label(self.foot, "", 9, fg=C["red"])
        self.msg.pack(side="left")
        buttons_right(self.foot, [("취소", "TButton", self.close), (ok_text, "Accent.TButton", self.ok)])[0].pack(side="right")
        self.win.bind("<Return>", lambda e: self.ok())

    def ok(self):
        got = {k: e.get().strip() for k, e in self.entries.items()}
        empty = [self.names[k] for k, v in got.items() if not v]
        if empty:
            self.msg.configure(text=f"{', '.join(empty)}을(를) 넣으세요.")
            return
        self.close(got)


class EmailDialog(Dialog):
    """메일 보내기 전에 받는 사람·제목·전할 말을 확인하고 고친다. 본문 요약은 결과에서 만들어 미리 보여 준다.

    preview(memo, 첨부 파일 이름 또는 "") -> 본문 글자 (미리보기·[본문 복사]용).
    결과: {to, cc, title, memo, attach, name(첨부 파일 이름)} 또는 None
    """

    def __init__(self, parent, to, cc, title, memo, preview, attach_name, attach_size, attach_error="", note=""):
        super().__init__(parent, "이메일 보내기", "@", (C["accent_light"], C["accent"]), "점검 결과를 이메일로 보냅니다",
                         note or None, width=780)
        self.preview, self.default_name = preview, attach_name
        self.win.resizable(True, True)
        g = tk.Frame(self.body, bg=C["panel"])
        g.pack(fill="x", pady=(6, 4))
        g.columnconfigure(1, weight=1)
        self.fields = {}
        for row, (key, name, value) in enumerate((("to", "받는 사람", to), ("cc", "참조", cc), ("title", "제목", title))):
            label(g, name, 10, True).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            e = ttk.Entry(g, font=(F["ui"], 10))
            e.insert(0, value)
            e.grid(row=row, column=1, sticky="ew", pady=3)
            self.fields[key] = e
        label(g, "여러 명은 , 또는 ; 로 구분", 9, fg=C["sub"]).grid(row=0, column=2, sticky="w", padx=8)
        label(g, "전할 말", 10, True).grid(row=3, column=0, sticky="nw", padx=(0, 10), pady=3)
        self.memo = tk.Text(g, height=3, wrap="word", font=(F["ui"], 10), bg=C["input"], fg=C["text"], relief="flat",
                            highlightthickness=1, highlightbackground=C["line"], padx=6, pady=4, insertbackground=C["text"])
        self.memo.grid(row=3, column=1, columnspan=2, sticky="ew", pady=3)
        self.memo.insert("1.0", memo)
        self.memo.bind("<KeyRelease>", lambda e: self._refresh())
        label(self.body, "보낼 본문 미리보기 (요약·IP 표는 점검 결과에서 만들어 표로 보냅니다)", 9, fg=C["sub"]).pack(anchor="w")
        box = tk.Frame(self.body, bg=C["panel"])
        box.pack(fill="both", expand=True, pady=(2, 0))
        self.text = tk.Text(box, height=12, width=90, wrap="word", font=(F["mono"], 9), bg=C["panel2"], fg=C["text"],
                            relief="flat", highlightthickness=1, highlightbackground=C["line"], padx=8, pady=6)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.attach = var(not attach_error)
        ar = tk.Frame(self.body, bg=C["panel"])
        ar.pack(fill="x", pady=(6, 0))
        cb = check(ar, "HTML 보고서 첨부", self.attach, command=self._refresh)
        cb.pack(side="left")
        label(ar, "파일 이름", 9).pack(side="left", padx=(14, 6))
        self.name = ttk.Entry(ar, width=44, font=(F["ui"], 10))
        self.name.insert(0, attach_name)
        self.name.pack(side="left")
        self.name.bind("<KeyRelease>", lambda e: self._refresh())
        label(ar, f"({attach_size})", 9, fg=C["sub"]).pack(side="left", padx=6)
        if attach_error:
            cb.configure(state="disabled")
            label(self.body, attach_error, 9, fg=C["red"], wraplength=720, justify="left").pack(anchor="w")
        label(self.body, "제목·본문의 개인정보(토큰·주민번호·전화번호·카드번호·이메일 등)는 가리고 보냅니다.", 9,
              fg=C["sub"]).pack(anchor="w", pady=(4, 0))
        self.msg = label(self.foot, "", 9, fg=C["green"])
        self.msg.pack(side="left")
        buttons_right(self.foot, [("본문 복사", "TButton", self.copy), ("취소", "TButton", self.close),
                                  ("보내기", "Accent.TButton", self.ok)])[0].pack(side="right")
        self._refresh()

    def _memo(self):
        return self.memo.get("1.0", "end-1c").strip()

    def _name(self):
        """첨부 파일 이름: 파일 이름에 못 쓰는 글자는 빼고, 확장자는 .html로."""
        n = re.sub(r'[\\/:*?"<>|]+', "", self.name.get()).strip().rstrip(".")
        if not n:
            return self.default_name
        return n if n.lower().endswith(".html") else n + ".html"

    def _attach_name(self):
        return self._name() if self.attach.get() else ""

    def _refresh(self):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", self.preview(self._memo(), self._attach_name()))
        t.configure(state="disabled")

    def copy(self):
        self.win.clipboard_clear()
        self.win.clipboard_append(self.preview(self._memo(), self._attach_name()))
        self.msg.configure(text="본문을 복사했습니다.", fg=C["green"])

    def ok(self):
        got = {k: e.get().strip() for k, e in self.fields.items()}
        if not got["to"]:
            self.msg.configure(text="받는 사람을 넣으세요.", fg=C["red"])
            return
        if not got["title"]:
            self.msg.configure(text="제목을 넣으세요.", fg=C["red"])
            return
        got["memo"] = self._memo()
        got["attach"] = bool(self.attach.get())
        got["name"] = self._name()
        self.close(got)


class ProgressDialog(Dialog):
    """오래 걸리는 작업의 진행 창. 띄운 채로 step()으로 고치고 끝나면 close()한다 (기다리지 않음).

    떠 있는 동안 메인 창은 누를 수 없다. [중지]나 창 닫기를 누르면 on_stop()을 한 번 부른다.
    """

    def __init__(self, parent, title, heading, sub, total, on_stop, stop_text="중지"):
        super().__init__(parent, title, "AI", (C["accent_light"], C["accent"]), heading, sub, width=520)
        self.total, self.on_stop, self.stopped = total, on_stop, False
        self.started = time.monotonic()
        self.bar = ttk.Progressbar(self.body, maximum=max(total, 1), mode="determinate")
        self.bar.pack(fill="x", pady=(10, 6))
        row = tk.Frame(self.body, bg=C["panel"])
        row.pack(fill="x")
        self.msg = label(row, "준비 중…", 10)
        self.msg.pack(side="left")
        self.clock = label(row, "", 9, fg=C["sub"])
        self.clock.pack(side="right")
        self.note = label(self.body, "", 9, fg=C["sub"], wraplength=470, justify="left")
        self.note.pack(anchor="w", pady=(6, 0))
        bar, btns = buttons_right(self.foot, [(stop_text, "TButton", self.stop)])
        bar.pack(side="right")
        self.btn_stop = btns[0]
        self.win.protocol("WM_DELETE_WINDOW", self.stop)
        self.win.bind("<Escape>", lambda e: self.stop())

    def open(self):
        w = self.win
        center_window(w, w.master)
        w.deiconify()
        try:
            w.grab_set()
        except tk.TclError:   # 메인 창이 아직 안 보이면 잡지 못한다 (테스트 등)
            pass
        self._tick()
        return self

    def _tick(self):
        if not self.win.winfo_exists():
            return
        sec = int(time.monotonic() - self.started)
        self.clock.configure(text=f"경과 {sec // 60}:{sec % 60:02d}")
        self.win.after(1000, self._tick)

    def step(self, i, text):
        if self.win.winfo_exists():
            self.bar["value"] = i - 1
            self.msg.configure(text=f"{i}/{self.total} · {text}")

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.btn_stop.state(["disabled"])
        self.btn_stop.configure(text="중지하는 중…")
        self.note.configure(text="지금 보낸 IP의 답을 받으면 멈춥니다.", fg=C["amber"])
        self.on_stop()

    def close(self, result=None):
        if self.win.winfo_exists():
            super().close(result)


def _open(path):
    try:
        os.startfile(path)  # noqa: S606 - Windows에서 파일/폴더 열기
    except (AttributeError, OSError):
        pass

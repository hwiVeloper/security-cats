"""공통 위젯: 카드, 요약 카드, 배지, 구간 버튼 등."""
import tkinter as tk
from tkinter import ttk

from .theme import C, F, GRADE_COLOR_KEY

KEEP = []  # tk 변수가 가비지 컬렉션되면 선택 표시가 사라지므로 참조를 유지한다


def var(v):
    if isinstance(v, bool):
        x = tk.BooleanVar(value=v)
    elif isinstance(v, int):
        x = tk.IntVar(value=v)
    else:
        x = tk.StringVar(value=v)
    KEEP.append(x)
    return x


def label(parent, text, size=10, bold=False, fg=None, bg=None, mono=False, **kw):
    return tk.Label(parent, text=text, bg=bg or C["panel"], fg=fg or C["text"],
                    font=(F["mono"] if mono else F["ui"], size, "bold" if bold else "normal"), **kw)


def card(parent, **kw):
    return tk.Frame(parent, bg=C["panel"], highlightthickness=1, highlightbackground=C["line"],
                    highlightcolor=C["line"], **kw)


def check(parent, text, variable, **kw):
    return tk.Checkbutton(parent, text=text, variable=variable, bg=C["panel"], fg=C["text"],
                          selectcolor=C["check"], activebackground=C["panel"], activeforeground=C["text"],
                          font=(F["ui"], 10), bd=0, highlightthickness=0, disabledforeground=C["faint"], **kw)


def step_title(parent, num, text):
    f = tk.Frame(parent, bg=C["panel"])
    tk.Label(f, text=num, bg=C["accent"], fg=C["accent_fg"], font=(F["ui"], 9, "bold"), width=2).pack(side="left")
    label(f, text, 11, True).pack(side="left", padx=(8, 0))
    return f


def scroll_area(parent, bg=None, **pack):
    """세로로 넘치면 스크롤되는 영역. 안에 위젯을 넣을 Frame을 돌려준다.

    창이 작거나 화면 배율(125%·150%)이 커서 내용이 넘칠 때만 스크롤바가 보인다.
    마우스 휠은 이 영역 위에 있을 때만 움직이고, 안쪽의 입력칸(Text)은 자기 내용을 스크롤한다.
    """
    bg = bg or C["panel"]
    holder = tk.Frame(parent, bg=bg)
    holder.pack(fill="both", expand=True, **pack)
    canvas = tk.Canvas(holder, bg=bg, highlightthickness=0, bd=0, yscrollincrement=20)
    sb = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True)
    body = tk.Frame(canvas, bg=bg)
    win = canvas.create_window(0, 0, window=body, anchor="nw")

    def refresh(_=None):
        h = body.winfo_reqheight()
        canvas.configure(scrollregion=(0, 0, 0, h))
        if h > canvas.winfo_height() > 1:
            if not sb.winfo_ismapped():
                sb.pack(side="right", fill="y", padx=(4, 0), before=canvas)
        elif sb.winfo_ismapped():
            sb.pack_forget()
            canvas.yview_moveto(0)

    def resized(e):
        canvas.itemconfigure(win, width=e.width)
        refresh()

    def wheel(e):
        if not canvas.winfo_exists() or not sb.winfo_ismapped():
            return
        w = canvas.winfo_containing(e.x_root, e.y_root)
        while w is not None and w is not canvas:
            if isinstance(w, (tk.Text, tk.Listbox, ttk.Treeview)):
                return
            w = w.master
        if w is canvas:
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    canvas.bind("<Configure>", resized)
    body.bind("<Configure>", refresh)
    canvas.bind_all("<MouseWheel>", wheel)   # 화면을 다시 그릴 때마다 새 영역으로 바뀐다
    body.scroll_bar = sb                     # 테스트·확인용
    return body


def center_window(win, parent, width=None, height=None):
    """팝업을 메인 창 가운데에 놓는다 (화면 밖으로 나가지 않게).

    width·height를 주면 그 크기로, 아니면 내용 크기(최소 크기 이상)로 계산한다.
    """
    win.update_idletasks()
    top = parent.winfo_toplevel()
    min_w, min_h = win.minsize()
    w = width or max(win.winfo_reqwidth(), min_w)
    h = height or max(win.winfo_reqheight(), min_h)
    x = top.winfo_rootx() + (top.winfo_width() - w) // 2
    y = top.winfo_rooty() + (top.winfo_height() - h) // 2
    x = max(0, min(x, win.winfo_screenwidth() - w))
    y = max(0, min(y, win.winfo_screenheight() - h - 40))
    win.geometry(f"{w}x{h}+{x}+{y}" if width and height else f"+{x}+{y}")


class DatePicker:
    """입력칸 아래에 작은 달력을 띄워 날짜를 고른다. 고르면 on_pick(date)를 부르고 닫힌다."""

    def __init__(self, anchor, current, on_pick):
        import calendar
        import datetime as _dt
        self._cal, self._dt = calendar, _dt
        self.on_pick = on_pick
        self.today = _dt.date.today()
        self.sel = current or self.today
        self.year, self.month = self.sel.year, self.sel.month
        w = self.win = tk.Toplevel(anchor)
        w.title("날짜 선택")
        w.configure(bg=C["panel"])
        w.resizable(False, False)
        w.transient(anchor.winfo_toplevel())
        self.body = tk.Frame(w, bg=C["panel"])
        self.body.pack(padx=10, pady=8)
        self.draw()
        w.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        x = max(0, min(x, w.winfo_screenwidth() - w.winfo_reqwidth()))
        y = max(0, min(y, w.winfo_screenheight() - w.winfo_reqheight() - 40))
        w.geometry(f"+{x}+{y}")
        w.bind("<Escape>", lambda e: w.destroy())
        w.grab_set()
        w.focus_force()

    def move(self, delta):
        m = self.month - 1 + delta
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self.draw()

    def pick(self, d):
        self.win.grab_release()
        self.win.destroy()
        self.on_pick(d)

    def draw(self):
        for c in self.body.winfo_children():
            c.destroy()
        head = tk.Frame(self.body, bg=C["panel"])
        head.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 4))
        ttk.Button(head, text="◀", width=3, style="Small.TButton", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(head, text="▶", width=3, style="Small.TButton", command=lambda: self.move(1)).pack(side="right")
        label(head, f"{self.year}년 {self.month}월", 10, True).pack(side="top")
        for i, wd in enumerate("월화수목금토일"):
            label(self.body, wd, 9, fg=C["red"] if i == 6 else C["sub"]).grid(row=1, column=i, padx=1)
        for r, week in enumerate(self._cal.Calendar(firstweekday=0).monthdatescalendar(self.year, self.month), 2):
            for c, d in enumerate(week):
                is_sel, is_today = d == self.sel, d == self.today
                b = tk.Label(self.body, text=str(d.day), width=3, font=(F["ui"], 10, "bold" if is_today else "normal"),
                             bg=C["accent"] if is_sel else (C["accent_light"] if is_today else C["panel"]),
                             fg=C["accent_fg"] if is_sel else (C["faint"] if d.month != self.month else C["text"]),
                             cursor="hand2", pady=3)
                b.grid(row=r, column=c, padx=1, pady=1)
                b.bind("<Button-1>", lambda e, d_=d: self.pick(d_))
        foot = tk.Frame(self.body, bg=C["panel"])
        foot.grid(row=9, column=0, columnspan=7, sticky="ew", pady=(6, 0))
        ttk.Button(foot, text="오늘", style="Small.TButton", command=lambda: self.pick(self.today)).pack(side="left")
        ttk.Button(foot, text="닫기", style="Small.TButton", command=self.win.destroy).pack(side="right")


def divider(parent, pady=(14, 12)):
    """구역을 나누는 가는 선"""
    line = tk.Frame(parent, bg=C["line"], height=1)
    line.pack(fill="x", pady=pady)
    return line


def badge(parent, text, colors=None, grade=None):
    if grade:
        colors = C["lowbadge"] if grade in ("LOW",) else C[GRADE_COLOR_KEY.get(grade, "none")]
    return tk.Label(parent, text=f" {text} ", bg=colors[0], fg=colors[1], font=(F["ui"], 9, "bold"))


def stat_box(parent, title, value, unit, color, command=None):
    f = card(parent)
    tk.Frame(f, bg=color, height=4).pack(fill="x")
    t = label(f, title, 9, fg=C["sub"])
    t.pack(anchor="w", padx=12, pady=(8, 0))
    row = tk.Frame(f, bg=C["panel"])
    row.pack(anchor="w", padx=12, pady=(0, 8))
    v = label(row, str(value), 22, True, fg=color)
    v.pack(side="left")
    u = label(row, unit, 10, fg=C["sub"])
    u.pack(side="left", padx=(4, 0), pady=(10, 0))
    if command:
        for w in (f, t, row, v, u):
            w.bind("<Button-1>", lambda e: command())
            w.configure(cursor="hand2")
    return f


def seg(parent, items, variable, command=None, bg=None):
    f = tk.Frame(parent, bg=bg or C["panel"])
    for val, text in items:
        ttk.Radiobutton(f, text=text, value=val, variable=variable, style="Seg.Toolbutton",
                        command=(lambda x=val: command(x)) if command else None).pack(side="left")
    return f


def buttons_right(parent, buttons, bg=None):
    """buttons: [(text, style, command)] 화면 왼쪽 -> 오른쪽 순서."""
    f = tk.Frame(parent, bg=bg or parent["bg"])
    out = []
    for text, style, cmd in reversed(buttons):
        b = ttk.Button(f, text=text, style=style, command=cmd)
        b.pack(side="right", padx=(6, 0))
        out.append(b)
    return f, list(reversed(out))


def scrolled_tree(parent, xscroll=False, **kw):
    """세로 스크롤바가 붙은 Treeview. (frame, tree) 를 돌려준다.

    xscroll=True면 가로 스크롤바도 붙인다 (칸 너비 합이 표보다 넓을 때 잘린 칸을 옆으로 넘겨 볼 수 있게).
    """
    fr = tk.Frame(parent, bg=C["panel"])
    tv = ttk.Treeview(fr, **kw)
    sb = ttk.Scrollbar(fr, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    tv.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")
    if xscroll:
        hb = ttk.Scrollbar(fr, orient="horizontal", command=tv.xview)
        tv.configure(xscrollcommand=hb.set)
        hb.grid(row=1, column=0, sticky="ew")
    fr.rowconfigure(0, weight=1)
    fr.columnconfigure(0, weight=1)
    return fr, tv


def grade_tags(tv):
    """위험도별 행 색. row_style이 text면 배경은 그대로 두고 글자색만 바꾼다(선택 표시와 겹치지 않게)."""
    for g, key in GRADE_COLOR_KEY.items():
        if C.get("row_style") == "text":
            fg = {"crit": C["red"], "high": C["amber"], "med": C.get("med_text", C["amber"])}.get(key, C[key][1])
            tv.tag_configure(g, background=C["panel"], foreground=fg)
        else:
            tv.tag_configure(g, background=C[key][0], foreground=C[key][1])


def _ime_off(widget):
    """Windows 한글 입력기를 영문으로 바꾼다 (비밀번호 칸에 들어갈 때). 다른 OS나 실패하면 그냥 넘어간다."""
    try:
        import ctypes
        imm = ctypes.windll.imm32
        imm.ImmGetContext.restype = ctypes.c_void_p
        imm.ImmGetContext.argtypes = [ctypes.c_void_p]
        imm.ImmSetOpenStatus.argtypes = [ctypes.c_void_p, ctypes.c_int]
        imm.ImmReleaseContext.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        hwnd = widget.winfo_id()
        himc = imm.ImmGetContext(hwnd)
        if himc:
            imm.ImmSetOpenStatus(himc, 0)
            imm.ImmReleaseContext(hwnd, himc)
    except (AttributeError, OSError, tk.TclError):
        pass


def ascii_only(entry, on_reject=None):
    """비밀번호·키 칸: 영문·숫자·기호(키보드의 ASCII 문자)만 받는다. 한글 등이 들어오면 막고 on_reject()를 부른다."""
    def ok(new):
        good = all(" " <= ch <= "~" for ch in new)
        if not good:
            (on_reject or entry.bell)()
        return good
    entry.configure(validate="key", validatecommand=(entry.register(ok), "%P"))
    entry.bind("<FocusIn>", lambda e: _ime_off(entry), add="+")
    return entry

"""밝게/어둡게 색표와 ttk 스타일 (PLAN.md 6.1)."""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

THEMES = {
    "light": dict(
        bg="#eef1f4", panel="#ffffff", panel2="#f4f6f8", line="#d6dbe2",
        text="#1f2933", sub="#66717f", faint="#9aa3ad", input="#fbfcfd",
        accent="#0f766e", accent_fg="#ffffff", accent_light="#d5ece9", accent_hover="#115e59",
        sel="#dbeafe", warn_bg="#fff4d6", warn_fg="#7a4b00", check="#ffffff",
        blue="#1d4ed8", amber="#b45309", red="#b91c1c", green="#15803d",
        crit=("#fee2e2", "#991b1b"), high=("#ffedd5", "#9a3412"), med=("#fef9c3", "#854d0e"),
        low=("#ffffff", "#1f2933"), none=("#ffffff", "#9aa3ad"), lowbadge=("#e5e7eb", "#374151"),
        ok_bg="#dcfce7",
        # 결과 요약 카드 색 (점검한 IP / 로그 발견 / 404 / 500 / 위험)
        card_total="#64748b", card_found="#2563eb", card_404="#d97706", card_500="#7c3aed", card_danger="#dc2626",
        row_style="fill",   # 위험도 행 표시: fill = 행 배경색, text = 글자색만
    ),
    "dark": dict(
        bg="#0f141a", panel="#161c24", panel2="#1d2530", line="#2a3442",
        text="#e6edf3", sub="#8b98a8", faint="#5d6878", input="#0b1016",
        accent="#2dd4bf", accent_fg="#062420", accent_light="#163634", accent_hover="#5eead4",
        sel="#1e3a5f", warn_bg="#3a2e10", warn_fg="#fcd34d", check="#0b1016",
        blue="#60a5fa", amber="#fbbf24", red="#f87171", green="#4ade80",
        crit=("#4a1d1d", "#fecaca"), high=("#4a2b14", "#fed7aa"), med=("#45390f", "#fde68a"),
        low=("#161c24", "#e6edf3"), none=("#161c24", "#5d6878"), lowbadge=("#2a3442", "#cbd5e1"),
        ok_bg="#14352a",
        card_total="#94a3b8", card_found="#60a5fa", card_404="#fb923c", card_500="#a78bfa", card_danger="#f87171",
        row_style="fill",
    ),
}
GRADE_COLOR_KEY = {"CRITICAL": "crit", "HIGH": "high", "MEDIUM": "med", "LOW": "low", "NONE": "none", "-": "none"}

C = dict(THEMES["light"])  # 현재 테마 색 (테마를 바꾸면 내용을 교체한다)
F = {"ui": "맑은 고딕", "mono": "D2Coding"}


def setup(root: tk.Tk, theme: str) -> None:
    C.clear()
    C.update(THEMES.get(theme, THEMES["light"]))
    fams = set(tkfont.families(root))
    if F["ui"] not in fams:
        F["ui"] = "Segoe UI"
    if F["mono"] not in fams:
        F["mono"] = "Consolas"
    ui, mono = F["ui"], F["mono"]

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=C["panel"], foreground=C["text"], font=(ui, 10),
                bordercolor=C["line"], lightcolor=C["panel"], darkcolor=C["panel"],
                troughcolor=C["line"], fieldbackground=C["input"], insertcolor=C["text"],
                selectbackground=C["sel"], selectforeground=C["text"], focuscolor=C["panel"])
    s.configure("TFrame", background=C["panel"])
    s.configure("TButton", background=C["panel2"], foreground=C["text"], padding=(12, 6), bordercolor=C["line"])
    s.map("TButton", background=[("active", C["line"]), ("disabled", C["panel2"])],
          foreground=[("disabled", C["faint"])])
    s.configure("Small.TButton", padding=(8, 3), font=(ui, 9))
    s.configure("Accent.TButton", background=C["accent"], foreground=C["accent_fg"],
                bordercolor=C["accent"], font=(ui, 11, "bold"), padding=(14, 10))
    s.map("Accent.TButton", background=[("active", C["accent_hover"]), ("disabled", C["line"])],
          foreground=[("disabled", C["faint"])])
    s.configure("Danger.TButton", foreground=C["red"], font=(ui, 11, "bold"), padding=(14, 10))
    s.configure("Seg.Toolbutton", background=C["panel2"], foreground=C["sub"], padding=(12, 5))
    s.map("Seg.Toolbutton", background=[("selected", C["accent_light"]), ("active", C["line"])],
          foreground=[("selected", C["accent"]), ("disabled", C["faint"])])
    s.configure("Tab.Toolbutton", background=C["panel"], foreground=C["sub"], padding=(14, 7), font=(ui, 10, "bold"),
                bordercolor=C["panel"])
    s.map("Tab.Toolbutton", background=[("selected", C["accent_light"]), ("active", C["panel2"])],
          foreground=[("selected", C["accent"]), ("disabled", C["faint"])])
    s.configure("TRadiobutton", background=C["panel"], foreground=C["text"], indicatorbackground=C["input"])
    s.map("TRadiobutton", indicatorcolor=[("selected", C["accent"])], background=[("active", C["panel"])])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        s.configure(w, fieldbackground=C["input"], foreground=C["text"], padding=4, arrowcolor=C["sub"])
    s.map("TCombobox", fieldbackground=[("readonly", C["input"])], foreground=[("readonly", C["text"])])
    s.map("TEntry", foreground=[("disabled", C["faint"])])
    # 화면 배율(125%·150%)에서는 글자가 커지므로 표의 줄 높이도 같이 키운다
    scale = max(1.0, float(root.tk.call("tk", "scaling")) * 72 / 96)
    s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["text"],
                rowheight=int(28 * scale))
    s.configure("Mono.Treeview", font=(mono, 10))
    s.configure("Treeview.Heading", background=C["panel2"], foreground=C["sub"],
                font=(ui, 9, "bold"), relief="flat", padding=(6, 5))
    s.map("Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["text"])])
    s.map("Treeview.Heading", background=[("active", C["line"])])
    s.configure("Horizontal.TProgressbar", background=C["accent"], lightcolor=C["accent"],
                darkcolor=C["accent"], thickness=8)
    s.configure("Fail.Horizontal.TProgressbar", background=C["red"], lightcolor=C["red"], darkcolor=C["red"])
    s.configure("Big.Horizontal.TProgressbar", thickness=14)
    s.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["panel"], bordercolor=C["panel"],
                arrowcolor=C["sub"])
    root.option_add("*TCombobox*Listbox.background", C["input"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])

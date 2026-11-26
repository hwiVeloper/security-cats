"""[점검 이력] 탭: 어떤 조건으로 확인했고, 어디까지 검토했는지 남긴다.

이력에는 조건·결과 요약·근거 위치(파일·줄번호)·검토 상태·메모만 있다. 로그 원문은 없다.
"""
import os
import tkinter as tk
from tkinter import messagebox, ttk

from .. import APP_NAME
from ..core import history
from .theme import C, F
from .widgets import badge, buttons_right, card, label, scrolled_tree, var

REVIEW_COLOR = {"검토 전": "sub", "확인 완료": "green", "조치 필요": "red"}


def _dt(s, with_sec=False):
    if not s:
        return ""
    return s.replace("T", " ")[: 19 if with_sec else 16].split("+")[0]


class HistoryPage:
    def __init__(self, parent, main):
        self.main = main
        st = main.history_state
        self.query = var(st.get("query", ""))
        self.selected = st.get("selected")
        w = self.frame = tk.Frame(parent, bg=C["bg"])
        w.pack(fill="both", expand=True, padx=18, pady=(12, 12))
        head = tk.Frame(w, bg=C["bg"])
        head.pack(fill="x")
        label(head, "점검 이력", 15, True, bg=C["bg"]).pack(side="left")
        label(head, "어떤 조건으로 확인했고, 어디까지 검토했는지 남깁니다. 로그 원문은 저장하지 않습니다.", 9,
              fg=C["sub"], bg=C["bg"]).pack(side="left", padx=10, pady=(6, 0))

        body = tk.Frame(w, bg=C["bg"])
        body.pack(fill="both", expand=True, pady=(10, 0))
        left = card(body, width=self.main.px(420))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        sr = tk.Frame(left, bg=C["panel"])
        sr.pack(fill="x", padx=10, pady=(10, 6))
        e = ttk.Entry(sr, textvariable=self.query, width=22, font=(F["mono"], 10))
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.reload())
        ttk.Button(sr, text="IP로 찾기", style="Small.TButton", command=self.reload).pack(side="left", padx=4)
        ttk.Button(sr, text="전체", style="Small.TButton", command=lambda: (self.query.set(""), self.reload())).pack(side="left")
        self.lbl_ip = label(left, "", 9, fg=C["sub"], wraplength=350, justify="left")
        self.lbl_ip.pack(anchor="w", padx=10)
        fr, self.tv = scrolled_tree(left, columns=("t", "n", "s", "r"), show="headings", selectmode="browse")
        for c, h, wd in (("t", "점검 일시", 125), ("n", "IP", 40), ("s", "결과", 140), ("r", "검토", 70)):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=self.main.px(wd), anchor="w" if c in ("t", "s") else "center")
        for k, col in REVIEW_COLOR.items():
            self.tv.tag_configure(k, foreground=C[col])
        self.tv.bind("<<TreeviewSelect>>", lambda e: self.on_select())
        fr.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        self.detail = card(body)
        self.detail.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.reload()

    def save_state(self):
        self.main.history_state = {"query": self.query.get(), "selected": self.selected}

    def reload(self):
        q = self.query.get().strip()
        rows = history.list_checks(q)
        self.tv.delete(*self.tv.get_children())
        for r in rows:
            c = r["counts"] or {}
            res = f"발견 {c.get('accessed', 0)}" + (f" · 위험 {c['danger']}" if c.get("danger") else "")
            srv_fail = sum(1 for s in r["servers"] if s["state"] not in ("완료", "부분 분석"))
            if srv_fail:
                res += f" · 미확인 {srv_fail}"
            self.tv.insert("", "end", iid=str(r["id"]), values=(_dt(r["started"]), len(r["iocs"]), res, r["review"]),
                           tags=(r["review"],))
        if q:
            ih = history.ip_history(q)
            hits = ", ".join(f"{_dt(c['started'])[:10]}" for c, _ in ih["hits"][:3])
            self.lbl_ip.configure(text=f"{q}: 점검 대상 {ih['count']}회"
                                  + (f" (최초 {_dt(ih['first'])[:10]})" if ih["first"] else "")
                                  + (f" · 로그 발견 {len(ih['hits'])}회: {hits}" if ih["hits"] else " · 로그 발견 없음"))
        else:
            self.lbl_ip.configure(text=f"점검 {len(rows)}건 (최근 순)")
        ids = [str(r["id"]) for r in rows]
        if self.selected and str(self.selected) in ids:
            self.tv.selection_set(str(self.selected))
        elif ids:
            self.tv.selection_set(ids[0])
        else:
            self.selected = None
            self.show_detail(None)

    def on_select(self):
        sel = self.tv.selection()
        if sel:
            self.selected = int(sel[0])
            self.show_detail(history.get(self.selected))

    def show_detail(self, rec):
        d = self.detail
        for w in d.winfo_children():
            w.destroy()
        if not rec:
            label(d, "점검을 하면 요약이 여기에 쌓입니다.", 11, fg=C["sub"]).pack(padx=20, pady=30)
            return
        inner = tk.Frame(d, bg=C["panel"])
        inner.pack(fill="both", expand=True, padx=18, pady=14)
        top = tk.Frame(inner, bg=C["panel"])
        top.pack(fill="x")
        c = rec["counts"] or {}
        label(top, f"IP {len(rec['iocs'])}개 점검", 14, True).pack(side="left")
        tk.Label(top, text=f" {rec['review']} ", bg=C["panel2"], fg=C[REVIEW_COLOR.get(rec["review"], "sub")],
                 font=(F["ui"], 9, "bold")).pack(side="right")
        label(inner, f"{_dt(rec['started'], True)} · 실행자 {rec['user'] or '-'} · 프로그램 v{rec['app_version']}", 9,
              fg=C["sub"]).pack(anchor="w", pady=(2, 8))

        grid = tk.Frame(inner, bg=C["panel"])
        grid.pack(fill="x")
        ips = rec["iocs"]
        rows = (("조사 기간", f"{_dt(rec['period_start'])} ~ {_dt(rec['period_end'])}"),
                ("조회 IP", ", ".join(ips[:8]) + (f" 외 {len(ips) - 8}개" if len(ips) > 8 else "")),
                ("결과 요약", f"로그 발견 {c.get('accessed', 0)}개 · 조회 범위 내 미발견 {c.get('not_accessed', 0)}개 · "
                              f"404 있음 {c.get('with_404', 0)} · 500 있음 {c.get('with_500', 0)} · 위험 {c.get('danger', 0)}"),
                ("분석 범위", rec["coverage"] or ""),
                ("분석 버전", f"규칙 {rec['rule_version'] or '-'}"))
        for i, (k, v) in enumerate(rows):
            label(grid, k, 9, fg=C["sub"], width=9, anchor="w").grid(row=i, column=0, sticky="nw", pady=2)
            label(grid, v, 10, anchor="w", justify="left", wraplength=640).grid(row=i, column=1, sticky="w", pady=2)

        chips = tk.Frame(inner, bg=C["panel"])
        chips.pack(fill="x", pady=(8, 4))
        servers = rec["servers"]
        shown = servers
        if len(servers) > 3:
            # 서버가 많으면 한 줄 요약 + 완료가 아닌 서버만 따로 (이름이 길어도 잘리지 않게 줄을 바꿔 놓는다)
            counts = {}
            for s in servers:
                counts[s["state"]] = counts.get(s["state"], 0) + 1
            tk.Label(chips, text=f" 서버 {len(servers)}대 · " + " · ".join(f"{k} {v}" for k, v in counts.items()) + " ",
                     bg=C["panel2"], fg=C["text"], font=(F["ui"], 9, "bold")).grid(row=0, column=0, sticky="w", pady=2)
            shown = [s for s in servers if s["state"] != "완료"]
        base = 1 if len(servers) > 3 else 0
        for i, s in enumerate(shown):
            col = {"완료": "green", "부분 분석": "amber"}.get(s["state"], "red")
            tk.Label(chips, text=f" ● {s['name']} · {s['state']} ", bg=C["panel2"], fg=C[col],
                     font=(F["ui"], 9, "bold")).grid(row=(base + i) // 3, column=(base + i) % 3, sticky="w",
                                                    padx=(0, 6), pady=2)

        label(inner, "점검 당시 확인 근거", 10, True).pack(anchor="w", pady=(8, 2))
        fr, tv = scrolled_tree(inner, xscroll=True, columns=("g", "ip", "n", "c", "why", "srv", "loc"), show="headings",
                               height=5)
        longest = max((len(s["name"]) for s in rec["servers"]), default=8)
        # 칸 너비 합이 표 너비를 넘지 않게 (판정 칸만 남는 폭을 가져간다)
        for col, h, wd in (("g", "위험도", 72), ("ip", "의심 IP", 112), ("n", "요청", 46), ("c", "404 / 500", 70),
                           ("why", "판정", 160), ("srv", "서버", max(80, min(170, 9 * longest + 10))),
                           ("loc", "원본 위치 (파일:줄)", 165)):
            tv.heading(col, text=h)
            tv.column(col, width=self.main.px(wd), anchor="center" if col in ("n", "c") else "w", stretch=col in ("why", "loc"))
        from .widgets import grade_tags
        grade_tags(tv)
        results = {}
        for r in rec["results"]:
            results[r["ip"]] = r
            ev = r["evidence"]
            srv = ev[0][0] if ev else ""
            loc = f"{os.path.basename(str(ev[0][1]).replace(chr(92), '/'))}:{ev[0][2]}" if ev else ""
            if len(ev) > 1:
                loc += f" 외 {len(ev) - 1}"
            g = r["grade"] if r["requests"] else "-"
            tv.insert("", "end", iid=r["ip"], values=(g, r["ip"], r["requests"], f"{r['c404']} / {r['c500']}", r["verdict"],
                                                      srv, loc), tags=(g,))
        # 아래 검토 영역(상태·메모·버튼)을 먼저 아래에 붙여 항상 보이게 하고, 남는 높이를 확인 근거 표가 쓴다
        low = tk.Frame(inner, bg=C["panel"])
        low.pack(side="bottom", fill="x")
        fr.pack(fill="both", expand=True)

        # 표에서 잘린 판정·원본 위치는 줄을 고르면 아래에 전체가 나온다
        line = label(low, "줄을 고르면 판정과 원본 위치 전체가 여기에 나옵니다.", 9, fg=C["sub"], anchor="w", justify="left",
                     wraplength=self.main.px(760))
        line.pack(fill="x", pady=(4, 0))

        def show_line(_=None):
            sel = tv.selection()
            r = results.get(sel[0]) if sel else None
            if not r:
                return
            locs = ", ".join(f"{sv} {os.path.basename(str(f).replace(chr(92), '/'))}:{ln}" for sv, f, ln, _ in r["evidence"])
            line.configure(text=f"{r['ip']}: {r['verdict']}" + (f"  ·  원본 위치: {locs}" if locs else ""), fg=C["text"])
        tv.bind("<<TreeviewSelect>>", show_line)

        rv = tk.Frame(low, bg=C["panel"])
        rv.pack(fill="x", pady=(10, 0))
        label(rv, "검토 상태", 10, True).pack(side="left")
        self.review = ttk.Combobox(rv, values=history.REVIEW_STATES, state="readonly", width=12)
        self.review.set(rec["review"])
        self.review.pack(side="left", padx=8)
        if rec.get("reviewed_at"):
            label(rv, f"마지막 검토 {_dt(rec['reviewed_at'])}", 9, fg=C["sub"]).pack(side="left", padx=6)
        label(low, "확인 메모", 10, True).pack(anchor="w", pady=(8, 2))
        self.memo = tk.Text(low, height=3, font=(F["ui"], 10), bg=C["input"], fg=C["text"], relief="flat",
                            highlightthickness=1, highlightbackground=C["line"], insertbackground=C["text"], wrap="word")
        self.memo.insert("1.0", rec["memo"] or "")
        self.memo.pack(fill="x")
        bb = tk.Frame(low, bg=C["panel"])
        bb.pack(fill="x", pady=(10, 0))
        self.lbl_msg = label(bb, "", 9, fg=C["green"])
        self.lbl_msg.pack(side="left")
        buttons_right(bb, [("삭제", "TButton", lambda: self.delete(rec)),
                           ("이 IP로 다시 점검", "TButton", lambda: self.main.set_iocs("\n".join(rec["iocs"]))),
                           ("검토 내용 저장", "Accent.TButton", lambda: self.save_review(rec))])[0].pack(side="right")

    def save_review(self, rec):
        history.update_review(rec["id"], self.review.get(), self.memo.get("1.0", "end-1c"))
        self.reload()
        self.lbl_msg.configure(text="검토 내용을 저장했습니다.") if self.lbl_msg.winfo_exists() else None

    def delete(self, rec):
        if messagebox.askyesno(APP_NAME, f"{_dt(rec['started'])} 점검 이력을 삭제할까요?", parent=self.main.root):
            history.delete(rec["id"])
            self.selected = None
            self.reload()

"""원격 서버에서 로그를 집계하는 awk 프로그램.

napi.logformat의 "AWK PARITY CONTRACT"를 POSIX awk 문법만으로 그대로 구현한다
(LC_ALL=C에서 실행하므로 length()와 문자 클래스는 바이트·ASCII 기준).
gawk 전용 기능과 정규식 반복 횟수 {n}은 쓰지 않는다. 반복이 필요한 정규식은
여기서 문자 클래스를 펼쳐 만든다.

입력: 셸 루프가 파일마다 "\\001F\\t<경로>" 표시 줄을 먼저 내고 파일 내용을 이어 붙인다.
출력: Global Constraints의 "원격 출력 프로토콜" 줄만 낸다. 입력 줄은 절대 그대로 내지 않는다.
변수: SINCE (YYYYMMDD 또는 0), MAXROWS, TOP404 (awk -v로 받는다).
"""
from __future__ import annotations

from napi.logformat import _CLF_FORMATS
from napi.models import CustomFormat

_HEX = "[0-9a-fA-F]"
_DIGIT = "[0-9]"
_ALPHA = "[A-Za-z]"
_UUID_RE = "^" + "-".join(_HEX * n for n in (8, 4, 4, 4, 12)) + "$"
_CLF_TIME_RE = _DIGIT * 2 + r"\/" + _ALPHA * 3 + r"\/" + _DIGIT * 4
_ISO_DATE_RE = "^" + _DIGIT * 4 + "-" + _DIGIT * 2 + "-" + _DIGIT * 2
_STATUS_RE = "^" + _DIGIT * 3 + "$"

# 공통 부분: 파일 표시 처리, 정규화, 집계, END 출력. @PARSE@ 자리에 형식별 parse()가 들어간다.
_COMMON = r"""
BEGIN {
  MONTHS = "JanFebMarAprMayJunJulAugSepOctNovDec"
  USE_SINCE = (SINCE + 0 != 0)
  SINCE_S = SINCE ""
  MAXROWS += 0
  TOP404 += 0
  nfiles = 0
  cur = 0
}

# 공백(space/tab) 기준 토큰화. 앞뒤 공백은 먼저 지운다.
function ntok(s, arr) {
  sub(/^[ \t]+/, "", s)
  sub(/[ \t]+$/, "", s)
  if (s == "") { split("", arr); return 0 }
  return split(s, arr, /[ \t]+/)
}

# "Nov" 같은 월 이름 -> "11". 모르는 이름이면 "".
function month(mon,    mi) {
  mi = index(MONTHS, mon)
  if (mi == 0 || mi % 3 != 1) return ""
  return sprintf("%02d", (mi + 2) / 3)
}

# t는 dd/Mon/yyyy로 시작하는 문자열. 날짜를 P_D에 넣고 성공 여부를 돌려준다.
function clfdate(t,    m) {
  m = month(substr(t, 4, 3))
  if (m == "") return 0
  P_D = substr(t, 8, 4) m substr(t, 1, 2)
  return 1
}

function isvar(s,    n) {
  n = length(s)
  if (s ~ /^[0-9]+$/) return 1
  if (s ~ /@UUID@/) return 1
  if (n >= 16 && s ~ /^[0-9a-fA-F]+$/) return 1
  if (n >= 6 && s ~ /^[0-9][0-9-]+$/) return 1
  if (index(s, "@") > 0) return 1
  if (index(s, "%") > 0 && n >= 20) return 1
  if (n >= 20 && s ~ /^[A-Za-z0-9_-]+$/ && s ~ /[0-9]/ && s ~ /[A-Za-z]/) return 1
  return 0
}

function norm(p,    i, n, j, segs, s, out) {
  i = index(p, "?")
  if (i > 0) p = substr(p, 1, i - 1)
  i = index(p, "#")
  if (i > 0) p = substr(p, 1, i - 1)
  n = split(p, segs, "/")
  out = ""
  for (j = 1; j <= n; j++) {
    s = segs[j]
    i = index(s, ";")
    if (i > 0) s = substr(s, 1, i - 1)
    if (s == "") continue
    if (isvar(s)) s = "{*}"
    out = out "/" s
  }
  if (out == "") out = "/"
  return out
}

@PARSE@

function newfile(p) {
  nfiles++
  fpath[nfiles] = p
  flines[nfiles] = 0
  fpf[nfiles] = 0
  foor[nfiles] = 0
  fmin[nfiles] = ""
  fmax[nfiles] = ""
  cur = nfiles
}

function handle(l,    d, np, mp, k, cls) {
  if (cur == 0) return
  flines[cur]++
  sub(/\r$/, "", l)
  if (!parse(l)) { fpf[cur]++; return }
  d = P_D
  if (fmin[cur] == "" || d < fmin[cur]) fmin[cur] = d
  if (d > fmax[cur]) fmax[cur] = d
  if (USE_SINCE && d < SINCE_S) { foor[cur]++; return }
  np = norm(P_P)
  mp = P_M SUBSEP np
  cls = substr(P_S, 1, 1) "xx"
  k = mp SUBSEP cls
  if (!(k in cnt)) {
    cnt[k] = 0
    kfirst[k] = d
    klast[k] = d
    kmp[k] = mp
    km[k] = P_M
    kp[k] = np
    kc[k] = cls
  }
  cnt[k]++
  if (d < kfirst[k]) kfirst[k] = d
  if (d > klast[k]) klast[k] = d
  if (P_S != "404") non404[mp] = 1
}

{
  i = index($0, "\001F\t")
  if (i == 0) { handle($0); next }
  # 마지막 줄에 개행이 없던 파일이면 표시가 그 줄 뒤에 붙어 온다
  if (i > 1) handle(substr($0, 1, i - 1))
  newfile(substr($0, i + 3))
}

function aggline(tag, k,    s) {
  s = tag "\t" km[k] "\t" kp[k]
  if (tag == "R") s = s "\t" kc[k]
  return s "\t" cnt[k] "\t" kfirst[k] "\t" klast[k]
}

END {
  if (nfiles == 0) print "E\tno files matched"
  for (i = 1; i <= nfiles; i++) {
    p = fpath[i]
    gsub(/[\001-\037\177]/, "?", p)
    lo = fmin[i]
    hi = fmax[i]
    if (lo == "") lo = "-"
    if (hi == "") hi = "-"
    print "F\t" p "\t" lo "\t" hi "\t" flines[i] "\t" fpf[i] "\t" foor[i]
  }

  nr = 0
  for (k in cnt) if (kmp[k] in non404) nr++
  if (nr > MAXROWS) {
    fflush()
    cmd = "sort -t '\t' -k5,5nr 2>/dev/null | head -n " MAXROWS
    for (k in cnt) if (kmp[k] in non404) print aggline("R", k) | cmd
    close(cmd)
    print "T\t" MAXROWS
  } else {
    for (k in cnt) if (kmp[k] in non404) print aggline("R", k)
  }

  # 404만 받은 키: 상위 TOP404개만 P로, 나머지는 Q 합계로
  n404 = 0
  tot404 = 0
  for (k in cnt) {
    if (kmp[k] in non404) continue
    n404++
    tot404 += cnt[k]
    hist[cnt[k]]++
  }
  keep = n404 < TOP404 ? n404 : TOP404
  if (keep < 0) keep = 0
  if (keep > 0) {
    fflush()
    cmd = "sort -t '\t' -k4,4nr 2>/dev/null | head -n " TOP404
    for (k in cnt) if (!(kmp[k] in non404)) print aggline("P", k) | cmd
    close(cmd)
  }
  # P에 들어간 keep개의 count 합 = count 큰 순서로 keep개의 합 (hist로 계산)
  topsum = 0
  rem = keep
  prev = -1
  while (rem > 0) {
    best = -1
    for (c in hist) {
      v = c + 0
      if ((prev < 0 || v < prev) && v > best) best = v
    }
    if (best < 0) break
    take = hist[best] < rem ? hist[best] : rem
    topsum += take * best
    rem -= take
    prev = best
  }
  print "Q\t" (n404 - keep) "\t" (tot404 - topsum)
  print "D\tdone"
}
"""

# CLF 계열: 첫 [dd/Mon/yyyy, 첫 "..." 요청줄, 닫는 따옴표 뒤 첫 토큰 = status
_PARSE_CLF = r"""
function parse(line,    q, rest, req, t, r, n) {
  if (!match(line, /\[@CLFTIME@/)) return 0
  if (!clfdate(substr(line, RSTART + 1, 11))) return 0
  q = index(line, "\"")
  if (q == 0) return 0
  rest = substr(line, q + 1)
  q = index(rest, "\"")
  if (q == 0) return 0
  req = substr(rest, 1, q - 1)
  rest = substr(rest, q + 1)
  n = ntok(req, t)
  if (n < 2 || t[1] !~ /^[A-Z]+$/) return 0
  n = ntok(rest, r)
  if (n < 1 || r[1] !~ /@STATUS@/) return 0
  P_M = t[1]
  P_P = t[2]
  P_S = r[1]
  return 1
}
"""

# custom: [ \t]+로 나눈 N번째 필드
_PARSE_CUSTOM = r"""
function parse(line,    f, n, p, tm) {
  n = ntok(line, f)
  if (@MF@ > n || @PF@ > n || @SF@ > n || @TF@ > n) return 0
  if (f[@MF@] !~ /^[A-Z]+$/ || f[@SF@] !~ /@STATUS@/) return 0
  p = f[@PF@]
  n = length(p)
  if (n >= 2 && substr(p, 1, 1) == "\"" && substr(p, n, 1) == "\"") p = substr(p, 2, n - 2)
  else if (substr(p, 1, 1) == "\"") p = substr(p, 2)
  else if (substr(p, n, 1) == "\"") p = substr(p, 1, n - 1)
  if (p == "") return 0
  tm = f[@TF@]
@TIME@
  P_M = f[@MF@]
  P_P = p
  P_S = f[@SF@]
  return 1
}
"""

_TIME_ISO = r"""  if (tm !~ /@ISODATE@/) return 0
  P_D = substr(tm, 1, 4) substr(tm, 6, 2) substr(tm, 9, 2)"""

_TIME_CLF = r"""  if (!match(tm, /@CLFTIME@/)) return 0
  if (!clfdate(substr(tm, RSTART, 11))) return 0"""


def _field(n: object) -> str:
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"필드 번호는 1 이상의 정수여야 해요: {n!r}")
    return str(n)


def awk_program(fmt: str, custom: CustomFormat | None) -> str:
    """형식에 맞는 awk 소스를 돌려준다. 모르는 형식이면 ValueError."""
    if fmt in _CLF_FORMATS:
        parse = _PARSE_CLF
    elif fmt == "custom":
        if custom is None:
            raise ValueError("custom 형식에는 사용자 정의 형식이 필요해요")
        if custom.time_format == "iso8601":
            time_part = _TIME_ISO
        elif custom.time_format == "clf":
            time_part = _TIME_CLF
        else:
            raise ValueError(f"모르는 시각 형식: {custom.time_format!r}")
        parse = (_PARSE_CUSTOM.replace("@TIME@", time_part)
                 .replace("@MF@", _field(custom.method_field))
                 .replace("@PF@", _field(custom.path_field))
                 .replace("@SF@", _field(custom.status_field))
                 .replace("@TF@", _field(custom.time_field)))
    else:
        raise ValueError(f"모르는 로그 형식: {fmt!r}")
    return (_COMMON.replace("@PARSE@", parse.strip("\n"))
            .replace("@UUID@", _UUID_RE)
            .replace("@CLFTIME@", _CLF_TIME_RE)
            .replace("@ISODATE@", _ISO_DATE_RE)
            .replace("@STATUS@", _STATUS_RE)
            .strip("\n") + "\n")

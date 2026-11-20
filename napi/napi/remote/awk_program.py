"""원격 서버에서 로그를 집계하는 awk 프로그램.

napi.logformat의 "AWK PARITY CONTRACT"를 POSIX awk 문법만으로 그대로 구현한다
(LC_ALL=C에서 실행하므로 length()와 문자 클래스는 바이트·ASCII 기준).
gawk 전용 기능과 정규식 반복 횟수 {n}은 쓰지 않는다. 반복이 필요한 정규식은
여기서 문자 클래스를 펼쳐 만든다.

parse(): 기본 제공 형식과 사용자 형식은 같은 FormatSpec에서 "FORMAT PARSE CONTRACT"를
토큰마다 펼친 코드로 생성한다 (커서 변수 + substr/index/match). 사용자 패턴의 글자는
_awk_str로 이스케이프한 문자열 리터럴로만 들어가고 정규식, 주석, 코드에는 들어가지 않는다.
결과는 P_M(메서드), P_U(경로 원문), P_S(상태 3자리 또는 ""), P_D(YYYYMMDD)이다.
P_S가 ""이면(상태 없는 형식) 상태군은 "???"이고 404가 아닌 것으로 센다.

입력: 셸 루프가 파일마다 "\\n\\001F<MARK>\\t<경로>" 표시 줄을 먼저 내고 파일 내용을
이어 붙인다. 읽을 수 없는 파일과 압축 해제 실패는 "\\n\\001E<MARK>\\t<코드>\\t<경로>"로 알린다.
파일이 하나도 없는 glob은 파일 목록보다 먼저 "\\n\\001E<MARK>\\tglob_empty\\t<glob>"으로 알린다.
glob_empty는 파일로도 오류로도 세지 않는다. 파일(읽지 못한 파일 포함)이 하나도 없으면
"E\\tno files matched"만 내고 glob_empty 줄은 내지 않는다 (v1과 같은 출력).
MARK는 스캔마다 다른 취소 토큰이고, 표시 줄은 줄 전체가 맨 앞부터 일치할 때만 인정한다
(로그 내용으로 표시 줄을 위조할 수 없다). 표시 앞의 "\\n"은 마지막 줄에 개행이 없는 파일과
표시가 붙지 않게 하려는 것이다. 그래서 표시 바로 앞의 빈 줄 하나는 세지 않는다.
출력: Global Constraints의 "원격 출력 프로토콜" 줄만 낸다. 입력 줄은 절대 그대로 내지 않는다.
변수: SINCE (YYYYMMDD 또는 0), MAXROWS, MAXKEYS, TOP404, MARK (awk -v로 받는다).
집계 키는 MAXKEYS개까지만 만든다. 넘치면 새 키는 버리고 T로 잘렸음을 알린다.
"""
from __future__ import annotations

from collections.abc import Sequence

from napi.logformat import FormatSpec, Token, resolve_format
from napi.models import CustomFormat, LogFormatDef

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
  MAXKEYS += 0
  TOP404 += 0
  if (MARK !~ /^napi-scan-[0-9a-f]+$/) { badmark = 1; exit }
  FMARK = "\001F" MARK "\t"
  EMARK = "\001E" MARK "\t"
  MLEN = length(FMARK)
  nfiles = 0
  nerr = 0
  nempty = 0
  nkeys = 0
  overflow = 0
  pend = 0
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

# 두 자리 숫자 월/일이 01-12 / 01-31 안인가 (문자열 비교, Python twin과 같다).
function okmd(m, d) {
  return m "" >= "01" && m "" <= "12" && d "" >= "01" && d "" <= "31"
}

# t는 dd/Mon/yyyy로 시작하는 문자열. 날짜를 P_D에 넣고 성공 여부를 돌려준다.
function clfdate(t,    m) {
  m = month(substr(t, 4, 3))
  if (m == "" || !okmd(m, substr(t, 1, 2))) return 0
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

function clean(p) {
  gsub(/[\001-\037\177]/, "?", p)
  return p
}

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

# s = "<코드>\t<경로>". 코드는 셸이 정한 것만 받는다.
# glob_empty(<경로> = 비어 있던 glob)는 파일 오류가 아니라서 nerr에 세지 않는다.
function fileerr(s,    i, code, msg) {
  i = index(s, "\t")
  if (i == 0) return
  code = substr(s, 1, i - 1)
  msg = "E\t" code "\t" clean(substr(s, i + 1))
  if (code == "glob_empty") {
    nempty++
    empties[nempty] = msg
    return
  }
  if (code != "unreadable" && code != "decompress_failed") return
  nerr++
  errs[nerr] = msg
}

# 표시 줄 앞에서 미뤄 둔 빈 줄을 처리한다. 첫 빈 줄은 셸이 넣은 "\n"이라 세지 않는다.
function atmarker() {
  if (pend > 0) pend--
  flushpend()
}

function flushpend() {
  for (; pend > 0; pend--) handle("")
}

function handle(l,    d, mp, k) {
  if (cur == 0) return
  flines[cur]++
  sub(/\r$/, "", l)
  if (!parse(l)) { fpf[cur]++; return }
  d = P_D
  if (fmin[cur] == "" || d < fmin[cur]) fmin[cur] = d
  if (d > fmax[cur]) fmax[cur] = d
  if (USE_SINCE && d < SINCE_S) { foor[cur]++; return }
  mp = P_M SUBSEP norm(P_U)
  k = mp SUBSEP (P_S == "" ? "???" : substr(P_S, 1, 1) "xx")
  if (!(k in cnt)) {
    if (nkeys >= MAXKEYS) { overflow++; return }
    nkeys++
    cnt[k] = 0
    kfirst[k] = d
    klast[k] = d
  }
  cnt[k]++
  if (d < kfirst[k]) kfirst[k] = d
  if (d > klast[k]) klast[k] = d
  if (P_S != "404") non404[mp] = 1
}

{
  if (substr($0, 1, MLEN) == FMARK) {
    atmarker()
    newfile(substr($0, MLEN + 1))
  } else if (substr($0, 1, MLEN) == EMARK) {
    atmarker()
    fileerr(substr($0, MLEN + 1))
    cur = 0
  } else if ($0 == "") {
    pend++
  } else {
    flushpend()
    handle($0)
  }
}

# 키 = 메서드 SUBSEP 경로 SUBSEP 상태군. 메서드는 [A-Z]+, 상태군은 3글자라서
# 경로에 SUBSEP 바이트가 있어도 앞뒤로 잘라 내면 정확히 되살아난다.
function keymp(k) { return substr(k, 1, length(k) - 4) }

function aggline(tag, k,    i, n, s) {
  i = index(k, SUBSEP)
  n = length(k)
  s = tag "\t" substr(k, 1, i - 1) "\t" substr(k, i + 1, n - i - 4)
  if (tag == "R") s = s "\t" substr(k, n - 2)
  return s "\t" cnt[k] "\t" kfirst[k] "\t" klast[k]
}

END {
  if (badmark) { print "E\tbad marker"; exit }
  flushpend()
  # 파일이 하나도 없으면(읽지 못한 파일도 없으면) 어느 glob이 비었는지는 내지 않는다
  if (nfiles == 0 && nerr == 0) print "E\tno files matched"
  else for (i = 1; i <= nempty; i++) print empties[i]
  for (i = 1; i <= nerr; i++) print errs[i]
  for (i = 1; i <= nfiles; i++) {
    lo = fmin[i]
    hi = fmax[i]
    if (lo == "") lo = "-"
    if (hi == "") hi = "-"
    print "F\t" clean(fpath[i]) "\t" lo "\t" hi "\t" flines[i] "\t" fpf[i] "\t" foor[i]
  }

  nr = 0
  for (k in cnt) if (keymp(k) in non404) nr++
  if (nr > MAXROWS) {
    fflush()
    cmd = "sort -t '\t' -k5,5nr 2>/dev/null | head -n " MAXROWS
    for (k in cnt) if (keymp(k) in non404) print aggline("R", k) | cmd
    close(cmd)
  } else {
    for (k in cnt) if (keymp(k) in non404) print aggline("R", k)
  }
  if (nr > MAXROWS || overflow > 0) print "T\t" MAXROWS

  # 404만 받은 키: 상위 TOP404개만 P로, 나머지는 Q 합계로
  n404 = 0
  tot404 = 0
  for (k in cnt) {
    if (keymp(k) in non404) continue
    n404++
    tot404 += cnt[k]
    hist[cnt[k]]++
  }
  keep = n404 < TOP404 ? n404 : TOP404
  if (keep < 0) keep = 0
  if (keep > 0) {
    fflush()
    cmd = "sort -t '\t' -k4,4nr 2>/dev/null | head -n " TOP404
    for (k in cnt) if (!(keymp(k) in non404)) print aggline("P", k) | cmd
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
  P_U = p
  P_S = f[@SF@]
  return 1
}
"""

_TIME_ISO = r"""  if (tm !~ /@ISODATE@/ || !okmd(substr(tm, 6, 2), substr(tm, 9, 2))) return 0
  P_D = substr(tm, 1, 4) substr(tm, 6, 2) substr(tm, 9, 2)"""

_TIME_CLF = r"""  if (!match(tm, /@CLFTIME@/)) return 0
  if (!clfdate(substr(tm, RSTART, 11))) return 0"""


def _field(n: object) -> str:
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"필드 번호는 1 이상의 정수여야 해요: {n!r}")
    return str(n)


# 패턴 리터럴에서 awk 문자열에 그대로 둘 바이트. 나머지는 \" \\ 또는 3자리 8진수(\ooo)로 쓴다.
# 그래서 생성한 소스는 ASCII뿐이고 개행, 제어 문자, 자리표시자 문자(@)가 들어가지 않는다.
_SAFE_BYTES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    b" !#$%&'()*+,-./:;<=>?[]^_`{|}~")


def _awk_str(text: str) -> str:
    """text(UTF-8 바이트)를 awk 문자열 리터럴로 바꾼다. 정규식에는 절대 쓰지 않는다."""
    out = []
    for b in text.encode("utf-8"):
        if b == 0x5C:
            out.append("\\\\")
        elif b == 0x22:
            out.append('\\"')
        elif b in _SAFE_BYTES:
            out.append(chr(b))
        else:
            out.append(f"\\{b:03o}")
    return '"' + "".join(out) + '"'


_DATE_VARS = {"Y": "ty", "m": "tm", "d": "td"}


def _gen_field_value(tok: Token, nxt: Token | None) -> list[str]:
    """field(t, tfmt 제외)의 값 v를 정하고 커서 p를 옮긴다 (CONTRACT의 경계 규칙)."""
    keep = tok.directive in ("r", "m", "U", "s")  # 나머지 field는 값을 버린다
    if nxt is None:
        code = ["  v = substr(line, p)"] if keep else []
        return code + ["  p = n + 1"]
    if nxt.kind == "ws":
        code = ["  i = match(substr(line, p), /[ \\t]/)",
                "  if (i == 0) i = n - p + 2",
                "  if (i == 1) return 0"]
    elif nxt.kind == "lit":
        code = [f"  i = index(substr(line, p), {_awk_str(nxt.text)})",
                "  if (i == 0) return 0"]
    else:
        raise ValueError("필드 두 개가 붙어 있는 형식은 awk로 만들 수 없어요")
    if keep:
        code.append("  v = substr(line, p, i - 1)")
    return code + ["  p += i - 1"]


def _gen_token(tok: Token, nxt: Token | None) -> list[str]:
    if tok.kind == "lit":
        size = len(tok.text.encode("utf-8"))
        return [f"  if (substr(line, p, {size}) != {_awk_str(tok.text)}) return 0",
                f"  p += {size}"]
    if tok.kind == "ws":
        return ["  if (!match(substr(line, p), /^[ \\t]+/)) return 0",
                "  p += RLENGTH"]
    d = tok.directive
    if d == "t":
        return ['  if (substr(line, p, 1) != "[") return 0',
                '  i = index(substr(line, p), "]")',
                "  if (i == 0) return 0",
                "  v = substr(line, p + 1, 11)",
                f"  if (v !~ /^{_CLF_TIME_RE}/ || !clfdate(v)) return 0",
                "  p += i"]
    if d == "tfmt":
        width = int(tok.time_width)
        code = [f"  if (n - p + 1 < {width}) return 0",
                f"  v = substr(line, p, {width})"]
        for key, off, size in tok.time_layout:
            code.append(f"  c = substr(v, {int(off) + 1}, {int(size)})")
            if key == "b":
                code += ["  c = month(c)", '  if (c == "") return 0', "  tm = c"]
            else:
                code += ["  if (c !~ /^[0-9]+$/) return 0", f"  {_DATE_VARS[key]} = c"]
        if tok.time_layout:
            code += ["  if (!okmd(tm, td)) return 0", "  P_D = ty tm td"]
        return code + [f"  p += {width}"]
    code = _gen_field_value(tok, nxt)
    if d == "r":
        code += ["  if (ntok(v, t) < 2 || t[1] !~ /^[A-Z]+$/) return 0",
                 "  P_M = t[1]",
                 "  P_U = t[2]"]
    elif d == "m":
        code += ["  if (v !~ /^[A-Z]+$/) return 0", "  P_M = v"]
    elif d == "U":  # 첫 토큰만 (꼬리 원문이 경로로 새지 않게)
        code += ["  if (ntok(v, t) < 1) return 0", "  P_U = t[1]"]
    elif d == "s":
        code += [f"  if (v !~ /{_STATUS_RE}/) return 0", "  P_S = v"]
    return code


def _gen_parse(spec: FormatSpec) -> str:
    """FORMAT PARSE CONTRACT를 토큰마다 펼친 parse(). 커서 p는 1부터, 바이트 단위다.
    last_essential 토큰까지만 만들고 그 뒤에서 바로 성공한다. 패턴 글자는 _awk_str로만
    들어간다 (주석에도 넣지 않는다)."""
    last = spec.last_essential
    if last < 0:
        raise ValueError("필수 토큰이 없는 형식이에요")
    out = ["function parse(line,    n, p, i, v, c, t, ty, tm, td) {",
           "  n = length(line)",
           "  p = 1",
           '  P_S = ""']
    for idx in range(last + 1):
        tok = spec.tokens[idx]
        nxt = spec.tokens[idx + 1] if idx + 1 < len(spec.tokens) else None
        out += _gen_token(tok, nxt)
    out += ["  return 1", "}"]
    return "\n".join(out)


def _fill(text: str) -> str:
    """고정 템플릿의 정규식 자리표시자를 채운다 (생성한 parse()를 넣기 전에 한다)."""
    return (text.replace("@UUID@", _UUID_RE)
            .replace("@CLFTIME@", _CLF_TIME_RE)
            .replace("@ISODATE@", _ISO_DATE_RE)
            .replace("@STATUS@", _STATUS_RE))


def awk_program(fmt: str, custom: CustomFormat | None,
                formats: Sequence[LogFormatDef] = ()) -> str:
    """형식에 맞는 awk 소스를 돌려준다. 모르는 형식이면 ValueError(FormatError).

    기본 제공 형식과 사용자 형식(formats)은 compile_format의 FormatSpec에서 parse()를
    생성하고, "custom"은 v1 필드 번호 파서를 쓴다."""
    if fmt == "custom":
        if custom is None:
            raise ValueError("custom 형식에는 사용자 정의 형식이 필요해요")
        if custom.time_format == "iso8601":
            time_part = _TIME_ISO
        elif custom.time_format == "clf":
            time_part = _TIME_CLF
        else:
            raise ValueError(f"모르는 시각 형식: {custom.time_format!r}")
        parse = _fill(_PARSE_CUSTOM.replace("@TIME@", time_part)
                      .replace("@MF@", _field(custom.method_field))
                      .replace("@PF@", _field(custom.path_field))
                      .replace("@SF@", _field(custom.status_field))
                      .replace("@TF@", _field(custom.time_field)))
    else:
        spec = resolve_format(fmt, formats)
        if not isinstance(spec, FormatSpec):
            raise ValueError(f"모르는 로그 형식: {fmt!r}")
        parse = _gen_parse(spec)
    # parse()는 마지막에 한 번만 끼운다. 끼운 글자는 다시 치환되지 않는다.
    return _fill(_COMMON).replace("@PARSE@", parse.strip("\n")).strip("\n") + "\n"

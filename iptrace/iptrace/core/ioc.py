"""침해의심 IP 입력 처리.

담당자가 입력칸에 직접 적은 텍스트(또는 txt/csv 파일)를 IP 목록으로 바꾼다.
- 한 줄에 하나가 기본, 쉼표·공백·세미콜론으로 여러 개를 적어도 된다.
- 빈 줄과 '#'으로 시작하는 메모 줄은 무시한다.
- 형식이 틀린 토큰이 있는 줄은 오류로 보고하고 점검에서 뺀다.
"""
import ipaddress
import re

from .models import IocEntry, IocParseResult

# 사설·특수 대역. ipaddress의 is_private는 문서용 대역까지 사설로 판정하므로 쓰지 않는다.
SPECIAL_NETWORKS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "169.254.0.0/16", "100.64.0.0/10", "0.0.0.0/8",
    "::1/128", "fc00::/7", "fe80::/10",
)]

_DEFANG = [("[.]", "."), ("(.)", "."), ("{.}", "."), ("[dot]", "."), ("(dot)", "."),
           ("[:]", ":"), ("hxxps", "https"), ("hxxp", "http")]
_SPLIT = re.compile(r"[,\s;|]+")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)
# IP처럼 보이는 토큰(숫자·점·콜론·슬래시·하이픈으로만 구성)
_IP_LIKE = re.compile(r"^[0-9a-fA-F:.\-/]+$")
_V4_PORT = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):\d+$")
_V6_BRACKET_PORT = re.compile(r"^\[([0-9a-fA-F:]+)\](?::\d+)?$")


def defang(text: str) -> str:
    for a, b in _DEFANG:
        text = text.replace(a, b).replace(a.upper(), b)
    return text


def _clean_token(tok: str) -> str:
    tok = tok.strip().strip("\"'")
    tok = _SCHEME.sub("", tok)
    m = _V6_BRACKET_PORT.match(tok)
    if m:
        return m.group(1)
    # URL 경로 제거 (CIDR의 /숫자는 남긴다)
    if "/" in tok:
        head, _, tail = tok.partition("/")
        if not tail.isdigit():
            tok = head
    m = _V4_PORT.match(tok)
    if m:
        tok = m.group(1)
    return tok


def parse_token(tok: str) -> list:
    """토큰 하나를 ip_network 목록으로 바꾼다. 실패하면 ValueError."""
    if "-" in tok and "/" not in tok:
        a, _, b = tok.partition("-")
        start, end = ipaddress.ip_address(a.strip()), ipaddress.ip_address(b.strip())
        if start.version != end.version or start > end:
            raise ValueError("잘못된 범위")
        return list(ipaddress.summarize_address_range(start, end))
    return [ipaddress.ip_network(tok, strict=False)]


def _excluded_reason(net, own_networks) -> str:
    for special in SPECIAL_NETWORKS:
        if net.version == special.version and net.subnet_of(special):
            return f"사설·특수 대역({special})"
    for own in own_networks:
        if net.version == own.version and (net.subnet_of(own) or net.overlaps(own)):
            return f"자사 대역({own})"
    return ""


def parse_text(text: str, own_networks=(), include_private: bool = False) -> IocParseResult:
    own = []
    for n in own_networks:
        try:
            own.append(ipaddress.ip_network(n, strict=False))
        except ValueError:
            pass
    res = IocParseResult()
    seen = {}
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        content = defang(stripped.split("#", 1)[0])  # 줄 끝 메모(# ...)는 무시
        for tok in _SPLIT.split(content):
            if not tok:
                continue
            cleaned = _clean_token(tok)
            if not cleaned:
                continue
            if not _IP_LIKE.match(cleaned) or not any(c.isdigit() for c in cleaned):
                # 'IP', '메모' 같은 일반 글자는 오타가 아니면 무시하지만, 숫자가 섞인 글자는 오류로 본다
                if any(c.isdigit() for c in cleaned):
                    res.errors.append((line_no, line, tok))
                continue
            try:
                nets = parse_token(cleaned)
            except ValueError:
                res.errors.append((line_no, line, tok))
                continue
            for net in nets:
                reason = "" if include_private else _excluded_reason(net, own)
                if reason:
                    res.excluded.append((line_no, str(net) if net.num_addresses > 1 else str(net.network_address), reason))
                    continue
                if net.version == 4 and net.prefixlen < 8:
                    res.warnings.append(f"{line_no}번째 줄: {net}는 매우 넓은 대역입니다")
                elif (net.version == 4 and net.prefixlen < 16) or (net.version == 6 and net.prefixlen < 32):
                    # 서버에서 앞부분 문자열(예: '1.')로 거르므로 관련 없는 줄도 많이 걸린다
                    res.warnings.append(f"{line_no}번째 줄: {net}는 넓은 대역이라 서버에서 받는 줄이 많고, "
                                        "파일마다 최대 줄 수까지만 확인합니다")
                entry = IocEntry(network=net, original=tok, line_no=line_no)
                if entry.key in seen:
                    seen[entry.key].count += 1
                    res.duplicates += 1
                else:
                    seen[entry.key] = entry
                    res.entries.append(entry)
    return res


def summary_text(res: IocParseResult) -> str:
    return (f"{len(res.entries)}개 IP · 중복 {res.duplicates} · "
            f"제외 {len(res.excluded)} · 오류 {len(res.error_lines)}")


DRM_MARKERS = (b"<DOCUMENT SAFER", b"<DSCRYPT", b"DRMONE", b"FASOO", b"<##SoftCamp")


class EncryptedFileError(ValueError):
    pass


def read_file(path) -> str:
    """txt/csv 파일을 텍스트로 읽는다(utf-8, 실패하면 cp949).

    문서 보안(DRM)으로 암호화된 파일은 내용을 읽을 수 없으므로 알려 준다.
    """
    raw = open(path, "rb").read()
    head = raw[:64]
    if any(m in head for m in DRM_MARKERS) or (b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff"))):
        raise EncryptedFileError("문서 보안(DRM)으로 암호화된 파일이라 읽을 수 없습니다. "
                                 "메모장 등에서 파일을 열어 내용을 복사해 입력칸에 붙여넣으세요.")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def normalized_list(res: IocParseResult) -> str:
    return "\n".join(e.key for e in res.entries)

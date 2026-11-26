"""IP·대역 매칭.

단일 IP는 set으로, 대역은 prefix 길이별로 묶어 `ip & mask`로 찾는다.
같은 IP가 로그에 반복해서 나오므로 결과를 캐시한다.
"""
import ipaddress
from functools import lru_cache


class Matcher:
    def __init__(self, iocs, trusted_proxies=()):
        self.single = {}          # (version, int) -> [key]
        self.ranges = {}          # (version, prefixlen) -> {network_int: [key]}
        for e in iocs:
            n = e.network
            if n.num_addresses == 1:
                self.single.setdefault((n.version, int(n.network_address)), []).append(e.key)
            else:
                self.ranges.setdefault((n.version, n.prefixlen), {}).setdefault(
                    int(n.network_address), []).append(e.key)
        self.trusted = set()
        for t in trusted_proxies:
            try:
                self.trusted.add(ipaddress.ip_address(t))
            except ValueError:
                pass
        self.match_ip = lru_cache(maxsize=200_000)(self._match_ip)
        self.filter_strings = self.build_filter_strings(iocs)
        self.prefilter = None
        if self.filter_strings:
            import re
            self.prefilter = re.compile(b"|".join(re.escape(k.encode()) for k in self.filter_strings), re.I)

    @staticmethod
    def build_filter_strings(iocs):
        """서버 grep(-F)과 로컬 사전 필터에 쓰는 고정 문자열 목록.

        단일 IP는 그대로, 대역은 앞부분(예: 203.0.113.0/28 -> '203.0.113.')을 쓴다.
        필터는 넓게 잡고, 걸린 줄은 정확한 매칭을 다시 한다.
        /8보다 넓은 IPv4 대역이나 /16보다 넓은 IPv6 대역은 문자열로 거를 수 없어 None.
        """
        keys = set()
        for e in iocs:
            n = e.network
            if n.num_addresses == 1:
                keys.add(n.network_address.compressed.lower())
                continue
            if n.version == 4:
                octets = n.prefixlen // 8
                if octets == 0:
                    return None
                keys.add(".".join(str(n.network_address).split(".")[:octets]) + ".")
            else:
                groups = n.prefixlen // 16
                if groups == 0:
                    return None
                head = n.network_address.exploded.split(":")[:groups]
                keys.add(":".join(g.lstrip("0") or "0" for g in head) + ":")
        return sorted(keys, key=len, reverse=True) or None

    def _match_ip(self, ip_str: str) -> tuple:
        try:
            ip = ipaddress.ip_address(ip_str.strip().strip("[]"))
        except ValueError:
            return ()
        if ip in self.trusted:
            return ()
        v, n = ip.version, int(ip)
        keys = list(self.single.get((v, n), ()))
        bits = 32 if v == 4 else 128
        for (rv, plen), nets in self.ranges.items():
            if rv != v:
                continue
            mask = ((1 << plen) - 1) << (bits - plen)
            keys.extend(nets.get(n & mask, ()))
        return tuple(keys)

    def match_record(self, rec) -> list:
        """레코드의 remote_ip와 XFF 체인에서 매칭되는 (ioc_key, ip, field) 목록."""
        hits, seen = [], set()
        for key in self.match_ip(rec.remote_ip):
            hits.append((key, rec.remote_ip, "remote"))
            seen.add(key)
        for ip in rec.xff:
            for key in self.match_ip(ip):
                if key not in seen:
                    hits.append((key, ip, "xff"))
                    seen.add(key)
        return hits

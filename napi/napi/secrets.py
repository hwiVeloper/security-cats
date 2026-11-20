"""세션 동안만 메모리에 두는 비밀번호·키파일 암호. 디스크에 쓰지 않는다."""
from __future__ import annotations

from napi.models import ServerConfig


class SessionSecrets:
    def __init__(self) -> None:
        self._passwords: dict[tuple[str, str, int], str] = {}
        self._passphrases: dict[str, str] = {}

    def set_password(self, user: str, host: str, port: int, pw: str) -> None:
        self._passwords[(user, host, int(port))] = pw

    def get_password(self, user: str, host: str, port: int) -> str | None:
        return self._passwords.get((user, host, int(port)))

    def set_passphrase(self, key_file: str, pp: str) -> None:
        self._passphrases[key_file] = pp

    def get_passphrase(self, key_file: str) -> str | None:
        return self._passphrases.get(key_file)

    def has_password(self, user: str, host: str, port: int) -> bool:
        return (user, host, int(port)) in self._passwords

    def has_passphrase(self, key_file: str) -> bool:
        return key_file in self._passphrases

    def clear_password(self, user: str, host: str, port: int) -> None:
        self._passwords.pop((user, host, int(port)), None)

    def clear_passphrase(self, key_file: str) -> None:
        self._passphrases.pop(key_file, None)

    def missing_for(self, servers: list[ServerConfig]) -> list[ServerConfig]:
        """비밀번호가 필요한데 아직 없는 서버. 키파일 서버는 암호 없이 먼저 시도하므로 빼고 돌려준다."""
        return [s for s in servers
                if s.auth == "password" and self.get_password(s.user, s.host, s.port) is None]

    def clear(self) -> None:
        self._passwords.clear()
        self._passphrases.clear()

    def __repr__(self) -> str:
        return (f"SessionSecrets(passwords={len(self._passwords)}, "
                f"passphrases={len(self._passphrases)})")

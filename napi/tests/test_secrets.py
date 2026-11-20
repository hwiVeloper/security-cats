from napi.models import ServerConfig
from napi.secrets import SessionSecrets


def make_server(name, host, auth="password", key_file=None):
    return ServerConfig(name=name, host=host, user="app", log_globs=["/log/access.log*"],
                        auth=auth, key_file=key_file)


def test_set_get_password_and_passphrase():
    s = SessionSecrets()
    s.set_password("app", "10.0.0.1", 22, "pw-1")
    s.set_passphrase("C:/keys/id_rsa", "pp-1")
    assert s.get_password("app", "10.0.0.1", 22) == "pw-1"
    assert s.get_password("app", "10.0.0.1", 2222) is None
    assert s.get_password("root", "10.0.0.1", 22) is None
    assert s.get_passphrase("C:/keys/id_rsa") == "pp-1"
    assert s.get_passphrase("C:/keys/other") is None


def test_missing_for_returns_password_servers_without_secret():
    s = SessionSecrets()
    a = make_server("a", "10.0.0.1")
    b = make_server("b", "10.0.0.2")
    k = make_server("k", "10.0.0.3", auth="key", key_file="C:/keys/id_rsa")
    s.set_password("app", "10.0.0.1", 22, "pw-1")
    assert s.missing_for([a, b, k]) == [b]


def test_repr_does_not_expose_values():
    s = SessionSecrets()
    s.set_password("app", "10.0.0.1", 22, "Sup3rSecretPw")
    s.set_passphrase("C:/keys/id_rsa", "MyKeyPassphrase")
    text = repr(s) + str(s)
    assert "Sup3rSecretPw" not in text
    assert "MyKeyPassphrase" not in text


def test_clear_forgets_everything():
    s = SessionSecrets()
    s.set_password("app", "10.0.0.1", 22, "pw-1")
    s.set_passphrase("C:/keys/id_rsa", "pp-1")
    s.clear()
    assert s.get_password("app", "10.0.0.1", 22) is None
    assert s.get_passphrase("C:/keys/id_rsa") is None

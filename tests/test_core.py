import pytest

from muse.core.api import (DEFAULT_TTL, CaptchaRequired, TempMailClient, TempMailError, WrongPin,
                           is_valid_address)
from muse.core.batch import STATUS_CAPTCHA, STATUS_DONE, STATUS_STOPPED, BatchCreator
from muse.core.secrets import SecretStore
from muse.core.storage import Storage


class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


class FakeServer:
    """Mô phỏng các endpoint /api/box, /api/box/hidden, /api/mails của TempMail.Plus."""

    def __init__(self, captcha_after=None):
        self.headers = {}
        self.boxes = {}  # email -> {"ttl":, "pin":}
        self.posts = 0
        self.captcha_after = captcha_after

    def request(self, method, url, params=None, data=None, timeout=None):
        q = params or data or {}
        path = url.split("tempmail.plus", 1)[1]
        email = q.get("email")
        box = self.boxes.get(email)
        if box and box["pin"] and q.get("epin") != box["pin"]:
            return FakeResp({"result": False, "err": {"code": 1021, "msg": "pin"}})
        if path == "/api/box" and method == "POST":
            self.posts += 1
            if self.captcha_after is not None and self.posts > self.captcha_after:
                return FakeResp({"result": False, "with_captcha": True})
            self.boxes[email] = {"ttl": int(q["ttl_minutes"]), "pin": q.get("pin", "")}
            return FakeResp({"result": True})
        if path == "/api/box":
            if not box:
                return FakeResp({"result": False, "err": {"code": 1001, "msg": "no box"}})
            return FakeResp({"result": True, "ttl_minutes": box["ttl"]})
        if path == "/api/box/hidden":
            return FakeResp({"result": True, "email": "hid" + email.split("@")[0] + "@mailto.plus"})
        if path == "/api/mails":
            return FakeResp({"result": True, "count": 1, "mail_list": [
                {"mail_id": 7, "from_mail": "a@b.c", "from_name": "A", "subject": "Hi",
                 "time": "2026-11-23 10:00", "is_new": True}]})
        if path == "/api/mails/7":
            return FakeResp({"result": True, "from_mail": "a@b.c", "subject": "Hi",
                             "text": "hello", "html": "", "date": "2026-11-23"})
        return FakeResp({}, 404)


@pytest.fixture
def env(tmp_path):
    server = FakeServer()
    client = TempMailClient(session=server)
    storage = Storage(tmp_path / "t.db")
    yield server, client, storage, SecretStore(backend="memory")
    storage.close()


def test_validate_address():
    assert is_valid_address("abc.def@mailto.plus")
    assert not is_valid_address("abc@")
    assert not is_valid_address("a b@mailto.plus")


def test_apply_settings_and_read_mail(env):
    server, client, _, _ = env
    assert client.apply_settings("x1@mailto.plus", DEFAULT_TTL) is True
    assert client.get_box("x1@mailto.plus").ttl_minutes == 10080
    assert client.get_secret_address("x1@mailto.plus") == "hidx1@mailto.plus"
    mails = client.list_mails("x1@mailto.plus")
    assert mails[0].subject == "Hi" and mails[0].is_new
    assert client.get_mail("x1@mailto.plus", 7).text == "hello"


def test_pin_required(env):
    _, client, _, _ = env
    client.apply_settings("p@mailto.plus", 60, pin="1234")
    with pytest.raises(WrongPin):
        client.get_box("p@mailto.plus")
    assert client.get_box("p@mailto.plus", epin="1234").ttl_minutes == 60


def test_invalid_ttl(env):
    with pytest.raises(ValueError):
        env[1].set_box("a@mailto.plus", 99)


def test_batch_full(env):
    _, client, storage, secrets = env
    res = BatchCreator(client, storage, secrets, count=5, ttl_minutes=DEFAULT_TTL,
                       delay=0).run()
    assert (res.status, res.created) == (STATUS_DONE, 5)
    boxes = storage.list_mailboxes()
    assert len(boxes) == 5 and all(b.applied and b.ttl_minutes == 10080 for b in boxes)
    assert all(secrets.secret(b.address) for b in boxes)
    assert storage.get_batch(res.batch_id).created == 5


def test_batch_stop_keeps_created(env):
    _, client, storage, secrets = env
    creator = BatchCreator(client, storage, secrets, count=20, ttl_minutes=10080, delay=0)
    seen = []

    def progress(c, n, mb):
        seen.append(c)
        if c == 12:
            creator.stop()

    res = creator.run(on_progress=progress)
    assert res.status == STATUS_STOPPED and res.created == 12
    assert len(storage.list_mailboxes()) == 12
    assert seen[-1] == 12


def test_batch_captcha_stops(tmp_path):
    server = FakeServer(captcha_after=3)
    storage = Storage(tmp_path / "c.db")
    res = BatchCreator(TempMailClient(session=server), storage, SecretStore(backend="memory"),
                       count=10, ttl_minutes=10080, delay=0).run()
    assert res.status == STATUS_CAPTCHA and res.created == 3
    assert len(storage.list_mailboxes()) == 3
    storage.close()


def test_batch_with_pin_stores_secret_not_db(env):
    _, client, storage, secrets = env
    BatchCreator(client, storage, secrets, count=2, ttl_minutes=60, pin="9999", delay=0).run()
    for b in storage.list_mailboxes():
        assert b.has_pin and secrets.pin(b.address) == "9999"
    raw = open(storage.path, "rb").read()
    assert b"9999" not in raw


def test_captcha_error_from_client(env):
    server, client, _, _ = env
    server.captcha_after = 0
    with pytest.raises(CaptchaRequired):
        client.set_box("z@mailto.plus", 60)


def test_http_error(env):
    _, client, _, _ = env
    with pytest.raises(TempMailError):
        client._call("GET", "/nope", {})


def test_interrupted_batches(env):
    _, _, storage, _ = env
    b = storage.start_batch(5, 60, False)
    storage.mark_interrupted_batches()
    assert storage.get_batch(b.id).status == "stopped"


class CaptchaServer(FakeServer):
    """Mỗi lần lưu cài đặt đều đòi captcha, trừ khi có mã xác minh hợp lệ (dùng 1 lần)."""

    def __init__(self):
        super().__init__()
        self.valid_tokens = set()

    def request(self, method, url, params=None, data=None, timeout=None):
        q = params or data or {}
        if url.endswith("/api/box") and method == "POST":
            tok = q.get("recaptcha")
            if tok not in self.valid_tokens:
                return FakeResp({"result": False, "with_captcha": True})
            self.valid_tokens.discard(tok)
        return super().request(method, url, params, data, timeout)


def test_batch_continues_after_user_solves_captcha(tmp_path):
    server = CaptchaServer()
    storage = Storage(tmp_path / "s.db")
    asked = []

    def solver():
        tok = f"tok{len(asked)}"
        asked.append(tok)
        server.valid_tokens.add(tok)
        return tok

    creator = BatchCreator(TempMailClient(session=server), storage, SecretStore(backend="memory"),
                           count=4, ttl_minutes=10080, delay=0, solve_captcha=solver)
    res = creator.run()
    assert (res.status, res.created) == (STATUS_DONE, 4)
    assert len(asked) == 4 and creator.captchas_solved == 4
    assert all(b.applied for b in storage.list_mailboxes())
    storage.close()


def test_batch_stops_when_user_skips_captcha(tmp_path):
    server = CaptchaServer()
    storage = Storage(tmp_path / "k.db")
    calls = {"n": 0}

    def solver():
        calls["n"] += 1
        if calls["n"] > 2:
            return None  # người dùng bấm Bỏ qua
        server.valid_tokens.add("ok%d" % calls["n"])
        return "ok%d" % calls["n"]

    res = BatchCreator(TempMailClient(session=server), storage, SecretStore(backend="memory"),
                       count=10, ttl_minutes=60, delay=0, solve_captcha=solver).run()
    assert res.status == STATUS_CAPTCHA and res.created == 2
    assert len(storage.list_mailboxes()) == 2
    storage.close()

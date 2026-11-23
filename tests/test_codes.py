from muse.core.api import TempMailClient
from muse.core.codes import extract
from muse.core.secrets import SecretStore
from muse.core.storage import Storage
from muse.core.watcher import MailWatcher, resolve_account

from test_core import FakeResp


def test_meta_code_vi():
    html = ("<p>Mã xác nhận</p><div style='font-size:24px'>089574</div>"
            "<p>Nếu bạn không yêu cầu mã xác nhận, hãy bỏ qua thông báo này.</p>"
            "<p>© Meta. 1 Meta Way, Menlo Park, CA 94025</p>")
    v = extract("Xác nhận email cho Tài khoản Meta mới", "", html)
    assert v.is_verification and v.code == "089574"


def test_code_in_subject_en():
    assert extract("123456 is your Instagram code", "").code == "123456"
    assert extract("Your verification code", "Use code: 4821 to sign in").code == "4821"
    assert extract("Mã của bạn", "Mã xác minh của bạn là G-889912").code == "G-889912"


def test_not_verification():
    v = extract("Bản tin tháng 11", "Khuyến mãi 2026, giảm 50%")
    assert not v.is_verification and v.code is None


def test_ignores_year():
    assert extract("Confirm your email", "© 2026 Example. Code 7731").code == "7731"


def test_resolve_account_prefers_secret():
    assert resolve_account("main@mailto.plus", "sec@mailto.plus", "sec@mailto.plus") == \
        ("sec@mailto.plus", True)
    assert resolve_account("main@mailto.plus", "sec@mailto.plus", "Main <main@mailto.plus>") == \
        ("main@mailto.plus", False)
    # trường "Đến" trống: thư nhắc tới địa chỉ bí mật
    assert resolve_account("main@mailto.plus", "sec@mailto.plus", "",
                           "Thư này đã được gửi đến sec@mailto.plus") == ("sec@mailto.plus", True)
    assert resolve_account("main@mailto.plus", "", "", "") == ("main@mailto.plus", False)


class InboxServer:
    def __init__(self):
        self.headers = {}
        self.mails = {}  # address -> list of dict

    def request(self, method, url, params=None, data=None, timeout=None):
        q = params or data or {}
        path = url.split("tempmail.plus", 1)[1]
        box = self.mails.get(q.get("email"), [])
        if path == "/api/mails":
            return FakeResp({"result": True, "mail_list": [
                {"mail_id": m["id"], "from_mail": m["from"], "subject": m["subject"],
                 "time": "2026-11-23 13:21:55"} for m in box]})
        mid = int(path.rsplit("/", 1)[1])
        m = next(m for m in box if m["id"] == mid)
        return FakeResp({"result": True, "from_mail": m["from"], "from_name": "Meta",
                         "subject": m["subject"], "to": m["to"], "text": "", "html": m["html"]})


def test_watcher_notifies_new_codes_with_account(tmp_path):
    srv = InboxServer()
    st = Storage(tmp_path / "w.db")
    sec = SecretStore(backend="memory")
    st.add_mailbox("ku29@merepost.com", ttl_minutes=10080, applied=True)
    sec.set("ku29@merepost.com", "secret", "vkhidden@merepost.com")
    srv.mails["ku29@merepost.com"] = [
        {"id": 1, "from": "a@x.com", "subject": "Old code 111111", "to": "ku29@merepost.com", "html": ""}]
    w = MailWatcher(TempMailClient(session=srv), st, sec, pause=0)
    assert not w.scan_once()  # lần đầu: chỉ ghi nhận thư cũ
    srv.mails["ku29@merepost.com"].append(
        {"id": 2, "from": "security@account.meta.com", "subject": "Xác nhận email cho Tài khoản Meta mới",
         "to": "vkhidden@merepost.com", "html": "<p>Mã xác nhận</p><p>089574</p>"})
    srv.mails["ku29@merepost.com"].append(
        {"id": 3, "from": "news@shop.com", "subject": "Khuyến mãi", "to": "ku29@merepost.com",
         "html": "giảm giá"})
    ev = w.scan_once().codes
    assert len(ev) == 1
    e = ev[0]
    assert (e.code, e.account, e.via_secret, e.mailbox) == \
        ("089574", "vkhidden@merepost.com", True, "ku29@merepost.com")
    assert not w.scan_once()  # không báo lại
    assert st.recent_codes()[0]["code"] == "089574"
    st.close()


def test_no_false_match_inside_words():
    v = extract("Confirm your email", "Visit us at 12345 Main St. Your code: 5521")
    assert v.code == "5521"
    assert extract("Xác nhận email", "Gửi tới email 90210 của bạn. Mã xác nhận 667 788")\
        .code == "667788"

from muse.core.api import TempMailClient
from muse.core.secrets import SecretStore
from muse.core.services import KIND_LOGIN, KIND_VERIFY, KIND_WELCOME, classify, identify_service
from muse.core.storage import Storage
from muse.core.watcher import MailWatcher

from test_codes import InboxServer


def test_identify_service():
    assert identify_service("security@account.meta.com", "Meta") == "Meta"
    assert identify_service("Facebook <security@facebookmail.com>") == "Facebook"
    assert identify_service("no-reply@accounts.google.com") == "Google"
    assert identify_service("hello@notion.so", "Notion Team") == "Notion Team"
    assert identify_service("noreply@mail.someshop.vn", "noreply") == "Someshop"


def test_classify():
    assert classify("Xác nhận email cho Tài khoản Meta mới", "", "<p>Mã xác nhận</p><p>089574</p>") \
        == KIND_VERIFY
    assert classify("Chào mừng bạn đến với Shopee") == KIND_WELCOME
    assert classify("Welcome to Notion!") == KIND_WELCOME
    assert classify("Đăng nhập mới vào Facebook từ Windows") == KIND_LOGIN
    assert classify("Hoá đơn tháng 11") is None


def _setup(tmp_path):
    srv = InboxServer()
    st = Storage(tmp_path / "r.db")
    sec = SecretStore(backend="memory")
    return srv, st, sec, MailWatcher(TempMailClient(session=srv), st, sec, pause=0)


def test_new_verification_mail_registers_mailbox(tmp_path):
    srv, st, sec, w = _setup(tmp_path)
    st.add_mailbox("a1@merepost.com", ttl_minutes=10080, applied=True)
    st.add_mailbox("b2@merepost.com", ttl_minutes=10080, applied=True)
    srv.mails = {"a1@merepost.com": [], "b2@merepost.com": []}
    assert not w.scan_once()
    assert st.registered_map() == {}
    srv.mails["a1@merepost.com"].append(
        {"id": 5, "from": "security@account.meta.com", "subject": "Xác nhận email cho Tài khoản Meta mới",
         "to": "a1@merepost.com", "html": "<p>Mã xác nhận</p><p>089574</p>"})
    res = w.scan_once()
    assert res.codes[0].service == "Meta" and res.codes[0].newly_registered
    reg = st.registered_map()
    assert list(reg) == ["a1@merepost.com"] and reg["a1@merepost.com"][0]["service"] == "Meta"
    # hộp b2 vẫn chưa dùng
    assert "b2@merepost.com" not in reg


def test_multiple_services_and_welcome_without_code(tmp_path):
    srv, st, sec, w = _setup(tmp_path)
    st.add_mailbox("a1@merepost.com", ttl_minutes=10080, applied=True)
    srv.mails = {"a1@merepost.com": []}
    w.scan_once()
    srv.mails["a1@merepost.com"] += [
        {"id": 1, "from": "security@account.meta.com", "subject": "Your Meta code 123456",
         "to": "a1@merepost.com", "html": ""},
        {"id": 2, "from": "hi@shopee.vn", "subject": "Chào mừng bạn đến với Shopee",
         "to": "a1@merepost.com", "html": "Cảm ơn bạn đã đăng ký"},
    ]
    res = w.scan_once()
    assert [r.service for r in res.registrations] == ["Shopee"]  # thư chào mừng không có mã
    assert sorted(r["service"] for r in st.registrations("a1@merepost.com")) == ["Meta", "Shopee"]


def test_backfill_existing_mails_without_notifying(tmp_path):
    srv, st, sec, w = _setup(tmp_path)
    st.add_mailbox("old@merepost.com", ttl_minutes=10080, applied=True)
    st.mark_scanned("old@merepost.com", [1, 2])  # đã quét từ bản 1.3.x
    srv.mails = {"old@merepost.com": [
        {"id": 1, "from": "security@account.meta.com", "subject": "Xác nhận email cho Tài khoản Meta mới",
         "to": "old@merepost.com", "html": ""},
        {"id": 2, "from": "security@account.meta.com", "subject": "Xác nhận email cho Tài khoản Meta mới",
         "to": "old@merepost.com", "html": ""}]}
    res = w.scan_once()
    assert not res  # không báo
    assert [r["service"] for r in st.registrations("old@merepost.com")] == ["Meta"]
    assert st.get_setting("reg_backfill_done") == "1"


def test_manual_mark_and_unmark(tmp_path):
    st = Storage(tmp_path / "m.db")
    st.add_mailbox("x@mailto.plus")
    assert st.add_registration(mailbox="x@mailto.plus", service="TikTok", kind="manual", source="manual")
    assert not st.add_registration(mailbox="x@mailto.plus", service="tiktok")  # trùng
    assert st.services() == ["TikTok"]
    st.clear_registrations("x@mailto.plus")
    assert st.registered_map() == {}

from muse.core.api import TempMailClient
from muse.core.cleanup import remove_mailboxes
from muse.core.secrets import SecretStore
from muse.core.storage import Storage

from test_core import FakeResp


class DeleteServer:
    def __init__(self, mails):
        self.headers = {}
        self.mails = mails  # address -> [ids]
        self.deleted = []

    def request(self, method, url, params=None, data=None, timeout=None):
        q = params or data or {}
        ids = self.mails.get(q["email"], [])
        if method == "GET":
            return FakeResp({"result": True, "first_id": max(ids) if ids else 0,
                             "mail_list": [{"mail_id": i} for i in ids]})
        assert int(q["first_id"]) == max(ids)
        self.deleted.append(q["email"])
        self.mails[q["email"]] = []
        return FakeResp({"result": True})


def test_remove_many_local_only(tmp_path):
    st = Storage(tmp_path / "d.db")
    sec = SecretStore(backend="memory")
    for a in ("a@mailto.plus", "b@mailto.plus", "c@mailto.plus"):
        st.add_mailbox(a)
        sec.set(a, "secret", "s" + a)
    st.add_registration(mailbox="a@mailto.plus", service="Meta")
    res = remove_mailboxes(st, sec, ["a@mailto.plus", "b@mailto.plus", "nope@mailto.plus"])
    assert res.removed == ["a@mailto.plus", "b@mailto.plus"]
    assert [m.address for m in st.list_mailboxes()] == ["c@mailto.plus"]
    assert sec.secret("a@mailto.plus") == "" and sec.secret("c@mailto.plus")
    assert st.registered_map() == {}


def test_remove_and_clear_remote(tmp_path):
    st = Storage(tmp_path / "r.db")
    sec = SecretStore(backend="memory")
    st.add_mailbox("a@mailto.plus")
    st.add_mailbox("empty@mailto.plus")
    srv = DeleteServer({"a@mailto.plus": [3, 7, 5], "empty@mailto.plus": []})
    res = remove_mailboxes(st, sec, ["a@mailto.plus", "empty@mailto.plus"],
                           client=TempMailClient(session=srv))
    assert res.cleared_mails == 3 and srv.deleted == ["a@mailto.plus"]
    assert st.list_mailboxes() == []

"""Xoá hộp thư không muốn dùng nữa."""

from __future__ import annotations

from dataclasses import dataclass, field

from .api import TempMailClient, TempMailError
from .secrets import SecretStore
from .storage import Storage


@dataclass
class RemoveResult:
    removed: list[str] = field(default_factory=list)
    cleared_mails: int = 0
    remote_errors: dict[str, str] = field(default_factory=dict)


def remove_mailboxes(storage: Storage, secrets: SecretStore, addresses: list[str],
                     client: TempMailClient | None = None) -> RemoveResult:
    """Xoá các hộp khỏi danh sách trên máy (kèm PIN, địa chỉ bí mật, dấu đăng ký).

    Có ``client`` thì xoá luôn toàn bộ thư của hộp trên TempMail.Plus trước (cần PIN
    nên phải làm trước khi quên PIN). Lỗi phía trang không chặn việc xoá trên máy.
    """
    res = RemoveResult()
    for address in addresses:
        mb = storage.find(address)
        if not mb:
            continue
        if client is not None:
            try:
                res.cleared_mails += client.clear_inbox(mb.address, epin=secrets.pin(mb.address))
            except TempMailError as exc:
                res.remote_errors[mb.address] = str(exc)
        secrets.forget(mb.address)
        storage.forget_mailbox_mails(mb.address)
        storage.delete_mailbox(mb.id)
        res.removed.append(mb.address)
    return res

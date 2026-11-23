"""Xuất danh sách hộp thư ra file."""

from __future__ import annotations

LIST_URL = "https://tempmail.plus/"


def format_list(addresses: list[str]) -> str:
    """Mỗi dòng (cách nhau bằng TAB), đúng mẫu người dùng đưa:

    1<TAB><TAB>Available<TAB>-<TAB>abc@fexbox.org | https://tempmail.plus/<TAB>Xóa
    """
    lines = [f"{i}\t\tAvailable\t-\t{addr} | {LIST_URL}\tXóa"
             for i, addr in enumerate(addresses, 1)]
    return "\r\n".join(lines) + ("\r\n" if lines else "")

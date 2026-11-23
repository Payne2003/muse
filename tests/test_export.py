from muse.core.export import format_list


def test_format_matches_user_sample():
    out = format_list(["tl26fsjbws0@fexbox.org", "jrnw2q5sql9@mailbox.in.ua"])
    assert out == ("1\t\tAvailable\t-\ttl26fsjbws0@fexbox.org | https://tempmail.plus/\tXóa\r\n"
                   "2\t\tAvailable\t-\tjrnw2q5sql9@mailbox.in.ua | https://tempmail.plus/\tXóa\r\n")


def test_empty():
    assert format_list([]) == ""

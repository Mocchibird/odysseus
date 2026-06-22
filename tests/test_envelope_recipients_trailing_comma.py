"""Regression: SMTP send failed with a bare `{}` on Python 3.13+.

Python 3.13 made `email.utils.getaddresses()` strict-by-default
(CVE-2023-27043). A To field with a trailing comma — e.g. `"addr,"`, exactly
what the compose chip emits for a single recipient — then parses to NO
addresses. `_envelope_recipients` returned `[]`, `smtplib.sendmail` was handed
an empty recipient list, and it raised `SMTPRecipientsRefused({})`, whose
`str()` is the literal `"{}"` the user saw in the error toast.

`_envelope_recipients` must parse leniently (these are the user's own outbound
addresses) so a trailing comma still yields the recipient, while still
honouring the original fix: display names containing commas must not split.
"""
from routes.email_routes import _envelope_recipients


def test_trailing_comma_still_yields_the_recipient():
    # The exact shape from the bug report.
    assert _envelope_recipients("hyunmin.chang@protonmail.com,") == [
        "hyunmin.chang@protonmail.com"
    ]


def test_clean_single_recipient():
    assert _envelope_recipients("a@b.com") == ["a@b.com"]


def test_display_name_with_comma_is_not_split():
    # The original #1464 case must keep working.
    assert _envelope_recipients('"Smith, John" <john@corp.com>') == ["john@corp.com"]


def test_multiple_recipients_with_trailing_comma():
    assert _envelope_recipients("a@b.com, c@d.com,") == ["a@b.com", "c@d.com"]


def test_to_cc_bcc_merge_and_skip_blank_fields():
    assert _envelope_recipients("a@b.com,", "c@d.com", "") == ["a@b.com", "c@d.com"]


def test_truly_empty_returns_empty_list():
    # No addresses anywhere → empty (caller surfaces a clear "no recipient").
    assert _envelope_recipients("", "  ", ",") == []

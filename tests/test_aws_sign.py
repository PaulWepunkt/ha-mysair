"""P0 tests of the AWS SigV4 URL signature (no Home Assistant).

Requires `requests` (imported by api) and `freezegun` to freeze the clock.
Does not validate against AWS: only the structure and determinism of the
signature.
"""

import re
import urllib.parse

import pytest

pytest.importorskip("requests")
freezegun = pytest.importorskip("freezegun")

from api import MySairAPI  # noqa: E402 (deliberate: needs importorskip first)


FIXED = "2026-07-19T10:00:00Z"


def _sign():
    return MySairAPI.aws_sign_url(
        host="test.iot.eu-west-1.amazonaws.com",
        region="eu-west-1",
        access_key="TESTKEYID",
        secret_key="TESTSECRET",
        token="TESTTOKEN",
    )


def test_signed_url_structure():
    with freezegun.freeze_time(FIXED):
        url = _sign()
    assert url.startswith("wss://test.iot.eu-west-1.amazonaws.com/mqtt?")
    qs = urllib.parse.parse_qs(url.split("?", 1)[1])
    assert qs["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert qs["X-Amz-SignedHeaders"] == ["host"]
    assert qs["X-Amz-Security-Token"] == ["TESTTOKEN"]
    # Credential includes the scope with region and service iotdevicegateway
    cred = qs["X-Amz-Credential"][0]
    assert "TESTKEYID/" in cred
    assert "eu-west-1/iotdevicegateway/aws4_request" in cred
    # The signature is a 64-character SHA256 hex string
    assert re.fullmatch(r"[0-9a-f]{64}", qs["X-Amz-Signature"][0])


def test_signature_is_deterministic_for_fixed_time():
    with freezegun.freeze_time(FIXED):
        url1 = _sign()
    with freezegun.freeze_time(FIXED):
        url2 = _sign()
    assert url1 == url2


def test_amz_date_matches_frozen_time():
    with freezegun.freeze_time(FIXED):
        url = _sign()
    qs = urllib.parse.parse_qs(url.split("?", 1)[1])
    assert qs["X-Amz-Date"] == ["20260719T100000Z"]

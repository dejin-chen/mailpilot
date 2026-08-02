"""JWT 访问令牌测试。"""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.security.jwt import AccessTokenError, create_access_token, decode_access_token


def test_access_token_round_trip_preserves_user_id() -> None:
    user_id = uuid4()

    token = create_access_token(user_id)
    payload = decode_access_token(token)

    assert payload.sub == user_id
    assert payload.token_type == "access"
    assert payload.issuer == "mailpilot"
    assert payload.audience == "mailpilot-api"
    assert payload.token_id
    assert payload.expires_at > payload.issued_at


def test_access_token_rejects_tampered_signature() -> None:
    token = create_access_token(uuid4())
    header, payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered_token = ".".join((header, payload, replacement + signature[1:]))

    with pytest.raises(AccessTokenError, match="无效或已过期"):
        decode_access_token(tampered_token)


def test_access_token_rejects_expired_token() -> None:
    token = create_access_token(uuid4(), expires_delta=timedelta(seconds=-1))

    with pytest.raises(AccessTokenError, match="无效或已过期"):
        decode_access_token(token)

"""密码哈希测试。"""

from app.security.password import hash_password, verify_password


def test_password_hash_is_salted_and_verifiable() -> None:
    password = "mailpilot-demo-password"
    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash != password
    assert first_hash != second_hash
    assert verify_password(password, first_hash) is True
    assert verify_password(password, second_hash) is True


def test_password_verification_rejects_wrong_password_and_broken_hash() -> None:
    password_hash = hash_password("correct-password")

    assert verify_password("wrong-password", password_hash) is False
    assert verify_password("correct-password", "not-a-valid-hash") is False

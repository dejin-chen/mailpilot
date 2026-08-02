"""密码哈希与验证。"""

from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

_password_hasher = PasswordHash.recommended()
_DUMMY_PASSWORD_HASH = _password_hasher.hash(
    "mailpilot-dummy-password-used-only-for-timing-equalization"
)


def hash_password(password: str) -> str:
    """使用当前推荐的 Argon2 参数生成不可逆密码哈希。"""

    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证明文密码；损坏或未知格式的哈希统一视为不匹配。"""

    try:
        return _password_hasher.verify(password, password_hash)
    except PwdlibError:
        return False


def dummy_password_hash() -> str:
    """返回进程内固定的虚拟哈希，用于隐藏账号是否存在的耗时差异。"""

    return _DUMMY_PASSWORD_HASH

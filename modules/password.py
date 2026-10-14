import base64
import hashlib
import hmac
import os

_N, _R, _P = 2 ** 15, 8, 1
_MAXMEM = 64 * 1024 * 1024
_PREFIX = "scrypt$"


def hash_password(password):
    salt = os.urandom(16)
    digest = hashlib.scrypt(str(password).encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return f"{_PREFIX}{_N}${_R}${_P}${b64(salt)}${b64(digest)}"


def is_password_hash(value):
    return isinstance(value, str) and value.startswith(_PREFIX) and value.count("$") == 5


def verify_password(password, stored):
    if not is_password_hash(stored):
        return False
    try:
        _name, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(str(password or "").encode("utf-8"), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM, dklen=len(expected))
    except Exception:
        return False
    return hmac.compare_digest(got, expected)

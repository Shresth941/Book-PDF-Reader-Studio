"""Owner authentication for the separately deployed Vercel workspace."""
import base64
import hashlib
import hmac
import os
import secrets
import time

COOKIE = "__Host-pdf-review"
LIFETIME = 12 * 60 * 60


def secret() -> bytes:
    value = os.environ.get("REVIEW_SESSION_SECRET", "")
    if len(value) < 43:
        raise RuntimeError("Hosted session secret is not configured.")
    return value.encode()


def signature(value: str) -> str:
    return hmac.new(secret(), value.encode(), hashlib.sha256).hexdigest()


def new_session() -> str:
    body = f"{int(time.time()) + LIFETIME}.{secrets.token_urlsafe(24)}"
    return f"{body}.{signature(body)}"


def valid_session(value: str) -> bool:
    try:
        body, supplied = value.rsplit(".", 1)
        expiry = int(body.split(".", 1)[0])
        return time.time() < expiry <= time.time() + LIFETIME + 30 and hmac.compare_digest(signature(body), supplied)
    except (ValueError, AttributeError):
        return False


def review_token(session: str) -> str:
    return signature("review:" + session)


def password_matches(password: str) -> bool:
    # Format: pbkdf2_sha256$iterations$base64salt$hexhash. Never store the password.
    stored = os.environ.get("REVIEW_OWNER_PASSWORD_HASH", "")
    try:
        algorithm, rounds, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256" or not 200000 <= int(rounds) <= 1000000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt, validate=True), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def protect_passwords(values: dict) -> str:
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    key = hashlib.sha256(secret() + b"pdf-passwords-v1").digest()
    encrypted = AESGCM(key).encrypt(nonce, json.dumps(values).encode(), b"pdf-review-v1")
    return base64.b64encode(nonce + encrypted).decode()


def unprotect_passwords(value: str) -> dict:
    if not value:
        return {}
    import json
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    data = base64.b64decode(value, validate=True)
    key = hashlib.sha256(secret() + b"pdf-passwords-v1").digest()
    return json.loads(AESGCM(key).decrypt(data[:12], data[12:], b"pdf-review-v1"))

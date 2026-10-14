import hashlib
import hmac
import time


def sign_user_identity(user_id: str, timestamp: str, signing_key: str) -> str:
    message = f"{user_id}:{timestamp}".encode("utf-8")
    return hmac.new(signing_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_user_identity(
    user_id: str,
    timestamp: str | None,
    signature: str | None,
    signing_key: str,
    *,
    ttl_seconds: int,
    now: int | None = None,
) -> bool:
    if not timestamp or not signature:
        return False
    try:
        signed_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if len(signature) != 64 or any(char not in "0123456789abcdefABCDEF" for char in signature):
        return False
    current_time = int(time.time()) if now is None else now
    if abs(current_time - signed_at) > ttl_seconds:
        return False
    expected = sign_user_identity(user_id, timestamp, signing_key)
    return hmac.compare_digest(signature.lower(), expected)

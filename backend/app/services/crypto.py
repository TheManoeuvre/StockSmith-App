from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.config import settings


def _fernet() -> Fernet:
    if not settings.token_encryption_key:
        raise RuntimeError(
            "token_encryption_key is not configured — required to store or read encrypted "
            "platform tokens. Generate one with: python -c \"from cryptography.fernet import "
            'Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.token_encryption_key.encode("ascii"))


class EncryptedString(TypeDecorator):
    """Transparently encrypts/decrypts a string column at rest with Fernet.

    The app already gates all platform-connection reads/writes behind require_auth, but
    tokens are sensitive enough (they grant live Etsy account access) to warrant
    encryption independent of app-level access control — this limits blast radius if the
    database itself is ever exposed on its own (backup leak, misconfigured access, etc).
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return _fernet().encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        try:
            return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise RuntimeError(
                "Failed to decrypt a stored platform token — token_encryption_key is missing, "
                "wrong, or was rotated. Reconnect the platform to store a fresh token."
            ) from e

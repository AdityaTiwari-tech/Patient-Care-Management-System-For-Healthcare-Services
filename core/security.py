"""
core/security.py
Password hashing / verification using bcrypt directly.

Note: we call the `bcrypt` library directly rather than going through
`passlib`. passlib 1.7.x is unmaintained and its bcrypt handler breaks
against modern bcrypt (>=4.0) releases with errors like
"module 'bcrypt' has no attribute '__about__'" or a bogus
"password cannot be longer than 72 bytes" failure on short passwords.
"""
import bcrypt
import re

# bcrypt only looks at the first 72 bytes of the input; anything beyond
# that is silently ignored by the algorithm itself, so we truncate up
# front to avoid ValueErrors on unusually long passwords.
_MAX_BYTES = 72

MIN_PASSWORD_LENGTH = 8
_SPECIAL_CHAR_PATTERN = re.compile(r"[^A-Za-z0-9]")
_DIGIT_PATTERN = re.compile(r"[0-9]")


def validate_password_strength(password: str) -> list:
    """
    Returns a list of unmet requirements (empty list = password is valid).
    Rules: at least 8 characters, at least one digit, at least one
    special (non-alphanumeric) character.
    """
    errors = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not _DIGIT_PATTERN.search(password):
        errors.append("at least one number")
    if not _SPECIAL_CHAR_PATTERN.search(password):
        errors.append("at least one special character")
    return errors


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_BYTES]


def hash_password(plain_password: str) -> str:
    hashed = bcrypt.hashpw(_prepare(plain_password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain_password), password_hash.encode("utf-8"))
    except Exception:
        return False
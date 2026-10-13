"""Senhas e proteção contra tentativas repetidas de login."""

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

# scrypt: algoritmo feito para guardar senhas, propositalmente lento para dificultar
# ataques de força bruta. Vem embutido no Python (não precisa instalar nada).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@+-]{3,64}$")
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def validate_credentials(username: str, password: str) -> str | None:
    """Mensagem de erro, ou None se usuário e senha forem aceitáveis."""
    if not USERNAME_RE.match(username):
        return "O usuário deve ter de 3 a 64 caracteres: letras, números e . _ @ + -"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"A senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    return None


class LoginLimiter:
    """Bloqueia um usuário por um tempo depois de muitas senhas erradas seguidas."""

    def __init__(self, max_failures: int = 5, window_seconds: float = 15 * 60):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        attempts = self._failures[key.lower()]
        cutoff = time.time() - self.window
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def is_blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._recent(key).append(time.time())

    def reset(self, key: str) -> None:
        self._failures.pop(key.lower(), None)

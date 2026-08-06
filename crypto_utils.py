import os
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    key = os.environ.get("FERNET_KEY")
    if not key:
        raise RuntimeError(
            "La variable d'environnement FERNET_KEY est manquante. "
            "Génère-en une avec : python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt_password(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_password(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()

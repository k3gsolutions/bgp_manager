"""
Criptografia simétrica (Fernet/AES-128-CBC) para credenciais de dispositivos.
Usamos Fernet porque precisamos descriptografar senhas para usá-las via SSH.
"""
from cryptography.fernet import Fernet, InvalidToken

from .config import settings


def _get_fernet() -> Fernet:
    key = settings.fernet_key
    if not key:
        raise RuntimeError(
            "FERNET_KEY não configurada. "
            "Gere com: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Falha ao descriptografar — chave inválida ou dado corrompido")

"""Создаёт ключ шифрования для FERNET_KEY: python -m cargo_platform.tools.genkey"""
from cryptography.fernet import Fernet

print(Fernet.generate_key().decode())

# SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Adaptado de src/core/crypto/crypto.cpp e crypto.h do ShadPs4Plus / shadPS4.

import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import keys

XTS_SECTOR_SIZE = 0x1000


def _rsa_pkcs1v15_decrypt(ciphertext: bytes, modulus: int, private_exponent: int) -> bytes:
    """RSAES-PKCS1-v1_5: decifra e remove o padding (00 02 <aleatório> 00 <dados>)."""
    size = (modulus.bit_length() + 7) // 8
    m = pow(int.from_bytes(ciphertext, "big"), private_exponent, modulus).to_bytes(size, "big")
    if m[0] != 0 or m[1] != 2:
        raise ValueError("invalid RSA padding")
    sep = m.index(0, 2)
    return m[sep + 1 :]


def rsa2048_decrypt(ciphertext: bytes, is_dk3: bool) -> bytes:
    """Retorna os 32 primeiros bytes decifrados (DK3 ou EKPFS)."""
    if is_dk3:
        n, d = keys.DK3_MODULUS, keys.DK3_PRIVATE_EXPONENT
    else:
        n, d = keys.FAKE_MODULUS, keys.FAKE_PRIVATE_EXPONENT
    return _rsa_pkcs1v15_decrypt(ciphertext, n, d)[:32]


def iv_key_hash256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def aes_cbc_decrypt(iv_key: bytes, ciphertext: bytes) -> bytes:
    """AES-128-CBC com IV = iv_key[0:16] e chave = iv_key[16:32]."""
    decryptor = Cipher(algorithms.AES(iv_key[16:32]), modes.CBC(iv_key[:16])).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def pfs_gen_crypto_key(ekpfs: bytes, seed: bytes) -> tuple[bytes, bytes]:
    """Retorna (data_key, tweak_key) derivados do EKPFS e da seed do PFS."""
    digest = hmac.new(ekpfs, (1).to_bytes(4, "little") + seed, hashlib.sha256).digest()
    return digest[16:32], digest[:16]


class PfsDecryptor:
    """AES-128-XTS com setores de 0x1000 bytes, tweak = número do setor."""

    def __init__(self, data_key: bytes, tweak_key: bytes):
        self._algorithm = algorithms.AES(data_key + tweak_key)

    def decrypt(self, data: bytes, first_sector: int) -> bytes:
        out = bytearray(len(data))
        for i in range(0, len(data), XTS_SECTOR_SIZE):
            tweak = (first_sector + i // XTS_SECTOR_SIZE).to_bytes(16, "little")
            decryptor = Cipher(self._algorithm, modes.XTS(tweak)).decryptor()
            out[i : i + XTS_SECTOR_SIZE] = decryptor.update(data[i : i + XTS_SECTOR_SIZE])
        return bytes(out)

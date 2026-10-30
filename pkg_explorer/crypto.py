# SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Adapted from src/core/crypto/crypto.cpp and crypto.h of ShadPs4Plus / shadPS4.

import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import keys

XTS_SECTOR_SIZE = 0x1000


def _rsa_pkcs1v15_decrypt(ciphertext: bytes, modulus: int, private_exponent: int) -> bytes:
    """RSAES-PKCS1-v1_5: decrypts and strips the padding (00 02 <random> 00 <data>)."""
    size = (modulus.bit_length() + 7) // 8
    m = pow(int.from_bytes(ciphertext, "big"), private_exponent, modulus).to_bytes(size, "big")
    if m[0] != 0 or m[1] != 2:
        raise ValueError("invalid RSA padding")
    sep = m.index(0, 2)
    return m[sep + 1 :]


def rsa2048_decrypt(ciphertext: bytes, is_dk3: bool) -> bytes:
    """Returns the first 32 decrypted bytes (DK3 or EKPFS)."""
    if is_dk3:
        n, d = keys.DK3_MODULUS, keys.DK3_PRIVATE_EXPONENT
    else:
        n, d = keys.FAKE_MODULUS, keys.FAKE_PRIVATE_EXPONENT
    return _rsa_pkcs1v15_decrypt(ciphertext, n, d)[:32]


def iv_key_hash256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def aes_cbc_decrypt(iv_key: bytes, ciphertext: bytes) -> bytes:
    """AES-128-CBC with IV = iv_key[0:16] and key = iv_key[16:32]."""
    decryptor = Cipher(algorithms.AES(iv_key[16:32]), modes.CBC(iv_key[:16])).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def pfs_gen_crypto_key(ekpfs: bytes, seed: bytes) -> tuple[bytes, bytes]:
    """Returns (data_key, tweak_key) derived from the EKPFS and the PFS seed."""
    digest = hmac.new(ekpfs, (1).to_bytes(4, "little") + seed, hashlib.sha256).digest()
    return digest[16:32], digest[:16]


class PfsDecryptor:
    """AES-128-XTS with 0x1000-byte sectors, tweak = sector number."""

    def __init__(self, data_key: bytes, tweak_key: bytes):
        self._algorithm = algorithms.AES(data_key + tweak_key)

    def decrypt(self, data: bytes, first_sector: int) -> bytes:
        out = bytearray(len(data))
        for i in range(0, len(data), XTS_SECTOR_SIZE):
            tweak = (first_sector + i // XTS_SECTOR_SIZE).to_bytes(16, "little")
            decryptor = Cipher(self._algorithm, modes.XTS(tweak)).decryptor()
            out[i : i + XTS_SECTOR_SIZE] = decryptor.update(data[i : i + XTS_SECTOR_SIZE])
        return bytes(out)

# SPDX-License-Identifier: GPL-2.0-or-later
"""Generates synthetic fake PKGs for the tests (the reverse of pkg_explorer/pkg.py).

The generated PKG is encrypted like a real fake PKG: DK3 and EKPFS encrypted with RSA, IMAGE_KEY
and NP entries with AES-CBC, and the PFS with AES-XTS. Inside the PFS there is a file system with
a superblock, inodes and directories, with compressed (zlib) or uncompressed blocks.
See docs/FORMAT.md.

Names are deliberately not validated, so that tests can build malicious PKGs.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
import zlib
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from pkg_explorer import keys

BLOCK = 0x10000
SECTOR = 0x1000
INODE_SIZE = 0xA8
PFSC_OFFSET = 0x20000  # inside the PFS image
RSA_E = 65537

PFS_FILE, PFS_DIR, PFS_CUR, PFS_PARENT = 2, 3, 4, 5

Tree = dict  # name -> bytes (file) or Tree (folder)


def _rsa_encrypt(data: bytes, modulus: int, rng) -> bytes:
    size = (modulus.bit_length() + 7) // 8
    pad = bytes(rng.randrange(1, 256) for _ in range(size - len(data) - 3))
    m = b"\x00\x02" + pad + b"\x00" + data
    return pow(int.from_bytes(m, "big"), RSA_E, modulus).to_bytes(size, "big")


def _aes_cbc_encrypt(iv_key: bytes, data: bytes) -> bytes:
    enc = Cipher(algorithms.AES(iv_key[16:32]), modes.CBC(iv_key[:16])).encryptor()
    return enc.update(data) + enc.finalize()


def _xts_encrypt(data: bytes, data_key: bytes, tweak_key: bytes, first_sector: int) -> bytes:
    algo = algorithms.AES(data_key + tweak_key)
    out = bytearray()
    for i in range(0, len(data), SECTOR):
        tweak = (first_sector + i // SECTOR).to_bytes(16, "little")
        out += Cipher(algo, modes.XTS(tweak)).encryptor().update(data[i : i + SECTOR])
    return bytes(out)


def build_sfo(values: dict[str, str | int]) -> bytes:
    keys_sorted = sorted(values)
    key_table = b""
    data_table = b""
    index = b""
    for key in keys_sorted:
        value = values[key]
        if isinstance(value, int):
            fmt, raw = 0x0404, struct.pack("<I", value)
        else:
            fmt, raw = 0x0204, value.encode() + b"\x00"
        max_len = (len(raw) + 3) & ~3
        index += struct.pack("<HHIII", len(key_table), fmt, len(raw), max_len, len(data_table))
        key_table += key.encode() + b"\x00"
        data_table += raw.ljust(max_len, b"\x00")
    key_table = key_table.ljust((len(key_table) + 3) & ~3, b"\x00")
    key_off = 0x14 + len(index)
    data_off = key_off + len(key_table)
    header = b"\x00PSF" + struct.pack("<IIII", 0x0101, key_off, data_off, len(keys_sorted))
    return header + index + key_table + data_table


@dataclass
class _Inode:
    mode: int
    size: int = 0
    blocks: int = 0
    loc: int = 0


@dataclass
class _Fs:
    inodes: list[_Inode] = field(default_factory=list)
    blocks: list[bytes] = field(default_factory=list)  # inner blocks (without the superblock)


def _dirent(ino: int, typ: int, name: bytes) -> bytes:
    size = (16 + len(name) + 1 + 7) & ~7
    return struct.pack("<iiii", ino, typ, len(name), size) + name.ljust(size - 16, b"\x00")


def _build_fs(tree: Tree) -> tuple[list[bytes], int, int]:
    """Returns (inner blocks starting at 0, number of inodes, superroot inode)."""
    # Inodes: 0 = reserved (ino 0 marks the end of a directory), 1 = superroot,
    # 2 = flat_path_table, 3 = uroot, then the content.
    inodes: list[_Inode] = [_Inode(0x8000), _Inode(0x4000 | 0o755), _Inode(0x8000 | 0o644), _Inode(0x4000 | 0o755)]
    files: list[tuple[int, bytes]] = [(2, b"flat path table")]
    dirs: list[tuple[int, int, list[tuple[bytes, int, int]]]] = []  # (ino, parent, [(name, ino, type)])

    def walk(node: Tree, ino: int, parent: int) -> None:
        children = []
        for name, value in node.items():
            child = len(inodes)
            if isinstance(value, dict):
                inodes.append(_Inode(0x4000 | 0o755))
                children.append((name.encode(), child, PFS_DIR))
                walk(value, child, ino)
            else:
                inodes.append(_Inode(0x8000 | 0o644, size=len(value)))
                children.append((name.encode(), child, PFS_FILE))
                files.append((child, value))
        dirs.append((ino, parent, children))

    dirs.append((1, 1, [(b"flat_path_table", 2, PFS_FILE), (b"uroot", 3, PFS_DIR)]))
    walk(tree, 3, 1)

    per_block = BLOCK // INODE_SIZE
    inode_blocks = -(-len(inodes) // per_block)
    next_block = 1 + inode_blocks
    data_blocks: list[bytes] = []

    for ino, parent, children in dirs:
        entries = [_dirent(ino, PFS_CUR, b"."), _dirent(parent, PFS_PARENT, b"..")]
        entries += [_dirent(c, t, n) for n, c, t in children]
        blocks = [b""]
        for e in entries:
            if len(blocks[-1]) + len(e) > BLOCK:
                blocks.append(b"")
            blocks[-1] += e
        inodes[ino].loc, inodes[ino].blocks = next_block, len(blocks)
        inodes[ino].size = len(blocks) * BLOCK
        data_blocks += [b.ljust(BLOCK, b"\x00") for b in blocks]
        next_block += len(blocks)

    for ino, content in files:
        count = -(-len(content) // BLOCK)
        inodes[ino].loc, inodes[ino].blocks = (next_block if count else 0), count
        data_blocks += [content[i : i + BLOCK].ljust(BLOCK, b"\x00") for i in range(0, len(content), BLOCK)]
        next_block += count

    superblock = bytearray(BLOCK)
    struct.pack_into("<q", superblock, 0x30, len(inodes))
    struct.pack_into("<q", superblock, 0x48, 1)
    table = bytearray(inode_blocks * BLOCK)
    for i, node in enumerate(inodes):
        base = (i // per_block) * BLOCK + (i % per_block) * INODE_SIZE
        struct.pack_into("<HHIq", table, base, node.mode, 1, 0, node.size)
        struct.pack_into("<II", table, base + 0x60, node.blocks, node.loc)
    blocks = [bytes(superblock)] + [bytes(table[i : i + BLOCK]) for i in range(0, len(table), BLOCK)]
    return blocks + data_blocks, len(inodes), 1


def _build_pfsc(blocks: list[bytes], compress: bool) -> bytes:
    stored = []
    for block in blocks:
        packed = zlib.compress(block, 6) if compress else block
        stored.append(packed if len(packed) < BLOCK else block)
    map_offset = 0x400
    data_start = (map_offset + (len(blocks) + 1) * 8 + 0xFFF) & ~0xFFF
    offsets, pos = [], data_start
    for s in stored:
        offsets.append(pos)
        pos += len(s)
    offsets.append(pos)
    header = struct.pack("<4siiiqqQq", b"PFSC", 0, 6, BLOCK, BLOCK, map_offset, data_start, len(blocks) * BLOCK)
    image = bytearray(header.ljust(map_offset, b"\x00"))
    image += struct.pack(f"<{len(offsets)}Q", *offsets)
    image = image.ljust(data_start, b"\x00") + b"".join(stored)
    return bytes(image)


def build_pkg(
    path: str | os.PathLike,
    tree: Tree,
    *,
    sfo: dict[str, str | int] | None = None,
    np_data: bytes = b"",
    icon: bytes = b"",
    content_flags: int = 0,
    compress: bool = True,
    seed: int = 0,
) -> dict[str, bytes]:
    """Writes a fake PKG to `path`. Returns the plaintext entries (name -> data) for checking."""
    import random

    rng = random.Random(seed)
    title_id = "TEST00001"
    content_id = f"UP0000-{title_id}_00-PKGEXPLORERTEST1"
    sfo_bytes = build_sfo(sfo or {"TITLE": "Test Package", "CATEGORY": "gd", "APP_VER": "01.00",
                                  "CONTENT_ID": content_id, "TITLE_ID": title_id})

    dk3 = bytes(rng.randrange(256) for _ in range(32))
    ekpfs = bytes(rng.randrange(256) for _ in range(32))
    pfs_seed = bytes(rng.randrange(256) for _ in range(16))

    # Entries: (id, plaintext data, encrypted as NP?)
    entries = [(0x0010, None, False), (0x0020, None, False)]
    if np_data:
        entries.append((0x0400, np_data, True))
    entries.append((0x1000, sfo_bytes, False))
    if icon:
        entries.append((0x1200, icon, False))

    table_offset = 0x2000
    pos = table_offset + len(entries) * 32
    layout = []
    for entry_id, data, _np in entries:
        size = {0x0010: 32 + 7 * 32 + 7 * 256, 0x0020: 256}.get(entry_id, len(data or b""))
        pos = (pos + 15) & ~15
        layout.append((pos, size))
        pos += size + (-size % 16)  # room for the last AES block of NP entries
    raws = [struct.pack(">6I8x", eid, 0, 0, 0, off, size) for (eid, _d, _n), (off, size) in zip(entries, layout)]

    payloads = []
    for (entry_id, data, is_np), raw in zip(entries, raws):
        if entry_id == 0x0010:
            key1 = [bytes(256)] * 7
            key1[3] = _rsa_encrypt(dk3, keys.DK3_MODULUS, rng)
            payloads.append(bytes(32 + 7 * 32) + b"".join(key1))
        elif entry_id == 0x0020:
            img_key = _rsa_encrypt(ekpfs, keys.FAKE_MODULUS, rng)
            payloads.append(_aes_cbc_encrypt(hashlib.sha256(raw + dk3).digest(), img_key))
        elif is_np:
            padded = data + bytes(rng.randrange(256) for _ in range(-len(data) % 16))
            payloads.append(_aes_cbc_encrypt(hashlib.sha256(raw + dk3).digest(), padded))
        else:
            payloads.append(data)

    # PFS image: plaintext header (with the seed) + PFSC encrypted with AES-XTS.
    digest = hmac.new(ekpfs, (1).to_bytes(4, "little") + pfs_seed, hashlib.sha256).digest()
    tweak_key, data_key = digest[:16], digest[16:32]
    inner, _count, _root = _build_fs(tree)
    pfsc = _build_pfsc(inner, compress)
    pfsc = pfsc.ljust((len(pfsc) + SECTOR - 1) & ~(SECTOR - 1), b"\x00")
    pfs = bytearray(PFSC_OFFSET)
    pfs[0x370:0x380] = pfs_seed
    pfs += _xts_encrypt(pfsc, data_key, tweak_key, PFSC_OFFSET // SECTOR)

    pfs_offset = (pos + 0xFFFF) & ~0xFFFF
    total = pfs_offset + len(pfs)

    header = bytearray(0x1000)
    struct.pack_into(">I", header, 0x00, 0x7F434E54)
    struct.pack_into(">I", header, 0x10, len(entries))
    struct.pack_into(">I", header, 0x18, table_offset)
    struct.pack_into(">QQ", header, 0x30, pfs_offset, len(pfs))
    header[0x40:0x64] = content_id.encode()
    struct.pack_into(">I", header, 0x78, content_flags)
    struct.pack_into(">QQ", header, 0x410, pfs_offset, len(pfs))
    struct.pack_into(">Q", header, 0x430, total)

    out = bytearray(total)
    out[: len(header)] = header
    out[table_offset : table_offset + len(raws) * 32] = b"".join(raws)
    for (off, _size), payload in zip(layout, payloads):
        out[off : off + len(payload)] = payload
    out[pfs_offset:] = pfs
    with open(path, "wb") as f:
        f.write(out)

    names = {0x0010: "entry_keys", 0x0020: "image_key", 0x0400: "license.dat", 0x1000: "param.sfo", 0x1200: "icon0.png"}
    plain = {}
    for (entry_id, data, _np), payload in zip(entries, payloads):
        plain[names[entry_id]] = data if data is not None else payload
    return plain

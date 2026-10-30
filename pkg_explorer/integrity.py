# SPDX-License-Identifier: GPL-2.0-or-later
"""Integrity check: compares the SHA-256 digests stored in the PKG with its contents.

What each digest covers (docs/FORMAT.md, "Digests"); together they cover the whole package:
- header 0xFE0: the header, bytes 0..0xFE0
- header 0x160: the body (all entries), `[body offset, + body size)`
- header 0x140: the `digests` entry (0x0001), which holds one digest per entry, in table order
  (zero for itself). Encrypted entries are hashed with their size rounded up to 16 bytes.
- header 0x460: the start of the PFS image (`pfs signed size` bytes)
- header 0x440: the whole PFS image (the game files) - read only by the full check

The quick check reads everything except the whole PFS image, so it is instant; the full check
also reads the PFS image (the whole file, ~1 minute per 10 GB).
"""

from __future__ import annotations

import hashlib
import struct
import threading
from dataclasses import dataclass

from .i18n import _
from .pkg import ENTRY_CHUNK, ExtractionCancelled, Pkg, PkgEntry, PkgError, ProgressCallback

ENTRY_ENCRYPTED = 0x80000000  # bit of "flags 1" (entry record offset 0x08)
DIGESTS_ENTRY_ID = 0x0001
READ_CHUNK = 8 << 20

# Problems: the part names shown to the user.
HEADER = "header"
DIGESTS = "digest table"
BODY = "package body"
PFS_START = "start of the game data"
PFS = "game data"

PART_LABELS = {
    HEADER: _("header"),
    DIGESTS: _("digest table"),
    BODY: _("package body"),
    PFS_START: _("start of the game data"),
    PFS: _("game data (PFS image)"),
}


@dataclass
class Result:
    checked: list[str]  # parts that were checked
    bad: list[str]  # parts whose digest does not match
    full: bool  # whether the whole PFS image was checked

    @property
    def ok(self) -> bool:
        return not self.bad


def part_label(part: str) -> str:
    if part in PART_LABELS:
        return PART_LABELS[part]
    return part  # entry names (param.sfo, icon0.png, ...)


def _digest_at(pkg: Pkg, offset: int) -> bytes | None:
    """A digest stored in the header, or None if the field is empty (not filled in)."""
    value = pkg.header[offset : offset + 32]
    return None if value == bytes(32) else value


def _sha256_range(
    pkg: Pkg,
    offset: int,
    size: int,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> bytes:
    digest = hashlib.sha256()
    for pos in range(0, size, READ_CHUNK):
        if cancel is not None and cancel.is_set():
            raise ExtractionCancelled()
        chunk = pkg.read_raw(offset + pos, min(READ_CHUNK, size - pos))
        digest.update(chunk)
        if progress is not None:
            progress(len(chunk))
    return digest.digest()


def _entry_digest(pkg: Pkg, entry: PkgEntry) -> bytes:
    flags1 = struct.unpack_from(">I", entry.raw, 8)[0]
    size = entry.size + (-entry.size % 16) if flags1 & ENTRY_ENCRYPTED else entry.size
    digest = hashlib.sha256()
    for pos in range(0, size, ENTRY_CHUNK):
        digest.update(pkg.read_raw(entry.offset + pos, min(ENTRY_CHUNK, size - pos)))
    return digest.digest()


def body_range(pkg: Pkg) -> tuple[int, int]:
    return struct.unpack_from(">QQ", pkg.header, 0x20)


def full_check_size(pkg: Pkg) -> int:
    """Bytes read by a full check (for a progress bar)."""
    return body_range(pkg)[1] + pkg.pfs_image_size


def verify(
    pkg: Pkg,
    full: bool = False,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Result:
    """Checks the digests. `progress` receives the bytes read from the body and PFS image.
    Raises ExtractionCancelled if `cancel` is set."""
    result = Result([], [], full)

    def check(part: str, expected: bytes | None, compute) -> None:
        if expected is None:
            return
        result.checked.append(part)
        try:
            actual = compute()
        except PkgError:  # the range points outside the file
            actual = None
        if actual != expected:
            result.bad.append(part)

    check(HEADER, _digest_at(pkg, 0xFE0), lambda: hashlib.sha256(pkg.header[:0xFE0]).digest())

    digests_entry = next((e for e in pkg.entries if e.id == DIGESTS_ENTRY_ID), None)
    if digests_entry is not None:
        table = pkg.read_raw(digests_entry.offset, digests_entry.size)
        check(DIGESTS, _digest_at(pkg, 0x140), lambda: hashlib.sha256(table).digest())
        for i, entry in enumerate(pkg.entries):
            expected = table[i * 32 : i * 32 + 32]
            if entry is digests_entry or len(expected) < 32 or expected == bytes(32):
                continue
            check(entry.name, expected, lambda entry=entry: _entry_digest(pkg, entry))

    body_offset, body_size = body_range(pkg)
    check(
        BODY,
        _digest_at(pkg, 0x160),
        lambda: _sha256_range(pkg, body_offset, body_size, progress, cancel),
    )

    signed_size = struct.unpack_from(">I", pkg.header, 0x438)[0]
    check(
        PFS_START,
        _digest_at(pkg, 0x460),
        lambda: _sha256_range(pkg, pkg.pfs_image_offset, signed_size),
    )
    if full:
        check(
            PFS,
            _digest_at(pkg, 0x440),
            lambda: _sha256_range(pkg, pkg.pfs_image_offset, pkg.pfs_image_size, progress, cancel),
        )
    return result

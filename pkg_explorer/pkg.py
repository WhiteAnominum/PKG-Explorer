# SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Adapted from src/core/file_format/pkg.cpp, pkg.h and pfs.h of ShadPs4Plus / shadPS4.
#
# Unlike the original, which extracts the whole package, here the file tree is built once and
# each file is read on demand, decrypting/decompressing only its own blocks.
# See docs/FORMAT.md for the layout of the format.

from __future__ import annotations

import os
import struct
import threading
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Iterator

from . import crypto
from .entry_names import ENTRY_NAMES
from .i18n import _
from .sfo import parse_sfo

PKG_MAGIC = 0x7F434E54
PFSC_MAGIC = b"PFSC"

PFS_FILE = 2
PFS_DIR = 3
PFS_CURRENT_DIR = 4
PFS_PARENT_DIR = 5

INODE_SIZE = 0xA8
NP_ENTRY_IDS = (0x400, 0x401, 0x402, 0x403)

CONTENT_FLAGS = (
    (0x100000, "FIRST_PATCH"),
    (0x200000, "PATCHGO"),
    (0x400000, "REMASTER"),
    (0x800000, "PS_CLOUD"),
    (0x2000000, "GD_AC"),
    (0x4000000, "NON_GAME"),
    (0x8000000, "UNKNOWN_0x8000000"),
    (0x40000000, "SUBSEQUENT_PATCH"),
    (0x41000000, "DELTA_PATCH"),
    (0x60000000, "CUMULATIVE_PATCH"),
)

# How many inner blocks (0x10000 each) to read from disk at once when extracting.
READ_BATCH_BLOCKS = 64

# Limits against malformed/malicious PKGs (far above what real PKGs use).
PFS_BLOCK_SIZE = 0x10000
MAX_ENTRIES = 10_000
MAX_SFO_SIZE = 1 << 20
MAX_NP_ENTRY_SIZE = 1 << 20
MAX_INODES = 2_000_000
MAX_DIR_DEPTH = 128
PFSC_SEARCH_LIMIT = 256 << 20
ENTRY_CHUNK = 1 << 20

ProgressCallback = Callable[[int], None]  # receives the bytes written since the last call


class PkgError(Exception):
    pass


class ExtractionCancelled(Exception):
    pass


def _corrupt(detail: str) -> PkgError:
    return PkgError(_("invalid or corrupted PKG: {detail}").format(detail=detail))


def check_name(name: str) -> str:
    """Names come from inside the PKG: a malicious PKG could use "../" or "/" to write outside the
    destination folder when extracting ("Zip Slip"). No legitimate PKG uses such names."""
    if name in ("", ".", "..") or any(c in name for c in "/\\\0"):
        raise PkgError(_("invalid file name in the PKG: {name!r}").format(name=name))
    return name


def _safe_target(dest_dir: str, name: str) -> str:
    """Path of `name` inside `dest_dir`, making sure it does not escape `dest_dir`
    (neither by name nor through symbolic links already present in the destination)."""
    target = os.path.join(dest_dir, check_name(name))
    base = os.path.realpath(dest_dir)
    if os.path.commonpath([base, os.path.realpath(target)]) != base:
        raise PkgError(_("refusing to write outside the destination folder: {path}").format(path=target))
    return target


@dataclass
class PkgEntry:
    id: int
    name: str
    offset: int
    size: int
    raw: bytes  # the entry's 32 bytes as stored in the file (used to derive keys)


@dataclass
class Inode:
    mode: int
    flags: int
    size: int
    blocks: int
    loc: int


@dataclass(eq=False)
class Node:
    name: str
    is_dir: bool
    parent: Node | None = None
    size: int = 0
    inode: int | None = None  # file inside the PFS
    entry: PkgEntry | None = None  # file from the PKG entry table (sce_sys)
    children: dict[str, Node] = field(default_factory=dict)

    @property
    def path(self) -> str:
        parts = []
        node: Node | None = self
        while node is not None and node.parent is not None:
            parts.append(node.name)
            node = node.parent
        return "/".join(reversed(parts))

    def total_size(self) -> int:
        if not self.is_dir:
            return self.size
        return sum(child.total_size() for child in self.children.values())

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children.values():
            yield from child.walk()


class Pkg:
    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)
        self._file = open(self.path, "rb")
        self._lock = threading.Lock()
        self._block_cache: OrderedDict[int, bytes] = OrderedDict()
        try:
            self._parse_header()
            self._parse_entries()
            self._derive_keys()
            self._open_pfs()
            self.root = self._build_tree()
        except PkgError:
            self._file.close()
            raise
        except Exception as exc:
            # Any other failure while parsing the file (absurd structure, out of memory, etc.)
            # becomes an "invalid PKG" message instead of crashing the program.
            self._file.close()
            if isinstance(exc, OSError):
                raise
            raise _corrupt(f"{type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Pkg:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- raw reading ----

    def _read(self, offset: int, size: int) -> bytes:
        with self._lock:
            self._file.seek(offset)
            data = self._file.read(size)
        if len(data) != size:
            raise PkgError(
                _("read beyond end of file (offset 0x{offset:X}, size 0x{size:X})").format(
                    offset=offset, size=size
                )
            )
        return data

    # ---- header and entries ----

    def _parse_header(self) -> None:
        """Header fields: docs/FORMAT.md, section 1 (big-endian)."""
        header = self._read(0, 0x1000)
        if struct.unpack_from(">I", header, 0)[0] != PKG_MAGIC:
            raise PkgError(_("this does not look like a valid PKG file"))

        self.file_size = os.path.getsize(self.path)
        self.entry_count = struct.unpack_from(">I", header, 0x10)[0]  # entry count
        self.entry_table_offset = struct.unpack_from(">I", header, 0x18)[0]  # entry table offset
        content_offset, content_size = struct.unpack_from(">QQ", header, 0x30)  # content offset/size
        self.content_id = header[0x40:0x64].rstrip(b"\x00").decode("ascii", "replace")
        self.title_id = header[0x47:0x50].decode("ascii", "replace")  # content ID chars 7..15
        self.content_flags = struct.unpack_from(">I", header, 0x78)[0]  # content flags
        # PFS image offset/size
        self.pfs_image_offset, self.pfs_image_size = struct.unpack_from(">QQ", header, 0x410)
        self.pkg_size = struct.unpack_from(">Q", header, 0x430)[0]  # package size

        if self.pkg_size > self.file_size:
            raise PkgError(
                _("the PKG size is different from expected (incomplete or corrupted file?)")
            )
        if content_offset + content_size > self.pkg_size:
            raise PkgError(_("the content is larger than the PKG"))
        if self.pfs_image_offset + self.pfs_image_size > self.file_size:
            raise _corrupt("PFS image extends past the end of the file")
        if self.entry_count > MAX_ENTRIES:
            raise _corrupt(f"too many entries ({self.entry_count})")

        self.flags = [name for bit, name in CONTENT_FLAGS if self.content_flags & bit]

    def _parse_entries(self) -> None:
        """Entry table: docs/FORMAT.md, section 2."""
        table = self._read(self.entry_table_offset, self.entry_count * 32)
        self.entries: list[PkgEntry] = []
        for i in range(self.entry_count):
            raw = table[i * 32 : i * 32 + 32]
            entry_id, _name_off, _f1, _f2, offset, size = struct.unpack_from(">6I", raw)
            name = ENTRY_NAMES.get(entry_id) or str(entry_id)
            self.entries.append(PkgEntry(entry_id, name, offset, size, raw))

        self.sfo: dict = {}
        sfo_entry = self._entry_by_id(0x1000)
        if sfo_entry is not None:
            if sfo_entry.size > MAX_SFO_SIZE:
                raise _corrupt("param.sfo is too large")
            try:
                self.sfo = parse_sfo(self._read(sfo_entry.offset, sfo_entry.size))
            except (ValueError, IndexError, struct.error) as exc:
                raise PkgError(_("invalid param.sfo")) from exc

    def _entry_by_id(self, entry_id: int) -> PkgEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    @property
    def title(self) -> str:
        return str(self.sfo.get("TITLE", ""))

    @property
    def category(self) -> str:
        return str(self.sfo.get("CATEGORY", ""))

    @property
    def kind(self) -> str:
        """'patch', 'dlc' or 'game', using the same rule as the original extractor."""
        if any("PATCH" in f for f in self.flags):
            return "patch"
        if self.category == "ac":
            return "dlc"
        return "game"

    @property
    def output_folder_name(self) -> str:
        """Folder used by the original extractor / shadPS4: TITLEID, TITLEID-patch or TITLEID/DLC."""
        if self.kind == "patch":
            return f"{self.title_id}-patch"
        if self.kind == "dlc":
            parts = str(self.sfo.get("CONTENT_ID", self.content_id)).split("-")
            if len(parts) > 2:
                return os.path.join(self.title_id, parts[2])
        return self.title_id

    # ---- keys ----

    def _derive_keys(self) -> None:
        """DK3 -> EKPFS -> PFS data/tweak keys: docs/FORMAT.md, section 3."""
        keys_entry = self._entry_by_id(0x10)
        image_entry = self._entry_by_id(0x20)
        if keys_entry is None or image_entry is None:
            raise PkgError(_("the PKG has no key entries (ENTRY_KEYS / IMAGE_KEY)"))

        # ENTRY_KEYS: seed_digest[32], digest1[7][32], key1[7][256]; the DK3 is key1[3].
        key1_3 = self._read(keys_entry.offset + 32 + 7 * 32 + 3 * 256, 256)
        try:
            self._dk3 = crypto.rsa2048_decrypt(key1_3, is_dk3=True)
            iv_key = crypto.iv_key_hash256(image_entry.raw + self._dk3)
            img_key = crypto.aes_cbc_decrypt(iv_key, self._read(image_entry.offset, 256))
            ekpfs = crypto.rsa2048_decrypt(img_key, is_dk3=False)
        except ValueError as exc:
            raise PkgError(
                _("could not decrypt the PKG keys (only fake/fpkg PKGs are supported)")
            ) from exc

        seed = self._read(self.pfs_image_offset + 0x370, 16)  # PFS seed (plaintext)
        data_key, tweak_key = crypto.pfs_gen_crypto_key(ekpfs, seed)
        self._pfs = crypto.PfsDecryptor(data_key, tweak_key)

    def iter_entry(self, entry: PkgEntry) -> Iterator[bytes]:
        if entry.id in NP_ENTRY_IDS:
            yield self.read_entry(entry)
            return
        # In chunks: the size comes from the file and must not turn into a huge allocation.
        for pos in range(0, entry.size, ENTRY_CHUNK):
            yield self._read(entry.offset + pos, min(ENTRY_CHUNK, entry.size - pos))

    def read_entry(self, entry: PkgEntry) -> bytes:
        if entry.id not in NP_ENTRY_IDS:
            return b"".join(self.iter_entry(entry))
        if entry.size > MAX_NP_ENTRY_SIZE:
            raise _corrupt(f"entry {entry.name} is too large")
        # AES-CBC works on 16-byte blocks: the last encrypted block continues in the file past
        # `size`. (The original reads memory past its buffer here and gets the last bytes wrong.)
        padded = self._read(entry.offset, entry.size + (-entry.size % 16))
        iv_key = crypto.iv_key_hash256(entry.raw + self._dk3)
        return crypto.aes_cbc_decrypt(iv_key, padded)[: entry.size]

    # ---- PFS ----

    def _read_pfs(self, offset: int, size: int) -> bytes:
        """Reads and decrypts `size` bytes of the outer PFS image starting at `offset`."""
        start = offset & ~(crypto.XTS_SECTOR_SIZE - 1)
        end = -(-(offset + size) // crypto.XTS_SECTOR_SIZE) * crypto.XTS_SECTOR_SIZE
        data = self._read(self.pfs_image_offset + start, end - start)
        data = self._pfs.decrypt(data, start // crypto.XTS_SECTOR_SIZE)
        return data[offset - start : offset - start + size]

    def _open_pfs(self) -> None:
        """Finds the PFSC and reads its sector map: docs/FORMAT.md, section 4."""
        for offset in range(0x20000, min(self.pfs_image_size, PFSC_SEARCH_LIMIT), 0x10000):
            if self._read_pfs(offset, 16)[:4] == PFSC_MAGIC:
                self._pfsc_offset = offset
                break
        else:
            raise PkgError(_("PFSC image not found inside the PKG"))

        hdr = self._read_pfs(self._pfsc_offset, 0x30)
        # block size, sector map offset, data start, data length
        self._block_size, block_offsets, _data_start, data_length = struct.unpack_from(
            "<qqQq", hdr, 0x10
        )
        if self._block_size != PFS_BLOCK_SIZE:
            raise _corrupt(f"unexpected PFS block size 0x{self._block_size:X}")
        num_blocks = data_length // self._block_size
        available = self.pfs_image_size - self._pfsc_offset  # bytes of the PFSC image in the file
        if not 0 < num_blocks or block_offsets < 0 or block_offsets + (num_blocks + 1) * 8 > available:
            raise _corrupt("invalid PFSC block table")
        sector_map = self._read_pfs(self._pfsc_offset + block_offsets, (num_blocks + 1) * 8)
        self._sector_map = struct.unpack(f"<{num_blocks + 1}Q", sector_map)
        smap = self._sector_map
        if smap[-1] > available or any(smap[i] > smap[i + 1] for i in range(num_blocks)):
            raise _corrupt("invalid PFSC block table")
        self.num_blocks = num_blocks

    def _decode_block(self, raw: bytes) -> bytes:
        if len(raw) == self._block_size:
            return raw
        if len(raw) > self._block_size:
            raise PkgError(_("PFSC block larger than expected"))
        # max_length: a block never decompresses to more than block_size (compression bomb).
        decompressor = zlib.decompressobj()
        try:
            data = decompressor.decompress(raw, self._block_size)
        except zlib.error as exc:
            raise _corrupt(f"bad compressed block ({exc})") from exc
        if decompressor.unconsumed_tail:
            raise PkgError(_("PFSC block larger than expected"))
        return data.ljust(self._block_size, b"\x00")

    def _read_blocks(self, first: int, count: int) -> Iterator[bytes]:
        """Yields inner blocks `first`..`first+count-1`, decrypted and decompressed."""
        if first < 0 or count < 0 or first + count > self.num_blocks:
            raise _corrupt(f"block range {first}+{count} outside the PFS")
        smap = self._sector_map
        block = first
        end = first + count
        while block < end:
            batch_end = min(block + READ_BATCH_BLOCKS, end)
            span_start = smap[block]
            span = self._read_pfs(self._pfsc_offset + span_start, smap[batch_end] - span_start)
            for b in range(block, batch_end):
                yield self._decode_block(span[smap[b] - span_start : smap[b + 1] - span_start])
            block = batch_end

    def _read_block(self, index: int) -> bytes:
        """One inner block, with a small cache (used for inodes and directories)."""
        cached = self._block_cache.get(index)
        if cached is not None:
            self._block_cache.move_to_end(index)
            return cached
        data = next(self._read_blocks(index, 1))
        self._block_cache[index] = data
        if len(self._block_cache) > 64:
            self._block_cache.popitem(last=False)
        return data

    def _read_inodes(self) -> list[Inode]:
        """Superblock and inode table: docs/FORMAT.md, section 5."""
        superblock = self._read_block(0)
        inode_count = struct.unpack_from("<q", superblock, 0x30)[0]  # inode count
        self._superroot_ino = struct.unpack_from("<q", superblock, 0x48)[0]  # superroot inode
        per_block = self._block_size // INODE_SIZE
        if not 0 < inode_count <= min(MAX_INODES, per_block * (self.num_blocks - 1)):
            raise _corrupt(f"invalid inode count ({inode_count})")
        if not 0 <= self._superroot_ino < inode_count:
            raise _corrupt("invalid root directory")

        inodes: list[Inode] = []
        block = 1
        while len(inodes) < inode_count:
            data = self._read_block(block)
            for p in range(0, per_block * INODE_SIZE, INODE_SIZE):
                if len(inodes) == inode_count:
                    break
                mode, _nlink, flags, size = struct.unpack_from("<HHIq", data, p)  # mode, flags, size
                blocks, loc = struct.unpack_from("<II", data, p + 0x60)  # number of blocks, first block
                inodes.append(Inode(mode, flags, size, blocks, loc))
            block += 1
        return inodes

    def _read_dir(self, inode: Inode) -> list[tuple[str, int, int]]:
        """Lists (name, inode number, type) of a directory, without '.' and '..'.
        Directory entry layout: docs/FORMAT.md, section 5."""
        result = []
        for b in range(inode.loc, inode.loc + inode.blocks):
            data = self._read_block(b)
            pos = 0
            while pos + 16 <= len(data):
                ino, typ, namelen, entsize = struct.unpack_from("<iiii", data, pos)
                if ino == 0 or entsize <= 0:
                    break
                if entsize < 16 or pos + entsize > len(data) or not 0 <= namelen <= entsize - 16:
                    raise _corrupt("invalid directory entry")
                if typ in (PFS_FILE, PFS_DIR):
                    if not 0 <= ino < len(self._inodes):
                        raise _corrupt("directory entry points to an invalid inode")
                    name = data[pos + 16 : pos + 16 + namelen].decode("utf-8", "replace")
                    result.append((name, ino, typ))
                pos += entsize
        return result

    def _build_tree(self) -> Node:
        self._inodes = self._read_inodes()
        root = Node("", True)

        superroot = self._read_dir(self._inodes[self._superroot_ino])
        uroot = next((ino for name, ino, typ in superroot if name == "uroot" and typ == PFS_DIR), None)
        if uroot is None:
            raise PkgError(_("'uroot' directory not found in the PFS"))

        visited: set[int] = set()

        def fill(node: Node, dir_ino: int, depth: int) -> None:
            if dir_ino in visited:  # protection against loops in a malformed PFS
                return
            if depth > MAX_DIR_DEPTH:
                raise _corrupt("folders nested too deeply")
            visited.add(dir_ino)
            for name, ino, typ in self._read_dir(self._inodes[dir_ino]):
                check_name(name)
                if typ == PFS_DIR:
                    child = node.children.get(name) or Node(name, True, node)
                    node.children[name] = child
                    fill(child, ino, depth + 1)
                else:
                    size = self._inodes[ino].size
                    if size < 0:
                        raise _corrupt(f"negative size for {name!r}")
                    node.children[name] = Node(name, False, node, size, inode=ino)

        fill(root, uroot, 0)

        # Entries from the PKG table go into sce_sys/, as in the original extractor. If the PFS
        # already has a file with the same name, the PFS one wins (the original overwrites it).
        for entry in self.entries:
            parent = root
            *dirs, filename = ("sce_sys/" + entry.name).split("/")
            for d in dirs:
                if d not in parent.children:
                    parent.children[d] = Node(d, True, parent)
                parent = parent.children[d]
            if filename not in parent.children:
                parent.children[filename] = Node(filename, False, parent, entry.size, entry=entry)

        self._sort(root)
        return root

    @staticmethod
    def _sort(node: Node) -> None:
        node.children = dict(
            sorted(node.children.items(), key=lambda kv: (not kv[1].is_dir, kv[0].lower()))
        )
        for child in node.children.values():
            if child.is_dir:
                Pkg._sort(child)

    # ---- extraction ----

    def iter_file(self, node: Node) -> Iterator[bytes]:
        """Yields a file's content in chunks."""
        if node.is_dir:
            raise PkgError(_("'{path}' is a directory").format(path=node.path))
        if node.entry is not None:
            yield from self.iter_entry(node.entry)
            return
        inode = self._inodes[node.inode]
        remaining = inode.size
        for block in self._read_blocks(inode.loc, inode.blocks):
            if remaining <= 0:
                break
            chunk = block[: min(remaining, len(block))]
            remaining -= len(chunk)
            yield chunk

    def read_file(self, node: Node) -> bytes:
        return b"".join(self.iter_file(node))

    def extract(
        self,
        node: Node,
        dest_dir: str | os.PathLike,
        progress: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> str:
        """Extracts `node` (file or folder) into `dest_dir`. Returns the path created.
        The root is extracted directly into `dest_dir`."""
        dest_dir = os.fspath(dest_dir)
        target = _safe_target(dest_dir, node.name) if node.parent else dest_dir
        if node.is_dir:
            os.makedirs(target, exist_ok=True)
            for child in node.children.values():
                self.extract(child, target, progress, cancel)
            return target

        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        # O_NOFOLLOW: do not write through a symbolic link already present in the destination.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        out = os.fdopen(os.open(target, flags, 0o644), "wb")  # outside the try: if it fails, there is nothing to delete
        try:
            with out:
                for chunk in self.iter_file(node):
                    if cancel is not None and cancel.is_set():
                        raise ExtractionCancelled()
                    out.write(chunk)
                    if progress is not None:
                        progress(len(chunk))
        except BaseException:
            # Do not leave a half-written file behind.
            try:
                os.remove(target)
            except OSError:
                pass
            raise
        return target

    def find(self, path: str) -> Node | None:
        node = self.root
        for part in filter(None, path.strip("/").split("/")):
            node = node.children.get(part)
            if node is None:
                return None
        return node

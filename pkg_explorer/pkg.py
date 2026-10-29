# SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project
# SPDX-License-Identifier: GPL-2.0-or-later
# Adaptado de src/core/file_format/pkg.cpp, pkg.h e pfs.h do ShadPs4Plus / shadPS4.
#
# Diferente do original, que extrai o pacote inteiro, aqui a árvore de arquivos é montada uma
# vez e cada arquivo é lido sob demanda, decifrando/descomprimindo só os blocos dele.

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

# Quantos blocos internos (0x10000 cada) ler do disco de uma vez ao extrair.
READ_BATCH_BLOCKS = 64

ProgressCallback = Callable[[int], None]  # recebe bytes escritos desde a última chamada


class PkgError(Exception):
    pass


class ExtractionCancelled(Exception):
    pass


@dataclass
class PkgEntry:
    id: int
    name: str
    offset: int
    size: int
    raw: bytes  # os 32 bytes da entrada como estão no arquivo (usados para derivar chaves)


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
    inode: int | None = None  # arquivo dentro do PFS
    entry: PkgEntry | None = None  # arquivo da tabela de entradas do PKG (sce_sys)
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
        except Exception:
            self._file.close()
            raise

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Pkg:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- leitura bruta ----

    def _read(self, offset: int, size: int) -> bytes:
        with self._lock:
            self._file.seek(offset)
            data = self._file.read(size)
        if len(data) != size:
            raise PkgError(f"leitura fora do arquivo (offset 0x{offset:X}, tamanho 0x{size:X})")
        return data

    # ---- cabeçalho e entradas ----

    def _parse_header(self) -> None:
        header = self._read(0, 0x1000)
        if struct.unpack_from(">I", header, 0)[0] != PKG_MAGIC:
            raise PkgError("não parece ser um arquivo PKG válido")

        self.file_size = os.path.getsize(self.path)
        self.entry_count = struct.unpack_from(">I", header, 0x10)[0]
        self.entry_table_offset = struct.unpack_from(">I", header, 0x18)[0]
        content_offset, content_size = struct.unpack_from(">QQ", header, 0x30)
        self.content_id = header[0x40:0x64].rstrip(b"\x00").decode("ascii", "replace")
        self.title_id = header[0x47:0x50].decode("ascii", "replace")
        self.content_flags = struct.unpack_from(">I", header, 0x78)[0]
        self.pfs_image_offset, self.pfs_image_size = struct.unpack_from(">QQ", header, 0x410)
        self.pkg_size = struct.unpack_from(">Q", header, 0x430)[0]

        if self.pkg_size > self.file_size:
            raise PkgError(
                "o tamanho do PKG é diferente do esperado (arquivo incompleto ou corrompido?)"
            )
        if content_offset + content_size > self.pkg_size:
            raise PkgError("o conteúdo é maior que o PKG")

        self.flags = [name for bit, name in CONTENT_FLAGS if self.content_flags & bit]

    def _parse_entries(self) -> None:
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
            self.sfo = parse_sfo(self._read(sfo_entry.offset, sfo_entry.size))

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
        """'patch', 'dlc' ou 'game', com a mesma regra do extrator original."""
        if any("PATCH" in f for f in self.flags):
            return "patch"
        if self.category == "ac":
            return "dlc"
        return "game"

    @property
    def output_folder_name(self) -> str:
        """Pasta usada pelo extrator original / shadPS4: TITLEID, TITLEID-patch ou TITLEID/DLC."""
        if self.kind == "patch":
            return f"{self.title_id}-patch"
        if self.kind == "dlc":
            parts = str(self.sfo.get("CONTENT_ID", self.content_id)).split("-")
            if len(parts) > 2:
                return os.path.join(self.title_id, parts[2])
        return self.title_id

    # ---- chaves ----

    def _derive_keys(self) -> None:
        keys_entry = self._entry_by_id(0x10)
        image_entry = self._entry_by_id(0x20)
        if keys_entry is None or image_entry is None:
            raise PkgError("o PKG não tem as entradas de chave (ENTRY_KEYS / IMAGE_KEY)")

        # ENTRY_KEYS: seed_digest[32], digest1[7][32], key1[7][256]; a DK3 é key1[3].
        key1_3 = self._read(keys_entry.offset + 32 + 7 * 32 + 3 * 256, 256)
        try:
            self._dk3 = crypto.rsa2048_decrypt(key1_3, is_dk3=True)
            iv_key = crypto.iv_key_hash256(image_entry.raw + self._dk3)
            img_key = crypto.aes_cbc_decrypt(iv_key, self._read(image_entry.offset, 256))
            ekpfs = crypto.rsa2048_decrypt(img_key, is_dk3=False)
        except ValueError as exc:
            raise PkgError(
                "não foi possível decifrar as chaves do PKG (só PKGs fake/fpkg são suportados)"
            ) from exc

        seed = self._read(self.pfs_image_offset + 0x370, 16)
        data_key, tweak_key = crypto.pfs_gen_crypto_key(ekpfs, seed)
        self._pfs = crypto.PfsDecryptor(data_key, tweak_key)

    def read_entry(self, entry: PkgEntry) -> bytes:
        if entry.id not in NP_ENTRY_IDS:
            return self._read(entry.offset, entry.size)
        # O AES-CBC trabalha em blocos de 16 bytes: o último bloco cifrado continua no arquivo
        # além de `size`. (O original lê memória fora do buffer aqui e erra os últimos bytes.)
        padded = self._read(entry.offset, entry.size + (-entry.size % 16))
        iv_key = crypto.iv_key_hash256(entry.raw + self._dk3)
        return crypto.aes_cbc_decrypt(iv_key, padded)[: entry.size]

    # ---- PFS ----

    def _read_pfs(self, offset: int, size: int) -> bytes:
        """Lê e decifra `size` bytes da imagem PFS externa a partir de `offset`."""
        start = offset & ~(crypto.XTS_SECTOR_SIZE - 1)
        end = -(-(offset + size) // crypto.XTS_SECTOR_SIZE) * crypto.XTS_SECTOR_SIZE
        data = self._read(self.pfs_image_offset + start, end - start)
        data = self._pfs.decrypt(data, start // crypto.XTS_SECTOR_SIZE)
        return data[offset - start : offset - start + size]

    def _open_pfs(self) -> None:
        for offset in range(0x20000, self.pfs_image_size, 0x10000):
            if self._read_pfs(offset, 16)[:4] == PFSC_MAGIC:
                self._pfsc_offset = offset
                break
        else:
            raise PkgError("imagem PFSC não encontrada dentro do PKG")

        hdr = self._read_pfs(self._pfsc_offset, 0x30)
        self._block_size, block_offsets, _data_start, data_length = struct.unpack_from(
            "<qqQq", hdr, 0x10
        )
        num_blocks = data_length // self._block_size
        sector_map = self._read_pfs(self._pfsc_offset + block_offsets, (num_blocks + 1) * 8)
        self._sector_map = struct.unpack(f"<{num_blocks + 1}Q", sector_map)
        self.num_blocks = num_blocks

    def _decode_block(self, raw: bytes) -> bytes:
        if len(raw) == self._block_size:
            return raw
        if len(raw) > self._block_size:
            raise PkgError("bloco PFSC maior que o esperado")
        data = zlib.decompress(raw)
        return data.ljust(self._block_size, b"\x00")

    def _read_blocks(self, first: int, count: int) -> Iterator[bytes]:
        """Gera os blocos internos `first`..`first+count-1`, já decifrados e descomprimidos."""
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
        """Um bloco interno, com cache pequeno (usado para inodes e diretórios)."""
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
        superblock = self._read_block(0)
        inode_count = struct.unpack_from("<q", superblock, 0x30)[0]
        self._superroot_ino = struct.unpack_from("<q", superblock, 0x48)[0]
        per_block = self._block_size // INODE_SIZE

        inodes: list[Inode] = []
        block = 1
        while len(inodes) < inode_count:
            data = self._read_block(block)
            for p in range(0, per_block * INODE_SIZE, INODE_SIZE):
                if len(inodes) == inode_count:
                    break
                mode, _nlink, flags, size = struct.unpack_from("<HHIq", data, p)
                blocks, loc = struct.unpack_from("<II", data, p + 0x60)
                inodes.append(Inode(mode, flags, size, blocks, loc))
            block += 1
        return inodes

    def _read_dir(self, inode: Inode) -> list[tuple[str, int, int]]:
        """Lista (nome, número do inode, tipo) de um diretório, sem '.' e '..'."""
        result = []
        for b in range(inode.loc, inode.loc + inode.blocks):
            data = self._read_block(b)
            pos = 0
            while pos + 16 <= len(data):
                ino, typ, namelen, entsize = struct.unpack_from("<iiii", data, pos)
                if ino == 0 or entsize <= 0:
                    break
                if typ in (PFS_FILE, PFS_DIR):
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
            raise PkgError("diretório 'uroot' não encontrado no PFS")

        visited: set[int] = set()

        def fill(node: Node, dir_ino: int) -> None:
            if dir_ino in visited:  # proteção contra laços em PFS malformado
                return
            visited.add(dir_ino)
            for name, ino, typ in self._read_dir(self._inodes[dir_ino]):
                if typ == PFS_DIR:
                    child = node.children.get(name) or Node(name, True, node)
                    node.children[name] = child
                    fill(child, ino)
                else:
                    node.children[name] = Node(name, False, node, self._inodes[ino].size, inode=ino)

        fill(root, uroot)

        # Entradas da tabela do PKG vão para sce_sys/, como no extrator original. Se o PFS já
        # tiver um arquivo com o mesmo nome, o do PFS prevalece (no original ele sobrescreve).
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

    # ---- extração ----

    def iter_file(self, node: Node) -> Iterator[bytes]:
        """Gera o conteúdo de um arquivo em pedaços."""
        if node.is_dir:
            raise PkgError(f"'{node.path}' é um diretório")
        if node.entry is not None:
            yield self.read_entry(node.entry)
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
        """Extrai `node` (arquivo ou pasta) para dentro de `dest_dir`. Retorna o caminho criado.
        A raiz é extraída diretamente em `dest_dir`."""
        target = os.path.join(os.fspath(dest_dir), node.name) if node.parent else os.fspath(dest_dir)
        if node.is_dir:
            os.makedirs(target, exist_ok=True)
            for child in node.children.values():
                self.extract(child, target, progress, cancel)
            return target

        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        try:
            with open(target, "wb") as out:
                for chunk in self.iter_file(node):
                    if cancel is not None and cancel.is_set():
                        raise ExtractionCancelled()
                    out.write(chunk)
                    if progress is not None:
                        progress(len(chunk))
        except BaseException:
            # Não deixa arquivo pela metade para trás.
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

# PS4 PKG format (as read by PKG Explorer)

This document describes the parts of the PS4 package format that PKG Explorer reads, in the
order the reader (`pkg_explorer/pkg.py`) processes them. It covers **fake PKGs** (fpkg), the ones
the shadPS4 extractor supports; retail PKGs use keys that are not public.

The reference implementation is shadPS4's `src/core/file_format/pkg.cpp`, `pkg.h`, `pfs.h` and
`src/core/crypto/`. The test generator `tests/pkgbuilder.py` does the exact reverse of what is
described here, so it is also a good executable reference.

```
PKG file
├── header (0x1000 bytes, big-endian)
├── entry table  → entries: keys, param.sfo, icons, license files… (shown under sce_sys/)
└── PFS image (outer)
    ├── plaintext header (contains the seed at +0x370)
    └── PFSC (AES-XTS encrypted from here on)
        ├── PFSC header + sector map
        └── inner blocks (0x10000 bytes each, zlib-compressed or stored)
            ├── block 0: superblock
            ├── blocks 1…: inode table
            └── directory and file data blocks
```

## 1. Header

Big-endian. Only the fields PKG Explorer uses are listed.

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0x000 | 4 | magic | `7F 43 4E 54` (`"\x7FCNT"`) |
| 0x010 | 4 | entry count | number of entries in the entry table |
| 0x018 | 4 | entry table offset | |
| 0x030 | 8 | content offset | |
| 0x038 | 8 | content size | `content offset + size` must fit in the package |
| 0x040 | 36 | content ID | e.g. `UP0002-CUSA13795_00-CRASHTEAMRACING1`; the Title ID is bytes 7–15 |
| 0x078 | 4 | content flags | patch flags decide whether the package is an update |
| 0x410 | 8 | PFS image offset | |
| 0x418 | 8 | PFS image size | |
| 0x430 | 8 | package size | must not be larger than the file |

**Package kind** (same rule as the original extractor): if any content flag name contains
`PATCH` it is an update; otherwise, if `param.sfo` has `CATEGORY = "ac"` it is a DLC; otherwise
it is a base game.

## 2. Entry table

`entry count` records of 32 bytes each, big-endian:

| Offset | Size | Field |
|---|---|---|
| 0x00 | 4 | id |
| 0x04 | 4 | filename offset (unused) |
| 0x08 | 4 | flags 1 |
| 0x0C | 4 | flags 2 |
| 0x10 | 4 | data offset |
| 0x14 | 4 | data size |
| 0x18 | 8 | padding |

The id determines the name (`pkg_explorer/entry_names.py`, generated from shadPS4's table): for
example `0x0010` = `entry_keys`, `0x0020` = `image_key`, `0x0400` = `license.dat`, `0x1000` =
`param.sfo`, `0x1200` = `icon0.png`. Unknown ids are named after their decimal number. PKG
Explorer shows every entry under `sce_sys/`, like the original extractor; when the PFS already has
a file with the same name, the PFS file wins.

## 3. Keys

All the keys needed to read a fake PKG come from two entries and two RSA-2048 key sets
(`pkg_explorer/keys.py`, from shadPS4's `keys.h`). Decryption is RSAES-PKCS1-v1_5.

1. **DK3**: entry `0x0010` (`entry_keys`) is `seed_digest[32]`, `digest1[7][32]`, `key1[7][256]`.
   `key1[3]` decrypted with the *PkgDerivedKey3* key set gives the 32-byte DK3.
2. **IV key** for an entry: `SHA-256(raw 32-byte table record of that entry ‖ DK3)`. The first
   16 bytes are the AES IV and the last 16 bytes the AES key.
3. **EKPFS**: the 256 bytes of entry `0x0020` (`image_key`) are AES-128-CBC-decrypted with the IV
   key of that entry, and the result is decrypted with the *Fake* RSA key set. The first 32
   bytes are the EKPFS.
4. **PFS keys**: read the 16-byte seed at `PFS image offset + 0x370` and compute
   `HMAC-SHA256(key = EKPFS, data = uint32_le(1) ‖ seed)`. Bytes 0–15 are the **tweak key** and
   bytes 16–31 the **data key**.

### Encrypted entries (NP)

Entries `0x0400`–`0x0403` (`license.dat`, `license.info`, `nptitle.dat`, `npbind.dat`) are
AES-128-CBC-encrypted with their own IV key (step 2). Their size is not always a multiple of 16:
the last cipher block continues in the file past `data size`, so it must be read from the file
(the original extractor reads past its buffer here and corrupts the last bytes; `npbind.dat` ends
with a SHA-1 of the rest of the file, which shows the difference).

## 4. PFS image

The outer PFS image starts at `PFS image offset`. Its first part is plaintext (the seed lives
there); from the PFSC onwards it is encrypted with **AES-128-XTS**: data unit = 0x1000 bytes,
tweak = the sector number (`offset inside the PFS image / 0x1000`, little-endian, 16 bytes), key =
`data key ‖ tweak key`.

The PFSC is found by scanning the PFS image from 0x20000 in steps of 0x10000, decrypting the
first bytes of each candidate and looking for the magic `PFSC`.

### PFSC header

Little-endian, at the start of the PFSC:

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0x00 | 4 | magic | `PFSC` |
| 0x10 | 8 | block size | always 0x10000 |
| 0x18 | 8 | sector map offset | relative to the PFSC start |
| 0x20 | 8 | data start | |
| 0x28 | 8 | data length | uncompressed size; `/ block size` = number of inner blocks |

The **sector map** has `blocks + 1` little-endian `uint64` offsets (relative to the PFSC start).
Inner block `i` is stored at `[map[i], map[i+1])`: if that is exactly 0x10000 bytes the block is
stored as is, otherwise it is zlib-compressed and inflates to at most 0x10000 bytes.

## 5. Inner file system

The inner blocks form a PFS file system with 0x10000-byte blocks.

**Superblock** (block 0, little-endian):

| Offset | Size | Field |
|---|---|---|
| 0x30 | 8 | inode count |
| 0x48 | 8 | superroot inode number |

**Inodes** start at block 1, 0xA8 bytes each, `0x10000 / 0xA8 = 390` per block (they do not cross
block boundaries). Inode number *n* is the *n*-th inode.

| Offset | Size | Field |
|---|---|---|
| 0x00 | 2 | mode (`0x4000` directory, `0x8000` file) |
| 0x04 | 4 | flags |
| 0x08 | 8 | size in bytes |
| 0x60 | 4 | number of blocks |
| 0x64 | 4 | first block (`loc`) |

A file's data is the contiguous inner blocks `loc … loc + blocks − 1`, truncated to `size`.

**Directories** are inodes whose blocks contain directory entries (little-endian), packed one after
another inside each block:

| Offset | Size | Field |
|---|---|---|
| 0x00 | 4 | inode number (`0` = end of entries in this block) |
| 0x04 | 4 | type: `2` file, `3` directory, `4` `.`, `5` `..` |
| 0x08 | 4 | name length |
| 0x0C | 4 | entry size (distance to the next entry) |
| 0x10 | … | name (UTF-8) |

The **superroot** directory contains `flat_path_table` (a file) and `uroot` (a directory). The
game's files are the contents of `uroot`.

## 6. What the reader validates

A malicious or broken PKG must produce a clear error, never a crash, a huge allocation or a
write outside the chosen folder. Besides the size checks mentioned above, `pkg.py` checks that:

- file and folder names are not empty, `.` or `..`, and contain no `/`, `\` or NUL ("Zip Slip");
- extraction targets stay inside the destination folder, without following symbolic links;
- the PFS image fits in the file, the block size is 0x10000 and the sector map fits in the image
  and is non-decreasing;
- inode counts, the superroot, block ranges and directory entries are within bounds;
- folders are not nested more than 128 levels and directories do not loop;
- compressed blocks never inflate beyond 0x10000 bytes;
- entries are read in chunks, `param.sfo` and NP entries have size limits.

The limits are defined at the top of `pkg.py`.

## 7. Digests (integrity check)

The header stores SHA-256 digests that, together, cover the whole package. They are not used by
shadPS4; PKG Explorer checks them in `pkg_explorer/integrity.py`. The rules below were confirmed
against real fake PKGs (game, update and DLCs); an all-zero field means "not filled in".

| Header offset | Digest of |
|---|---|
| 0x020 / 0x028 | (body offset / body size, 8 bytes each: the region holding the entry table and all entries) |
| 0x140 | the `digests` entry (`0x0001`) |
| 0x160 | the body: `[body offset, + body size)` |
| 0x438 | (PFS signed size, 4 bytes) |
| 0x440 | the whole PFS image: `[PFS image offset, + PFS image size)` |
| 0x460 | the start of the PFS image: its first `PFS signed size` bytes |
| 0xFE0 | the header itself, bytes `0x000..0xFE0` |

The `digests` entry holds one SHA-256 per entry, in entry table order (its own slot is zero). An
entry is hashed as stored in the file (encrypted entries are not decrypted); entries with bit 31
of `flags 1` set (encrypted) are hashed with their size rounded up to 16 bytes, the padding of
the last AES block.

The fields at 0x100 and 0x120 also look like digests but their input is unknown; they are not
checked. The body ends where the PFS image starts, so the header, body and PFS image digests
leave nothing out except the rest of the 0x1000-byte header area (0xFE0–0x1000 holds the header
digest itself).

The **quick check** (run when a PKG is opened) checks everything except the whole PFS image, so
it is instant. The **full check** also hashes the PFS image, which means reading the whole file.

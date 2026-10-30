# SPDX-License-Identifier: GPL-2.0-or-later
# Leitor de param.sfo (formato PSF), baseado em src/core/file_format/psf.cpp do shadPS4.

import struct

PSF_MAGIC = b"\x00PSF"
FMT_UTF8_SPECIAL = 0x0004
FMT_UTF8 = 0x0204
FMT_INT32 = 0x0404


def parse_sfo(data: bytes) -> dict[str, str | int | bytes]:
    if data[:4] != PSF_MAGIC:
        raise ValueError("invalid param.sfo")
    key_table, data_table, count = struct.unpack_from("<III", data, 8)
    values: dict[str, str | int | bytes] = {}
    for i in range(count):
        key_off, fmt, length, _max_len, data_off = struct.unpack_from("<HHIII", data, 0x14 + i * 16)
        key_start = key_table + key_off
        key = data[key_start : data.index(0, key_start)].decode("utf-8", "replace")
        raw = data[data_table + data_off : data_table + data_off + length]
        if fmt == FMT_INT32:
            values[key] = struct.unpack("<I", raw[:4])[0]
        elif fmt == FMT_UTF8:
            values[key] = raw.rstrip(b"\x00").decode("utf-8", "replace")
        else:
            values[key] = raw
    return values

"""UF2 parsing and the checks made before a file is copied to an RPI-RP2 disk."""

from __future__ import annotations

import hashlib
import re
import struct

MAGIC0, MAGIC1, MAGIC_END = 0x0A324655, 0x9E5D5157, 0x0AB16F30
BLOCK = 512
PAYLOAD_MAX = 476
FLAG_NOT_MAIN_FLASH = 0x00000001
FLAG_FAMILY_ID = 0x00002000
RP2040_FAMILY = 0xE48BFF56
XIP_BASE = 0x10000000
MARKER_RE = re.compile(rb"FM1T-BOARD:([a-z0-9_]+);FM1T-BUILD:([A-Za-z0-9_.+-]*);")


class UF2Error(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse(data: bytes, name="UF2"):
    """Every block checked: magics, numbering, payload size, family RP2040,
    main flash only, consecutive addresses. Returns (blocks, flat, base)."""
    if len(data) < BLOCK or len(data) % BLOCK:
        raise UF2Error("%s: size %d is not a positive multiple of 512" % (name, len(data)))
    n = len(data) // BLOCK
    blocks, flat, base, nxt = [], bytearray(), None, None
    for i in range(n):
        blk = data[i * BLOCK:(i + 1) * BLOCK]
        m0, m1, flags, addr, size, num, total, fam = struct.unpack_from("<8I", blk, 0)
        (mend,) = struct.unpack_from("<I", blk, 508)
        where = "%s block %d" % (name, i)
        if m0 != MAGIC0 or m1 != MAGIC1 or mend != MAGIC_END:
            raise UF2Error("%s: bad magic" % where)
        if flags & FLAG_NOT_MAIN_FLASH:
            raise UF2Error("%s: not a main-flash block" % where)
        if not flags & FLAG_FAMILY_ID or fam != RP2040_FAMILY:
            raise UF2Error("%s: family 0x%08X is not RP2040 (0x%08X)" % (where, fam, RP2040_FAMILY))
        if not 0 < size <= PAYLOAD_MAX:
            raise UF2Error("%s: payload size %d" % (where, size))
        if num != i or total != n:
            raise UF2Error("%s says it is block %d of %d" % (where, num, total))
        if nxt is not None and addr != nxt:
            raise UF2Error("%s: address 0x%08X does not follow 0x%08X" % (where, addr, nxt))
        if base is None:
            base = addr
        nxt = addr + size
        blocks.append((blk, size, addr))
        flat += blk[32:32 + size]
    return blocks, bytes(flat), base


def info(data: bytes, name="UF2") -> dict:
    blocks, flat, base = parse(data, name)
    markers = MARKER_RE.findall(flat)
    return {
        "size": len(data),
        "blocks": len(blocks),
        "base": base,
        "end": base + len(flat),
        "image_bytes": len(flat),
        "sha256": sha256(data),
        "boards": [m[0].decode() for m in markers],
        "builds": [m[1].decode() for m in markers],
    }


def check_for_board(data: bytes, profile, name="UF2") -> dict:
    """Refuses a UF2 that is not an RP2040 flash image built for `profile`:
    it must start at the flash base, fit the profile's build flash size and
    carry exactly one FM1T-BOARD marker naming that board."""
    i = info(data, name)
    if i["base"] != XIP_BASE:
        raise UF2Error("%s starts at 0x%08X, not at the RP2040 flash base 0x%08X" % (name, i["base"], XIP_BASE))
    limit = XIP_BASE + int(profile["flash"]["build_bytes"])
    if i["end"] > limit:
        raise UF2Error("%s ends at 0x%08X, beyond the %d bytes this board is built for"
                       % (name, i["end"], profile["flash"]["build_bytes"]))
    if not i["boards"]:
        raise UF2Error("%s carries no FM1T-BOARD marker: it is not a multiboard FM-1 Transporter "
                       "build (an upstream XIAO-only image, or another program)" % name)
    if len(i["boards"]) != 1:
        raise UF2Error("%s carries %d board markers (%s); refusing" % (name, len(i["boards"]), ", ".join(i["boards"])))
    if i["boards"][0] != profile.id:
        raise UF2Error("%s was built for board %s, not %s; refusing" % (name, i["boards"][0], profile.id))
    return i


def splice(uf2: bytes, pattern: bytes, replacement: bytes, name="UF2") -> bytes:
    """Replace the single occurrence of `pattern` in the flat image with
    `replacement` (same length) and re-emit the same blocks."""
    if not pattern or len(pattern) != len(replacement):
        raise UF2Error("pattern and replacement must be non-empty and the same length")
    blocks, flat, _ = parse(uf2, name)
    at = flat.find(pattern)
    if at < 0:
        raise UF2Error("%s does not contain the placeholder" % name)
    if flat.find(pattern, at + 1) >= 0:
        raise UF2Error("%s contains the placeholder more than once" % name)
    flat = flat[:at] + replacement + flat[at + len(pattern):]
    out, pos = bytearray(), 0
    for blk, size, _addr in blocks:
        out += blk[:32] + flat[pos:pos + size] + blk[32 + size:]
        pos += size
    out = bytes(out)
    parse(out, name)
    return out

"""UF2 checks and flash geometry: what may be copied to which board."""

import json
import struct

import pytest

from fm1_board_tool import uf2, profiles as P
from fm1_board_tool.flash import check_uf2_file, FlashError
from fm1_board_tool.sim import make_uf2


@pytest.fixture
def dolla():
    return P.get_profile("dollatek_rp2040_typec")


def test_marker_and_family(dolla):
    data = make_uf2("dollatek_rp2040_typec", "abc123")
    i = uf2.check_for_board(data, dolla)
    assert i["boards"] == ["dollatek_rp2040_typec"] and i["builds"] == ["abc123"]


def test_wrong_board_refused(dolla):
    with pytest.raises(uf2.UF2Error, match="built for board xiao_rp2040"):
        uf2.check_for_board(make_uf2("xiao_rp2040"), dolla)


def test_unmarked_image_refused(dolla):
    data = bytearray(make_uf2("dollatek_rp2040_typec"))
    i = data.find(b"FM1T-BOARD")
    data[i:i + 10] = b"XXXXXXXXXX"
    with pytest.raises(uf2.UF2Error, match="no FM1T-BOARD marker"):
        uf2.check_for_board(bytes(data), dolla)


def reblock(data, **fields):
    out = bytearray(data)
    for k in range(len(out) // 512):
        m0, m1, flags, addr, size, num, total, fam = struct.unpack_from("<8I", out, k * 512)
        vals = dict(flags=flags, addr=addr, family=fam)
        vals.update({x: (v(k) if callable(v) else v) for x, v in fields.items()})
        struct.pack_into("<8I", out, k * 512, m0, m1, vals["flags"], vals["addr"], size, num, total,
                         vals["family"])
    return bytes(out)


def test_rp2350_family_refused(dolla):
    with pytest.raises(uf2.UF2Error, match="not RP2040"):
        uf2.check_for_board(reblock(make_uf2("dollatek_rp2040_typec"), family=0xE48BFF59), dolla)


def test_image_beyond_build_flash_refused(dolla):
    data = reblock(make_uf2("dollatek_rp2040_typec"), addr=lambda k: 0x10200000 - 1024 + k * 256)
    with pytest.raises(uf2.UF2Error):
        uf2.check_for_board(data, dolla)


def test_image_not_at_flash_base_refused(dolla):
    data = reblock(make_uf2("dollatek_rp2040_typec"), addr=lambda k: 0x20000000 + k * 256)
    with pytest.raises(uf2.UF2Error, match="flash base"):
        uf2.check_for_board(data, dolla)


def test_gap_and_truncation_refused():
    data = make_uf2("pico_rp2040")
    with pytest.raises(uf2.UF2Error, match="does not follow"):
        uf2.parse(reblock(data, addr=lambda k: 0x10000000 + k * 512))
    with pytest.raises(uf2.UF2Error, match="multiple of 512"):
        uf2.parse(data[:-3])
    with pytest.raises(uf2.UF2Error, match="block"):
        uf2.parse(data[:-512])


def test_splice_round_trip():
    pat = bytes(range(64)) * 2
    rep = bytes(reversed(pat))
    base = make_uf2("pico_rp2040", size=4096)
    blocks, flat, _ = uf2.parse(base)
    flat = flat[:1000] + pat + flat[1000 + len(pat):]          # straddles a block boundary
    img, pos = bytearray(), 0
    for blk, size, _ in blocks:
        img += blk[:32] + flat[pos:pos + size] + blk[32 + size:]
        pos += size
    sp = uf2.splice(bytes(img), pat, rep)
    assert len(sp) == len(img)
    assert uf2.parse(sp)[1] == flat[:1000] + rep + flat[1000 + len(rep):]
    with pytest.raises(uf2.UF2Error, match="does not contain"):
        uf2.splice(sp, pat, rep)
    with pytest.raises(uf2.UF2Error, match="same length"):
        uf2.splice(bytes(img), pat, rep[:-1])


@pytest.mark.parametrize("board", ["xiao_rp2040", "dollatek_rp2040_typec", "pico_rp2040"])
def test_shipped_images(root, board):
    """dist/ images: right board, inside the linked flash size, recorded hashes,
    the placeholder exactly once in the placeholder image, never in the other."""
    p = P.get_profile(board)
    d = root / "dist" / board
    pattern = (root / "dist" / "placeholder_loader.pattern").read_bytes()
    for kind in ("placeholder", "noloader"):
        man = json.loads((d / ("manifest-%s.json" % kind)).read_text())
        data = (d / man["files"]["uf2"]["file"]).read_bytes()
        assert uf2.sha256(data) == man["files"]["uf2"]["sha256"]
        i = uf2.check_for_board(data, p)
        assert i["end"] <= 0x10000000 + p["flash"]["build_bytes"]
        assert man["status"] == "BUILD VERIFIED" and man["board"] == board
        flat = uf2.parse(data)[1]
        assert flat.count(pattern) == (1 if kind == "placeholder" else 0)
    assert len(json.loads((d / "manifest-placeholder.json").read_text())["spliced_uf2_sha256"]) == 64


def test_flash_refuses_placeholder_image(root):
    p = P.get_profile("dollatek_rp2040_typec")
    ph = root / "dist/dollatek_rp2040_typec/fm1_transporter_dollatek_rp2040-placeholder.uf2"
    with pytest.raises(FlashError, match="PLACEHOLDER"):
        check_uf2_file(ph, p)
    nl = root / "dist/dollatek_rp2040_typec/fm1_transporter_dollatek_rp2040-noloader.uf2"
    check_uf2_file(nl, p)

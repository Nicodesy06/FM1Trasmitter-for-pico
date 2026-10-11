"""RPI-RP2 detection: by contents, never by drive letter; nothing but a .uf2 copy."""

from pathlib import Path

import pytest

from fm1_board_tool import volumes as V

GOOD = "UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n"


def mk(tmp_path, name, info=GOOD, index=True):
    d = tmp_path / name
    d.mkdir()
    if info is not None:
        (d / "INFO_UF2.TXT").write_text(info)
    if index:
        (d / "INDEX.HTM").write_text("<html/>")
    return d


def test_genuine_volume(tmp_path):
    d = mk(tmp_path, "E")
    v = V.check_volume(d, max_bytes=None)
    assert v and v.info["Board-ID"] == "RPI-RP2" and v.bootloader == "v3.0"


@pytest.mark.parametrize("info, index", [
    (None, True),                                                          # a USB stick
    ("UF2 Bootloader v1.0\nModel: Adafruit Feather M0\nBoard-ID: SAMD21G18A\n", True),
    ("UF2 Bootloader v1.0\nModel: Raspberry Pi RP2\nBoard-ID: RP2350\n", True),  # RP2350 boot ROM
    (GOOD, False),
])
def test_lookalikes_rejected(tmp_path, info, index):
    assert V.check_volume(mk(tmp_path, "X", info, index), max_bytes=None) is None


def test_label_required_when_asked(tmp_path):
    d = mk(tmp_path, "F")
    assert V.check_volume(d, label="KINGSTON", require_label=True, max_bytes=None) is None
    assert V.check_volume(d, label="RPI-RP2", require_label=True, max_bytes=None)


def test_big_disk_rejected(tmp_path):
    # tmp_path lives on a large file system: the 256 MB heuristic rejects it
    assert V.check_volume(mk(tmp_path, "G")) is None


def test_find_dedups_and_filters(tmp_path):
    good, bad = mk(tmp_path, "A"), mk(tmp_path, "B", info=None)
    out = V.find([(good, "RPI-RP2"), (good, "RPI-RP2"), (bad, None)], max_bytes=None)
    assert [v.path for v in out] == [good]


def test_copy_only_uf2(tmp_path):
    vol = V.check_volume(mk(tmp_path, "H"), max_bytes=None)
    src = tmp_path / "fw.bin"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        V.copy_uf2(src, vol)
    before = sorted(p.name for p in vol.path.iterdir())
    u = tmp_path / "fw.uf2"
    u.write_bytes(b"y" * 512)
    V.copy_uf2(u, vol)
    assert sorted(p.name for p in vol.path.iterdir()) == sorted(before + ["fw.uf2"])

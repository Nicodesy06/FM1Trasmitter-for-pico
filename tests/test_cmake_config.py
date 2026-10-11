"""CMake configuration per board profile (needs cmake and a pico-sdk; skipped
otherwise). Configure only: builds are covered by scripts/build.ps1,
fm1-board-tool build and the dist/ manifests."""

import os
import re
import shutil
import subprocess

import pytest

from conftest import sdk_path

SDK = sdk_path()
CMAKE = shutil.which("cmake")
pytestmark = pytest.mark.skipif(not (SDK and CMAKE), reason="cmake / pico-sdk not available")


def configure(root, tmp_path, *defs):
    b = tmp_path / "b"
    env = dict(os.environ, PICO_SDK_PATH=SDK)
    gen = ["-G", "Ninja"] if shutil.which("ninja") else []
    p = subprocess.run([CMAKE, "-S", str(root), "-B", str(b), *gen, "-DPICO_SDK_PATH=" + SDK,
                        "-DFM1T_LOADER_BIN=" + str(tmp_path / "none.bin"), *defs],
                       capture_output=True, text=True, env=env, timeout=600)
    return p, b


def cfg(b):
    return (b / "generated" / "fm1t_board_config.h").read_text()


def cache(b, key):
    m = re.search(r"^%s:\w+=(.*)$" % key, (b / "CMakeCache.txt").read_text(), re.M)
    return m.group(1) if m else None


@pytest.mark.parametrize("board, pico_board, leds, binary", [
    ("xiao_rp2040", "seeed_xiao_rp2040", 2, "default"),
    ("dollatek_rp2040_typec", "fm1t_dollatek_rp2040_typec", 1, "copy_to_ram"),
    ("pico_rp2040", "pico", 1, "default"),
])
def test_board_configures(root, tmp_path, board, pico_board, leds, binary):
    p, b = configure(root, tmp_path, "-DFM1T_BOARD=" + board)
    assert p.returncode == 0, p.stdout + p.stderr
    assert cache(b, "PICO_BOARD") == pico_board
    h = cfg(b)
    assert '#define FM1T_BOARD_ID "%s"' % board in h
    assert "#define FM1T_PIN_DP 0" in h and "#define FM1T_PIN_DM 1" in h
    assert "#define FM1T_LED_KIND %d" % leds in h
    assert '#define FM1T_BINARY_TYPE "%s"' % binary in h
    ninja = (b / "build.ninja").read_text() if (b / "build.ninja").exists() else ""
    if binary == "copy_to_ram" and ninja:
        assert "memmap_copy_to_ram.ld" in ninja
    if board == "xiao_rp2040":
        for c, pin in (("RED", 17), ("GREEN", 16), ("BLUE", 25)):
            assert "#define FM1T_LED_%s_PIN %d\n#define FM1T_LED_%s_ACTIVE_LOW 1" % (c, pin, c) in h
    if board == "dollatek_rp2040_typec":
        assert "#define FM1T_LED_MONO_PIN 25\n#define FM1T_LED_MONO_ACTIVE_LOW 0" in h
        mask = re.search(r"#define FM1T_RESERVED_MASK (0x[0-9A-Fa-f]+)u", h).group(1)
        assert int(mask, 16) == (1 << 23) | (1 << 24)
        assert "Pico-PIO-USB patched with" in p.stdout


def test_leds_off(root, tmp_path):
    p, b = configure(root, tmp_path, "-DFM1T_BOARD=dollatek_rp2040_typec", "-DFM1T_LEDS=OFF")
    assert p.returncode == 0
    assert "#define FM1T_LED_KIND 0" in cfg(b) and "LED_MONO_PIN" not in cfg(b)


def test_upstream_pico_board_argument_still_works(root, tmp_path):
    p, b = configure(root, tmp_path, "-DPICO_BOARD=seeed_xiao_rp2040")
    assert p.returncode == 0
    assert '#define FM1T_BOARD_ID "xiao_rp2040"' in cfg(b)


def test_default_is_the_reference_board(root, tmp_path):
    p, b = configure(root, tmp_path)
    assert p.returncode == 0
    assert '#define FM1T_BOARD_ID "xiao_rp2040"' in cfg(b)


@pytest.mark.parametrize("defs, message", [
    (["-DFM1T_BOARD=generic_rp2040"], "disabled until its pinout is"),
    (["-DFM1T_BOARD=nonesuch"], "unknown board"),
    (["-DPICO_BOARD=adafruit_qtpy_rp2040"], "no board profile uses"),
    (["-DFM1T_BOARD=dollatek_rp2040_typec", "-DPICO_BOARD=pico"], "conflicts with profile"),
])
def test_refusals(root, tmp_path, defs, message):
    p, b = configure(root, tmp_path, *defs)
    assert p.returncode != 0
    assert message in re.sub(r"\s+", " ", p.stderr + p.stdout)


def test_generic_allowed_explicitly(root, tmp_path):
    p, b = configure(root, tmp_path, "-DFM1T_BOARD=generic_rp2040", "-DFM1T_ALLOW_UNVALIDATED=ON")
    assert p.returncode == 0
    assert "#define FM1T_LED_KIND 0" in cfg(b)


def test_patch_applies_to_crlf_checkout(root, tmp_path):
    """A Windows checkout with core.autocrlf=true has CRLF sources and patch."""
    src = tmp_path / "src"
    src.mkdir()
    for f in (root / "lib/Pico-PIO-USB/src").iterdir():
        (src / f.name).write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    patch = tmp_path / "x.patch"
    patch.write_bytes((root / "lib/pico-pio-usb-bulk-multi-xact.patch").read_bytes().replace(b"\n", b"\r\n"))
    for force in ("OFF", "ON"):
        script = tmp_path / ("t%s.cmake" % force)
        out = tmp_path / ("out" + force)
        script.write_text('set(CMAKE_BINARY_DIR "%s")\nset(FM1T_FORCE_GIT_APPLY %s)\n'
                          'include("%s")\nfm1t_copy_and_patch("%s" "%s" "%s")\n'
                          % (tmp_path.as_posix(), force, (root / "cmake/fm1t_patch.cmake").as_posix(),
                             src.as_posix(), out.as_posix(), patch.as_posix()))
        p = subprocess.run([CMAKE, "-P", str(script)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        assert "PIO_USB_BULK_FRAME_BUDGET_US 700" in (out / "src/pio_usb_host.c").read_text()

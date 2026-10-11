"""Board profile parsing, GPIO validation and pin conflict detection."""

import copy
import json
import re

import pytest

from fm1_board_tool import profiles as P


@pytest.fixture(scope="module")
def all_profiles():
    return P.load_profiles()


def raw(board):
    return copy.deepcopy(P.load_profiles()[board].raw)


def test_all_shipped_profiles_valid(all_profiles):
    assert set(all_profiles) == {"xiao_rp2040", "dollatek_rp2040_typec", "pico_rp2040", "generic_rp2040"}
    for p in all_profiles.values():
        assert p.errors == [], (p.id, p.errors)


def test_xiao_profile_is_upstream_configuration(all_profiles):
    """The reference board must stay exactly what upstream a632d92 hard-codes."""
    x = all_profiles["xiao_rp2040"]
    assert (x.dp, x.dm) == (0, 1)
    assert x["pico_board"] == "seeed_xiao_rp2040" and x["pico_board_header"] == "sdk"
    assert x["binary_type"] == "default"
    leds = x["leds"]
    assert leds["kind"] == "rgb"
    assert {c: (leds[c]["gpio"], leds[c]["active_low"]) for c in ("red", "green", "blue")} == \
        {"red": (17, True), "green": (16, True), "blue": (25, True)}   # upstream LED_R/G/B, rgb() drives !x
    assert x["usb_pio"]["recovery_pio"] == 0 and x["usb_pio"]["host_pio"] == 1
    assert x.validation == "HARDWARE VERIFIED"


def test_dollatek_profile_is_conservative(all_profiles):
    d = all_profiles["dollatek_rp2040_typec"]
    assert d.enabled and d.validation == "BUILD VERIFIED"
    assert (d.dp, d.dm) == (0, 1)
    assert d["flash"]["declared_bytes"] == 16 << 20
    assert d["flash"]["build_bytes"] == 2 << 20          # not the unverified 16 MB
    assert d["flash"]["verified"] is False and d["flash"]["boot2"] == "generic_03h"
    assert d["binary_type"] == "copy_to_ram"
    assert d["leds"]["kind"] == "mono" and d["leds"]["verified"] is False
    assert d.led_pins() == {"mono": 25}
    assert 23 in d.reserved() and 24 in d.reserved()     # never driven


def test_generic_profile_disabled(all_profiles):
    g = all_profiles["generic_rp2040"]
    assert not g.enabled and g.validation == "UNVALIDATED"
    with pytest.raises(P.ProfileError, match="disabled until validated"):
        P.get_profile("generic_rp2040")
    assert P.get_profile("generic_rp2040", allow_disabled=True).id == "generic_rp2040"


def test_custom_headers_match_json(root):
    """boards/include/boards/*.h and the JSON must agree on what both state."""
    for board, header in (("dollatek_rp2040_typec", "fm1t_dollatek_rp2040_typec.h"),
                          ("generic_rp2040", "fm1t_generic_rp2040.h")):
        j = json.loads((root / "boards" / (board + ".json")).read_text())
        h = (root / "boards" / "include" / "boards" / header).read_text()
        m = re.search(r"#define PICO_FLASH_SIZE_BYTES \((\d+) \* 1024 \* 1024\)", h)
        assert int(m.group(1)) << 20 == j["flash"]["build_bytes"]
        assert "PICO_BOOT_STAGE2_CHOOSE_GENERIC_03H 1" in h and j["flash"]["boot2"] == "generic_03h"
        m = re.search(r"#define PICO_FLASH_SPI_CLKDIV (\d+)", h)
        assert int(m.group(1)) == j["flash"]["spi_clkdiv"]
        m = re.search(r"#define PICO_XOSC_STARTUP_DELAY_MULTIPLIER (\d+)", h)
        assert int(m.group(1)) == j["clock"]["xosc_startup_delay_multiplier"]
        assert "PICO_DEFAULT_UART" not in h            # GP0/GP1 belong to the FM-1
        led = re.search(r"#define PICO_DEFAULT_LED_PIN (\d+)", h)
        if j["leds"]["kind"] == "mono":
            assert int(led.group(1)) == j["leds"]["mono"]["gpio"]
        else:
            assert led is None


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["usb_pio"].update(dm=2), "must be D\\+ \\+ 1"),
    (lambda r: r["usb_pio"].update(dp=29, dm=30), "GPIO 0..29"),
    (lambda r: r["usb_pio"].update(pinout="DMDP"), "pinout must be DPDM"),
    (lambda r: r["usb_pio"].update(host_pio=0), "recovery_pio and host_pio"),
    (lambda r: r.update(soc="RP2350"), "RP2350"),
    (lambda r: r["clock"].update(sys_khz=125000), "120000"),
    (lambda r: r["clock"].update(xosc_hz=16000000), "xosc_hz"),
    (lambda r: r["leds"]["mono"].update(gpio=0), "collides with the FM-1 USB lines"),
    (lambda r: r["leds"]["mono"].update(gpio=23), "reserved"),
    (lambda r: r["leds"].update(kind="neopixel"), "leds.kind"),
    (lambda r: r["reserved_gpios"].update({"1": "button"}), "used for the FM-1 USB lines but reserved"),
    (lambda r: r["flash"].update(build_bytes=32 << 20), "exceeds declared"),
    (lambda r: r["flash"].update(declared_bytes=3 << 20), "power of two"),
    (lambda r: r["validation"].update(level="UNVALIDATED"), "must stay disabled"),
    (lambda r: r.update(uf2_name="dollatek.uf2"), "uf2_name"),
    (lambda r: r.update(binary_type="no_flash"), "binary_type"),
])
def test_validation_catches(mutate, message):
    r = raw("dollatek_rp2040_typec")
    mutate(r)
    errs = P.validate(r, "dollatek_rp2040_typec")
    assert any(re.search(message, e) for e in errs), errs


def test_rgb_led_sharing_a_pin_is_caught():
    r = raw("xiao_rp2040")
    r["leds"]["green"]["gpio"] = 17
    assert any("share GP17" in e for e in P.validate(r, "xiao_rp2040"))


def test_id_must_match_filename():
    assert any("does not match the file name" in e for e in P.validate(raw("pico_rp2040"), "pico"))


def test_duplicate_uf2_names_rejected(tmp_path, root):
    for f in ("pico_rp2040.json", "xiao_rp2040.json"):
        (tmp_path / f).write_text((root / "boards" / f).read_text())
    r = json.loads((tmp_path / "xiao_rp2040.json").read_text())
    r["uf2_name"] = "fm1_transporter_pico_rp2040.uf2"
    (tmp_path / "xiao_rp2040.json").write_text(json.dumps(r))
    with pytest.raises(P.ProfileError, match="UF2 name"):
        P.load_profiles(tmp_path)


@pytest.mark.parametrize("board, use, conflict", [
    ("dollatek_rp2040_typec", {0: "UART0 TX"}, "conflicts with FM-1 USB D\\+"),
    ("dollatek_rp2040_typec", {1: "I2C0 SCL"}, "conflicts with FM-1 USB D-"),
    ("dollatek_rp2040_typec", {25: "SPI CS"}, "status LED"),
    ("dollatek_rp2040_typec", {23: "NeoPixel"}, "board's unknown on this board"),
    ("pico_rp2040", {24: "button"}, "VBUS sense"),
    ("xiao_rp2040", {16: "I2C"}, "status LED \\(green\\)"),
    ("xiao_rp2040", {30: "x"}, "does not exist"),
])
def test_pin_conflicts(board, use, conflict):
    p = P.get_profile(board, allow_disabled=True)
    out = P.check_conflicts(p, use)
    assert any(re.search(conflict, x) for x in out), out


def test_free_pins_have_no_conflict():
    p = P.get_profile("dollatek_rp2040_typec")
    assert P.check_conflicts(p, {2: "spare", 3: "spare"}) == []


def test_wiring_rows_use_profile_labels():
    rows = P.get_profile("dollatek_rp2040_typec").wiring_rows()
    assert [s for _, s in rows] == ["D+", "D-", "GND"]
    assert rows[0][0].startswith("GP0") and rows[1][0].startswith("GP1")
    assert P.get_profile("xiao_rp2040").wiring_rows()[0][0] == "D6 (GP0)"

"""Board profiles: boards/<id>.json, shared with cmake/fm1t_board.cmake.

The validation here mirrors the CMake checks (tests/test_profiles.py runs
both on every profile) and adds the checks only a host can do cheaply:
unique UF2 names, header files present, pin conflict reports.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BOARDS_DIR = REPO_ROOT / "boards"

RP2040_GPIOS = range(0, 30)          # GPIO0..GPIO29; QSPI pins are not GPIOs
ROLES = ("reference", "primary", "secondary", "experimental")
LEVELS = ("HARDWARE VERIFIED", "BUILD VERIFIED", "SIMULATED", "UNVALIDATED")
BINARY_TYPES = ("default", "copy_to_ram")
LED_KINDS = {"none": (), "mono": ("mono",), "rgb": ("red", "green", "blue")}


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    raw: dict
    path: Path | None = None
    errors: list = field(default_factory=list)

    def __getitem__(self, k):
        return self.raw[k]

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def name(self) -> str:
        return self.raw["name"]

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled"))

    @property
    def dp(self) -> int:
        return int(self.raw["usb_pio"]["dp"])

    @property
    def dm(self) -> int:
        return int(self.raw["usb_pio"]["dm"])

    @property
    def uf2_name(self) -> str:
        return self.raw["uf2_name"]

    @property
    def validation(self) -> str:
        return self.raw["validation"]["level"]

    @property
    def labels(self) -> dict:
        return self.raw["usb_pio"].get("labels", {})

    def led_pins(self) -> dict:
        leds = self.raw.get("leds", {})
        return {c: int(leds[c]["gpio"]) for c in LED_KINDS.get(leds.get("kind", "none"), ())}

    def reserved(self) -> dict:
        return {int(k): v for k, v in self.raw.get("reserved_gpios", {}).items()}

    def pin_usage(self) -> dict:
        """GPIO -> what the Transporter firmware uses it for on this board."""
        use = {self.dp: "FM-1 USB D+ (USB_KEY clock, PIO USB host)",
               self.dm: "FM-1 USB D- (USB_KEY data, PIO USB host)"}
        for colour, pin in self.led_pins().items():
            use.setdefault(pin, "status LED (%s)" % colour)
        return use

    def wiring_rows(self):
        lab = self.labels
        return [(lab.get("dp", "GP%d" % self.dp), "D+"),
                (lab.get("dm", "GP%d" % self.dm), "D-"),
                (lab.get("gnd", "GND"), "GND")]


def validate(raw: dict, filename_id: str | None = None) -> list:
    """All problems with a profile, as human-readable strings (empty = valid)."""
    err = []

    def need(path, typ=None):
        cur = raw
        for k in path.split("."):
            if not isinstance(cur, dict) or k not in cur:
                err.append("missing %s" % path)
                return None
            cur = cur[k]
        if typ and not isinstance(cur, typ):
            err.append("%s must be %s" % (path, typ.__name__ if isinstance(typ, type) else typ))
            return None
        return cur

    if raw.get("schema") != 1:
        err.append("schema must be 1")
    pid = need("id", str)
    if pid is not None and not re.fullmatch(r"[a-z0-9_]+", pid):
        err.append("id must be lowercase [a-z0-9_]")
    if filename_id is not None and pid is not None and pid != filename_id:
        err.append("id %r does not match the file name %r" % (pid, filename_id))
    need("name", str)
    if need("role", str) not in ROLES:
        err.append("role must be one of %s" % ", ".join(ROLES))
    if not isinstance(raw.get("enabled"), bool):
        err.append("enabled must be true or false")
    if raw.get("soc") != "RP2040":
        err.append("soc must be RP2040 (RP2350 A2's E9 erratum breaks the D+ sensing)")
    uf2 = need("uf2_name", str)
    if uf2 is not None and not re.fullmatch(r"fm1_transporter_[a-z0-9_]+\.uf2", uf2):
        err.append("uf2_name must look like fm1_transporter_<name>.uf2")
    need("pico_board", str)
    if raw.get("pico_board_header") not in ("sdk", "boards/include"):
        err.append("pico_board_header must be sdk or boards/include")
    if raw.get("binary_type") not in BINARY_TYPES:
        err.append("binary_type must be one of %s" % ", ".join(BINARY_TYPES))
    if need("validation.level", str) not in LEVELS:
        err.append("validation.level must be one of %s" % ", ".join(LEVELS))
    if raw.get("enabled") and raw.get("validation", {}).get("level") == "UNVALIDATED":
        err.append("an UNVALIDATED profile must stay disabled")

    dp, dm = need("usb_pio.dp", int), need("usb_pio.dm", int)
    if dp is not None and dm is not None:
        if dp not in RP2040_GPIOS or dm not in RP2040_GPIOS:
            err.append("D+/D- must be GPIO 0..29")
        if dm != dp + 1:
            err.append("D- (GP%d) must be D+ + 1 (GP%d): Pico-PIO-USB DPDM pinout and the "
                       "usb_key program drive them as one consecutive pair" % (dm, dp + 1))
    if raw.get("usb_pio", {}).get("pinout") != "DPDM":
        err.append("usb_pio.pinout must be DPDM")
    rp, hp = need("usb_pio.recovery_pio", int), need("usb_pio.host_pio", int)
    if rp is not None and hp is not None and (rp == hp or {rp, hp} != {0, 1}):
        err.append("recovery_pio and host_pio must be 0 and 1 (Pico-PIO-USB takes a whole PIO)")
    if need("clock.sys_khz", int) not in (None, 120000):
        err.append("clock.sys_khz must be 120000 (validated USB_KEY and PIO USB timing)")
    if need("clock.xosc_hz", int) not in (None, 12000000):
        err.append("clock.xosc_hz must be 12000000")

    reserved = {}
    for k, v in raw.get("reserved_gpios", {}).items():
        try:
            g = int(k)
        except ValueError:
            err.append("reserved_gpios key %r is not a GPIO number" % k)
            continue
        if g not in RP2040_GPIOS:
            err.append("reserved GPIO %d is not an RP2040 GPIO" % g)
        reserved[g] = v
    usb = {dp, dm} - {None}
    for g in usb & set(reserved):
        err.append("GP%d is used for the FM-1 USB lines but reserved: %s" % (g, reserved[g]))

    leds = raw.get("leds", {})
    kind = leds.get("kind")
    if kind not in LED_KINDS:
        err.append("leds.kind must be none, mono or rgb")
    else:
        seen = {}
        for c in LED_KINDS[kind]:
            pin = leds.get(c, {}).get("gpio")
            if not isinstance(pin, int) or pin not in RP2040_GPIOS:
                err.append("leds.%s.gpio must be a GPIO 0..29" % c)
                continue
            if not isinstance(leds[c].get("active_low"), bool):
                err.append("leds.%s.active_low must be true or false" % c)
            if pin in usb:
                err.append("LED %s on GP%d collides with the FM-1 USB lines" % (c, pin))
            if pin in reserved:
                err.append("LED %s on GP%d, which is reserved: %s" % (c, pin, reserved[pin]))
            if pin in seen:
                err.append("LEDs %s and %s share GP%d" % (seen[pin], c, pin))
            seen[pin] = c
        if not isinstance(leds.get("verified"), bool):
            err.append("leds.verified must be true or false")

    db, bb = need("flash.declared_bytes", int), need("flash.build_bytes", int)
    if db is not None and bb is not None:
        if bb > db:
            err.append("flash.build_bytes exceeds declared_bytes")
        for n, what in ((db, "declared_bytes"), (bb, "build_bytes")):
            if n < 1 << 20 or n & (n - 1):
                err.append("flash.%s must be a power of two >= 1 MiB" % what)
    if not isinstance(raw.get("flash", {}).get("verified"), bool):
        err.append("flash.verified must be true or false")
    return err


def check_conflicts(profile: Profile, extra: dict) -> list:
    """Conflicts between the firmware's pin use and `extra` {gpio: purpose},
    e.g. a UART or I2C someone wants to add."""
    out = []
    use = profile.pin_usage()
    res = profile.reserved()
    for g, what in sorted(extra.items()):
        g = int(g)
        if g not in RP2040_GPIOS:
            out.append("GP%d does not exist on the RP2040" % g)
        if g in use:
            out.append("GP%d: %s conflicts with %s" % (g, what, use[g]))
        if g in res:
            out.append("GP%d: %s conflicts with the board's %s" % (g, what, res[g]))
    return out


def load_profile(path, strict=True) -> Profile:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ProfileError("%s: %s" % (path, e))
    p = Profile(raw=raw, path=path, errors=validate(raw, path.stem))
    if p.raw.get("pico_board_header") == "boards/include":
        hdr = path.parent / "include" / "boards" / ("%s.h" % raw.get("pico_board"))
        if not hdr.is_file():
            p.errors.append("board header %s is missing" % hdr)
    if strict and p.errors:
        raise ProfileError("%s: %s" % (path, "; ".join(p.errors)))
    return p


def load_profiles(boards_dir=None, strict=True) -> dict:
    d = Path(boards_dir) if boards_dir else BOARDS_DIR
    profiles = {}
    for f in sorted(d.glob("*.json")):
        p = load_profile(f, strict=strict)
        profiles[p.raw.get("id", f.stem)] = p
    names = {}
    for p in profiles.values():
        n = p.raw.get("uf2_name")
        if n in names:
            msg = "UF2 name %s used by both %s and %s" % (n, names[n], p.raw.get("id"))
            if strict:
                raise ProfileError(msg)
            p.errors.append(msg)
        names[n] = p.raw.get("id")
    return profiles


def get_profile(board_id: str, boards_dir=None, allow_disabled=False) -> Profile:
    profiles = load_profiles(boards_dir)
    if board_id not in profiles:
        raise ProfileError("unknown board %r; known: %s" % (board_id, ", ".join(sorted(profiles))))
    p = profiles[board_id]
    if not p.enabled and not allow_disabled:
        raise ProfileError("board %r is disabled until validated (%s); pass --allow-unvalidated "
                           "only after checking its pinout yourself" % (board_id, p.validation))
    return p

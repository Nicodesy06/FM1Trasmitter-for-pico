"""Putting a Transporter UF2 on a board, with every check done first.

1. The UF2 is parsed block by block and must be an RP2040 flash image built
   for the selected profile (one FM1T-BOARD marker naming it), not an
   unspliced placeholder image.
2. Exactly one RPI-RP2 boot disk must be identified by its contents (not by a
   drive letter). If there is none, exactly one RP2040 board must be on USB:
   a multiboard Transporter is asked to reboot into BOOTSEL (`bootsel`), any
   other RP2040 program gets the standard 1200-baud touch on its first CDC
   interface. With several candidates the tool stops and asks for --port.
3. After the copy, the boot disk must vanish and a Transporter data port must
   come back reporting the selected board and the UF2's build id.
Nothing is ever formatted or deleted; only the .uf2 file is copied.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import uf2 as uf2mod
from . import volumes
from .ports import discover, group_boards
from .transporter import Client

log = logging.getLogger("fm1_board_tool")

PLACEHOLDER_PATTERN = Path(__file__).resolve().parents[2] / "dist" / "placeholder_loader.pattern"


class FlashError(RuntimeError):
    pass


@dataclass
class FlashResult:
    steps: dict = field(default_factory=dict)       # step -> (ok, detail)
    ident: dict = field(default_factory=dict)
    data_port: str | None = None
    volume: str | None = None
    uf2_info: dict = field(default_factory=dict)

    def mark(self, step, ok, detail=""):
        self.steps[step] = (ok, detail)
        (log.info if ok else log.error)("%-22s %s  %s", step, "ok" if ok else "FAILED", detail)


def check_uf2_file(path: Path, profile, allow_placeholder=False) -> tuple:
    path = Path(path)
    if path.suffix.lower() != ".uf2":
        raise FlashError("%s is not a .uf2 file" % path)
    data = path.read_bytes()
    try:
        info = uf2mod.check_for_board(data, profile, path.name)
    except uf2mod.UF2Error as e:
        raise FlashError(str(e))
    if not allow_placeholder and PLACEHOLDER_PATTERN.is_file():
        _, flat, _ = uf2mod.parse(data)
        if PLACEHOLDER_PATTERN.read_bytes() in flat:
            raise FlashError("%s still holds the loader PLACEHOLDER: it would upload garbage to the "
                             "FM-1's RAM. Run `fm1-board-tool make-firmware` to splice the real "
                             "wl82loader.bin in, or flash the -noloader image." % path.name)
    return data, info


def _enter_bootsel(env, port, result):
    """Reboot exactly one RP2040 board into its boot ROM."""
    if port:
        found = discover(env.backend, explicit=port)
        if found and found[0].ident:
            c = Client(env.backend.open(port), port)
            c.reboot("bootsel")
            c.close()
            return "bootsel command on %s" % port
        env.backend.touch_1200(port)
        return "1200-baud touch on %s" % port
    boards = group_boards(env.backend.list_ports())
    if len(boards) > 1:
        raise FlashError("%d RP2040 boards with a Pico SDK USB serial interface are connected; "
                         "refusing to guess. Unplug the others or pass --port." % len(boards))
    if not boards:
        return None
    found = discover(env.backend)
    if found and found[0].ident:
        c = Client(env.backend.open(found[0].device), found[0].device)
        c.reboot("bootsel")
        c.close()
        return "bootsel command on %s (board %s)" % (found[0].device, found[0].ident.get("board"))
    console = boards[0].by_role("console") or boards[0].ports
    env.backend.touch_1200(console[0].device)
    return "1200-baud touch on %s" % console[0].device


def flash(env, profile, uf2_path, port=None, wait_bootsel=20.0, wait_app=25.0,
          allow_placeholder=False) -> FlashResult:
    """Raises FlashError (with .result, the steps reached) on any failure."""
    r = FlashResult()
    try:
        return _flash(env, profile, uf2_path, port, wait_bootsel, wait_app, allow_placeholder, r)
    except FlashError as e:
        e.result = r
        raise


def _flash(env, profile, uf2_path, port, wait_bootsel, wait_app, allow_placeholder, r):
    try:
        data, info = check_uf2_file(uf2_path, profile, allow_placeholder)
    except FlashError as e:
        r.mark("uf2_verified", False, str(e))
        raise
    r.uf2_info = info
    r.mark("uf2_verified", True, "%s: %d blocks, board %s, build %s, sha256 %s"
           % (Path(uf2_path).name, info["blocks"], info["boards"][0], info["builds"][0], info["sha256"]))

    vols = env.find_volumes()
    if not vols:
        how = _enter_bootsel(env, port, r)
        if how:
            log.info("Rebooting the board into BOOTSEL: %s", how)
        else:
            log.info("No RPI-RP2 disk and no RP2040 serial port. Hold BOOT on the board while "
                     "plugging it into the PC (USB data cable), then release BOOT.")
        vols = env.wait(env.find_volumes, wait_bootsel)
    if not vols:
        r.mark("bootsel_enumeration", False, "no RPI-RP2 disk within %.0f s" % wait_bootsel)
        raise FlashError("no RPI-RP2 disk appeared. Hold BOOT while plugging the board in.")
    if len(vols) > 1:
        r.mark("bootsel_enumeration", False, "%d RPI-RP2 disks" % len(vols))
        raise FlashError("several RPI-RP2 disks (%s); refusing to guess. Leave one board in BOOTSEL."
                         % ", ".join(str(v.path) for v in vols))
    vol = vols[0]
    r.volume = str(vol.path)
    r.mark("bootsel_enumeration", True, "RPI-RP2 at %s" % vol.path)
    r.mark("rpi_rp2_identified", True, "INFO_UF2.TXT: %s, Board-ID %s"
           % (vol.info.get("Model"), vol.info.get("Board-ID")))

    volumes.copy_uf2(Path(uf2_path), vol)
    gone = env.wait(lambda: not any(str(v.path) == str(vol.path) for v in env.find_volumes()), 20.0)
    r.mark("uf2_load", bool(gone), "boot disk left after the copy" if gone
           else "the RPI-RP2 disk is still there: the copy was not accepted")
    if not gone:
        raise FlashError("the board did not take the UF2")

    t0 = time.monotonic()
    found = env.wait(lambda: discover(env.backend), wait_app, step=0.5)
    if not found:
        r.mark("reboot_enumeration", False, "no Transporter data port within %.0f s" % wait_app)
        raise FlashError("the board did not come back as an FM-1 Transporter")
    if len(found) > 1:
        log.warning("%d Transporters answer; checking the one whose board matches", len(found))
    match = [f for f in found if f.ident.get("board") == profile.id]
    if not match:
        r.mark("reboot_enumeration", True, "data port %s" % found[0].device)
        r.mark("ident_board", False, "board %r answered, expected %s"
               % (found[0].ident.get("board"), profile.id))
        raise FlashError("the board came back reporting %r, not %s"
                         % (found[0].ident.get("board") or "no ident (legacy firmware)", profile.id))
    f = match[0]
    r.data_port, r.ident = f.device, f.ident
    r.mark("reboot_enumeration", True, "data port %s, console %s, %.1f s after the copy"
           % (f.device, f.console, time.monotonic() - t0))
    build_ok = f.ident.get("build") == info["builds"][0]
    r.mark("ident_board", build_ok, "board %s, firmware %s build %s"
           % (f.ident.get("board"), f.ident.get("fw"), f.ident.get("build")))
    if not build_ok:
        raise FlashError("the board runs build %s, not the flashed %s"
                         % (f.ident.get("build"), info["builds"][0]))
    return r

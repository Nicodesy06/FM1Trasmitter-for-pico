"""Guided hardware test for a new board (run when the board is on the desk).

Phase A, the board alone (nothing on GP0/GP1, no FM-1):
  A1 identification questions, A2 BOOTSEL enumeration with a data cable,
  A3 flashing the selected UF2, A4 the full self-test (LED asked), report.
Phase B, first FM-1 contact, read-only, only after a wiring checklist that
  the user must confirm item by item:
  B1 USB_KEY ACK / pulses / PIO USB enumeration / UBOOT, watched through
     `diag` phases; B2 ROM identification (980F / 856014); B3 two full reads,
     identical, saved with their record. Nothing is ever written to the FM-1.

Every answer and result goes into logs/hwtest-<board>-<time>.{json,md},
with HARDWARE evidence (or SIMULATED under --sim).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from . import fm1diag, selftest
from .flash import flash, FlashError
from .ports import discover
from .selftest import PASS, FAIL, NOT_TESTED, REQ_INSTR, Report
from .transporter import Client

log = logging.getLogger("fm1_board_tool")

ABORT_CRITERIA = """\
Stop and do not continue the FM-1 phase if any of these happens:
  * the board gets warm, or the PC reports a USB over-current;
  * a multimeter shows continuity between VBUS (red) and D+, D- or GND;
  * the FM-1 screen or LEDs react while it is switched OFF (it is being powered
    through the data lines: unplug at once);
  * `diag` stays in phase keying for 60 s after the FM-1 is switched on (wiring,
    battery, or swapped D+/D-) - retry once with D+/D- swapped, then stop;
  * the info reply is not key=980F type=3 id=856014;
  * the two full reads differ.
The FM-1's own flash is never written by this procedure."""


class Quit(Exception):
    pass


def _ask(env, prompt, choices=("y", "n")):
    while True:
        try:
            a = env.input_fn(prompt).strip()
        except EOFError:
            raise Quit("no keyboard input")
        if a.lower() in ("q", "quit"):
            raise Quit("stopped by the user")
        if choices is None:
            return a
        if a.lower()[:1] in choices:
            return a.lower()[:1]


def phase_a(env, rep, profile, uf2):
    p = profile
    log.info("")
    log.info("=== Phase A: the %s alone (no FM-1, nothing on GP%d/GP%d) ===", p.name, p.dp, p.dm)
    a = _ask(env, "A1. Is anything wired to GP%d or GP%d right now? [y/n] " % (p.dp, p.dm))
    if a == "y":
        rep.add("hw.gp_free", "GP0/GP1 free for the self-test", FAIL, "user: wired; unplug them first")
        raise Quit("disconnect GP0/GP1 first")
    rep.add("hw.gp_free", "GP0/GP1 free for the self-test", PASS, "user confirmed")
    mark = _ask(env, "A1. Text printed on the big chip (expected RP2040 / RP2-B2): ", None)
    rep.meta["soc_marking"] = mark
    rep.add("hw.soc_marking", "SoC marking reads RP2040", PASS if "2040" in mark or "RP2" in mark.upper() else FAIL,
            repr(mark), blocking=False)
    rep.meta["flash_marking"] = _ask(env, "A1. Text on the 8-pin flash chip next to it (e.g. W25Q128JV; "
                                          "Enter if unreadable): ", None)
    btn = _ask(env, "A1. Two buttons labelled %s present? [y/n] " % p["bootsel"]["label"])
    rep.add("hw.buttons", "BOOT and RESET buttons present", PASS if btn == "y" else FAIL, "user", blocking=False)

    log.info("A2. Unplug the board. Hold BOOT, plug the USB-C DATA cable into the PC, release BOOT.")
    log.info("    (Already running the Transporter firmware? type s to skip.)")
    a = _ask(env, "    Press Enter when done, or s: ", None)
    vols = []
    if a.lower() != "s":
        vols = env.wait(env.find_volumes, 60)
        rep.add("hw.bootsel_enumeration", "BOOTSEL: RPI-RP2 disk appears (data cable OK)",
                PASS if len(vols) == 1 else FAIL,
                "%s" % (vols[0].path if vols else "no RPI-RP2 within 60 s: charge-only cable? try another "
                        "cable/port, and hold BOOT while plugging in"))
        if not vols:
            raise Quit("no RPI-RP2 disk")
        rep.meta["bootrom"] = "%s %s" % (vols[0].info.get("Model"), vols[0].info.get("UF2 Bootloader"))
    if uf2:
        log.info("A3. Flashing %s", uf2)
        try:
            r = flash(env, p, uf2)
            for k, (ok, d) in r.steps.items():
                rep.add("flash." + k, k.replace("_", " "), PASS if ok else FAIL, d)
        except FlashError as e:
            for k, (ok, d) in getattr(e, "result", None).steps.items() if getattr(e, "result", None) else []:
                rep.add("flash." + k, k.replace("_", " "), PASS if ok else FAIL, d)
            rep.add("flash.error", "flashing", FAIL, str(e))
            raise Quit("flashing failed")
    log.info("A4. Self-test (watch the LED when asked).")
    st = selftest.run(env, p, console_seconds=6.0, logdir=Path(rep.meta.get("logdir", "logs")))
    for c in st.checks:
        if c.id.startswith(("fm1.", "signal.", "logs.", "flash.bootsel", "flash.uf2_load")):
            continue
        rep.checks.append(c)
    rep.meta.update({k: v for k, v in st.meta.items() if k in ("firmware", "hwinfo", "data_port", "console_port")})
    if st.failed:
        raise Quit("the board self-test failed")


FM1_CHECKLIST = [
    "The FM-1 is fully charged and switched OFF",
    "The pigtail's VBUS (5 V) wire is NOT connected to anything and is insulated",
    "Multimeter: continuity board %(dp)s <-> USB-C D+ (A6/B6) of the plug going into the FM-1",
    "Multimeter: continuity board %(dm)s <-> USB-C D- (A7/B7)",
    "Multimeter: continuity board GND <-> USB-C GND (A1/A12/B1/B12)",
    "Multimeter: NO continuity between VBUS and D+, D- or GND",
    "Nothing else is connected to the board's 3V3, VBUS, VSYS or VIN pins",
    "You have read the stop criteria printed above",
]


def phase_b(env, rep, profile, dump_dir, wait=120):
    p = profile
    log.info("")
    log.info("=== Phase B: first FM-1 contact, READ-ONLY ===")
    log.info(ABORT_CRITERIA)
    lab = {"dp": p.labels.get("dp", "GP%d" % p.dp), "dm": p.labels.get("dm", "GP%d" % p.dm)}
    for i, item in enumerate(FM1_CHECKLIST, 1):
        a = _ask(env, "B0.%d %s? [y/n] " % (i, item % lab))
        if a != "y":
            rep.add("fm1.preconditions", "wiring checklist confirmed", NOT_TESTED, "not confirmed: " + item % lab)
            raise Quit("checklist item not confirmed; nothing was done with the FM-1")
    rep.add("fm1.preconditions", "wiring checklist confirmed", PASS, "%d items, user" % len(FM1_CHECKLIST))
    rep.add("signal.levels", "continuity / no VBUS short (multimeter)", PASS,
            "confirmed by the user with a multimeter", evidence="HARDWARE (user measurement)")
    _ask(env, "B1. Unplug and replug the board's USB (it restarts keying), then press Enter: ", None)
    found = env.wait(lambda: discover(env.backend), 20, step=0.5)
    if not found:
        rep.add("fm1.transporter", "Transporter back after replug", FAIL, "no data port")
        raise Quit("no Transporter")
    c = Client(env.backend.open(found[0].device), found[0].device)
    try:
        log.info("B1. Switch the FM-1 ON now. Watching the Transporter for up to %d s ...", wait)
        seen = {}

        def on_phase(ph, t, d):
            seen[ph] = t
            log.info("  %5.1f s  phase %-8s key packets %s", t, ph, d.get("key_packets"))
        try:
            fm1diag.wait_uboot(c, wait, on_phase)
        except fm1diag.Fm1Error as e:
            rep.add("fm1.usb_key_ack", "FM-1 ROM acknowledges the USB_KEY",
                    PASS if "pulses" in seen or "host" in seen else FAIL, "phases %s" % seen)
            rep.add("fm1.pio_usb_enumeration", "PIO USB host enumerates the UBOOT",
                    PASS if "mounted" in seen else FAIL, str(e))
            raise Quit("no UBOOT")
        rep.add("fm1.usb_key_ack", "FM-1 ROM acknowledges the USB_KEY", PASS,
                "pulses seen at %s s" % seen["pulses"] if "pulses" in seen
                else "UBOOT reached (the pulse phase fell between two polls)")
        rep.add("fm1.pio_usb_enumeration", "PIO USB host enumerates the JieLi UBOOT", PASS,
                "mounted at %s s, UBOOT (INQUIRY ok) at %s s" % (seen.get("mounted", "?"), seen.get("uboot")))
        try:
            st, info, problems = fm1diag.identify(c)
        except fm1diag.Fm1Error as e:
            rep.add("fm1.rom_identification", "chip key 980F, flash 856014", FAIL, str(e))
            raise Quit(str(e))
        rep.add("fm1.rom_identification", "chip key 980F, flash 856014", FAIL if problems else PASS,
                " ".join("%s=%s" % kv for kv in info.items()))
        if problems:
            raise Quit("not the expected chip: " + "; ".join(problems))
        a = _ask(env, "B3. Read the whole FM-1 flash twice and save it (read-only, ~1 min)? [y/n] ")
        if a != "y":
            rep.add("fm1.flash_read_repeatable", "two full reads identical", NOT_TESTED, "declined")
            return
        try:
            side = fm1diag.double_dump(c, dump_dir, found[0].ident, env.evidence, info)
        except fm1diag.Fm1Error as e:
            rep.add("fm1.flash_read_repeatable", "two full reads identical", FAIL, str(e))
            raise Quit("reads differ")
        rep.add("fm1.flash_read_repeatable", "two full reads identical", PASS,
                "%s sha256 %s" % (side["path"], side["sha256"]))
        rep.meta["fm1_dump"] = side["path"]
    finally:
        c.close()


def run(env, profile, uf2=None, phases="a", dump_dir=Path("backups"), logdir=Path("logs"), wait=120):
    rep = Report(profile.id + "-hwtest", "HARDWARE" if env.sim is None else "SIMULATED")
    rep.meta["logdir"] = str(logdir)
    rep.meta["procedure"] = "phases " + phases.upper()
    try:
        if "a" in phases:
            phase_a(env, rep, profile, uf2)
        if "b" in phases:
            phase_b(env, rep, profile, dump_dir, wait)
        else:
            rep.add("fm1.next", "FM-1 phase", NOT_TESTED, "run again with --phase b when the wiring is ready")
    except Quit as e:
        rep.meta["stopped"] = str(e)
        log.error("Stopped: %s. Nothing was written to the FM-1.", e)
    rep.add("signal.usb_key_waveform", "USB_KEY waveform timing", REQ_INSTR,
            "optional: logic analyser on GP0/GP1 during keying")
    j, m = rep.save(logdir)
    log.info("Report: %s", m)
    return rep

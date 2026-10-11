"""Board self-test without an FM-1.

Each check ends in one of:
  PASS                      verified now, on this board (or in the simulator)
  FAIL                      a real error
  NOT TESTED                not run (skipped, or a prerequisite failed)
  REQUIRES FM-1             can only be shown with the synthesiser attached
  REQUIRES INSTRUMENTATION  needs a logic analyser / oscilloscope / multimeter
and carries the evidence it rests on: HARDWARE (a real board on this PC) or
SIMULATED (fm1_board_tool.sim). A COM port answering is never taken as proof
that the PIO USB path to an FM-1 works: those checks stay REQUIRES FM-1.

Safety: nothing is connected to D+/D- during this test, and no FM-1 request
(status/info/read) is sent, because the firmware would hold such a request
until an FM-1 answers the USB_KEY and the first real session would then see
a stale reply.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import platform
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from . import __version__
from .ports import discover, group_boards, readline
from .transporter import Client, TransporterError

log = logging.getLogger("fm1_board_tool")

PASS, FAIL, NOT_TESTED = "PASS", "FAIL", "NOT TESTED"
REQ_FM1, REQ_INSTR = "REQUIRES FM-1", "REQUIRES INSTRUMENTATION"
STATUSES = (PASS, FAIL, NOT_TESTED, REQ_FM1, REQ_INSTR)
GPIO_FUNC_PIO0, GPIO_FUNC_PIO1 = 6, 7


@dataclass
class Check:
    id: str
    title: str
    status: str = NOT_TESTED
    detail: str = ""
    evidence: str = ""
    blocking: bool = True


class Report:
    def __init__(self, board_id, evidence):
        self.board_id, self.evidence = board_id, evidence
        self.checks = []
        self.started = _dt.datetime.now().astimezone()
        self.meta = {}

    def add(self, cid, title, status, detail="", blocking=True, evidence=None):
        assert status in STATUSES, status
        if status in (REQ_FM1, REQ_INSTR, NOT_TESTED):
            ev = evidence or ""
        else:
            ev = evidence or self.evidence
        c = Check(cid, title, status, detail, ev, blocking)
        self.checks.append(c)
        (log.error if status == FAIL else log.info)("[%-24s] %-30s %s", status, cid, detail)
        return c

    def count(self, status):
        return sum(1 for c in self.checks if c.status == status)

    @property
    def failed(self):
        return any(c.status == FAIL and c.blocking for c in self.checks)

    @property
    def verdict(self) -> str:
        if self.failed:
            return "FAIL"
        if self.count(PASS) == 0:
            return "NOT TESTED"
        return "PASS (board only; FM-1 path not yet tested)"

    def to_dict(self):
        return {
            "tool": "fm1-board-tool %s" % __version__,
            "board": self.board_id,
            "evidence": self.evidence,
            "started": self.started.isoformat(timespec="seconds"),
            "host": "%s %s, Python %s" % (platform.system(), platform.release(), platform.python_version()),
            "verdict": self.verdict,
            "counts": {s: self.count(s) for s in STATUSES},
            "meta": self.meta,
            "checks": [asdict(c) for c in self.checks],
        }

    def markdown(self) -> str:
        lines = ["# FM-1 Transporter self-test: %s" % self.board_id, "",
                 "- started: %s" % self.started.isoformat(timespec="seconds"),
                 "- evidence: %s" % self.evidence,
                 "- verdict: **%s**" % self.verdict, ""]
        for k, v in self.meta.items():
            lines.append("- %s: `%s`" % (k, v))
        lines += ["", "| result | check | detail | evidence |", "|---|---|---|---|"]
        for c in self.checks:
            lines.append("| %s | %s (`%s`) | %s | %s |" % (c.status, c.title, c.id,
                                                       c.detail.replace("|", "/"), c.evidence))
        return "\n".join(lines) + "\n"

    def save(self, outdir: Path) -> tuple:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        stem = "selftest-%s-%s" % (self.board_id, self.started.strftime("%Y%m%d-%H%M%S"))
        j, m = outdir / (stem + ".json"), outdir / (stem + ".md")
        j.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        m.write_text(self.markdown(), encoding="utf-8")
        return j, m


def _console_capture(env, device, seconds):
    """Read-only: the console port is opened and read, never written to."""
    lines = []
    try:
        s = env.backend.open(device)
    except Exception as e:
        return None, str(e)
    try:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            line = readline(s, 0.3)
            if line:
                lines.append(line)
    except Exception as e:
        return lines, str(e)
    finally:
        try:
            s.close()
        except Exception:
            pass
    return lines, None


def run(env, profile, flash_uf2=None, reboot_test=True, console_seconds=4.0, port=None,
        wait=15.0, logdir=Path("logs")) -> Report:
    rep = Report(profile.id, env.evidence)
    rep.meta["profile"] = "%s (%s), validation %s" % (profile.id, profile.name, profile.validation)
    rep.add("host.python", "Python on the host", PASS if sys.version_info >= (3, 8) else FAIL,
            platform.python_version())
    rep.add("profile.valid", "board profile parses and validates", PASS,
            "D+=GP%d D-=GP%d, LEDs %s" % (profile.dp, profile.dm, profile["leds"]["kind"]))

    # 1. optional: flash through BOOTSEL (covers enumeration, RPI-RP2, UF2, reboot)
    if flash_uf2:
        from .flash import flash, FlashError
        try:
            r = flash(env, profile, flash_uf2, port=port)
            err = None
        except FlashError as e:
            r, err = getattr(e, "result", None), str(e)
        steps = r.steps if r else {}
        names = [("uf2_verified", "UF2 built for this board"),
                 ("bootsel_enumeration", "BOOTSEL enumeration (RPI-RP2 disk)"),
                 ("rpi_rp2_identified", "RPI-RP2 identified by INFO_UF2.TXT"),
                 ("uf2_load", "UF2 accepted by the boot ROM"),
                 ("reboot_enumeration", "reboot into the Transporter firmware"),
                 ("ident_board", "firmware reports this board and build")]
        for key, title in names:
            if key in steps:
                ok, detail = steps[key]
                rep.add("flash." + key, title, PASS if ok else FAIL, detail)
            else:
                rep.add("flash." + key, title, NOT_TESTED, "not reached")
        if err:
            rep.meta["flash_error"] = err
            if not any(not ok for ok, _ in steps.values()):
                rep.add("flash.error", "flashing", FAIL, err)
    else:
        for key, title in (("bootsel_enumeration", "BOOTSEL enumeration (RPI-RP2 disk)"),
                           ("uf2_load", "UF2 accepted by the boot ROM")):
            rep.add("flash." + key, title, NOT_TESTED, "run with --flash UF2 to include flashing")

    # 2. enumeration and the two CDC interfaces
    found = env.wait(lambda: discover(env.backend, explicit=port), wait, step=0.5)
    boards = group_boards(env.backend.list_ports())
    if not found:
        rep.add("cdc.enumeration", "USB CDC enumeration", FAIL,
                "no Transporter data port answers ping (%d RP2040 USB serial boards seen)" % len(boards))
        _requires_fm1(rep)
        return _finish(rep, logdir)
    if len(found) > 1:
        rep.add("cdc.selection", "one Transporter selected", FAIL,
                "%d Transporters answer: %s; pass --port" % (len(found), ", ".join(f.device for f in found)))
        return _finish(rep, logdir)
    f = found[0]
    rep.meta["data_port"], rep.meta["console_port"] = f.device, f.console
    rep.add("cdc.enumeration", "USB CDC enumeration", PASS, "board serial %s" % f.serial)
    rep.add("cdc.interfaces", "console and data interfaces told apart",
            PASS if f.console else FAIL,
            "data %s (interface 2), console %s (interface 0)" % (f.device, f.console))
    rep.add("handshake.ping", "data port handshake (ping/PONG)", PASS, f.device)
    if not f.ident:
        rep.add("ident.board", "firmware identifies its board", FAIL,
                "no `ident` answer: upstream/legacy firmware. Flash the multiboard UF2, and unplug "
                "and replug the board before using it with the recovery tool")
        _requires_fm1(rep)
        return _finish(rep, logdir)
    ok = f.ident.get("board") == profile.id
    rep.add("ident.board", "firmware identifies its board", PASS if ok else FAIL,
            "board=%s (expected %s)" % (f.ident.get("board"), profile.id))
    rep.add("ident.version", "firmware version and build id", PASS,
            "fw=%s build=%s upstream=%s" % (f.ident.get("fw"), f.ident.get("build"), f.ident.get("upstream")))
    rep.meta["firmware"] = "%s build %s" % (f.ident.get("fw"), f.ident.get("build"))
    pins_ok = f.ident.get("dp") == str(profile.dp) and f.ident.get("dm") == str(profile.dm)
    rep.add("ident.pins", "firmware D+/D- pins match the profile", PASS if pins_ok else FAIL,
            "firmware dp=%s dm=%s, profile GP%d/GP%d" % (f.ident.get("dp"), f.ident.get("dm"), profile.dp, profile.dm))

    c = Client(env.backend.open(f.device), f.device)
    try:
        _board_checks(env, rep, c, profile)
    except (TransporterError, *env.backend.errors) as e:
        rep.add("protocol.board_commands", "board commands on the data port", FAIL, str(e))
    finally:
        c.close()

    # 3. console output (read-only)
    if f.console:
        lines, err = _console_capture(env, f.console, console_seconds)
        if lines is None:
            rep.add("console.output", "console port prints the log", FAIL, "cannot open: %s" % err)
        else:
            useful = [x for x in lines if "KEY:" in x or "FM-1 Transporter" in x or "HOST:" in x
                      or "PULSE" in x or "Board " in x]
            rep.meta["console_sample"] = " | ".join(lines[-3:])
            if useful:
                rep.add("console.output", "console port prints the log", PASS,
                        "%d lines in %.0f s, e.g. %r" % (len(lines), console_seconds, useful[-1][:80]))
            elif env.sim is not None:
                rep.add("console.output", "console port prints the log", NOT_TESTED,
                        "the simulator has no console log")
            else:
                rep.add("console.output", "console port prints the log", FAIL,
                        "no log line in %.0f s (expected `KEY: n packets` every 2 s)" % console_seconds)
    else:
        rep.add("console.output", "console port prints the log", NOT_TESTED, "console port not identified")

    # 4. reboot through the watchdog: disconnect, timeout, re-enumeration
    if reboot_test:
        _reboot_check(env, rep, f, profile, wait)
    else:
        rep.add("watchdog.reboot", "watchdog reboot and re-enumeration", NOT_TESTED, "--no-reboot")

    _requires_fm1(rep)
    return _finish(rep, logdir)


def _board_checks(env, rep, c, profile):
    hw = c.board_cmd("hwinfo")
    rep.meta["hwinfo"] = " ".join("%s=%s" % kv for kv in hw.items())
    clk = int(hw.get("clk_sys", 0))
    rep.add("clock.sys", "clk_sys is 120 MHz (12 MHz multiple for PIO USB)",
            PASS if clk == 120_000_000 else FAIL, "%d Hz" % clk)
    jedec = hw.get("flash_jedec", "none")
    rep.add("flash.jedec", "board QSPI flash answers JEDEC 9Fh", PASS if jedec != "none" else FAIL,
            "JEDEC %s (%s)" % (jedec, hw.get("flash_vendor")))
    sfdp = int(hw.get("flash_sfdp_bytes", 0) or 0)
    jb = int(hw.get("flash_jedec_bytes", 0) or 0)
    declared = int(profile["flash"]["declared_bytes"])
    build = int(profile["flash"]["build_bytes"])
    measured = sfdp or jb
    src = "SFDP" if sfdp else ("JEDEC capacity byte" if jb else None)
    if not measured:
        rep.add("flash.capacity", "board flash size (listing says %d MiB)" % (declared >> 20), NOT_TESTED,
                "neither SFDP nor the JEDEC capacity byte gave a size", blocking=False)
    else:
        rep.add("flash.capacity", "board flash size (listing says %d MiB)" % (declared >> 20),
                PASS if measured == declared else FAIL,
                "%s reports %d bytes (%g MiB); image is linked for %d MiB%s"
                % (src, measured, measured / 2 ** 20, build >> 20,
                   "" if measured == declared else " - the listing's size is NOT what is fitted"),
                blocking=False)
        rep.add("flash.fits_build", "board flash holds the linked image size", PASS if measured >= build else FAIL,
                "%d >= %d" % (measured, build))
    rep.add("reset.reason", "reset reason reported", PASS, hw.get("reset", "?"))

    d = c.board_cmd("diag")
    rep.meta["diag"] = " ".join("%s=%s" % kv for kv in d.items())
    phase = d.get("phase")
    funcs_ok = d.get("dp_func") == str(GPIO_FUNC_PIO0) or d.get("dm_func") == str(GPIO_FUNC_PIO0)
    if phase == "keying":
        rep.add("pio.recovery_config", "USB_KEY state machine owns D+/D- (PIO0)",
                PASS if funcs_ok else FAIL,
                "phase keying, GPIO functions dp=%s dm=%s (6 = PIO0), %s key packets"
                % (d.get("dp_func"), d.get("dm_func"), d.get("key_packets")))
        p = c.board_cmd("piotest")
        edges = int(p.get("dp_edges", 0)), int(p.get("dm_edges", 0))
        rep.add("pio.pad_activity", "USB_KEY toggles the D+/D- pads (internal pad readback)",
                PASS if edges[0] > 0 and edges[1] > 0 else FAIL,
                "30 ms: D+ %d edges, D- %d edges (no FM-1 attached; this shows the PIO drives the "
                "pads, not that the waveform meets the JieLi timing)" % edges)
    else:
        rep.add("pio.recovery_config", "USB_KEY state machine owns D+/D- (PIO0)", NOT_TESTED,
                "phase is %s, not keying: is something attached to D+/D-?" % phase)
        rep.add("pio.pad_activity", "USB_KEY toggles the D+/D- pads", NOT_TESTED, "phase %s" % phase)

    leds = c.board_cmd("ledtest").get("leds")
    if leds == "none":
        rep.add("led.visual", "status LED blinks", NOT_TESTED, "this build drives no LED", blocking=False)
    else:
        a = env.ask("  LED test: the %s LED should now blink for 4 s (%s). Did it? [y/n/s] "
                    % (leds, ", ".join("GP%d" % g for g in profile.led_pins().values())))
        st = {"y": PASS, "n": FAIL}.get(a, NOT_TESTED)
        rep.add("led.visual", "status LED blinks", st,
                {"y": "seen by the user", "n": "the user saw no blink (LED absent, other pin, or "
                 "other polarity; the firmware does not depend on it)"}.get(a, "not confirmed (non-interactive)"),
                blocking=False)
        time.sleep(0.2 if env.sim is not None else 4.2)
    rep.add("protocol.board_commands", "board commands on the data port", PASS,
            "ident, hwinfo, diag%s, ledtest answered" % (", piotest" if phase == "keying" else ""))


def _reboot_check(env, rep, f, profile, wait):
    c = Client(env.backend.open(f.device), f.device)
    t0 = time.monotonic()
    try:
        c.reboot("reboot")
    except (TransporterError, *env.backend.errors) as e:
        rep.add("watchdog.reboot", "watchdog reboot and re-enumeration", FAIL, str(e))
        c.close()
        return
    # the open port must fail promptly once the device is gone (no hang)
    gone_after = None
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            c.s.timeout = 0.2
            c.s.readline()
            c.s.reset_input_buffer()
        except Exception:
            gone_after = time.monotonic() - t0
            break
    c.close()
    rep.add("usb.disconnect_detected", "a vanished port is detected, not waited on forever",
            PASS if gone_after is not None else FAIL,
            "port error %.2f s after the reboot request" % gone_after if gone_after is not None
            else "the old port still answered 5 s after the reboot")
    back = env.wait(lambda: [x for x in discover(env.backend) if x.serial == f.serial], wait, step=0.5)
    if not back:
        rep.add("watchdog.reboot", "watchdog reboot and re-enumeration", FAIL,
                "the board did not come back within %.0f s" % wait)
        return
    dt = time.monotonic() - t0
    c2 = Client(env.backend.open(back[0].device), back[0].device)
    try:
        reset = c2.board_cmd("hwinfo").get("reset")
    finally:
        c2.close()
    rep.add("watchdog.reboot", "watchdog reboot and re-enumeration",
            PASS if reset == "reboot-cmd" else FAIL,
            "back on %s after %.1f s, reset reason %s" % (back[0].device, dt, reset))


def _requires_fm1(rep):
    rep.add("fm1.usb_key_ack", "FM-1 ROM acknowledges the USB_KEY", REQ_FM1,
            "power the FM-1 on with D+/D-/GND wired; console shows `ACK` and `PULSE`")
    rep.add("fm1.pio_usb_enumeration", "PIO USB host enumerates the JieLi UBOOT (4C4A:8057)", REQ_FM1,
            "console shows `DEVICE DESCRIPTOR OK` and `UBOOT READY`")
    rep.add("fm1.rom_identification", "chip key 980F, flash 856014", REQ_FM1, "fm1-board-tool fm1-check")
    rep.add("fm1.flash_read_repeatable", "two full reads of the FM-1 flash identical", REQ_FM1,
            "fm1-board-tool fm1-check --dump DIR")
    rep.add("signal.usb_key_waveform", "USB_KEY 0x16EF waveform and 1 ms pulses on D+/D-", REQ_INSTR,
            "logic analyser on GP0/GP1: 16 clocks of ~19 us per packet, 4 us pulses every 1 ms")
    rep.add("signal.levels", "3.3 V logic levels and continuity of D+/D-/GND", REQ_INSTR,
            "multimeter: continuity GP0-D+, GP1-D-, GND-GND; no short to VBUS")


def _finish(rep, logdir):
    try:
        j, m = rep.save(logdir)
        rep.meta["report_json"], rep.meta["report_md"] = str(j), str(m)
        rep.add("logs.written", "self-test report and log written", PASS, str(m))
        rep.save(logdir)        # again, with the last entry
    except OSError as e:
        rep.add("logs.written", "self-test report and log written", FAIL, str(e))
    return rep

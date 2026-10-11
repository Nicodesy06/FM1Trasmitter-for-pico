"""fm1-board-tool command line.

  fm1-board-tool boards [show ID | validate | conflicts ID GPIO=use ...]
  fm1-board-tool list
  fm1-board-tool probe [--port COMx]
  fm1-board-tool build --board ID [--loader FILE | --fetch-loader | --placeholder] [--sdk DIR]
  fm1-board-tool make-firmware --board ID (--loader FILE | --fetch-loader)
  fm1-board-tool flash --board ID [--uf2 FILE] [--port COMx]
  fm1-board-tool selftest --board ID [--flash FILE] [--interactive] [--no-reboot]
  fm1-board-tool transporter-info [--port COMx]
  fm1-board-tool console [--port COMx] [--seconds N]
  fm1-board-tool fm1-check [--dump DIR] [--wait S]
  fm1-board-tool hwtest --board ID [--phase a|b|ab] [--uf2 FILE]

`--sim BOARD` runs any command against the behavioural simulator instead of
real ports (SIMULATED evidence only). Exit codes: 0 ok, 1 failure, 2 usage.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import sys
import time
import zlib
from pathlib import Path

from . import __version__, profiles as prof, volumes
from .env import Env
from .ports import discover, group_boards
from .profiles import REPO_ROOT, ProfileError
from .transporter import Client, TransporterError, check_fm1_info, FM1_FLASH_SIZE

log = logging.getLogger("fm1_board_tool")


def setup_logging(cmd, logdir: Path, verbose: bool) -> Path:
    logdir.mkdir(parents=True, exist_ok=True)
    path = logdir / ("fm1-board-tool-%s-%s.log" % (cmd, _dt.datetime.now().strftime("%Y%m%d-%H%M%S")))
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        log.removeHandler(h)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if verbose else logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    log.addHandler(ch)
    log.debug("fm1-board-tool %s, argv %s", __version__, sys.argv)
    return path


def make_env(args) -> Env:
    if getattr(args, "sim", None):
        from .sim import SimWorld, SimBoard, FakeFM1
        w = SimWorld()
        w.add(SimBoard(board_id=args.sim, fm1=FakeFM1() if args.sim_fm1 else None,
                       state="bootsel" if args.sim_bootsel else "app"))
        log.info("SIMULATOR: board %s%s (no hardware is used)", args.sim, " + fake FM-1" if args.sim_fm1 else "")
        return Env(sim=w, interactive=args.interactive)
    return Env(interactive=getattr(args, "interactive", False))


# --------------------------------------------------------------------------

def cmd_boards(args):
    if args.action == "validate":
        bad = 0
        for p in prof.load_profiles(strict=False).values():
            st = "OK" if not p.errors else "INVALID: " + "; ".join(p.errors)
            bad += bool(p.errors)
            print("%-24s %s" % (p.raw.get("id"), st))
        return 1 if bad else 0
    if args.action == "show":
        p = prof.get_profile(args.id, allow_disabled=True)
        print(json.dumps(p.raw, indent=2))
        return 0
    if args.action == "conflicts":
        p = prof.get_profile(args.id, allow_disabled=True)
        extra = {}
        for u in args.use:
            g, _, what = u.partition("=")
            extra[int(g.upper().lstrip("GP"))] = what or "user function"
        problems = prof.check_conflicts(p, extra)
        for x in problems:
            print("CONFLICT", x)
        if not problems:
            print("no conflict with the Transporter's pins on %s" % p.id)
        return 1 if problems else 0
    if args.json:
        print(json.dumps({k: v.raw for k, v in prof.load_profiles().items()}, indent=2))
        return 0
    print("%-24s %-12s %-8s %-18s %-7s %s" % ("board", "role", "enabled", "validation", "D+/D-", "UF2"))
    for p in prof.load_profiles().values():
        print("%-24s %-12s %-8s %-18s GP%d/%-3d %s" % (p.id, p.raw["role"], "yes" if p.enabled else "NO",
                                                     p.validation, p.dp, p.dm, p.uf2_name))
    return 0


def cmd_list(args, env):
    ports = env.backend.list_ports()
    print("Serial ports:")
    if not ports:
        print("  (none)")
    for p in ports:
        tag = ""
        if p.is_transporter_candidate:
            tag = "RP2040 2E8A:000A, interface %s -> %s" % (p.interface_number, p.role)
        elif p.is_rpi:
            tag = "Raspberry Pi VID, PID %04X" % (p.pid or 0)
        print("  %-14s %-34s %s" % (p.device, (p.description or "")[:34], tag))
    vols = env.find_volumes()
    print("RPI-RP2 boot disks (identified by INFO_UF2.TXT, not by drive letter):")
    for v in vols:
        print("  %s  %s, %s" % (v.path, v.info.get("Model"), v.info.get("UF2 Bootloader")))
    if not vols:
        print("  (none)")
    print("RP2040 boards with a USB serial interface: %d" % len(group_boards(ports)))
    return 0


def cmd_probe(args, env):
    found = discover(env.backend, explicit=args.port)
    if not found:
        log.info("No FM-1 Transporter answers ping%s.", " on %s" % args.port if args.port else "")
        return 1
    for f in found:
        if not f.ident:
            log.warning("%s answers ping but not ident: upstream/legacy firmware (XIAO a632d92?). "
                        "Unplug and replug it before using the recovery tool.", f.device)
            continue
        log.info("%s  board=%s fw=%s build=%s validation=%s console=%s", f.device, f.ident.get("board"),
                 f.ident.get("fw"), f.ident.get("build"), f.ident.get("validation"), f.console)
    return 0


def _one_transporter(env, port):
    found = discover(env.backend, explicit=port)
    if not found:
        raise TransporterError("no FM-1 Transporter data port answers ping")
    if len(found) > 1:
        raise TransporterError("%d Transporters connected (%s); pass --port"
                               % (len(found), ", ".join(f.device for f in found)))
    return found[0]


def cmd_transporter_info(args, env):
    f = _one_transporter(env, args.port)
    c = Client(env.backend.open(f.device), f.device)
    try:
        print("data port      %s (console %s)" % (f.device, f.console))
        if not f.ident:
            print("firmware       legacy (no ident): only ping is safe to use")
            return 0
        for cmd in ("ident", "hwinfo", "diag"):
            d = c.board_cmd(cmd)
            print("%-14s %s" % (cmd, " ".join("%s=%s" % kv for kv in d.items())))
            if cmd == "diag":
                phase = d.get("phase")
        if phase == "uboot":
            print("%-14s %s" % ("status", " ".join("%s=%s" % kv for kv in c.status(10).items())))
        else:
            print("status         not asked: the FM-1 is not in UBOOT (phase %s); asking now would "
                  "queue the request until it is" % phase)
    finally:
        c.close()
    return 0


def cmd_console(args, env):
    f = _one_transporter(env, args.port) if not args.console_port else None
    dev = args.console_port or (f and f.console)
    if not dev:
        raise TransporterError("console port not identified; pass --console-port")
    log.info("Reading %s for %d s (read-only; Ctrl+C to stop)", dev, args.seconds)
    s = env.backend.open(dev)
    end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < end:
            s.timeout = 0.5
            line = s.readline().decode("utf-8", "replace").rstrip()
            if line:
                log.info("console| %s", line)
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
    return 0


def cmd_build(args, env):
    from . import build as b
    if args.check_tools:
        t = b.check_tools(args.sdk)
        for k, v in t.items():
            print("%-16s %s" % (k, v))
        return 1 if t["missing"] else 0
    boards = [p.id for p in prof.load_profiles().values() if p.enabled] if args.all else [args.board]
    if not boards or boards == [None]:
        raise SystemExit("build: --board ID or --all")
    loader_mode, lpath = "none", None
    if args.placeholder:
        loader_mode = "placeholder"
    elif args.fetch_loader:
        loader_mode, lpath = "real", b.fetch_loader(REPO_ROOT / ".cache" / "wl82loader.bin")
    elif args.loader:
        loader_mode, lpath = "real", args.loader
    rc = 0
    for board in boards:
        try:
            m = b.build(board, sdk=args.sdk, loader_mode=loader_mode, loader_path=lpath,
                        out_dir=args.out, build_root=args.build_dir, clean=args.clean,
                        allow_unvalidated=args.allow_unvalidated, jobs=args.jobs)
            print("%s: %s (%s) sha256 %s" % (board, m["files"]["uf2"]["file"], m["status"],
                                             m["files"]["uf2"]["sha256"]))
        except (b.BuildError, ProfileError) as e:
            log.error("%s: BUILD FAILED: %s", board, e)
            rc = 1
    return rc


def cmd_make_firmware(args, env):
    from . import build as b
    loader = b.fetch_loader(REPO_ROOT / ".cache" / "wl82loader.bin") if args.fetch_loader else args.loader
    if not loader:
        raise SystemExit("make-firmware: --loader FILE or --fetch-loader")
    r = b.make_firmware(args.board, loader, out=args.out)
    print("%s sha256 %s" % (r["file"], r["sha256"]))
    return 0


def default_uf2(profile):
    stem = Path(profile.uf2_name).stem
    for d in (REPO_ROOT / "artifacts" / profile.id, REPO_ROOT / "dist" / profile.id):
        for name in (stem + ".uf2", stem + "-noloader.uf2"):
            if (d / name).is_file():
                return d / name
    return None


def cmd_flash(args, env):
    from .flash import flash, FlashError
    p = prof.get_profile(args.board, allow_disabled=args.allow_unvalidated)
    uf2 = Path(args.uf2) if args.uf2 else default_uf2(p)
    if not uf2:
        raise SystemExit("flash: no UF2 for %s found; pass --uf2 or run build/make-firmware" % p.id)
    if uf2.name.endswith("-noloader.uf2"):
        log.warning("%s has no JieLi loader: fine for the board self-test and for reaching UBOOT, "
                    "but `info`/`read` will answer ERR loader. Use make-firmware for recovery.", uf2.name)
    log.info("Board %s (%s), validation %s; UF2 %s", p.id, p.name, p.validation, uf2)
    try:
        r = flash(env, p, uf2, port=args.port, allow_placeholder=args.allow_placeholder)
    except FlashError as e:
        log.error("FLASH FAILED: %s", e)
        return 1
    log.info("Flashed: %s answers as %s build %s", r.data_port, r.ident.get("board"), r.ident.get("build"))
    return 0


def cmd_selftest(args, env):
    from . import selftest
    p = prof.get_profile(args.board, allow_disabled=args.allow_unvalidated)
    log.info("Self-test of %s WITHOUT the FM-1: leave GP%d/GP%d unconnected.", p.id, p.dp, p.dm)
    rep = selftest.run(env, p, flash_uf2=args.flash, reboot_test=not args.no_reboot,
                       console_seconds=args.console_seconds, port=args.port,
                       logdir=Path(args.logdir))
    print()
    print(rep.markdown())
    return 1 if rep.failed else 0


def cmd_fm1_check(args, env):
    """First contact with the FM-1: read-only. Waits for UBOOT by polling `diag`
    (answered by the RP2040, so nothing is queued), then status, info, and
    optionally two full reads that must be identical."""
    from . import fm1diag
    f = _one_transporter(env, args.port)
    if not f.ident:
        raise TransporterError("legacy firmware without ident/diag; use the recovery tool's `info`")
    c = Client(env.backend.open(f.device), f.device)
    try:
        log.info("Waiting up to %d s for the FM-1 to enter UBOOT. FM-1 OFF -> wire D+/D-/GND -> "
                 "switch the FM-1 ON now.", args.wait)
        try:
            fm1diag.wait_uboot(c, args.wait, lambda ph, t, d: log.info("  %5.1f s  phase %s", t, ph))
            st, info, problems = fm1diag.identify(c)
        except fm1diag.Fm1Error as e:
            log.error("%s. Nothing was written. See the console log (fm1-board-tool console).", e)
            return 1
        log.info("status   %s", " ".join("%s=%s" % kv for kv in st.items()))
        log.info("info     %s", " ".join("%s=%s" % kv for kv in info.items()))
        if problems:
            log.error("NOT an FM-1 as expected: %s. Stop here; nothing was written.", "; ".join(problems))
            return 1
        log.info("chip key 980F and flash 856014: an FM-1 (JieLi WL82) in UBOOT. Nothing was written.")
        if not args.dump:
            return 0
        try:
            side = fm1diag.double_dump(c, Path(args.dump), f.ident, env.evidence, info)
        except fm1diag.Fm1Error as e:
            log.error("%s", e)
            return 1
        log.info("Saved %s (sha256 %s) and its .json record. Keep both.", side["path"], side["sha256"])
        return 0
    finally:
        c.close()


# --------------------------------------------------------------------------

def build_parser():
    def common(p, defaults):
        kw = (lambda v: {"default": v}) if defaults else (lambda v: {"default": argparse.SUPPRESS})
        p.add_argument("-v", "--verbose", action="store_true", **kw(False))
        p.add_argument("--logdir", **kw(str(REPO_ROOT / "logs")))
        p.add_argument("--sim", metavar="BOARD", help="use the simulator with a board flashed for BOARD",
                       **kw(None))
        p.add_argument("--sim-fm1", action="store_true", help="simulator: attach a fake FM-1", **kw(False))
        p.add_argument("--sim-bootsel", action="store_true", help="simulator: board starts in BOOTSEL",
                       **kw(False))
        p.add_argument("--interactive", action="store_true", help="ask the user (LED check)", **kw(False))

    ap = argparse.ArgumentParser(prog="fm1-board-tool", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    common(ap, True)
    shared = argparse.ArgumentParser(add_help=False)
    common(shared, False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *a, **k: _add(*a, parents=[shared], **k)

    b = sub.add_parser("boards", help="list/show/validate board profiles")
    b.add_argument("action", nargs="?", choices=("list", "show", "validate", "conflicts"), default="list")
    b.add_argument("id", nargs="?")
    b.add_argument("use", nargs="*", help="conflicts: GPIO=purpose, e.g. GP0=UART0_TX")
    b.add_argument("--json", action="store_true")

    sub.add_parser("list", help="serial ports and RPI-RP2 disks (writes nothing)")
    p = sub.add_parser("probe", help="find Transporters by handshake")
    p.add_argument("--port")

    p = sub.add_parser("build", help="build the firmware for a board")
    p.add_argument("--board")
    p.add_argument("--all", action="store_true", help="every enabled profile")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--loader", help="wl82loader.bin (sha256-checked)")
    g.add_argument("--fetch-loader", action="store_true", help="download wl82loader.bin (pinned, checked)")
    g.add_argument("--placeholder", action="store_true", help="placeholder build for make-firmware")
    p.add_argument("--sdk")
    p.add_argument("--out")
    p.add_argument("--build-dir")
    p.add_argument("--jobs", type=int)
    p.add_argument("--clean", action="store_true")
    p.add_argument("--check-tools", action="store_true", help="only report the toolchain")
    p.add_argument("--allow-unvalidated", action="store_true")

    p = sub.add_parser("make-firmware", help="splice wl82loader.bin into the shipped placeholder UF2")
    p.add_argument("--board", required=True)
    p.add_argument("--loader")
    p.add_argument("--fetch-loader", action="store_true")
    p.add_argument("--out")

    p = sub.add_parser("flash", help="put a UF2 on the board through BOOTSEL")
    p.add_argument("--board", required=True)
    p.add_argument("--uf2")
    p.add_argument("--port")
    p.add_argument("--allow-placeholder", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--allow-unvalidated", action="store_true")

    p = sub.add_parser("selftest", help="board self-test without the FM-1")
    p.add_argument("--board", required=True)
    p.add_argument("--flash", metavar="UF2", help="flash this UF2 first (tests BOOTSEL too)")
    p.add_argument("--port")
    p.add_argument("--no-reboot", action="store_true")
    p.add_argument("--console-seconds", type=float, default=5.0)
    p.add_argument("--allow-unvalidated", action="store_true")

    p = sub.add_parser("transporter-info", help="ident, hwinfo, diag (read-only)")
    p.add_argument("--port")
    p = sub.add_parser("console", help="show the console log (read-only)")
    p.add_argument("--port")
    p.add_argument("--console-port")
    p.add_argument("--seconds", type=int, default=30)
    p = sub.add_parser("fm1-check", help="first FM-1 contact: read-only identification and dump")
    p.add_argument("--port")
    p.add_argument("--wait", type=int, default=120)
    p.add_argument("--dump", metavar="DIR", help="read the 1 MiB flash twice and save it if identical")
    p = sub.add_parser("hwtest", help="guided hardware test: A = board alone, B = first read-only FM-1 contact")
    p.add_argument("--board", required=True)
    p.add_argument("--phase", choices=("a", "b", "ab"), default="a")
    p.add_argument("--uf2", help="UF2 to flash in phase A (default: artifacts/ or dist/ for the board)")
    p.add_argument("--no-flash", action="store_true", help="board already runs the Transporter firmware")
    p.add_argument("--dump-dir", default="backups")
    p.add_argument("--wait", type=int, default=120)
    p.add_argument("--allow-unvalidated", action="store_true")
    return ap


def cmd_hwtest(args, env):
    from . import hwtest
    p = prof.get_profile(args.board, allow_disabled=args.allow_unvalidated)
    env.interactive = True
    uf2 = Path(args.uf2) if args.uf2 else None
    if args.phase in ("a", "ab") and not uf2 and not args.no_flash:
        uf2 = default_uf2(p)
        if not uf2:
            raise SystemExit("hwtest: no UF2 for %s; pass --uf2 or --no-flash" % p.id)
    rep = hwtest.run(env, p, uf2=None if args.no_flash else uf2, phases=args.phase,
                     dump_dir=Path(args.dump_dir), logdir=Path(args.logdir), wait=args.wait)
    print()
    print(rep.markdown())
    return 1 if rep.failed or rep.meta.get("stopped") else 0


COMMANDS = {"hwtest": cmd_hwtest, "list": cmd_list, "probe": cmd_probe, "transporter-info": cmd_transporter_info,
            "console": cmd_console, "build": cmd_build, "make-firmware": cmd_make_firmware,
            "flash": cmd_flash, "selftest": cmd_selftest, "fm1-check": cmd_fm1_check}


def main(argv=None):
    args = build_parser().parse_args(argv)
    path = setup_logging(args.cmd, Path(args.logdir), args.verbose)
    try:
        if args.cmd == "boards":
            return cmd_boards(args)
        env = None if args.cmd in ("build", "make-firmware") else make_env(args)
        return COMMANDS[args.cmd](args, env)
    except (ProfileError, TransporterError, RuntimeError, OSError) as e:
        log.error("ERROR: %s", e)
        return 1
    except KeyboardInterrupt:
        log.error("interrupted")
        return 1
    finally:
        log.debug("log file %s", path)

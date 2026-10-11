"""Behavioural simulator: Transporter boards, their RPI-RP2 boot disks and a
fake FM-1, without any hardware.

It models what the host tools can observe: ports that appear and vanish
(reboot, BOOTSEL, unplug), the data-port line protocol (core 0 commands
answered at once, FM-1 requests only once the FM-1 is in UBOOT, one pending
request at a time), the boot disk accepting a UF2, and an FM-1 whose 1 MiB
NOR flash can be read. Fault injection covers the failure modes the tools
must survive: a flaky read, a stalled transfer, a disconnect mid-read, an
FM-1 that never answers the key, a legacy firmware without `ident`.

Everything here is SIMULATED evidence; it never stands in for a hardware test.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import uf2 as uf2mod
from .ports import PortInfo, RPI_VID, TRANSPORTER_PID

FLASH = 0x100000


class SimSerialException(OSError):
    pass


@dataclass
class FakeFM1:
    """The synthesiser: dark (bricked) until keyed, then ROM UBOOT."""
    flash: bytearray = field(default_factory=lambda: bytearray(
        ((i * 2654435761) >> 24) & 0xFF for i in range(FLASH)))
    chip_key: str = "980F"
    flash_id: str = "856014"
    answers_key: bool = True        # False: wiring wrong / battery flat
    powered: bool = True
    flaky_read: int | None = None   # 1-based read number that returns one flipped bit
    stall_read: int | None = None   # read number that stops after half the data
    unplug_on_read: int | None = None
    reads: int = 0
    writes: list = field(default_factory=list)   # stays empty: tools here never write


@dataclass
class SimBoard:
    board_id: str                   # firmware board id currently flashed
    uid: str = "E6614103E7452D2F"
    legacy: bool = False            # upstream firmware: no ident/self-test commands
    state: str = "app"              # app | bootsel | gone
    reset: str = "por"
    fm1: FakeFM1 | None = None
    reboot_s: float = 0.3
    port_base: str = "COM"
    port_num: int = 7
    led_kind: str = "mono"
    jedec: str = "EF4018"
    sfdp_bytes: int = 16 * 1024 * 1024
    build_bytes: int = 2 * 1024 * 1024
    declared_bytes: int = 16 * 1024 * 1024
    version: str = "1.0.0-mb"
    build: str = "simulated"
    volume_dir: Path | None = None
    flashed: list = field(default_factory=list)
    _pending: str | None = None
    _uboot: bool = False
    _boot_t: float = field(default_factory=time.monotonic)
    _keying: bool = True

    @property
    def data_dev(self):
        return "%s%d" % (self.port_base, self.port_num + 1)

    @property
    def console_dev(self):
        return "%s%d" % (self.port_base, self.port_num)

    def phase(self):
        if self._uboot:
            return "uboot"
        return "keying"

    def key_tick(self):
        """The USB_KEY runs from boot; a powered FM-1 that answers it enters UBOOT."""
        if self.state == "app" and not self._uboot and self.fm1 and self.fm1.powered \
                and self.fm1.answers_key:
            self._uboot = True

    def reboot(self, reset="watchdog", to_bootsel=False, world=None):
        """Like the firmware: the reply leaves first, the USB device drops
        ~50 ms later, and re-enumerates after reboot_s."""
        def run():
            time.sleep(0.05)
            self.state = "gone"
            self._uboot = False
            self._pending = None
            if world:
                world._gen[id(self)] += 1
            time.sleep(self.reboot_s)
            if to_bootsel:
                if world:
                    world.make_volume(self)
                self.state = "bootsel"
            else:
                self.reset = reset
                self._boot_t = time.monotonic()
                self.state = "app"
        threading.Thread(target=run, daemon=True).start()


class SimSerial:
    """pyserial-like data or console port of a SimBoard."""

    def __init__(self, world, board, role, baudrate=115200, timeout=0.3):
        self.world, self.b, self.role, self.timeout = world, board, role, timeout
        self.baudrate = baudrate
        self._out = bytearray()
        self._in = bytearray()
        self._closed = False
        self._gen = world.generation(board)
        if role == "console" and baudrate == 1200:
            board.reboot(to_bootsel=True, world=world)

    # --- pyserial surface ---------------------------------------------------
    def close(self):
        self._closed = True

    def reset_input_buffer(self):
        self._check()
        self._out.clear()

    def write(self, data):
        self._check()
        if self.role != "data":
            self.world.console_writes.append(bytes(data))
            return len(data)
        self._in += data
        while b"\n" in self._in:
            line, _, rest = bytes(self._in).partition(b"\n")
            self._in = bytearray(rest)
            self._line(line.decode("ascii", "replace").strip())
        return len(data)

    def readline(self):
        deadline = time.monotonic() + (self.timeout or 0)
        while True:
            self._check()
            self._service()
            i = self._out.find(b"\n")
            if i >= 0:
                line = bytes(self._out[:i + 1])
                del self._out[:i + 1]
                return line
            if time.monotonic() >= deadline:
                line = bytes(self._out)
                self._out.clear()
                return line
            time.sleep(0.005)

    def read(self, n=1):
        deadline = time.monotonic() + (self.timeout or 0)
        while len(self._out) < n and time.monotonic() < deadline:
            self._check()
            self._service()
            time.sleep(0.002)
        self._check()
        out = bytes(self._out[:n])
        del self._out[:n]
        return out

    # --- device behaviour ---------------------------------------------------
    def _check(self):
        if self._closed:
            raise SimSerialException("port closed")
        if self.b.state != "app" or self.world.generation(self.b) != self._gen:
            raise SimSerialException("device disconnected (%s)" % self.b.data_dev)

    def _reply(self, s):
        self._out += s.encode("ascii") + b"\n"

    def _service(self):
        """Core 1: answers the pending FM-1 request once the FM-1 is in UBOOT."""
        self.b.key_tick()
        if self.b._pending and self.b._uboot:
            line, self.b._pending = self.b._pending, None
            self._core1(line)

    def _line(self, line):
        b = self.b
        if line == "ping":
            return self._reply("PONG")
        if not b.legacy:
            if line == "ident":
                return self._reply(
                    "OK fm1t=2 fw=%s build=%s upstream=a632d92 board=%s soc=RP2040 dp=0 dm=1 "
                    "leds=%s binary=sim validation=SIMULATED" % (b.version, b.build, b.board_id, b.led_kind))
            if line == "hwinfo":
                return self._reply(
                    "OK clk_sys=120000000 chip_rev=2 flash_jedec=%s flash_vendor=winbond "
                    "flash_sfdp_bytes=%d flash_jedec_bytes=%d flash_build_bytes=%d "
                    "flash_declared_bytes=%d reset=%s uid=%s"
                    % (b.jedec, b.sfdp_bytes, b.sfdp_bytes, b.build_bytes, b.declared_bytes, b.reset, b.uid))
            if line == "diag":
                return self._reply("OK phase=%s uptime_ms=%d key_packets=%d log_dropped=0 dp_func=6 "
                                   "dm_func=6 dp_in=0 dm_in=0 uboot=%d"
                                   % (b.phase(), (time.monotonic() - b._boot_t) * 1000,
                                      0 if b._uboot else 1234, int(b._uboot)))
            if line == "piotest":
                keying = not b._uboot
                return self._reply("OK phase=%s samples=60000 dp_edges=%d dm_edges=%d dp_high=%d dm_high=%d"
                                   % (b.phase(), 1800 if keying else 0, 950 if keying else 0,
                                      15000 if keying else 0, 9000 if keying else 0))
            if line == "ledtest":
                self.world.led_tests += 1
                return self._reply("OK leds=%s" % b.led_kind)
            if line == "reboot":
                self._reply("OK reboot")
                return b.reboot(reset="reboot-cmd", world=self.world)
            if line == "bootsel":
                self._reply("OK bootsel")
                return b.reboot(to_bootsel=True, world=self.world)
        if line == "rekey":
            self._reply("OK rekey")
            return b.reboot(reset="rekey", world=self.world)
        if b._pending is None:
            b._pending = line           # one request at a time; extras are dropped

    def _core1(self, line):
        fm1 = self.b.fm1
        argv = line.split()
        if not argv:
            return
        if argv[0] == "status":
            return self._reply("OK uboot=1 v15=0 loader=1 loader_running=0")
        if argv[0] == "info":
            return self._reply("OK key=%s type=3 id=%s" % (fm1.chip_key, fm1.flash_id))
        if argv[0] == "read" and len(argv) == 3:
            addr, n = int(argv[1], 0), int(argv[2], 0)
            if n <= 0 or addr + n > FLASH:
                return self._reply("ERR range")
            fm1.reads += 1
            data = bytearray(fm1.flash[addr:addr + n])
            if fm1.flaky_read == fm1.reads:
                data[len(data) // 2] ^= 0x10
            self._reply("DATA %d" % n)
            if fm1.unplug_on_read == fm1.reads:
                self._out += data[:n // 3]
                self.world.unplug(self.b)
                return
            if fm1.stall_read == fm1.reads:
                self._out += data[:n // 2]
                return
            self._out += data
            return self._reply("END %08X" % (zlib.crc32(data) & 0xFFFFFFFF))
        if argv[0] in ("wsec", "memw", "jump", "runapp", "memr"):
            fm1.writes.append(line)
            return self._reply("ERR simulator-refuses-%s" % argv[0])
        return self._reply("ERR unknown")


class SimWorld:
    """A set of SimBoards behind a pyserial-like backend and a volume finder."""

    errors = (SimSerialException,)

    def __init__(self, tmpdir=None):
        self.boards = []
        self.other_ports = []       # unrelated serial devices (must never be written to)
        self.console_writes = []
        self.led_tests = 0
        self._gen = {}
        self.tmp = Path(tmpdir or tempfile.mkdtemp(prefix="fm1sim-"))

    def add(self, board: SimBoard) -> SimBoard:
        self.boards.append(board)
        self._gen[id(board)] = 0
        if board.state == "bootsel":
            self.make_volume(board)
        return board

    def generation(self, b):
        return self._gen[id(b)]

    def unplug(self, b):
        b.state = "gone"
        self._gen[id(b)] += 1

    def plug(self, b):
        b.state = "app"
        b._uboot = False
        b._boot_t = time.monotonic()
        b.reset = "por"
        self._gen[id(b)] += 1

    # --- serial backend -----------------------------------------------------
    def list_ports(self):
        self._poll_volumes()
        out = list(self.other_ports)
        for b in self.boards:
            if b.state != "app":
                continue
            for itf, dev, name in ((0, b.console_dev, "console"), (2, b.data_dev, "data")):
                out.append(PortInfo(device=dev, vid=RPI_VID, pid=TRANSPORTER_PID,
                                    serial_number=b.uid, location="1-1:x.%d" % itf,
                                    description="USB Serial Device (%s)" % dev,
                                    hwid="USB VID:PID=2E8A:000A SER=%s LOCATION=1-1:x.%d" % (b.uid, itf),
                                    interface="FM-1 Transporter %s" % name))
        return out

    def open(self, device, baudrate=115200, timeout=0.3):
        self._poll_volumes()
        for b in self.boards:
            if b.state == "app" and device in (b.data_dev, b.console_dev):
                return SimSerial(self, b, "data" if device == b.data_dev else "console",
                                 baudrate, timeout)
        for p in self.other_ports:
            if p.device == device:
                self.console_writes.append(("other-port-opened", device))
                raise SimSerialException("simulated foreign device %s refuses" % device)
        raise SimSerialException("could not open port %s" % device)

    def touch_1200(self, device):
        try:
            self.open(device, 1200).close()
        except SimSerialException:
            pass

    # --- boot disks ---------------------------------------------------------
    def make_volume(self, b):
        d = self.tmp / ("RPI-RP2-%s" % b.uid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "INFO_UF2.TXT").write_text(
            "UF2 Bootloader v3.0\nModel: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")
        (d / "INDEX.HTM").write_text("<html>simulated</html>")
        b.volume_dir = d

    def volume_candidates(self):
        self._poll_volumes()
        return [(b.volume_dir, "RPI-RP2") for b in self.boards
                if b.state == "bootsel" and b.volume_dir]

    def _poll_volumes(self):
        for b in self.boards:
            if b.state != "bootsel" or not b.volume_dir:
                continue
            for f in list(b.volume_dir.glob("*.uf2")) + list(b.volume_dir.glob("*.UF2")):
                data = f.read_bytes()
                f.unlink()
                try:
                    info = uf2mod.info(data)
                except uf2mod.UF2Error:
                    continue        # the boot ROM ignores invalid blocks
                b.flashed.append(info["sha256"])
                if info["boards"]:
                    b.board_id = info["boards"][0]
                    b.build = info["builds"][0] or b.build
                    b.legacy = False
                else:
                    b.legacy = True
                for x in b.volume_dir.iterdir():
                    x.unlink()
                b.volume_dir.rmdir()
                b.volume_dir = None
                b.reboot(reset="por", world=self)


def make_uf2(board_id: str, build="simulated", size=4096) -> bytes:
    """A small valid RP2040 UF2 carrying a board marker (for tests)."""
    import struct
    payload = bytearray(size)
    marker = ("FM1T-BOARD:%s;FM1T-BUILD:%s;" % (board_id, build)).encode()
    payload[256:256 + len(marker)] = marker
    blocks = []
    n = (len(payload) + 255) // 256
    for i in range(n):
        chunk = bytes(payload[i * 256:(i + 1) * 256])
        hdr = struct.pack("<8I", uf2mod.MAGIC0, uf2mod.MAGIC1, uf2mod.FLAG_FAMILY_ID,
                          uf2mod.XIP_BASE + i * 256, 256, i, n, uf2mod.RP2040_FAMILY)
        blk = hdr + chunk + bytes(476 - 256) + struct.pack("<I", uf2mod.MAGIC_END)
        blocks.append(blk)
    return b"".join(blocks)


def sim_env_from_environment():
    """FM1T_SIM=<board_id> makes the CLI run against a simulated board."""
    bid = os.environ.get("FM1T_SIM")
    if not bid:
        return None
    w = SimWorld()
    w.add(SimBoard(board_id=bid, fm1=FakeFM1() if os.environ.get("FM1T_SIM_FM1") else None))
    return w

"""Serial port discovery and Transporter identification.

The FM-1 Transporter enumerates as 2E8A:000A with two CDC interfaces:
interface 0 = console (CDC 0), interface 2 = data port (CDC 1), plus the
Raspberry Pi reset interface. Any Pico SDK program with USB stdio uses the
same VID:PID, so the VID:PID only selects candidates; a port is a Transporter
data port only once it answers `ping` with PONG, and its board is what `ident`
reports.

Writes: only ports with the Raspberry Pi VID (or a port the user named) are
ever opened, and the only bytes written during discovery are `ping`/`ident`
lines to the data interface. The console interface is never written to.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger("fm1_board_tool")

RPI_VID = 0x2E8A
TRANSPORTER_PID = 0x000A
BOOTSEL_PID = 0x0003            # RP2040 USB boot ROM (RPI-RP2 + PICOBOOT)
ITF_CONSOLE, ITF_DATA = 0, 2


@dataclass
class PortInfo:
    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    location: str | None = None
    interface: str | None = None
    description: str = ""
    hwid: str = ""

    @property
    def interface_number(self):
        """USB interface number, from pyserial's location ('1-1:1.2' on Linux,
        '1-1:x.2' on Windows) or hwid ('LOCATION=1-1:x.2')."""
        for text in (self.location or "", self.hwid or ""):
            m = re.search(r":(?:\d+|x)\.(\d+)", text)
            if m:
                return int(m.group(1))
        return None

    @property
    def role(self) -> str:
        """'data', 'console' or 'unknown', from the interface number or the
        interface string ('FM-1 Transporter data' / '... console')."""
        n = self.interface_number
        if n == ITF_DATA:
            return "data"
        if n == ITF_CONSOLE:
            return "console"
        text = ("%s %s" % (self.interface or "", self.description or "")).lower()
        if "transporter data" in text:
            return "data"
        if "transporter console" in text:
            return "console"
        return "unknown"

    @property
    def is_rpi(self) -> bool:
        return self.vid == RPI_VID

    @property
    def is_transporter_candidate(self) -> bool:
        return self.vid == RPI_VID and self.pid == TRANSPORTER_PID


@dataclass
class Board:
    """One physical board: its ports grouped by USB serial number."""
    serial: str
    ports: list = field(default_factory=list)

    def by_role(self, role):
        return [p for p in self.ports if p.role == role]


class PySerialBackend:
    """Real serial ports through pyserial."""

    def __init__(self):
        try:
            import serial  # noqa: F401
            import serial.tools.list_ports  # noqa: F401
        except ImportError as e:
            raise RuntimeError("pyserial is not installed: run `python -m pip install pyserial`") from e
        import serial
        import serial.tools.list_ports
        self.serial = serial

    def list_ports(self):
        out = []
        for p in self.serial.tools.list_ports.comports():
            out.append(PortInfo(device=p.device, vid=p.vid, pid=p.pid,
                                serial_number=p.serial_number, location=p.location,
                                interface=getattr(p, "interface", None),
                                description=p.description or "", hwid=p.hwid or ""))
        return out

    def open(self, device, baudrate=115200, timeout=0.3):
        return self.serial.Serial(device, baudrate, timeout=timeout)

    def touch_1200(self, device):
        """Opening the console at 1200 baud reboots a Pico SDK board (and the
        Transporter) into the RPI-RP2 boot ROM. Nothing is written."""
        try:
            s = self.serial.Serial(device, 1200)
            s.close()
        except (self.serial.SerialException, OSError) as e:
            log.debug("1200-baud touch on %s: %s (normal if the board reset at once)", device, e)

    errors = (OSError,)


def group_boards(ports) -> list:
    boards = {}
    for p in ports:
        if not p.is_transporter_candidate:
            continue
        key = p.serial_number or ("?" + p.device)
        boards.setdefault(key, Board(serial=key)).ports.append(p)
    return list(boards.values())


def parse_kv(reply: str) -> dict:
    return dict(kv.split("=", 1) for kv in reply.split()[1:] if "=" in kv)


def readline(s, timeout):
    s.timeout = timeout
    return s.readline().decode("ascii", "replace").strip()


def handshake(backend, device, timeout=1.0):
    """(serial, ident dict) if `device` is a Transporter data port, else None.

    ident is {} for an upstream (pre-multiboard) Transporter, which answers
    ping but queues ident for its core 1; that queued line is harmless there
    and answered `ERR unknown` once a UBOOT session runs."""
    try:
        s = backend.open(device)
    except Exception as e:
        log.debug("cannot open %s: %s", device, e)
        return None
    try:
        s.reset_input_buffer()
        s.write(b"ping\n")
        deadline = time.monotonic() + timeout
        got = False
        while time.monotonic() < deadline:
            line = readline(s, 0.2)
            if line == "PONG":
                got = True
                break
        if not got:
            s.close()
            return None
        s.write(b"ident\n")
        deadline = time.monotonic() + timeout
        ident = {}
        while time.monotonic() < deadline:
            line = readline(s, 0.2)
            if line.startswith("OK fm1t="):
                ident = parse_kv(line)
                break
        return s, ident
    except Exception as e:
        log.debug("handshake on %s failed: %s", device, e)
        try:
            s.close()
        except Exception:
            pass
        return None


@dataclass
class Found:
    device: str
    serial: str
    ident: dict
    console: str | None
    port: object = None     # the open serial object (caller closes)


def discover(backend, explicit=None, keep_open=False) -> list:
    """Transporter data ports that answer the handshake.

    With `explicit`, only that port is tried (any VID). Otherwise only ports
    with VID 2E8A / PID 000A are tried, the data interface first; console
    interfaces are never written to."""
    found = []
    if explicit:
        r = handshake(backend, explicit)
        if r:
            s, ident = r
            found.append(Found(explicit, "?", ident, None, s if keep_open else None))
            if not keep_open:
                s.close()
        return found
    for b in group_boards(backend.list_ports()):
        data = b.by_role("data") or b.by_role("unknown")
        console = (b.by_role("console") or [None])[0]
        for p in data:
            r = handshake(backend, p.device)
            if not r:
                continue
            s, ident = r
            found.append(Found(p.device, b.serial, ident, console.device if console else None,
                               s if keep_open else None))
            if not keep_open:
                s.close()
            break
    return found

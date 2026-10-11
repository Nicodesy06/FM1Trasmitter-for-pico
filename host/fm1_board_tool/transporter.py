"""Read-only client for the Transporter data port.

Only requests that change nothing on the FM-1 exist here: ping, the board
self-test commands (answered by the RP2040 itself), status, info and read.
There is deliberately no wsec/memw/jump/runapp method: writes live in
recovery/fm1_transporter_recover.py behind its safety gates.
"""

from __future__ import annotations

import logging
import time
import zlib

from .ports import parse_kv

log = logging.getLogger("fm1_board_tool")

FM1_FLASH_SIZE = 0x100000
EXPECTED_INFO = {"key": "980F", "type": "3", "id": "856014"}
READ_ONLY_COMMANDS = ("ping", "ident", "hwinfo", "diag", "piotest", "ledtest", "status", "info", "read")
BOARD_COMMANDS = ("ident", "hwinfo", "diag", "piotest", "ledtest")


class TransporterError(RuntimeError):
    pass


class Client:
    def __init__(self, s, port="?"):
        self.s, self.port = s, port

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass

    def _readline(self, timeout):
        self.s.timeout = timeout
        return self.s.readline().decode("ascii", "replace").strip()

    def _send(self, line):
        verb = line.split()[0]
        if verb not in READ_ONLY_COMMANDS and verb not in ("reboot", "bootsel"):
            raise TransporterError("refusing to send %r: not a read-only request" % verb)
        self.s.reset_input_buffer()
        self.s.write(line.encode("ascii") + b"\n")
        log.debug("> %s", line)

    def request(self, line, timeout=5.0, expect_prefix=None):
        self._send(line)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TransporterError("no reply to %r within %.1f s (%s)" % (line, timeout, self.port))
            reply = self._readline(min(left, 0.5))
            if not reply:
                continue
            log.debug("< %s", reply)
            if expect_prefix and not reply.startswith(expect_prefix) and not reply.startswith("ERR"):
                continue        # e.g. a stray PONG
            return reply

    def ping(self, timeout=1.0) -> bool:
        try:
            return self.request("ping", timeout, "PONG") == "PONG"
        except TransporterError:
            return False

    def board_cmd(self, cmd, timeout=3.0) -> dict:
        if cmd not in BOARD_COMMANDS:
            raise TransporterError("%s is not a board command" % cmd)
        reply = self.request(cmd, timeout, "OK")
        if not reply.startswith("OK"):
            raise TransporterError("%s: %s" % (cmd, reply))
        return parse_kv(reply)

    def ident(self, timeout=2.0) -> dict:
        return self.board_cmd("ident", timeout)

    def reboot(self, kind="reboot"):
        """`reboot` (watchdog) or `bootsel` (USB boot ROM). The port vanishes."""
        if kind not in ("reboot", "bootsel"):
            raise TransporterError("unknown reboot kind %r" % kind)
        reply = self.request(kind, 2.0, "OK")
        if reply != "OK %s" % kind:
            raise TransporterError("%s: %s" % (kind, reply))

    # --- FM-1 side: needs the FM-1 in UBOOT; all read-only -------------------

    def status(self, timeout=30.0) -> dict:
        reply = self.request("status", timeout, "OK uboot=")
        if not reply.startswith("OK uboot="):
            raise TransporterError("status: %s" % reply)
        return parse_kv(reply)

    def info(self, timeout=30.0) -> dict:
        reply = self.request("info", timeout, "OK key=")
        if not reply.startswith("OK"):
            raise TransporterError("info: %s" % reply)
        return parse_kv(reply)

    def read(self, addr, length, progress=None) -> bytes:
        if addr < 0 or length <= 0 or addr + length > FM1_FLASH_SIZE:
            raise TransporterError("read 0x%X+0x%X is outside the 1 MiB FM-1 flash" % (addr, length))
        reply = self.request("read %#x %d" % (addr, length), 30.0, "DATA")
        if reply != "DATA %d" % length:
            raise TransporterError("read: %s" % reply)
        data = bytearray()
        self.s.timeout = 10
        while len(data) < length:
            chunk = self.s.read(min(65536, length - len(data)))
            if not chunk:
                raise TransporterError("read stalled at 0x%X of 0x%X" % (len(data), length))
            data += chunk
            if progress:
                progress(len(data), length)
        end = self._readline(10)
        want = "END %08X" % (zlib.crc32(data) & 0xFFFFFFFF)
        if end != want:
            raise TransporterError("read 0x%X+0x%X: transfer check failed (%s, local %s)"
                                   % (addr, length, end or "no END", want))
        return bytes(data)


def check_fm1_info(info: dict) -> list:
    """Problems with an `info` reply, empty if it is an FM-1 (WL82 key 980F,
    1 MiB flash 856014)."""
    return ["%s=%s, expected %s" % (k, info.get(k), v) for k, v in EXPECTED_INFO.items()
            if info.get(k) != v]

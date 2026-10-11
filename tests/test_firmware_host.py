"""The firmware's own data-port protocol (src/transporter_proto.c) and JieLi
UBOOT/loader code (src/jieli_uboot.c), compiled for the host and run against
a mock UBOOT (tests/firmware_host/mock_uboot.c). SIMULATED evidence: it
checks framing, guards and the read-only paths, not USB timing."""

import os
import shutil
import subprocess
import zlib

import pytest

FLASH = 0x100000
SECTOR = 0x1000


def pattern():
    return bytes(((i * 2654435761) >> 24) & 0xFF for i in range(FLASH))


def run(harness, stdin: bytes, tmp_path, **env):
    e = dict(os.environ)
    e["MOCK_LOG"] = str(tmp_path / "mock.log")
    e["MOCK_FLASH_OUT"] = str(tmp_path / "flash.out")
    e.update({k: str(v) for k, v in env.items()})
    p = subprocess.run([str(harness)], input=stdin, capture_output=True, env=e, timeout=60)
    log = (tmp_path / "mock.log").read_text() if (tmp_path / "mock.log").exists() else ""
    return p, log


def lines(out: bytes):
    return out.decode("latin-1").splitlines()


def test_sfdp_parser(root, tmp_path):
    cc = shutil.which("gcc") or shutil.which("cc")
    if not cc:
        pytest.skip("no host C compiler")
    exe = tmp_path / "t"
    subprocess.run([cc, "-Wall", "-Werror", "-I", str(root / "src"), "-o", str(exe),
                    str(root / "tests/firmware_host/test_flash_sfdp.c"), str(root / "src/flash_sfdp.c")],
                   check=True)
    assert subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.strip() == "flash_sfdp OK"


def test_ping_and_ident_answered(harness, tmp_path):
    p, log = run(harness, b"ping\nident\n", tmp_path)
    out = lines(p.stdout)
    assert out[0] == "PONG"
    assert out[1].startswith("OK fm1t=2 ") and "board=host_harness" in out[1]
    assert log == ""                                  # nothing reached the FM-1


def test_status_and_info_decode_the_chip(harness, tmp_path):
    p, log = run(harness, b"status\n", tmp_path)
    assert lines(p.stdout) == ["OK uboot=1 v15=0 loader=1 loader_running=0"]
    p, log = run(harness, b"info\n", tmp_path)
    assert lines(p.stdout) == ["OK key=980F type=3 id=856014"]
    # loader uploaded through the ROM, then the JUMP, then read-only queries
    assert log.count("WRITE_MEMORY") == 47 and "JUMP 01C02000 1" in log
    assert "READ_KEY 00AC6900" in log and "GET_ONLINE_DEVICE" in log


def test_info_reports_other_chips_faithfully(harness, tmp_path):
    p, _ = run(harness, b"info\n", tmp_path, MOCK_CHIP_KEY="1234", MOCK_FLASH_ID="EF4015")
    assert lines(p.stdout) == ["OK key=1234 type=3 id=EF4015"]


def test_no_uboot(harness, tmp_path):
    for req in (b"info\n", b"read 0 16\n", b"wsec 0x4000 00000000\n" + bytes(SECTOR)):
        p, log = run(harness, req, tmp_path, MOCK_UBOOT=0)
        assert lines(p.stdout)[-1] == "ERR no-uboot"


def read_frame(out: bytes, n: int):
    head, _, rest = out.partition(b"\n")
    assert head == b"DATA %d" % n
    data, tail = rest[:n], rest[n:]
    return data, tail.decode().strip()


@pytest.mark.parametrize("addr, n", [(0, 512), (0x4000, 4096), (0xFF000, 4096), (0x12345, 1000), (0, FLASH)])
def test_read_framing_and_crc(harness, tmp_path, addr, n):
    p, log = run(harness, b"read %#x %d\n" % (addr, n), tmp_path)
    data, end = read_frame(p.stdout, n)
    assert data == pattern()[addr:addr + n]
    assert end == "END %08X" % (zlib.crc32(data) & 0xFFFFFFFF)
    assert "ERASE" not in log and "WRITE_FLASH" not in log      # reads stay read-only


@pytest.mark.parametrize("req", [b"read 0 0", b"read 0xFFFFF 2", b"read 0x100000 1", b"read 0 0x100001"])
def test_read_range_refused(harness, tmp_path, req):
    p, log = run(harness, req + b"\n", tmp_path)
    assert lines(p.stdout) == ["ERR range"]
    assert "READ_FLASH" not in log


def test_failing_read_is_padded_and_flagged(harness, tmp_path):
    """Three failed tries: the stream keeps its length (the host counts bytes)
    and ends in END FAIL instead of a CRC."""
    p, _ = run(harness, b"read 0 16384\n", tmp_path, MOCK_FAIL_READ=2)
    data, end = read_frame(p.stdout, 16384)
    assert end == "END FAIL"


def test_flaky_read_passes_crc_but_double_read_catches_it(harness, tmp_path):
    """A bit flipped inside the FM-1 read path is CRC-consistent on the wire:
    only comparing two full reads detects it - which is why dumps are read twice."""
    p1, _ = run(harness, b"read 0 8192\n", tmp_path, MOCK_FLAKY_READ=1)
    d1, e1 = read_frame(p1.stdout, 8192)
    p2, _ = run(harness, b"read 0 8192\n", tmp_path)
    d2, e2 = read_frame(p2.stdout, 8192)
    assert e1 == "END %08X" % (zlib.crc32(d1) & 0xFFFFFFFF)
    assert d1 != d2


@pytest.mark.parametrize("addr", [0x0, 0x3000, 0x93000, 0xFF000, 0x4800])
def test_wsec_outside_app_area_refused_by_firmware(harness, tmp_path, addr):
    sec = bytes(SECTOR)
    req = b"wsec %#x %08X\n" % (addr, zlib.crc32(sec)) + sec
    p, log = run(harness, req, tmp_path)
    assert lines(p.stdout) == ["ERR range"]
    assert "ERASE_SECTOR" not in log and "WRITE_FLASH" not in log
    assert (tmp_path / "flash.out").read_bytes() == pattern()


def test_wsec_bad_crc_refused(harness, tmp_path):
    sec = bytes(range(256)) * 16
    p, log = run(harness, b"wsec 0x4000 DEADBEEF\n" + sec, tmp_path)
    assert lines(p.stdout)[0].startswith("ERR crc")
    assert "ERASE_SECTOR" not in log


def test_wsec_in_app_area_erases_writes_verifies(harness, tmp_path):
    sec = bytes((i * 13) & 0xFF for i in range(SECTOR))
    p, log = run(harness, b"wsec 0x50000 %08X\n" % zlib.crc32(sec) + sec, tmp_path)
    assert lines(p.stdout) == ["OK"]
    out = (tmp_path / "flash.out").read_bytes()
    want = bytearray(pattern())
    want[0x50000:0x51000] = sec
    assert out == bytes(want)
    assert log.count("ERASE_SECTOR 050000") == 1 and log.count("WRITE_FLASH") == 8


def test_unknown_and_malformed(harness, tmp_path):
    p, _ = run(harness, b"format\n", tmp_path)
    assert lines(p.stdout) == ["ERR unknown"]
    p, _ = run(harness, b"memw 0x1C02000 0 0\n", tmp_path)       # bad length goes to core 1
    assert lines(p.stdout) == ["ERR len"]
    p, _ = run(harness, b"x" * 300 + b"\n", tmp_path)            # long line is truncated, not overflowed
    assert lines(p.stdout) == ["ERR unknown"]


def test_one_request_at_a_time(harness, tmp_path):
    """Two requests in one USB packet: the second is dropped while the first
    is pending (firmware behaviour the host tools rely on)."""
    p, _ = run(harness, b"status\ninfo\n", tmp_path)
    assert lines(p.stdout) == ["OK uboot=1 v15=0 loader=1 loader_running=0"]


class Session:
    """One harness process = one UBOOT session; requests sent one by one."""

    def __init__(self, harness, tmp_path, **env):
        e = dict(os.environ)
        e["MOCK_LOG"] = str(tmp_path / "session.log")
        e.update({k: str(v) for k, v in env.items()})
        self.p = subprocess.Popen([str(harness)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=e)

    def send(self, data: bytes):
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def line(self):
        return self.p.stdout.readline().decode().strip()

    def read(self, n):
        return self.p.stdout.read(n)

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


def test_ram_run_cipher_round_trip_then_loader_lockout(harness, tmp_path):
    """memw/memr through the ROM stage: the firmware ciphers, the mock
    deciphers (independent implementation), so a round trip returns the same
    bytes. Once the flash loader runs, RAM-run requests are refused."""
    s = Session(harness, tmp_path)
    try:
        payload = bytes((i * 31 + 7) & 0xFF for i in range(300))
        s.send(b"memw 0x1C10000 300 %08X\n" % zlib.crc32(payload) + payload)
        assert s.line() == "OK"
        s.send(b"memr 0x1C10000 300\n")
        assert s.line() == "DATA 300"
        back = s.read(300)
        assert back == payload
        assert s.line() == "END %08X" % zlib.crc32(payload)
        s.send(b"info\n")
        assert s.line() == "OK key=980F type=3 id=856014"
        s.send(b"memr 0x1C10000 16\n")
        assert s.line() == "ERR loader-running"
        s.send(b"status\n")
        assert s.line() == "OK uboot=1 v15=0 loader=1 loader_running=1"
    finally:
        s.close()


def test_dump_session_is_read_only(harness, tmp_path):
    """A first-contact session (status, info, two full reads) sends only
    read-only commands to the FM-1."""
    s = Session(harness, tmp_path)
    try:
        for req in (b"status\n", b"info\n"):
            s.send(req)
            assert s.line().startswith("OK")
        reads = []
        for _ in range(2):
            s.send(b"read 0 1048576\n")
            assert s.line() == "DATA 1048576"
            reads.append(s.read(FLASH))
            assert s.line() == "END %08X" % zlib.crc32(reads[-1])
        assert reads[0] == reads[1] == pattern()
    finally:
        s.close()
    cmds = {l.split()[0] for l in (tmp_path / "session.log").read_text().splitlines()}
    assert cmds <= {"WRITE_MEMORY", "JUMP", "GET_USB_BUFF_SIZE", "READ_KEY", "GET_ONLINE_DEVICE",
                    "READ_FLASH"}
    assert not cmds & {"ERASE_SECTOR", "WRITE_FLASH", "RUN_APP"}

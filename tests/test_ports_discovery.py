"""Serial port discovery, CDC interface roles, device selection, Transporter
recognition. Uses the simulator's port list (SIMULATED)."""

import pytest

from fm1_board_tool.ports import PortInfo, discover, group_boards, handshake
from fm1_board_tool.sim import SimWorld, SimBoard


@pytest.mark.parametrize("loc, hwid, iface, n, role", [
    ("1-1:x.2", "", None, 2, "data"),                                  # Windows
    ("1-1:x.0", "", None, 0, "console"),
    ("3-2:1.2", "", None, 2, "data"),                                  # Linux
    ("3-2:1.0", "", None, 0, "console"),
    (None, "USB VID:PID=2E8A:000A SER=E66 LOCATION=1-4:x.2", None, 2, "data"),
    (None, "", "FM-1 Transporter data", None, "data"),                 # macOS: interface string only
    (None, "", "FM-1 Transporter console", None, "console"),
    (None, "", "Board CDC", None, "unknown"),
])
def test_interface_roles(loc, hwid, iface, n, role):
    p = PortInfo("COM9", 0x2E8A, 0x000A, "E66", loc, iface, "", hwid)
    assert p.interface_number == n
    assert p.role == role


def test_grouping_by_serial_ignores_other_devices():
    ports = [PortInfo("COM3", 0x0403, 0x6001, "FTDI1", "1-2:x.0"),          # an FTDI adapter
             PortInfo("COM7", 0x2E8A, 0x000A, "AAA", "1-1:x.0"),
             PortInfo("COM8", 0x2E8A, 0x000A, "AAA", "1-1:x.2"),
             PortInfo("COM9", 0x2E8A, 0x0005, "BBB", "1-3:x.0")]            # MicroPython, other PID
    boards = group_boards(ports)
    assert len(boards) == 1 and boards[0].serial == "AAA"
    assert [p.device for p in boards[0].by_role("data")] == ["COM8"]


def world(*boards, others=()):
    w = SimWorld()
    for b in boards:
        w.add(b)
    w.other_ports = list(others)
    return w


def test_discover_finds_data_port_and_never_writes_console():
    w = world(SimBoard("dollatek_rp2040_typec"))
    found = discover(w)
    assert len(found) == 1
    f = found[0]
    assert (f.device, f.console) == ("COM8", "COM7")
    assert f.ident["board"] == "dollatek_rp2040_typec"
    assert w.console_writes == []


def test_discover_never_opens_foreign_ports():
    other = PortInfo("COM3", 0x0403, 0x6001, "FT1", "1-2:x.0", None, "USB Serial Port")
    w = world(SimBoard("pico_rp2040"), others=[other])
    discover(w)
    assert ("other-port-opened", "COM3") not in w.console_writes


def test_multiple_boards_each_identified():
    w = world(SimBoard("dollatek_rp2040_typec", uid="A1", port_num=7),
              SimBoard("xiao_rp2040", uid="B2", port_num=20))
    found = {f.ident["board"]: f.device for f in discover(w)}
    assert found == {"dollatek_rp2040_typec": "COM8", "xiao_rp2040": "COM21"}


def test_legacy_firmware_recognised_without_ident():
    w = world(SimBoard("xiao_rp2040", legacy=True))
    found = discover(w)
    assert len(found) == 1 and found[0].ident == {}


def test_handshake_fails_cleanly_on_missing_port():
    w = world()
    assert handshake(w, "COM42") is None
    assert discover(w, explicit="COM42") == []

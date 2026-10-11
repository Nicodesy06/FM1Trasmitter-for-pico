"""End-to-end host flows against the behavioural simulator (SIMULATED):
self-test, flashing safety, watchdog reboot/reconnection, disconnects,
timeouts, device selection, fake FM-1 and dump verification."""

import json
import time

import pytest

from fm1_board_tool import cli, profiles as P, selftest
from fm1_board_tool.env import Env
from fm1_board_tool.flash import flash, FlashError
from fm1_board_tool.ports import discover
from fm1_board_tool.sim import SimWorld, SimBoard, FakeFM1, SimSerialException, make_uf2
from fm1_board_tool.transporter import Client, TransporterError, check_fm1_info

DOLLA = "dollatek_rp2040_typec"


def env_with(*boards, interactive=False, answers=()):
    w = SimWorld()
    for b in boards:
        w.add(b)
    it = iter(answers)
    return Env(sim=w, interactive=interactive, input_fn=lambda p: next(it)), w


def uf2_file(tmp_path, board, build="b1", name=None):
    f = tmp_path / (name or "fm1_transporter_%s.uf2" % board)
    f.write_bytes(make_uf2(board, build))
    return f


# --- self-test -----------------------------------------------------------

def test_selftest_without_fm1_passes_board_checks(tmp_path):
    env, w = env_with(SimBoard(DOLLA))
    rep = selftest.run(env, P.get_profile(DOLLA), console_seconds=0.2, logdir=tmp_path)
    st = {c.id: c.status for c in rep.checks}
    assert not rep.failed and rep.verdict.startswith("PASS (board only")
    for cid in ("cdc.enumeration", "cdc.interfaces", "ident.board", "ident.pins", "clock.sys",
                "flash.jedec", "pio.recovery_config", "pio.pad_activity", "watchdog.reboot",
                "usb.disconnect_detected", "protocol.board_commands", "logs.written"):
        assert st[cid] == selftest.PASS, cid
    for cid in ("fm1.usb_key_ack", "fm1.pio_usb_enumeration", "fm1.rom_identification",
                "fm1.flash_read_repeatable"):
        assert st[cid] == selftest.REQ_FM1        # a COM port is no proof of the FM-1 path
    assert st["signal.usb_key_waveform"] == selftest.REQ_INSTR
    assert st["led.visual"] == selftest.NOT_TESTED                # non-interactive
    assert all(c.evidence in ("SIMULATED", "") for c in rep.checks)
    j = json.loads(next(tmp_path.glob("selftest-*.json")).read_text())
    assert j["evidence"] == "SIMULATED" and j["counts"]["REQUIRES FM-1"] == 4


def test_selftest_never_queues_fm1_requests(tmp_path):
    b = SimBoard(DOLLA)
    env, w = env_with(b)
    selftest.run(env, P.get_profile(DOLLA), console_seconds=0.1, logdir=tmp_path, reboot_test=False)
    assert b._pending is None


def test_selftest_led_answer_recorded(tmp_path):
    env, w = env_with(SimBoard(DOLLA), interactive=True, answers=["n"])
    rep = selftest.run(env, P.get_profile(DOLLA), console_seconds=0.1, logdir=tmp_path, reboot_test=False)
    led = next(c for c in rep.checks if c.id == "led.visual")
    assert led.status == selftest.FAIL and not led.blocking and not rep.failed
    assert w.led_tests == 1


def test_selftest_flags_wrong_board_and_capacity(tmp_path):
    b = SimBoard("xiao_rp2040", sfdp_bytes=4 << 20)
    env, w = env_with(b)
    rep = selftest.run(env, P.get_profile(DOLLA), console_seconds=0.1, logdir=tmp_path, reboot_test=False)
    st = {c.id: (c.status, c.detail) for c in rep.checks}
    assert st["ident.board"][0] == selftest.FAIL
    assert st["flash.capacity"][0] == selftest.FAIL and "NOT what is fitted" in st["flash.capacity"][1]
    assert rep.failed


def test_selftest_legacy_firmware(tmp_path):
    env, w = env_with(SimBoard("xiao_rp2040", legacy=True))
    rep = selftest.run(env, P.get_profile("xiao_rp2040"), console_seconds=0.1, logdir=tmp_path)
    c = next(c for c in rep.checks if c.id == "ident.board")
    assert c.status == selftest.FAIL and "legacy" in c.detail


def test_selftest_no_board(tmp_path):
    env, w = env_with()
    rep = selftest.run(env, P.get_profile(DOLLA), console_seconds=0.1, logdir=tmp_path, wait=0.5)
    assert next(c for c in rep.checks if c.id == "cdc.enumeration").status == selftest.FAIL


# --- flashing ------------------------------------------------------------

def test_flash_from_running_firmware_via_bootsel_command(tmp_path):
    b = SimBoard(DOLLA, build="old")
    env, w = env_with(b)
    r = flash(env, P.get_profile(DOLLA), uf2_file(tmp_path, DOLLA, "new1"), wait_bootsel=5, wait_app=5)
    assert all(ok for ok, _ in r.steps.values())
    assert r.ident["build"] == "new1" and len(b.flashed) == 1


def test_flash_from_legacy_uses_1200_touch_on_console(tmp_path):
    b = SimBoard("xiao_rp2040", legacy=True)
    env, w = env_with(b)
    r = flash(env, P.get_profile("xiao_rp2040"), uf2_file(tmp_path, "xiao_rp2040"), wait_bootsel=5,
              wait_app=5)
    assert r.ident["board"] == "xiao_rp2040" and not b.legacy


def test_flash_refuses_wrong_board_image_before_touching_anything(tmp_path):
    b = SimBoard(DOLLA, state="bootsel")
    env, w = env_with(b)
    with pytest.raises(FlashError, match="built for board xiao_rp2040"):
        flash(env, P.get_profile(DOLLA), uf2_file(tmp_path, "xiao_rp2040"))
    assert b.flashed == [] and b.state == "bootsel"


def test_flash_refuses_two_boot_disks(tmp_path):
    env, w = env_with(SimBoard(DOLLA, uid="A", state="bootsel"), SimBoard(DOLLA, uid="B", state="bootsel"))
    with pytest.raises(FlashError, match="several RPI-RP2"):
        flash(env, P.get_profile(DOLLA), uf2_file(tmp_path, DOLLA))
    assert all(b.flashed == [] for b in w.boards)


def test_flash_refuses_two_running_boards(tmp_path):
    env, w = env_with(SimBoard(DOLLA, uid="A", port_num=3), SimBoard(DOLLA, uid="B", port_num=10))
    with pytest.raises(FlashError, match="refusing to guess"):
        flash(env, P.get_profile(DOLLA), uf2_file(tmp_path, DOLLA))
    assert all(b.state == "app" for b in w.boards)


def test_flash_detects_board_coming_back_with_other_firmware(tmp_path):
    """A board that keeps its old firmware (copy ignored) is reported, not assumed."""
    b = SimBoard(DOLLA, state="bootsel")
    env, w = env_with(b)
    bad = tmp_path / "x.uf2"
    good = make_uf2(DOLLA, "zz")
    bad.write_bytes(good)
    orig = w._poll_volumes

    def ignore_and_reboot():
        for f in list(b.volume_dir.glob("*.uf2")) if b.volume_dir else []:
            f.unlink()
            b.board_id = "pico_rp2040"
            for x in b.volume_dir.iterdir():
                x.unlink()
            b.volume_dir.rmdir()
            b.volume_dir = None
            b.reboot(world=w)
        orig()
    w._poll_volumes = ignore_and_reboot
    with pytest.raises(FlashError, match="reporting 'pico_rp2040'"):
        flash(env, P.get_profile(DOLLA), bad, wait_bootsel=3, wait_app=5)


def test_flash_times_out_without_board(tmp_path):
    env, w = env_with()
    t0 = time.monotonic()
    with pytest.raises(FlashError, match="no RPI-RP2"):
        flash(env, P.get_profile(DOLLA), uf2_file(tmp_path, DOLLA), wait_bootsel=0.5)
    assert time.monotonic() - t0 < 5


# --- protocol, reconnection, faults -------------------------------------------

def connect(w):
    f = discover(w)[0]
    return Client(w.open(f.device), f.device)


def test_watchdog_reboot_reconnects_with_reset_reason():
    b = SimBoard(DOLLA)
    w = SimWorld()
    w.add(b)
    c = connect(w)
    c.reboot("reboot")
    time.sleep(0.15)
    with pytest.raises(SimSerialException):
        c.ping()
        c.s.readline()
    deadline = time.monotonic() + 3
    while not discover(w) and time.monotonic() < deadline:
        time.sleep(0.1)
    c2 = connect(w)
    assert c2.board_cmd("hwinfo")["reset"] == "reboot-cmd"


def test_fm1_requests_wait_for_uboot_then_time_out_cleanly():
    w = SimWorld()
    w.add(SimBoard(DOLLA, fm1=FakeFM1(answers_key=False)))
    c = connect(w)
    t0 = time.monotonic()
    with pytest.raises(TransporterError, match="no reply"):
        c.status(timeout=0.5)
    assert time.monotonic() - t0 < 2


def test_read_only_session_with_fake_fm1():
    fm1 = FakeFM1()
    w = SimWorld()
    w.add(SimBoard(DOLLA, fm1=fm1))
    c = connect(w)
    assert c.board_cmd("diag")["phase"] == "uboot"
    assert c.status()["uboot"] == "1"
    info = c.info()
    assert check_fm1_info(info) == []
    a, b = c.read(0, 0x100000), c.read(0, 0x100000)
    assert a == b == bytes(fm1.flash)
    assert fm1.writes == []


def test_wrong_chip_detected():
    w = SimWorld()
    w.add(SimBoard(DOLLA, fm1=FakeFM1(chip_key="1234", flash_id="EF4018")))
    probs = check_fm1_info(connect(w).info())
    assert any("key=1234" in p for p in probs) and any("id=EF4018" in p for p in probs)


def test_flaky_read_caught_by_double_read():
    w = SimWorld()
    w.add(SimBoard(DOLLA, fm1=FakeFM1(flaky_read=2)))
    c = connect(w)
    assert c.read(0, 0x10000) != c.read(0, 0x10000)


def test_stalled_read_times_out():
    w = SimWorld()
    w.add(SimBoard(DOLLA, fm1=FakeFM1(stall_read=1)))
    c = connect(w)
    with pytest.raises(TransporterError, match="stalled|transfer check"):
        c.s.timeout = 0.3
        c.read(0, 0x8000)


def test_unplug_mid_read_is_an_error_not_a_hang():
    w = SimWorld()
    w.add(SimBoard(DOLLA, fm1=FakeFM1(unplug_on_read=1)))
    c = connect(w)
    t0 = time.monotonic()
    with pytest.raises((SimSerialException, TransporterError)):
        c.read(0, 0x100000)
    assert time.monotonic() - t0 < 5


def test_client_cannot_send_writes():
    w = SimWorld()
    w.add(SimBoard(DOLLA, fm1=FakeFM1()))
    c = connect(w)
    for line in ("wsec 0x4000 00000000", "memw 0x1C02000 4 0", "jump 0x1C02000 0", "runapp", "rekey"):
        with pytest.raises(TransporterError, match="not a read-only request"):
            c.request(line)


# --- CLI ---------------------------------------------------------------------

def test_cli_selftest_and_fm1_check_in_simulator(tmp_path, capsys):
    assert cli.main(["--sim", DOLLA, "--logdir", str(tmp_path), "selftest", "--board", DOLLA,
                     "--console-seconds", "0.1"]) == 0
    assert "PASS (board only" in capsys.readouterr().out
    assert cli.main(["--sim", DOLLA, "--sim-fm1", "fm1-check", "--wait", "3", "--dump",
                     str(tmp_path / "d"), "--logdir", str(tmp_path)]) == 0
    side = json.loads(next((tmp_path / "d").glob("*.json")).read_text())
    assert side["double_read"] and side["reads_identical"] and side["evidence"] == "SIMULATED"
    assert side["info"] == {"key": "980F", "type": "3", "id": "856014"}


def test_cli_fm1_check_refuses_other_chip(tmp_path, monkeypatch):
    real = cli.make_env

    def env(args):
        e = real(args)
        e.sim.boards[0].fm1 = FakeFM1(chip_key="ABCD")
        return e
    monkeypatch.setattr(cli, "make_env", env)
    assert cli.main(["--sim", DOLLA, "fm1-check", "--wait", "3", "--dump", str(tmp_path / "d"),
                     "--logdir", str(tmp_path)]) == 1
    assert not (tmp_path / "d").exists()


def test_cli_flash_requires_known_board(tmp_path):
    assert cli.main(["--sim", DOLLA, "--logdir", str(tmp_path), "flash", "--board", "nope",
                     "--uf2", str(uf2_file(tmp_path, DOLLA))]) == 1
    assert cli.main(["--sim", DOLLA, "--logdir", str(tmp_path), "flash", "--board", "generic_rp2040",
                     "--uf2", str(uf2_file(tmp_path, DOLLA))]) == 1


def test_cli_boards_and_conflicts(capsys, tmp_path):
    assert cli.main(["--logdir", str(tmp_path), "boards", "validate"]) == 0
    assert cli.main(["--logdir", str(tmp_path), "boards", "conflicts", DOLLA, "GP0=UART0_TX"]) == 1
    assert "CONFLICT" in capsys.readouterr().out

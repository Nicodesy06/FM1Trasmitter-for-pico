"""The guided hardware procedure, driven with scripted answers in the simulator."""

import pytest

from fm1_board_tool import hwtest, profiles as P
from fm1_board_tool.env import Env
from fm1_board_tool.sim import SimWorld, SimBoard, FakeFM1, make_uf2

DOLLA = "dollatek_rp2040_typec"


def env(board, answers):
    w = SimWorld()
    w.add(board)
    it = iter(answers)
    return Env(sim=w, interactive=True, input_fn=lambda p: next(it)), w


def statuses(rep):
    return {c.id: c.status for c in rep.checks}


def test_phase_a_flashes_and_selftests(tmp_path):
    uf2 = tmp_path / "fw.uf2"
    uf2.write_bytes(make_uf2(DOLLA, "hw1"))
    e, w = env(SimBoard(DOLLA, state="bootsel"), ["n", "RP2040", "W25Q128", "y", "", "y"])
    rep = hwtest.run(e, P.get_profile(DOLLA), uf2=uf2, phases="a", logdir=tmp_path)
    st = statuses(rep)
    assert not rep.failed and "stopped" not in rep.meta
    assert st["hw.bootsel_enumeration"] == st["flash.ident_board"] == st["watchdog.reboot"] == "PASS"
    assert st["led.visual"] == "PASS"
    assert rep.meta["flash_marking"] == "W25Q128"


def test_phase_a_stops_if_gp_wired(tmp_path):
    e, w = env(SimBoard(DOLLA), ["y"])
    rep = hwtest.run(e, P.get_profile(DOLLA), phases="a", logdir=tmp_path)
    assert rep.meta["stopped"].startswith("disconnect GP0/GP1")


def test_phase_b_refuses_without_full_checklist(tmp_path):
    fm1 = FakeFM1()
    e, w = env(SimBoard(DOLLA, fm1=fm1), ["y", "y", "y", "n"])
    rep = hwtest.run(e, P.get_profile(DOLLA), phases="b", logdir=tmp_path, dump_dir=tmp_path / "d")
    assert statuses(rep)["fm1.preconditions"] == "NOT TESTED"
    assert fm1.reads == 0 and not (tmp_path / "d").exists()


def test_phase_b_read_only_identification_and_dump(tmp_path):
    fm1 = FakeFM1()
    answers = ["y"] * len(hwtest.FM1_CHECKLIST) + ["", "y"]
    e, w = env(SimBoard(DOLLA, fm1=fm1), answers)
    rep = hwtest.run(e, P.get_profile(DOLLA), phases="b", logdir=tmp_path, dump_dir=tmp_path / "d", wait=5)
    st = statuses(rep)
    for cid in ("fm1.usb_key_ack", "fm1.pio_usb_enumeration", "fm1.rom_identification",
                "fm1.flash_read_repeatable"):
        assert st[cid] == "PASS", cid
    assert fm1.writes == [] and fm1.reads == 2
    assert len(list((tmp_path / "d").glob("*.bin"))) == 1


def test_phase_b_stops_on_wrong_chip(tmp_path):
    fm1 = FakeFM1(chip_key="1111")
    answers = ["y"] * len(hwtest.FM1_CHECKLIST) + [""]
    e, w = env(SimBoard(DOLLA, fm1=fm1), answers)
    rep = hwtest.run(e, P.get_profile(DOLLA), phases="b", logdir=tmp_path, dump_dir=tmp_path / "d", wait=5)
    assert statuses(rep)["fm1.rom_identification"] == "FAIL"
    assert fm1.reads == 0


def test_phase_b_fm1_never_answers(tmp_path):
    fm1 = FakeFM1(answers_key=False)
    answers = ["y"] * len(hwtest.FM1_CHECKLIST) + [""]
    e, w = env(SimBoard(DOLLA, fm1=fm1), answers)
    rep = hwtest.run(e, P.get_profile(DOLLA), phases="b", logdir=tmp_path, wait=1)
    st = statuses(rep)
    assert st["fm1.usb_key_ack"] == "FAIL" and rep.meta["stopped"] == "no UBOOT"
    assert w.boards[0]._pending is None          # nothing queued for the FM-1

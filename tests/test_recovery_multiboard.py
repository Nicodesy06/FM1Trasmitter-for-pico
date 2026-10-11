"""recovery/fm1_transporter_recover.py: explicit board selection, board
identity before use, per-board firmware, console never probed."""

import argparse
import types
from pathlib import Path
from unittest import mock

import pytest

import fm1_transporter_recover as tr

DOLLA = "dollatek_rp2040_typec"


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def run(*argv, transporter=None, input_fn=None):
    return tr.main(["--log-dir", "logs", "--wait", "1"] + list(argv), transporter=transporter,
                   input_fn=input_fn)


def test_board_list(capsys):
    assert set(tr.board_ids()) == {"xiao_rp2040", DOLLA, "pico_rp2040"}      # generic stays out
    assert run("boards") == 0


@pytest.mark.parametrize("cmd", [["setup"], ["flash-board"], ["--dry-run", "flash-board"]])
def test_commands_that_install_firmware_need_a_board(cmd, capsys):
    assert run(*cmd) != 0
    assert "needs the board: add --board" in capsys.readouterr().out


def test_unknown_or_disabled_board_refused(capsys):
    assert run("--dry-run", "--board", "generic_rp2040", "flash-board", "x.uf2") != 0
    assert run("--dry-run", "--board", "esp32", "flash-board", "x.uf2") != 0


def test_flash_board_dry_run_accepts_matching_image(root, capsys):
    uf2 = root / "dist" / DOLLA / "fm1_transporter_dollatek_rp2040-noloader.uf2"
    assert run("--dry-run", "--board", DOLLA, "flash-board", str(uf2)) == 0
    assert "UF2 built for dollatek_rp2040_typec" in capsys.readouterr().out


def test_flash_board_refuses_other_boards_image(root, capsys):
    uf2 = root / "dist" / "xiao_rp2040" / "fm1_transporter_xiao_rp2040-noloader.uf2"
    assert run("--dry-run", "--board", DOLLA, "flash-board", str(uf2)) != 0
    assert "built for board xiao_rp2040" in capsys.readouterr().out


def test_flash_board_refuses_upstream_image_for_dollatek(capsys):
    assert run("--dry-run", "--board", DOLLA, "flash-board", str(tr.PLACEHOLDER_UF2)) != 0
    assert "only be the upstream XIAO firmware" in capsys.readouterr().out


def test_flash_board_refuses_placeholder(root, capsys):
    uf2 = root / "dist" / DOLLA / "fm1_transporter_dollatek_rp2040-placeholder.uf2"
    assert run("--dry-run", "--board", DOLLA, "flash-board", str(uf2)) != 0
    assert "loader placeholder" in capsys.readouterr().out


def test_flash_xiao_is_flash_board_xiao(capsys):
    assert run("--dry-run", "--board", DOLLA, "flash-xiao", str(tr.PLACEHOLDER_UF2)) != 0
    assert "flash-xiao is for the XIAO" in capsys.readouterr().out


def real_transporter(board):
    m = tr.MockTransporter(board=board)
    return tr.Transporter(m, "COM8", mock=False), m


def test_ident_must_name_selected_board():
    t, m = real_transporter("xiao_rp2040")
    args = argparse.Namespace(board=DOLLA, firmware="auto")
    with pytest.raises(tr.fu.SafetyError, match="runs firmware for xiao_rp2040, not dollatek"):
        tr.check_board_ident(t, args)


def test_ident_missing_means_wrong_firmware():
    t, m = real_transporter(None)            # legacy firmware: queues ident, never answers it
    with mock.patch.object(t, "request", return_value="ERR unknown"):
        with pytest.raises(tr.fu.SafetyError, match="did not answer ident"):
            tr.check_board_ident(t, argparse.Namespace(board=DOLLA, firmware="auto"))


def test_legacy_xiao_firmware_never_sent_ident():
    t, m = real_transporter(None)
    tr.check_board_ident(t, argparse.Namespace(board="xiao_rp2040", firmware="auto"))
    assert "ident" not in m.requests and m._pending is None


def test_matching_ident_passes():
    t, m = real_transporter(DOLLA)
    tr.check_board_ident(t, argparse.Namespace(board=DOLLA, firmware="auto"))


def test_console_interface_never_probed():
    ports = [types.SimpleNamespace(device="COM7", vid=0x2E8A, location="1-1:x.0", hwid=""),
             types.SimpleNamespace(device="COM8", vid=0x2E8A, location="1-1:x.2", hwid=""),
             types.SimpleNamespace(device="COM9", vid=0x2E8A, location=None, hwid="")]
    with mock.patch.object(tr, "rp2_ports", return_value=ports), mock.patch.object(tr.sys, "platform", "win32"):
        assert tr.candidate_ports() == ["COM8", "COM9"]


def test_dollatek_firmware_comes_from_dist(root):
    fw = tr.board_firmware(DOLLA)
    assert fw["multiboard"] and fw["placeholder"].parent == root / "dist" / DOLLA
    assert tr.fu.sha256(fw["placeholder"].read_bytes()) == fw["placeholder_sha256"]
    assert fw["out"].name == "fm1_transporter_dollatek_rp2040.uf2"
    with pytest.raises(tr.fu.UnbrickError):
        tr.board_firmware(DOLLA, "upstream")


def test_wizard_dry_run_for_dollatek(capsys):
    """Board wiring text and flash step for the DollaTek; nothing else changes."""
    answers = iter(["q"])
    rc = run("--dry-run", "wizard", "--board", DOLLA, "--uf2", "none.uf2", "--out", "bk",
             input_fn=lambda p: next(answers))
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "Step 0 of 6" in out


def test_wizard_asks_for_the_board_when_missing(capsys):
    answers = iter(["7", "pico_rp2040", "q"])
    run("--dry-run", "wizard", "--uf2", "none.uf2", "--out", "bk", input_fn=lambda p: next(answers))
    out = capsys.readouterr().out
    assert "Which RP2040 board" in out and "Board: pico_rp2040" in out


def test_wiring_text_per_board():
    assert tr.wiring_text("xiao_rp2040") == tr.WIRING_TEXT
    t = tr.wiring_text(DOLLA)
    assert "GP0 (left edge, pin 1 from the USB-C end)" in t and "D+" in t
    assert "NOT connected" in t and "not by the" in t and "3V3" in t

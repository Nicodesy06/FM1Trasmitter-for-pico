"""The port must not change what made the XIAO work: the JieLi protocol, the
USB_KEY / pulse timing, the PIO programs, the PIO-USB patch and the host
logic. Compared against the pristine upstream import commit."""

import re
import subprocess

import pytest

UPSTREAM_COMMIT = "6724113"     # "Import FM-1 Transporter upstream a632d92 unmodified"


def upstream(root, path):
    try:
        return subprocess.run(["git", "-C", str(root), "show", "%s:%s" % (UPSTREAM_COMMIT, path)],
                               capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("git history with the upstream import not available")


@pytest.mark.parametrize("path", [
    "src/jieli_uboot.c", "src/jieli_uboot.h", "pio/usb_key.pio",
    "lib/pico-pio-usb-bulk-multi-xact.patch", "src/tusb_config.h", "src/log.c", "src/log.h",
    "src/usb_device.c", "docs/PROTOCOL.md",
])
def test_unchanged_files(root, path):
    assert (root / path).read_text() == upstream(root, path)


def defines(text):
    return dict(re.findall(r"^#define (\w+) (.+?)\s*(?://.*)?$", text, re.M))


def test_recovery_timing_constants_unchanged(root):
    old, new = defines(upstream(root, "src/recovery.c")), defines((root / "src/recovery.c").read_text())
    for k in ("USB_KEY_WORD", "PIO_HZ", "KEY_PACKET_US", "GAP_PROBES", "GAP_PROBE_SPACING_US",
              "ACK_PROBE_LOWS", "DP_HIGH_TIMEOUT_MS", "PULSE_SAMPLE_US", "PULSE_LOW_SAMPLES",
              "HANDOFF_PULSE_MS", "CAL_DONE_MIN_MS"):
        assert new[k] == old[k], k
    for k in ("LED_R", "LED_G", "LED_B"):
        assert k not in new                 # moved to the board profile (same values for XIAO)


def strip_port_lines(text):
    """Drops the lines the port added (phase/counter stores, board includes)."""
    keep = []
    for line in text.splitlines():
        if re.search(r"fm1t_phase|fm1t_key_packets|#include \"selftest.h\"|#include \"board.h\"", line):
            continue
        keep.append(line)
    return keep


def function(text, name):
    m = re.search(r"^[^\n]*\b%s\([^)]*\) \{\n.*?^\}" % re.escape(name), text, re.M | re.S)
    assert m, name
    return "\n".join(strip_port_lines(m.group(0)))


@pytest.mark.parametrize("name", ["recovery_release_bus", "probe_high", "key_start", "key_send_packet",
                                  "gap_sense", "pulse_start", "wait_dp_high", "pulse_phase", "recovery_run",
                                  "recovery_wait_detached", "recovery_target_attached",
                                  "recovery_pulses_only"])
def test_recovery_functions_unchanged(root, name):
    assert function((root / "src/recovery.c").read_text(), name) == \
        function(upstream(root, "src/recovery.c"), name)


@pytest.mark.parametrize("name", ["edpt_sync", "ctrl_sync", "bot_cmd", "probe_descriptors", "probe_inquiry",
                                  "fm1_host_softkey", "run_command", "fm1_pio_host_task"])
def test_pio_host_logic_unchanged(root, name):
    assert function((root / "src/pio_host.c").read_text(), name) == \
        function(upstream(root, "src/pio_host.c"), name)


def test_host_start_only_parametrised(root):
    new = function((root / "src/pio_host.c").read_text(), "fm1_pio_host_start")
    old = function(upstream(root, "src/pio_host.c"), "fm1_pio_host_start")
    norm = new.replace("FM1T_HOST_PIO", "1").replace("    cfg.pinout = PIO_USB_PINOUT_DPDM;   "
                                                      "// D- = D+ + 1, checked in board.h\n", "")
    assert norm == old


def test_main_clock_and_handoff_unchanged(root):
    new, old = (root / "src/main.c").read_text(), upstream(root, "src/main.c")
    assert "set_sys_clock_khz(FM1T_SYS_KHZ, true);" in new and "set_sys_clock_khz(120000, true);" in old
    for k in ("CONSOLE_WAIT_MS", "ATTACHED_PULSE_MS", "REKEY_MAGIC"):
        assert defines(new)[k] == defines(old)[k]
    handoff = re.compile(r"uint64_t t_release = time_us_64\(\);\n\s+recovery_release_bus\(\);\n\s+"
                         r"fm1_pio_host_start\(\);")
    assert handoff.search(new) and handoff.search(old)


def test_transporter_proto_only_gains_board_commands(root):
    new = strip_port_lines((root / "src/transporter_proto.c").read_text())
    old = upstream(root, "src/transporter_proto.c").splitlines()
    added = [l for l in new if l not in old]
    assert added == ["        // Board identification / self-test, answered here like ping (selftest.h).",
                     "        if (fm1t_selftest_command(rx_line, DATA_ITF)) continue;"]
    assert [l for l in old if l not in new] == []


def test_recovery_tool_keeps_proven_xiao_firmware(root):
    import fm1_transporter_recover as tr
    fw = tr.board_firmware("xiao_rp2040")
    assert not fw["multiboard"]
    assert fw["placeholder_sha256"] == "363ec4b98071a40a1eadec14574c936c78bd8f2afdf17cc890104e887771b577"
    assert fw["spliced_sha256"] == "f275a52a0bd870c72f523ae28a54ac4d81dda9068a271318aa21dbdf95ef55ea"
    assert tr.fu.sha256(tr.PLACEHOLDER_UF2.read_bytes()) == fw["placeholder_sha256"]

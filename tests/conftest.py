import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
sys.path.insert(0, str(ROOT / "recovery"))


def sdk_path():
    for c in (os.environ.get("PICO_SDK_PATH"), "/opt/sdk/pico-sdk", str(Path.home() / ".pico-sdk/sdk/2.2.0")):
        if c and (Path(c) / "pico_sdk_init.cmake").is_file():
            return c
    return None


@pytest.fixture(scope="session")
def root():
    return ROOT


@pytest.fixture(scope="session")
def harness(tmp_path_factory):
    """The real transporter_proto.c + jieli_uboot.c built for the host against
    tests/firmware_host/mock_uboot.c, with a fake 24064-byte loader blob."""
    cc = shutil.which("gcc") or shutil.which("cc")
    if not cc:
        pytest.skip("no host C compiler")
    d = tmp_path_factory.mktemp("harness")
    blob = bytes((i * 7 + 3) & 0xFF for i in range(24064))
    (d / "wl82loader.h").write_text(
        "static const uint8_t wl82loader[] = {%s};\n" % ",".join(map(str, blob)))
    exe = d / "harness"
    fh = ROOT / "tests" / "firmware_host"
    cmd = [cc, "-std=c11", "-D_DEFAULT_SOURCE", "-Wall", "-Wextra", "-Werror", "-O1",
           "-DFM1T_HAVE_LOADER=1", "-I", str(fh / "stubs"), "-I", str(fh), "-I", str(ROOT / "src"),
           "-I", str(d), "-o", str(exe), str(ROOT / "src" / "transporter_proto.c"),
           str(ROOT / "src" / "jieli_uboot.c"), str(fh / "harness.c"), str(fh / "mock_uboot.c")]
    subprocess.run(cmd, check=True)
    return exe

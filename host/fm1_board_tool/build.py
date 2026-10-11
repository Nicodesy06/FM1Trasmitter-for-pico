"""Building the firmware for a board profile, with a manifest of what was built.

A build is reported as BUILD VERIFIED only if CMake configured, the compiler
ran to completion, the UF2 exists, parses as an RP2040 image and carries the
board's marker. Missing tools are reported as such; nothing is claimed.
"""

from __future__ import annotations

import datetime as _dt
import glob
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import uf2 as uf2mod
from .profiles import REPO_ROOT, get_profile

log = logging.getLogger("fm1_board_tool")

LOADER_SHA256 = "d41da6126760c9d66660bcc0cac8d27d221806c5e369a8036921efe68dca5376"
LOADER_SIZE = 24064
JLUB_COMMIT = "adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a"
LOADER_URL = ("https://raw.githubusercontent.com/kagaimiq/jl-uboot-tool/%s/"
              "data/loaderblobs/usb/wl82loader.bin" % JLUB_COMMIT)
PLACEHOLDER = REPO_ROOT / "dist" / "placeholder_loader.pattern"
PLACEHOLDER_SHA256 = "25f62babdb1c62753f7e293338c8ee1b6f323aec9805644b794e6581e7c5bc29"
SDK_WANTED = "2.2.0"
CMAKE_MIN = (3, 19)


class BuildError(RuntimeError):
    pass


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def home_pico():
    return Path(os.path.expanduser("~")) / ".pico-sdk"


def sdk_version(path: Path):
    f = Path(path) / "pico_sdk_version.cmake"
    if not f.is_file():
        return None
    t = f.read_text(errors="replace")
    parts = [re.search(r"set\(PICO_SDK_VERSION_%s (\d+)\)" % k, t) for k in ("MAJOR", "MINOR", "REVISION")]
    return ".".join(m.group(1) for m in parts) if all(parts) else "?"


def find_sdk(explicit=None):
    cands = [explicit, os.environ.get("PICO_SDK_PATH"), home_pico() / "sdk" / SDK_WANTED]
    cands += sorted(glob.glob(str(home_pico() / "sdk" / "*")), reverse=True)
    cands += [Path.home() / "pico-sdk", "/opt/pico-sdk", "/opt/sdk/pico-sdk"]
    for c in cands:
        if c and (Path(c) / "pico_sdk_init.cmake").is_file():
            p = Path(c).resolve()
            if not (p / "lib" / "tinyusb" / "src").is_dir():
                raise BuildError("pico-sdk at %s has no lib/tinyusb: run `git submodule update --init` "
                                 "in it" % p)
            return p, sdk_version(p)
    return None, None


def find_tool(name, globs=()):
    exe = shutil.which(name)
    if exe:
        return exe
    for g in globs:
        for hit in sorted(glob.glob(str(g)), reverse=True):
            for cand in (Path(hit) / name, Path(hit) / (name + ".exe")):
                if cand.is_file():
                    return str(cand)
    return None


def tool_version(cmd):
    try:
        out = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=30)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except Exception as e:
        return "unavailable (%s)" % e


def find_picotool_dir():
    for d in sorted(glob.glob(str(home_pico() / "picotool" / "*" / "picotool")), reverse=True):
        if (Path(d) / "picotoolConfig.cmake").is_file():
            return d
    return None


def check_tools(sdk=None) -> dict:
    """What a build needs, found or not. Never raises; `missing` lists blockers."""
    t = {"python": sys.executable, "python_version": platform.python_version(), "missing": []}
    sdk_path, ver = None, None
    try:
        sdk_path, ver = find_sdk(sdk)
    except BuildError as e:
        t["missing"].append(str(e))
    t["sdk"], t["sdk_version"] = (str(sdk_path) if sdk_path else None), ver
    if not sdk_path and not t["missing"]:
        t["missing"].append("pico-sdk %s (set PICO_SDK_PATH or --sdk; the VS Code Raspberry Pi Pico "
                            "extension installs it in ~/.pico-sdk/sdk/%s)" % (SDK_WANTED, SDK_WANTED))
    elif ver and ver != SDK_WANTED:
        t["warning_sdk"] = "pico-sdk %s found; this project is tested with %s" % (ver, SDK_WANTED)
    t["cmake"] = find_tool("cmake", [home_pico() / "cmake" / "*" / "bin"])
    if t["cmake"]:
        t["cmake_version"] = tool_version(t["cmake"])
        m = re.search(r"(\d+)\.(\d+)", t["cmake_version"])
        if not m or (int(m.group(1)), int(m.group(2))) < CMAKE_MIN:
            t["missing"].append("CMake >= %d.%d (found %s)" % (CMAKE_MIN + (t["cmake_version"],)))
    else:
        t["missing"].append("cmake (>= 3.19)")
    t["gcc"] = find_tool("arm-none-eabi-gcc", [home_pico() / "toolchain" / "*" / "bin",
                                               "C:/Program Files*/Arm GNU Toolchain*/*/bin",
                                               "C:/Program Files*/GNU Arm Embedded Toolchain/*/bin"])
    if t["gcc"]:
        t["gcc_version"] = tool_version(t["gcc"])
    else:
        t["missing"].append("arm-none-eabi-gcc (Arm GNU Toolchain)")
    t["ninja"] = find_tool("ninja", [home_pico() / "ninja" / "*"])
    if t["ninja"]:
        t["ninja_version"] = tool_version(t["ninja"])
    elif sys.platform == "win32":
        t["missing"].append("ninja (on Windows the build uses the Ninja generator)")
    t["make"] = None if t["ninja"] else shutil.which("make")
    if not t["ninja"] and not t["make"] and sys.platform != "win32":
        t["missing"].append("ninja or make")
    t["git"] = shutil.which("git")
    if not t["git"]:
        t["missing"].append("git (submodule checkout and the Pico-PIO-USB patch)")
    t["picotool_dir"] = find_picotool_dir()
    if not (REPO_ROOT / "lib" / "Pico-PIO-USB" / "src" / "pio_usb_host.c").is_file():
        t["missing"].append("lib/Pico-PIO-USB is empty: run `git submodule update --init`")
    return t


def resolve_loader(mode, path=None, allow_unknown=False):
    """('none'|'placeholder'|'real', file or None, sha256 or None)."""
    if mode == "none":
        return "none", None, None
    if mode == "placeholder":
        if sha256_file(PLACEHOLDER) != PLACEHOLDER_SHA256:
            raise BuildError("%s is damaged (sha256 mismatch)" % PLACEHOLDER)
        return "placeholder", PLACEHOLDER, PLACEHOLDER_SHA256
    p = Path(path)
    if not p.is_file():
        raise BuildError("loader %s not found" % p)
    h = sha256_file(p)
    if h != LOADER_SHA256 and not allow_unknown:
        raise BuildError("%s has sha256 %s, not the known wl82loader.bin %s (jl-uboot-tool %s); "
                         "refusing" % (p, h, LOADER_SHA256, JLUB_COMMIT[:7]))
    if p.stat().st_size != LOADER_SIZE and not allow_unknown:
        raise BuildError("%s is %d bytes, expected %d" % (p, p.stat().st_size, LOADER_SIZE))
    return "real", p, h


def fetch_loader(dest: Path) -> Path:
    """Downloads wl82loader.bin from jl-uboot-tool at the pinned commit and
    checks its sha256 (the same file MvaveFM1Unbricker's setup uses)."""
    dest = Path(dest)
    if dest.is_file() and sha256_file(dest) == LOADER_SHA256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s", LOADER_URL)
    with urllib.request.urlopen(LOADER_URL, timeout=60) as r:
        data = r.read()
    h = hashlib.sha256(data).hexdigest()
    if h != LOADER_SHA256:
        raise BuildError("downloaded loader has sha256 %s, expected %s; not saved" % (h, LOADER_SHA256))
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest


def _run(cmd, logf, cwd=None, env=None):
    log.info("$ %s", " ".join(str(c) for c in cmd))
    logf.write("\n$ %s\n" % " ".join(str(c) for c in cmd))
    logf.flush()
    p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, errors="replace")
    tail = []
    for line in p.stdout:
        logf.write(line)
        tail.append(line.rstrip())
        tail = tail[-40:]
        if re.search(r"error|warning|FM-1 Transporter", line, re.I):
            log.info("  %s", line.rstrip())
    rc = p.wait()
    if rc != 0:
        raise BuildError("%s failed (exit %d). Last lines:\n  %s" % (Path(str(cmd[0])).name, rc, "\n  ".join(tail[-15:])))


def git_rev():
    try:
        r = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True,
                           text=True, timeout=20)
        return r.stdout.strip() or None
    except Exception:
        return None


def build(board, sdk=None, loader_mode="none", loader_path=None, allow_unknown_loader=False,
          out_dir=None, build_root=None, clean=False, allow_unvalidated=False, jobs=None) -> dict:
    profile = get_profile(board, allow_disabled=allow_unvalidated)
    tools = check_tools(sdk)
    if tools["missing"]:
        raise BuildError("cannot build, missing: " + "; ".join(tools["missing"]))
    kind, lpath, lsha = resolve_loader(loader_mode, loader_path, allow_unknown_loader)
    out_dir = Path(out_dir or REPO_ROOT / "artifacts")
    bdir = Path(build_root or REPO_ROOT / "build") / ("%s-%s" % (board, kind))
    if clean and bdir.exists():
        shutil.rmtree(bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().astimezone()
    log_path = out_dir / board / ("build-%s-%s.log" % (kind, stamp.strftime("%Y%m%d-%H%M%S")))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PICO_SDK_PATH"] = tools["sdk"]
    for k in ("gcc", "ninja", "cmake"):
        if tools.get(k):
            env["PATH"] = str(Path(tools[k]).parent) + os.pathsep + env["PATH"]
    cfg = [tools["cmake"], "-S", REPO_ROOT, "-B", bdir, "-DFM1T_BOARD=%s" % board,
           "-DPICO_SDK_PATH=%s" % tools["sdk"],
           "-DFM1T_LOADER_BIN=%s" % (lpath if lpath else bdir / "no-loader.bin")]
    if allow_unvalidated:
        cfg.append("-DFM1T_ALLOW_UNVALIDATED=ON")
    if tools["ninja"]:
        cfg += ["-G", "Ninja", "-DCMAKE_MAKE_PROGRAM=%s" % tools["ninja"]]
    elif sys.platform != "win32":
        cfg += ["-G", "Unix Makefiles"]
    if tools["picotool_dir"]:
        cfg.append("-Dpicotool_DIR=%s" % tools["picotool_dir"])
    with open(log_path, "w", encoding="utf-8") as logf:
        logf.write("fm1-board-tool build %s (%s), %s\n" % (board, kind, stamp.isoformat()))
        logf.write(json.dumps({k: v for k, v in tools.items()}, indent=1) + "\n")
        _run(cfg, logf, env=env)
        bcmd = [tools["cmake"], "--build", bdir]
        if jobs:
            bcmd += ["--parallel", str(jobs)]
        _run(bcmd, logf, env=env)

    stem = Path(profile.uf2_name).stem
    suffix = {"real": "", "none": "-noloader", "placeholder": "-placeholder"}[kind]
    dest = out_dir / board
    files = {}
    for ext in ("uf2", "elf", "bin"):
        src = bdir / ("%s.%s" % (stem, ext))
        if not src.is_file():
            raise BuildError("expected output %s is missing" % src)
        dst = dest / ("%s%s.%s" % (stem, suffix, ext))
        shutil.copyfile(src, dst)
        files[ext] = {"file": dst.name, "sha256": sha256_file(dst), "bytes": dst.stat().st_size}
    hostonly = bdir / "fm1_transporter_hostonly.uf2"
    if hostonly.is_file():
        dst = dest / ("%s-hostonly%s.uf2" % (stem, suffix))
        shutil.copyfile(hostonly, dst)
        files["hostonly_uf2"] = {"file": dst.name, "sha256": sha256_file(dst), "bytes": dst.stat().st_size}

    info = uf2mod.check_for_board((dest / files["uf2"]["file"]).read_bytes(), profile, files["uf2"]["file"])
    if kind == "placeholder":
        _, flat, _ = uf2mod.parse((dest / files["uf2"]["file"]).read_bytes())
        if flat.count(PLACEHOLDER.read_bytes()) != 1:
            raise BuildError("the placeholder pattern is not in the image exactly once")
    gen = bdir / "generated" / "fm1t_board_config.h"
    m = re.search(r'FM1T_BUILD_ID "([^"]*)"', gen.read_text()) if gen.is_file() else None
    manifest = {
        "status": "BUILD VERIFIED",
        "board": board,
        "board_name": profile.name,
        "profile_validation": profile.validation,
        "loader": kind,
        "loader_sha256": lsha,
        "build_id": m.group(1) if m else None,
        "marker_build": info["builds"][0],
        "git_commit": git_rev(),
        "upstream": "kurogedelic/FM-1-transporter a632d923203170e3a08565f4056dbb3fd3d65ca5",
        "pico_pio_usb": "fe9133fc513b82cc3dc62c67cb51f2339cf29ef7 (0.6.1) + lib/pico-pio-usb-bulk-multi-xact.patch",
        "sdk": tools["sdk"],
        "sdk_version": tools["sdk_version"],
        "toolchain": tools.get("gcc_version"),
        "cmake": tools.get("cmake_version"),
        "generator": "Ninja" if tools["ninja"] else "Unix Makefiles",
        "host": "%s %s %s" % (platform.system(), platform.release(), platform.machine()),
        "built_at": stamp.isoformat(timespec="seconds"),
        "uf2_range": "0x%08X-0x%08X" % (info["base"], info["end"]),
        "files": files,
        "log": log_path.name,
    }
    mpath = dest / ("manifest-%s.json" % kind)
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info("BUILD VERIFIED %s (%s): %s sha256 %s", board, kind, files["uf2"]["file"], files["uf2"]["sha256"])
    return manifest


def make_firmware(board, loader, out=None, dist=None) -> dict:
    """Splices the real loader into the shipped placeholder UF2 (no toolchain)."""
    profile = get_profile(board)
    dist = Path(dist or REPO_ROOT / "dist") / board
    stem = Path(profile.uf2_name).stem
    ph = dist / ("%s-placeholder.uf2" % stem)
    man = json.loads((dist / "manifest-placeholder.json").read_text())
    if sha256_file(ph) != man["files"]["uf2"]["sha256"]:
        raise BuildError("%s does not match its manifest; the dist folder is damaged" % ph)
    kind, lpath, lsha = resolve_loader("real", loader)
    out_data = uf2mod.splice(ph.read_bytes(), PLACEHOLDER.read_bytes(), Path(lpath).read_bytes(), ph.name)
    uf2mod.check_for_board(out_data, profile)
    h = hashlib.sha256(out_data).hexdigest()
    want = man.get("spliced_uf2_sha256")
    if want and h != want:
        raise BuildError("spliced firmware sha256 %s differs from the recorded %s; not written" % (h, want))
    out = Path(out or REPO_ROOT / "artifacts" / board / profile.uf2_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(out_data)
    log.info("Wrote %s (sha256 %s%s)", out, h, ", matches the recorded build" if want else "")
    return {"file": str(out), "sha256": h, "verified_against_manifest": bool(want)}

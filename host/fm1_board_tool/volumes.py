"""Finding the RP2040 boot ROM's RPI-RP2 disk without trusting drive letters.

A volume counts as an RP2040 BOOTSEL disk only if all of these hold:
  * INFO_UF2.TXT exists and says `Board-ID: RPI-RP2` and `Model: Raspberry Pi RP2`;
  * INDEX.HTM exists;
  * on Windows, the volume label is RPI-RP2 and the drive is removable;
  * the file system is small (the boot ROM presents ~128 MB).
The only operation ever performed on it is copying one .uf2 file to it.
Nothing here formats, deletes, renames or writes anything else.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("fm1_board_tool")

LABEL = "RPI-RP2"
MAX_BYTES = 256 * 1024 * 1024


@dataclass
class Volume:
    path: Path
    info: dict
    label: str | None = None
    total_bytes: int | None = None

    @property
    def bootloader(self) -> str:
        return self.info.get("UF2 Bootloader", "?")


def parse_info_uf2(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
        elif line.startswith("UF2 Bootloader"):
            out["UF2 Bootloader"] = line[len("UF2 Bootloader"):].strip()
    return out


def _windows_drives():
    import ctypes
    k32 = ctypes.windll.kernel32
    buf = ctypes.create_unicode_buffer(1024)
    n = k32.GetLogicalDriveStringsW(len(buf), buf)
    drives = [d for d in buf[:n].split("\x00") if d]
    out = []
    for d in drives:
        if k32.GetDriveTypeW(d) != 2:          # DRIVE_REMOVABLE
            continue
        name = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        ok = k32.GetVolumeInformationW(d, name, len(name), None, None, None, fs, len(fs))
        out.append((Path(d), name.value if ok else None))
    return out


def _posix_mounts():
    cands = []
    if sys.platform == "darwin":
        vols = Path("/Volumes")
        if vols.is_dir():
            cands += [p for p in vols.iterdir()]
    else:
        try:
            with open("/proc/mounts", encoding="utf-8") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) > 2 and parts[2] in ("vfat", "msdos", "fat"):
                        cands.append(Path(parts[1].replace("\\040", " ")))
        except OSError:
            pass
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
        cands += [Path("/media") / user / LABEL, Path("/run/media") / user / LABEL,
                  Path("/media") / LABEL, Path("/mnt") / LABEL]
    return [(p, None) for p in cands]


def candidates():
    if sys.platform == "win32":
        return _windows_drives()
    return _posix_mounts()


def check_volume(path: Path, label=None, require_label=None, max_bytes=MAX_BYTES):
    """A Volume if `path` is an RP2040 boot disk, else None (reasons logged)."""
    info_file = Path(path) / "INFO_UF2.TXT"
    try:
        if not info_file.is_file():
            return None
        info = parse_info_uf2(info_file.read_text(errors="replace"))
    except OSError as e:
        log.debug("%s: %s", path, e)
        return None
    if info.get("Board-ID") != LABEL or not info.get("Model", "").startswith("Raspberry Pi RP2"):
        log.debug("%s: INFO_UF2.TXT is not an RP2040 boot ROM's (%s)", path, info)
        return None
    if not (Path(path) / "INDEX.HTM").is_file():
        log.debug("%s: no INDEX.HTM", path)
        return None
    if require_label and label != LABEL:
        log.debug("%s: volume label %r is not %s", path, label, LABEL)
        return None
    try:
        total = shutil.disk_usage(str(path)).total
    except OSError:
        total = None
    if max_bytes and total is not None and total > max_bytes:
        log.debug("%s: %d bytes is too large for the RP2040 boot disk", path, total)
        return None
    return Volume(Path(path), info, label, total)


def find(cands=None, max_bytes=MAX_BYTES) -> list:
    win = sys.platform == "win32" if cands is None else False
    out, seen = [], set()
    for path, label in (cands if cands is not None else candidates()):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        v = check_volume(path, label, require_label=win, max_bytes=max_bytes)
        if v:
            out.append(v)
    return out


def copy_uf2(src: Path, vol: Volume) -> Path:
    """Copy one file onto the boot disk; the RP2040 reboots when it is complete."""
    dest = vol.path / Path(src).name
    if dest.suffix.lower() != ".uf2":
        raise ValueError("only .uf2 files are copied to %s" % LABEL)
    log.info("Copying %s to %s", Path(src).name, dest)
    try:
        with open(src, "rb") as fi, open(dest, "wb") as fo:
            shutil.copyfileobj(fi, fo, 64 * 1024)
            fo.flush()
            try:
                os.fsync(fo.fileno())
            except OSError:
                pass
    except OSError as e:
        # The boot ROM reboots as soon as the last block lands; the OS may complain.
        log.info("  copy ended with %s (normal if the board rebooted at once)", e)
    return dest

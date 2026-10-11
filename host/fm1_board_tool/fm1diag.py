"""First contact with the FM-1: read-only by construction.

Waiting is done by polling `diag`, which the RP2040 answers itself, so no FM-1
request is queued before the FM-1 is in UBOOT. Then `status`, `info` and,
optionally, two full reads that must be identical. Nothing here can erase or
write the FM-1 (the Client has no such request)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import time
import zlib
from pathlib import Path

from .transporter import FM1_FLASH_SIZE, check_fm1_info

log = logging.getLogger("fm1_board_tool")

PHASE_ORDER = ["boot", "wait-console", "keying", "pulses", "host", "mounted", "uboot"]


class Fm1Error(RuntimeError):
    pass


V15_CHECK_AFTER = 6.0      # s in phase mounted before asking status (core 1 is serving by then)


def wait_uboot(c, wait, on_phase=None, poll=0.5):
    """Polls diag until phase == uboot. Returns {phase: seconds first seen}.

    In phases host/mounted core 1 runs the request loop, so `status` is safe
    there; a unit that stays mounted without UBOOT is checked for stock V15
    (which needs the recovery tool's USB-MIDI soft key, not this check)."""
    seen, t0, last = {}, time.monotonic(), None
    deadline = t0 + wait
    while True:
        d = c.board_cmd("diag")
        ph = d.get("phase")
        if ph not in seen:
            seen[ph] = round(time.monotonic() - t0, 2)
            if on_phase:
                on_phase(ph, seen[ph], d)
        if ph == "uboot":
            return seen
        if ph == "mounted" and time.monotonic() - t0 - seen["mounted"] > V15_CHECK_AFTER:
            st = c.status(5)
            if st.get("v15") == "1":
                raise Fm1Error("the FM-1 runs its stock V15 firmware (4C4A:C755), so it is not "
                               "bricked; to reach UBOOT use the recovery tool (`info`/`dump` send "
                               "the USB-MIDI soft key)")
        if time.monotonic() >= deadline:
            raise Fm1Error("no UBOOT after %d s (phases seen: %s)" % (wait, ", ".join(seen)))
        last = ph
        time.sleep(poll)


def identify(c) -> tuple:
    st = c.status(10)
    if st.get("loader") != "1":
        raise Fm1Error("this firmware has no JieLi loader (a -noloader build): info/read are "
                       "impossible. Flash the make-firmware output.")
    info = c.info()
    return st, info, check_fm1_info(info)


def double_dump(c, outdir: Path, ident: dict, evidence: str, info=None, progress=None) -> dict:
    reads = []
    for i in (1, 2):
        t0 = time.monotonic()
        d = c.read(0, FM1_FLASH_SIZE, progress)
        log.info("read %d/2: sha256 %s (%.1f s)", i, hashlib.sha256(d).hexdigest(), time.monotonic() - t0)
        reads.append(d)
    if reads[0] != reads[1]:
        bad = [a for a in range(0, FM1_FLASH_SIZE, 4096) if reads[0][a:a + 4096] != reads[1][a:a + 4096]]
        raise Fm1Error("the two reads DIFFER in %d sectors (first %s): the link is not reliable. "
                       "Nothing saved." % (len(bad), ", ".join("0x%05X" % a for a in bad[:8])))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = "fm1-dump-%s" % _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    binp = outdir / (stem + ".bin")
    with open(binp, "xb") as fh:
        fh.write(reads[0])
    if binp.read_bytes() != reads[0]:
        raise Fm1Error("the dump file does not read back correctly")
    side = {"file": binp.name, "bytes": len(reads[0]), "sha256": hashlib.sha256(reads[0]).hexdigest(),
            "crc32": "%08X" % (zlib.crc32(reads[0]) & 0xFFFFFFFF), "double_read": True,
            "reads_identical": True, "info": info, "transporter": ident, "evidence": evidence,
            "saved": _dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    (outdir / (stem + ".json")).write_text(json.dumps(side, indent=2), encoding="utf-8")
    side["path"] = str(binp)
    return side

#!/usr/bin/env python3
"""Builds the shipped firmware in dist/ (maintainer script).

For every enabled board: a placeholder build (for make-firmware / setup) and
a no-loader build (board self-test, reaching UBOOT). With --loader, also a
real-loader build, used only to prove that splicing the loader into the
placeholder image gives exactly the real build's bytes; its sha256 goes into
the placeholder manifest and the real image itself is NOT shipped (the JieLi
loader is third-party and not redistributed).
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

from fm1_board_tool import build as b, uf2, profiles  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loader", help="wl82loader.bin, to record the spliced sha256")
    ap.add_argument("--sdk")
    ap.add_argument("--boards", nargs="*")
    args = ap.parse_args()
    dist = ROOT / "dist"
    boards = args.boards or [p.id for p in profiles.load_profiles().values() if p.enabled]
    tmp = Path(tempfile.mkdtemp(prefix="fm1dist-"))
    sums = []
    for board in boards:
        out = dist / board
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        mans = {}
        for kind in ("placeholder", "none") + (("real",) if args.loader else ()):
            m = b.build(board, sdk=args.sdk, loader_mode=kind, loader_path=args.loader,
                        out_dir=tmp / "out", build_root=tmp / "build", clean=True)
            mans[kind] = m
        ph = tmp / "out" / board / mans["placeholder"]["files"]["uf2"]["file"]
        if args.loader:
            real = (tmp / "out" / board / mans["real"]["files"]["uf2"]["file"]).read_bytes()
            spliced = uf2.splice(ph.read_bytes(), b.PLACEHOLDER.read_bytes(),
                                 Path(args.loader).read_bytes())
            if spliced != real:
                sys.exit("%s: splicing the loader into the placeholder does NOT give the real "
                         "build; not publishing" % board)
            mans["placeholder"]["spliced_uf2_sha256"] = hashlib.sha256(real).hexdigest()
            mans["placeholder"]["spliced_check"] = ("splice(placeholder, pattern, wl82loader.bin "
                                                    "sha256 %s) == real-loader build, byte for byte"
                                                    % b.LOADER_SHA256)
        for kind, label in (("placeholder", "placeholder"), ("none", "noloader")):
            m = mans[kind]
            f = m["files"]["uf2"]["file"]
            shutil.copyfile(tmp / "out" / board / f, out / f)
            for k in ("elf", "bin", "hostonly_uf2"):
                m["files"][k]["shipped"] = False
            m["files"]["uf2"]["shipped"] = True
            m.pop("log", None)
            m["sdk"] = "pico-sdk %s" % m["sdk_version"]
            (out / ("manifest-%s.json" % kind if kind != "none" else "manifest-noloader.json")).write_text(
                json.dumps(m, indent=2) + "\n", encoding="utf-8")
            sums.append("%s  %s/%s" % (m["files"]["uf2"]["sha256"], board, f))
            print("%-24s %-12s %s" % (board, label, m["files"]["uf2"]["sha256"]))
    (dist / "SHA256SUMS").write_text("\n".join(sorted(sums)) + "\n" +
                                     "%s  placeholder_loader.pattern\n" % b.PLACEHOLDER_SHA256)
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

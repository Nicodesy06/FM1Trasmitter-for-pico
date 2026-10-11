# Upstream sources, versions and licences

This repository is a derived workspace. The original projects are not modified
in place: each was imported unchanged in its own commit, and every change is a
later, separate commit (`git log --follow <file>` shows both).

| Component | Version used | Licence | Where | Notes |
|---|---|---|---|---|
| [kurogedelic/FM-1-transporter](https://github.com/kurogedelic/FM-1-transporter) | `a632d923203170e3a08565f4056dbb3fd3d65ca5` (2026-10-01) | MIT, © 2026 kurogedelic | repository root (`src/`, `pio/`, `tools/`, `docs/`, `CMakeLists.txt`) | imported unchanged in `6724113`; its README is kept as `docs/UPSTREAM-README.md` |
| [Quixotic7/MvaveFM1Unbricker](https://github.com/Quixotic7/MvaveFM1Unbricker) | `b8c8e8afbcba3e45b262b3b624ab9d0244b92628` (2026-10-09) | MIT, © 2026 Michael P Jones | `recovery/` | imported unchanged in `897b189`; includes the prebuilt XIAO placeholder UF2 built from FM-1-transporter `a632d92` |
| [sekigon-gonnoc/Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) | `fe9133fc513b82cc3dc62c67cb51f2339cf29ef7` (0.6.1) | MIT, © 2021 sekigon-gonnoc | `lib/Pico-PIO-USB` (submodule) | same commit as upstream; `lib/pico-pio-usb-bulk-multi-xact.patch` (upstream's) is applied to a copy in the build tree |
| [raspberrypi/pico-sdk](https://github.com/raspberrypi/pico-sdk) | 2.2.0 (`a1438dff1d38bd9c65dbd693f0e5db4b9ae91779`) | BSD-3-Clause | not vendored | `pico_sdk_import.cmake` is upstream's copy |
| [hathach/tinyusb](https://github.com/hathach/tinyusb) | `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` (pico-sdk 2.2.0 submodule) | MIT | not vendored | |
| [kagaimiq/jl-uboot-tool](https://github.com/kagaimiq/jl-uboot-tool) | `adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a` | MIT (the tool) | not vendored | only `data/loaderblobs/usb/wl82loader.bin` (24064 B, sha256 `d41da6126760c9d66660bcc0cac8d27d221806c5e369a8036921efe68dca5376`) is used. It is JieLi's loader; neither upstream nor this repository redistributes it. It is fetched by the user (`make-firmware --fetch-loader`, `build.ps1 -FetchLoader`, `recovery … setup`) and checked by hash |
| Arm GNU Toolchain | 14.2.Rel1 (x86_64 Linux, archive sha256 `62a63b98…` checked against Arm's `.sha256asc`) | GPL-3.0 with exceptions / newlib licences | not vendored | used for `dist/`; the toolchain of the upstream XIAO build |
| picotool | 2.1.1 (fetched by pico-sdk) | BSD-3-Clause | not vendored | needed by pico-sdk 2.x to write UF2 files |

`dist/placeholder_loader.pattern` is the 24064-byte stand-in from
MvaveFM1Unbricker (`transporter/placeholder_loader.pattern`, MIT): the bytes the
loader replaces when `make-firmware` / `setup` splice the real loader in.

## Licence of this work

The new code (board profiles, `cmake/`, `src/board.*`, `src/selftest.*`,
`src/flash_*`, `host/`, `scripts/`, `tests/`, the changes in `recovery/`) and the
documentation are MIT licensed, like the projects they extend; see `LICENSE`
and `recovery/LICENSE`. Every original copyright notice is kept.

## Documentation checked against sources

Where documentation and code disagree, the code was taken as authoritative and
the difference is recorded in `docs/PORTING-REPORT.md` section 5.

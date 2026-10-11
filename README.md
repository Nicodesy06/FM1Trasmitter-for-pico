# FM-1 Transporter, multiboard

Recover a dark, USB-silent **M-VAVE FM-1** (JieLi AC791N / WL82) through an RP2040 board wired to
the FM-1's USB D+, D- and GND — now with **board profiles**, so the Transporter firmware is no
longer tied to the Seeed XIAO RP2040.

This is a derived workspace of [kurogedelic/FM-1-transporter](https://github.com/kurogedelic/FM-1-transporter)
(firmware) and [Quixotic7/MvaveFM1Unbricker](https://github.com/Quixotic7/MvaveFM1Unbricker)
(recovery tool, in `recovery/`). Versions and licences: [UPSTREAM.md](UPSTREAM.md).

```
PC ──USB-C──► RP2040 board ──GP0 = D+ / GP1 = D- / GND──► FM-1 (VBUS not connected)
```

> **Italiano** — Guida per la DollaTek: [DOLLATEK-RP2040-GUIDE.md](DOLLATEK-RP2040-GUIDE.md).
> Analisi, problemi e verifiche ancora necessarie: [docs/PORTING-REPORT.md](docs/PORTING-REPORT.md).
> Collaudo guidato su Windows 11: `.\scripts\hardware-test.ps1 -Board dollatek_rp2040_typec`.

## Boards

| Profile (`--board`) | Board | D+ / D- | Status |
|---|---|---|---|
| `xiao_rp2040` | Seeed Studio XIAO RP2040 (reference) | D6 = GP0 / D7 = GP1 | **HARDWARE VERIFIED** with the upstream `a632d92` firmware; the multiboard build is BUILD VERIFIED |
| `dollatek_rp2040_typec` | DollaTek RP2040 Core Board TYPE-C ("16M") | pin 1 = GP0 / pin 2 = GP1 | **BUILD VERIFIED** — not yet run on a physical board |
| `pico_rp2040` | Raspberry Pi Pico (not Pico W) | pin 1 = GP0 / pin 2 = GP1 | BUILD VERIFIED |
| `generic_rp2040` | any Pico-compatible board | GP0 / GP1 | **disabled** until its pinout is validated (`-DFM1T_ALLOW_UNVALIDATED=ON`) |

A profile is `boards/<id>.json` (pins, LEDs and polarity, reserved GPIOs, flash, clock,
BOOTSEL, validation state); CMake and the host tools both read it. Adding a board means
adding a validated profile — no firmware duplication. RP2350 boards are refused (erratum E9).

What "verified" means here: **HARDWARE VERIFIED** = seen working on a physical board;
**BUILD VERIFIED** = compiled, image checked and reproducible; **SIMULATED** = run against the
simulator or the firmware's protocol code compiled for the PC with a mock JieLi ROM.

## Quick start (Windows 11)

Prerequisites: Git, Python 3.8+; to compile, the VS Code *Raspberry Pi Pico* extension (it
installs pico-sdk 2.2.0, Arm GCC, CMake, Ninja and picotool under `%USERPROFILE%\.pico-sdk`).

```powershell
git clone --recurse-submodules https://github.com/Nicodesy06/FM1Trasmitter-for-pico
cd FM1Trasmitter-for-pico

.\fm1-board-tool.cmd boards                      # profiles
.\fm1-board-tool.cmd list                        # serial ports and RPI-RP2 disks (writes nothing)

# Firmware without compiling: the shipped placeholder image + JieLi's loader, hash-checked
.\fm1-board-tool.cmd make-firmware --board dollatek_rp2040_typec --fetch-loader
# or compile it
.\scripts\build.ps1 -Board dollatek_rp2040_typec -FetchLoader

# Board alone (no FM-1): BOOTSEL, flash, self-test, LED, watchdog
.\scripts\hardware-test.ps1 -Board dollatek_rp2040_typec
# First FM-1 contact, read-only, after the wiring checklist
.\scripts\hardware-test.ps1 -Board dollatek_rp2040_typec -Phase b -NoFlash
```

Linux / macOS: `./fm1-board-tool …` and `python3 -m fm1_board_tool …` with `PYTHONPATH=host`.

## fm1-board-tool

| Command | Does | Writes to the FM-1? |
|---|---|---|
| `boards [show ID \| validate \| conflicts ID GPx=use]` | profiles, validation, pin conflict check | no |
| `list` | serial ports (roles: console / data) and RPI-RP2 disks | no (opens nothing) |
| `probe`, `transporter-info`, `console` | handshake `ping`/`ident`, board info, console log | no |
| `build --board ID [--loader F \| --fetch-loader \| --placeholder]` | CMake build + manifest + SHA-256 | no |
| `make-firmware --board ID --fetch-loader` | splices the loader into the shipped image; checks the recorded hash | no |
| `flash --board ID [--uf2 F]` | checks the UF2 is for that board, finds RPI-RP2 by its contents, copies, verifies the board comes back with that build | no |
| `selftest --board ID [--flash F] [--interactive]` | board self-test without an FM-1: PASS / FAIL / NOT TESTED / REQUIRES FM-1 / REQUIRES INSTRUMENTATION | no |
| `fm1-check [--dump DIR]` | waits for UBOOT, checks chip key 980F / flash 856014, two full reads that must match | **no** (read-only) |
| `hwtest --board ID --phase a\|b` | the guided procedure above | **no** |

Every command logs to `logs/` at DEBUG level. `--sim BOARD [--sim-fm1]` runs any command
against the behavioural simulator.

Writing the FM-1 stays in the recovery tool, behind its dry run, the double-read reference
(with its `.json` record), the identity checks and a typed `WRITE` confirmation:

```powershell
cd recovery
py -3 fm1_transporter_recover.py setup  --board dollatek_rp2040_typec
py -3 fm1_transporter_recover.py wizard --board dollatek_rp2040_typec --v15 FM-1.fwsc
```

The XIAO keeps the upstream hardware-proven firmware there (`--board xiao_rp2040`,
`flash-xiao` still works).

## Firmware

Upstream's firmware, unchanged where it matters: the JieLi UBOOT/loader code, the USB_KEY
and pulse timing, the PIO programs, the Pico-PIO-USB patch and the host logic are
byte-for-byte or function-for-function identical (`tests/test_xiao_regression.py`). Added:

- pins, LEDs and PIO blocks from the profile (`src/board.*`), with compile-time checks;
- data-port commands answered by core 0 for identification and self-test: `ident`,
  `hwinfo` (clock, board flash JEDEC + SFDP size, reset reason), `diag` (phase, pin
  functions), `piotest` (pad readback while keying), `ledtest`, `reboot`, `bootsel`;
- reproducible builds: build id and date come from the last commit that changed a firmware
  input.

Build by hand: `cmake -S . -B build -G Ninja -DFM1T_BOARD=dollatek_rp2040_typec -DFM1T_LOADER_BIN=path\to\wl82loader.bin`
then `cmake --build build` → `build/fm1_transporter_dollatek_rp2040.uf2`. Upstream's
`-DPICO_BOARD=seeed_xiao_rp2040` still selects the XIAO profile.

Shipped images (`dist/`, built with pico-sdk 2.2.0 + Arm GNU Toolchain 14.2.Rel1, hashes in
`dist/SHA256SUMS` and the manifests): `*-noloader.uf2` (board self-test, reaching UBOOT) and
`*-placeholder.uf2` (input of `make-firmware`; never flashed — the tools refuse it). The
JieLi loader is never redistributed.

## Tests

```sh
python -m pytest tests                # 191 tests; CMake tests need an SDK, harness tests a C compiler
cd recovery && python -m pytest tests # the recovery tool's own suite
```

## Safety

- The first FM-1 connection is diagnostic and read-only; nothing erases or writes it
  automatically. The RP2040 board's own flash is never written by the firmware.
- No restore without two identical full reads; the boot area `0x0000–0x3FFF` and the device
  data from `0x93000` are never written (enforced by the firmware too).
- RPI-RP2 disks are identified by their contents, never by drive letter; nothing is formatted.
- Never power the FM-1 from the board's 3V3 / VBUS / VSYS / VIN; VBUS stays disconnected.

Unofficial; not affiliated with M-VAVE, JieLi or the board makers. Use at your own risk.

## Documents

[DOLLATEK-RP2040-GUIDE.md](DOLLATEK-RP2040-GUIDE.md) · [docs/PORTING-REPORT.md](docs/PORTING-REPORT.md) ·
[UPSTREAM.md](UPSTREAM.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/PROTOCOL.md](docs/PROTOCOL.md) ·
[docs/UPSTREAM-README.md](docs/UPSTREAM-README.md) · [recovery/TRANSPORTER-GUIDE.md](recovery/TRANSPORTER-GUIDE.md)

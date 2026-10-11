# Development log

Bring-up history and the hardware results behind the README, kept for
reference. The milestones are M0 (UBOOT enumeration), M1 (read-only loader
and dump), and M2 (soft key and writes). The log lines quoted here are from
the firmware of that time; for example, "M0 DONE" is now "UBOOT READY".

## M0: UBOOT through the PIO USB host

**Passed on hardware, 2026-10-01.** XIAO RP2040 at 120 MHz, 3 wires, no VBUS. Log: [logs/m0_success_2026-10-01.log](logs/m0_success_2026-10-01.log).

```
ACK after 64724 packets -> 6000 ms of pulses (D+ held) -> HANDOFF in 1076 us
-> mounted 522 ms after host start -> 4C4A:8057 "WL80UBOOT1.00"
-> GET MAX LUN 0 -> INQUIRY "WL82" / "UBOOT1.00" / "1.00", 1.5 s after mount
```

The UBOOT stayed attached afterwards.

Lessons from the first attempts:

- `CFG_TUH_API_EDPT_XFER` must be 1. Without it, TinyUSB silently drops the completion of raw `tuh_edpt_xfer()` transfers. The CBW looked NAKed, the retries put the BOT state machine out of sync, and the ROM detached after about 4-5 s.
- Stock V15 attaches to USB without VBUS (`4C4A:C755`, 293-byte config). Use it to test the host side without the key path.
- If the FM-1 is already running V15 when the XIAO boots, the M0-era recovery mistook V15's D+ pull-up for the ROM. The firmware now detects an attached target at boot and skips the key.

The objective is one automatic transition:

```
USB_KEY -> ROM holds D+ under 1 ms pulses -> PIO USB Host -> UBOOT descriptor
```

No memory or flash commands in M0. The only class request is a read-only SCSI INQUIRY.

Protocol and hardware findings: [PROTOCOL.md](PROTOCOL.md).

## Build

```
cmake -S . -B build -DPICO_SDK_PATH=$HOME/pico-sdk -DPICO_BOARD=seeed_xiao_rp2040
make -C build -j8
```

Outputs:

- `fm1_transporter.uf2`: M0, USB_KEY recovery then PIO USB host.
- `fm1_transporter_hostonly.uf2`: PIO USB host only. Use it to bring up the host side without the key path, with any full-speed device or an FM-1 already in UBOOT.

Pico-PIO-USB is the `lib/Pico-PIO-USB` submodule, pinned to 0.6.1, the version TinyUSB 0.18 in pico-sdk 2.2.0 expects. Run `git submodule update --init` after cloning.

Flash: `picotool load -f -x build/fm1_transporter.uf2`. The firmware keeps the Raspberry Pi reset interface, so `picotool -f` and a 1200 baud touch still work.

## Firmware layout

- rhport 0: RP2040 native USB device. A CDC console with the log on the Mac.
- rhport 1: Pico-PIO-USB host on PIO1, with `pin_dp = 0` (GP0 = D+, GP1 = D-).
- core 0: `tud_task()` and log drain.
- core 1: recovery on PIO0, then handoff, then `tuh_task()`.

Both owners of GP0/GP1 live on core 1, so the handoff is one call sequence. There is no cross-core signalling in the critical gap.

## Handoff rule (changed 2026-10-01)

The original plan was to "start the host after `sof_phase()` succeeds" (D+ released after calibration). That never happens on hardware. In every successful run the ROM held D+ high for the full 6 s of pulses. The old baseline then stopped the pulses and the ROM gave up.

Current rule:

1. Send the key on polarity A only.
2. On ACK, wait for the ROM's D+ pull-up. A D- pull-up instead means the wires are swapped.
3. Start the 1 ms pulses.
4. Once D+ has been held high under pulses for `HANDOFF_PULSE_MS` (6000 ms, the pulse time of every proven run; the ROM may need far less, so lower it once M0 works), leave the pulses running.
5. `recovery_release_bus()`: disable the PIO0 state machines, return the pins to SIO inputs, clear the overrides.
6. Immediately `tuh_configure()` + `tuh_init(1)`. The log prints the gap in µs.

Known gap: after Pico-PIO-USB sees the attach, TinyUSB waits about 450 ms (`ENUM_DEBOUNCING_DELAY_MS`) before bus reset. During that time neither our pulses nor SOFs reach the ROM. The manual cable swap was a gap of seconds and still worked, so this is expected to be fine. Measured: the ROM still held D+ after 3.0 s without pulses, once it had been pulsed for 6 s (fm-1-research-lab notes/usbkey-xiao-run1.log).

## Test 1: host-only

- [ ] Flash `fm1_transporter_hostonly.uf2`.
- [ ] Attach any full-speed USB device to GP0/GP1/GND (with 5 V if it needs power) and check that `DEVICE DESCRIPTOR OK` appears.
- [ ] Optional: put a stock V15 FM-1 into UBOOT via the USB-MIDI soft key, move its cable to the XIAO, and check that `VID=4C4A PID=8057` and `INQUIRY OK: vendor="WL82"` appear.

## Test 2: full M0

- [ ] Flash `fm1_transporter.uf2` with the FM-1 OFF.
- [ ] Open the console, then switch the FM-1 ON when prompted.

Expected log:

```
KEY: sending 0x16EF, polarity A ...
ACK (data line held low) after N packets
PULSE: D+ high (ROM pull-up), sending 4 us pulses every 1 ms
PULSE: D+ held high for 6000 ms - ROM waiting for host
HANDOFF: pulses stopped, PIO host started in N us
HOST: device mounted, addr=1
DEVICE DESCRIPTOR OK
  VID=4C4A PID=8057 ...
  product: "WL80UBOOT1.00"
CONFIGURATION DESCRIPTOR ...
HOST: MSC bulk OUT=.. IN=..
INQUIRY OK: vendor="WL82    " product="UBOOT1.00 ..." rev="1.00"
M0 DONE
```

The UBOOT stays idle and connected afterwards. When the host releases it (for example when the XIAO is unplugged or reset), the ROM resets and boots flash, so with stock V15 expect a re-attach as `4C4A:C755`.

## M1: read-only loader and dump (passed 2026-10-01)

The firmware embeds `wl82loader.bin` at build time from `FM1T_LOADER_BIN`, which defaults to the jl-uboot-tool copy under `~/.fm1`. It is never committed.

The Mac talks to the second CDC port with `tools/fm1t.py` (needs pyserial):

```
python3 tools/fm1t.py info
python3 tools/fm1t.py dump out.bin --compare ref.bin
```

Result on the unit restored to stock V15:

```
loader upload 24064 B in 0.48 s
info: key=980F type=3 id=856014
dump: 1 MiB in 20.7 s (49 KiB/s), sha256 0e14274c... == v15_expected_full_2026-10-01.bin
```

Once the loader runs, the chip resets after about 3 s without a command. While idle, the firmware sends `GET_ONLINE_DEVICE` once per second as a keepalive.

## M2: soft key and guarded writes (2026-10-01)

**Soft key, no power cycle.** If the FM-1 is already on the bus when the transporter boots, the USB_KEY is skipped. The transporter sends 6 s of 1 ms pulses and then starts the host. Stock V15 failed its first SETUP without the pulses. `fm1t` info/dump/write then send the USB-MIDI soft key (`04 F0 22 24 07 35 7D F7`) to V15's MIDI OUT. V15 dropped off the bus 21 ms later and came back as UBOOT 1.0 s after that. A unit without working firmware still needs the USB_KEY: start the transporter with the FM-1 off, or run `fm1t.py rekey`, then switch the FM-1 on.

**Writes.** `fm1t.py write` follows fm-1-research-lab's restore flow and is a dry run without `--write`:

- package review via `fm1_ota.require_reviewed`
- check key and flash ID
- a full read must equal `--ref` in the package region
- write only the differing sectors, never below 0x4000
- read back every sector, then do a final full read

The firmware also refuses any sector outside `[0x4000, 0x93000)` or not 4 KiB aligned. On hardware it returned `ERR range` for 0x0, 0x3000, 0x93000 and 0x4100, and erased nothing.

Hardware check of the write path: `fm1t.py selftest-write --sector 0x92000 --write` erased sector 0x92000, rewrote it with its own bytes and read it back. The final 1 MiB read equalled the pre-write image, and stock V15 booted normally afterwards. A V15-to-V15 `write` dry run reported 0 differing sectors.

Known limits:

- Dumps now run at 307 KiB/s (1 MiB in 3.3 s, up from 20.7 s), for two reasons:
  - READ_FLASH uses the loader's USB buffer size, capped at 4 KiB. GET_USB_BUFF_SIZE reports 32768.
  - `lib/pico-pio-usb-bulk-multi-xact.patch` lets bulk endpoints keep transacting within a frame, NAK retries included. The loader NAKs briefly between packets while it refills its FIFO.
- Stock V15 enables USB only after a cold power-on. After a warm start (loader `RUN_APP`, or the loader watchdog reset) it runs normally but never attaches. The soft key therefore works when the FM-1 was powered on and attached before the transporter started. After a transporter session, power-cycle the FM-1 to use it over USB again.
- If something pulls D+ up but never enumerates (seen once with V15 coming up under a running host), the transporter reboots itself, pulses, and hosts again. It does this at most 3 times in a row.
- No USB-only reset exists. The FM-1's only external reset is the PB01 8 s long press from isd_config, which needs a wire.

## Watchdog reset → USB_KEY works (2026-10-01)

The mask ROM accepts the USB_KEY after a watchdog reset, not only after a cold power-on. Test, with no power cycle and log [logs/watchdog_rekey_2026-10-01.log](logs/watchdog_rekey_2026-10-01.log):

1. With the loader running, `fm1t.py rekey` reboots the transporter into forced-key mode, which stops the keepalive.
2. The transporter waits for D+ to drop. The loader's idle watchdog fired 2.3 s later.
3. Keying started at once. ACK came after 10 packets (about 5 ms).
4. 6 s of pulses, then `4C4A:8057` mounted, INQUIRY OK, and the loader info OK again.

So firmware that arms the watchdog early can be recovered with no hardware change. If it hangs, the chip reboots through the ROM, and a transporter that is already keying catches it. Only the loader's watchdog was tested; a CFW's own P33 WDT should behave the same, because every reset starts in ROM.

A truly bricked unit (no watchdog, hung) still needs one power-on while the transporter keys.

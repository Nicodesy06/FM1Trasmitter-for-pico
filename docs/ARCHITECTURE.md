# Architecture

## Data flow

```
Mac ── native USB (CDC 0: console, CDC 1: fm1t data) ──► XIAO RP2040
                                                            │ GP0 = D+, GP1 = D-, GND
                                                            ▼
                                                   M-VAVE FM-1 (JieLi WL82)
                                                   mask ROM UBOOT1.00 → wl82loader → SPI flash
```

## Cores and peripherals

| | role |
|---|---|
| core 0 | TinyUSB device on the native port: console, fm1t data channel, RPi reset interface; drains both output rings |
| core 1 | USB_KEY recovery (PIO0), then the TinyUSB host on Pico-PIO-USB (PIO1), the UBOOT/loader protocol and fm1t requests |
| PIO0 | USB_KEY sender and 1 ms keep-alive pulses (`pio/usb_key.pio`) |
| PIO1 | Pico-PIO-USB host, `pin_dp = 0` |
| clk_sys | 120 MHz (Pico-PIO-USB needs a multiple of 12 MHz) |

GP0/GP1 have one owner at a time, and both owners run on core 1, so the
recovery → host handoff is one call sequence (about 1 ms).

## Boot sequence (core 1)

```
D+ already pulled up? ── yes ──► 6 s of 1 ms pulses ─┐   (FM-1 app or a waiting UBOOT)
        │ no                                          │
        ▼                                             │
USB_KEY 0x16EF (polarity A) until ACK                 │
        ▼                                             │
1 ms pulses until D+ drops after ≥1 s, or 6 s ────────┤
                                                      ▼
                         release GP0/GP1 → start PIO USB host
                                                      ▼
            4C4A:8057 UBOOT → INQUIRY → ready for fm1t
            4C4A:C755 stock V15 → fm1t sends the USB-MIDI soft key → UBOOT
```

If D+ is pulled up but nothing enumerates for 3 s, the transporter reboots
itself (at most 3 times in a row). `rekey` reboots into forced USB_KEY mode:
it waits for D+ to drop, then keys.

## Why the pulses matter

After the key, the ROM trims its clock from the 1 ms edges. If they stop too
early, it gives up and boots flash. Stock V15 also fails its first SETUP
without them. The PIO pulse train is cycle-exact; the host's SOFs come from
a timer IRQ.

## Safety boundaries

- Writes exist only as whole 4 KiB sectors in `[0x4000, 0x93000)`. The firmware
  refuses anything else whatever the host asks (`src/jieli_uboot.c`).
- No block/chip erase and no chip-key write are implemented.
- Package review, reference checks and the final verify are done in
  `tools/fm1t.py`, using fm-1-research-lab's `fm1_ota.require_reviewed`.

## Source map

```
src/main.c              core split, boot sequence, rekey
src/recovery.c          USB_KEY, pulses, GP0/GP1 release
src/pio_host.c          TinyUSB host, descriptors, BOT, soft key, console diagnostics
src/jieli_uboot.c       UBOOT1.00 + LoaderV2: loader upload, info, read, guarded sector write
src/transporter_proto.c fm1t line protocol on CDC 1
src/usb_device.c        native USB descriptors, reset interface
src/log.c               cross-core log ring → CDC 0
pio/usb_key.pio         key sender and pulse generator
lib/Pico-PIO-USB        submodule (0.6.1) + lib/pico-pio-usb-bulk-multi-xact.patch
tools/fm1t.py           Mac-side client
```

# JieLi WL82 UBOOT protocol (FM-1)

Hardware-verified by fm-1-research-lab (2026-09-30 / 2026-10-01). Reference
implementations there: `tools/fm1_uboot_restore.py`, `tools/fm1_ramrun.py`
(on jl-uboot-tool plus the pyusb BOT port in
`tools/patches/jl-uboot-tool-macos.patch`).

## Entry

- USB_KEY `0x16EF`, MSB first, **polarity A only** (D+ = clock, D- = data).
  Polarity B never worked. If an "ACK" is followed by D- going high instead
  of D+, the D+/D- wires are swapped.
- After the ACK the ROM pulls D+ up within ~2 ms and holds it while 1 ms
  pulses arrive. It did not release D+ in 6 s of pulses.
- If the pulses stop before a host takes over, the ROM gives up and boots
  flash. The host must take over while, or right after, the pulses run.
- A "false ACK, D+=0 D-=0" right at power-on is a harmless power-up
  transient; keying resumes.
- Alternative entry on stock V15: USB-MIDI SysEx `F0 22 24 35 7D F7` enters
  the same ROM UBOOT1.00 (`tools/fm1_softkey.py`). It fails if another
  full-speed device shares the hub.

## Device

- VID `4C4A`, PID `8057`, product string `WL80UBOOT1.00`, full speed.
- Mass Storage, Bulk-Only Transport. Endpoint numbers: read them from the
  descriptor (M0 logs them).
- SCSI INQUIRY (36 B): vendor `WL82`, product `UBOOT1.00`, rev `1.00`.
- It waits indefinitely while idle. A JUMP target that does not return within
  ~2-4 s resets the chip. When the host drops the device it resets and boots
  flash, so dump + compare + write must happen in **one** session.
- ROM P33 helper calls (e.g. `0xFFC00F2E`) crash from inside UBOOT.

## Command framing

Standard CBW/CSW. The 16-byte CDB is `cmd` (u16 BE) + args, padded with
`0xFF`. Three shapes:

| shape | data phase | notes |
|---|---|---|
| cmd_exec | IN, 16 B | `resp[0:2]` echoes cmd (BE); payload = `resp[2:]` |
| datain | IN, N B | |
| dataout | OUT, N B | |

CSW: `USBS`, same tag, status 0.

`crc16` below is CRC-16/XMODEM (poly `0x1021`, init 0, no reflection).

### ROM UBOOT1.00

| cmd | name | shape | args |
|---|---|---|---|
| `FB06` | WRITE_MEMORY | dataout | addr u32 BE, len u16 BE, `00`, crc16(data) u16 LE |
| `FD07` | READ_MEMORY | datain | addr u32 BE, len u16 BE |
| `FB08` | JUMP | cmd_exec | addr u32 BE, arg u16 BE |

**WL82 quirk:** every memory write/read through UBOOT1.00 is XORed with
`jl_crc_cipher`, per transfer, in 512-byte blocks, key `0xFFFFFFFF`:

```
crc = crc16(bytes([0xFF, 0xFF]), init=0xFFFF)
for i in range(n):
    crc = crc16(magic[i % len(magic)], init=crc)
    buf[i] ^= crc & 0xFF
```

`magic` is a fixed 16-byte GB2312 string; see jl-uboot-tool
`jltech/cipher.py` and `crc.py`.

`wl82loader.bin` (24064 B, jl-uboot-tool
`data/loaderblobs/usb/wl82loader.bin`) is shipped **already ciphered**: send
it raw in 512-byte WRITE_MEMORY blocks to `0x1C02000`, then
`JUMP(0x1C02000, 0x0001)`. Our own RAM payloads must be ciphered per
512-byte block before writing, and data read back must be deciphered.

### Loader (LoaderV2), after the jump

Flash data is **not** ciphered.

| cmd | name | shape | args / result |
|---|---|---|---|
| `FC09` | READ_KEY | cmd_exec | arg `0x00AC6900` u32 BE; key = jl_crc_cipher(reverse(payload[4:6])) as u16 LE, expect `0x980F` |
| `FC0A` | GET_ONLINE_DEVICE | cmd_exec | type = payload[0] (expect 3), id = payload[2:6] LE (expect `0x856014`, 1 MiB SPI NOR) |
| `FD05` | READ_FLASH | datain | addr u32 BE, len u16 BE; 512 B reads, 1 MiB in ~1.5 s from a Mac |
| `FB01` | ERASE_SECTOR (4 KiB) | cmd_exec | addr u32 BE |
| `FB00` | ERASE_BLOCK (64 KiB) | — | avoid |
| `FB04` | WRITE_FLASH | dataout | addr u32 BE, len u16 BE, `00`, crc16(data) u16 LE; 512 B chunks |

## Flash layout and write policy

1 MiB SPI NOR.

| range | contents | policy |
|---|---|---|
| `[0, 0x4000)` | flash header, SPL `uboot.boot` (UBOOT2.00), isd_config | never write |
| `[0x4000, 0x93000)` | application | writable; the `.fwsc` type-0 "flash" entry (602112 B) is byte-identical to raw flash from 0 |
| `[0x93000, end)` | VM / BTIF / USR device data | preserve |

Write sequence used by fm-1-research-lab, to be mirrored by `fm1t`:

1. Read all of flash.
2. Require the package region to equal the caller's reference.
3. Erase + write only the differing 4 KiB sectors.
4. Read each written sector back.
5. Final full read must equal the expected image.

Packages are refused unless they are stock V15 (sha256 `db1642b2…`) or carry a
PASSED manifest (`fm1_hookcheck.verify_manifest`). Known-bad hashes:
`tools/fm1_ota.py` `KNOWN_BAD`.

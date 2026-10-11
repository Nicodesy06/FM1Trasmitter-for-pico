<#
.SYNOPSIS
  Guided hardware test of an RP2040 board for the FM-1 Transporter (Windows).

.DESCRIPTION
  Phase A (default): the board alone. Identification questions, BOOTSEL with a
  data cable, flashing the Transporter UF2 for the board, then the full board
  self-test (LED, console, PIO pads, watchdog reboot). Nothing is wired to
  GP0/GP1 and no FM-1 is involved.

  Phase B (-Phase b): first contact with the FM-1, READ-ONLY. Only after a
  wiring checklist confirmed item by item (multimeter continuity, VBUS not
  connected). Watches USB_KEY -> pulses -> PIO USB enumeration -> UBOOT,
  checks chip key 980F / flash 856014, then reads the flash twice and saves it
  only if both reads are identical. Nothing is written to the FM-1.

  Reports: logs\hwtest-<board>-<time>.md / .json

.EXAMPLE
  .\scripts\hardware-test.ps1 -Board dollatek_rp2040_typec
  .\scripts\hardware-test.ps1 -Board dollatek_rp2040_typec -Phase b
  .\scripts\hardware-test.ps1 -Board dollatek_rp2040_typec -Uf2 artifacts\dollatek_rp2040_typec\fm1_transporter_dollatek_rp2040.uf2
#>
param(
    [string]$Board = 'dollatek_rp2040_typec',
    [ValidateSet('a', 'b', 'ab')][string]$Phase = 'a',
    [string]$Uf2,
    [switch]$NoFlash,
    [string]$DumpDir = 'backups'
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$tool = Join-Path $PSScriptRoot 'fm1-board-tool.ps1'

Write-Host "FM-1 Transporter hardware test - board $Board, phase $($Phase.ToUpper())" -ForegroundColor Cyan
Write-Host "Read DOLLATEK-RP2040-GUIDE.md first. Type q at any question to stop."
Write-Host ""
Write-Host "Ports and boot disks seen now (nothing is written):"
& $tool list
$a = @('hwtest', '--board', $Board, '--phase', $Phase, '--dump-dir', $DumpDir)
if ($Uf2) { $a += @('--uf2', $Uf2) }
if ($NoFlash) { $a += '--no-flash' }
& $tool @a
$rc = $LASTEXITCODE
if ($rc -eq 0 -and $Phase -eq 'a') {
    Write-Host ""
    Write-Host "Phase A passed. When the wiring to the FM-1 is ready and checked with a multimeter:" -ForegroundColor Green
    Write-Host "  .\scripts\hardware-test.ps1 -Board $Board -Phase b"
}
exit $rc

<#
.SYNOPSIS
  Runs fm1-board-tool (host\fm1_board_tool) with Python 3 on Windows, checking
  Python and pyserial first. All arguments are passed through.
.EXAMPLE
  .\scripts\fm1-board-tool.ps1 list
  .\scripts\fm1-board-tool.ps1 selftest --board dollatek_rp2040_typec --interactive
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Find-Python {
    foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
        $c = Get-Command $cand[0] -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $c) { continue }
        $pre = @()
        if ($cand.Count -gt 1) { $pre = $cand[1..($cand.Count - 1)] }
        $v = & $c.Source @($pre + @('-c', 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')) 2>$null
        if ($LASTEXITCODE -eq 0 -and [int]$v -ge 308) { return , @($c.Source) + $pre }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Host 'Python 3.8+ not found. Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH").' -ForegroundColor Red
    exit 1
}
$exe = $py[0]
$pre = @()
if ($py.Count -gt 1) { $pre = $py[1..($py.Count - 1)] }
& $exe @($pre + @('-c', 'import serial.tools.list_ports')) 2>$null
if ($LASTEXITCODE -ne 0) {
    $a = Read-Host 'pyserial (serial port library) is missing. Install it now with pip? [y/N]'
    if ($a -notmatch '^[yY]') { Write-Host 'pyserial is required: python -m pip install pyserial'; exit 1 }
    & $exe @($pre + @('-m', 'pip', 'install', '--user', 'pyserial'))
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
$env:PYTHONPATH = (Join-Path $Root 'host') + [IO.Path]::PathSeparator + $env:PYTHONPATH
& $exe @($pre + @('-m', 'fm1_board_tool') + $args)
exit $LASTEXITCODE

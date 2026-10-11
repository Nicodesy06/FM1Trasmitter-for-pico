<#
.SYNOPSIS
  Builds the FM-1 Transporter firmware for one or more board profiles on Windows.

.DESCRIPTION
  1. finds the Pico SDK          (-SdkPath, $env:PICO_SDK_PATH, ~\.pico-sdk\sdk\2.2.0, ...)
  2. checks CMake (>= 3.19)      (PATH or ~\.pico-sdk\cmake\*\bin)
  3. checks the Arm toolchain    (PATH, ~\.pico-sdk\toolchain\*\bin, Program Files)
  4. checks Python               (for the host tools; the build itself does not need it)
  5. configures the board target (CMake + Ninja, picotool from ~\.pico-sdk if present)
  6. compiles
  7. collects UF2 / ELF / BIN     (artifacts\<board>\)
  8. shows the errors             (the tail of the log; the full log is kept)
  9. computes SHA-256             (Get-FileHash)
 10. records the build            (artifacts\build-history.csv)
 11. writes a manifest            (artifacts\<board>\manifest-<loader>.json)

  A build is reported BUILD VERIFIED only if every step ran and the UF2 carries
  the board's FM1T-BOARD marker. If a tool is missing, nothing is claimed.
  Runs on Windows PowerShell 5.1 and PowerShell 7.

.EXAMPLE
  .\scripts\build.ps1 -Board dollatek_rp2040_typec
  .\scripts\build.ps1 -Board dollatek_rp2040_typec -Loader C:\jl\wl82loader.bin
  .\scripts\build.ps1 -All -Placeholder
  .\scripts\build.ps1 -CheckOnly
#>
[CmdletBinding()]
param(
    [string]$Board,
    [switch]$All,
    [string]$Loader,
    [switch]$FetchLoader,
    [switch]$Placeholder,
    [string]$SdkPath,
    [string]$OutDir,
    [string]$BuildDir,
    [switch]$Clean,
    [switch]$CheckOnly,
    [switch]$AllowUnvalidated,
    [int]$Jobs = 0
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Root 'artifacts' }
if (-not $BuildDir) { $BuildDir = Join-Path $Root 'build' }
$PicoHome = Join-Path $HOME '.pico-sdk'
$SdkWanted = '2.2.0'
$LoaderSha = 'd41da6126760c9d66660bcc0cac8d27d221806c5e369a8036921efe68dca5376'
$PlaceholderSha = '25f62babdb1c62753f7e293338c8ee1b6f323aec9805644b794e6581e7c5bc29'
$JlubCommit = 'adb3f18889e88ac512ce0a3c4d8cc3d3cb30696a'
$LoaderUrl = "https://raw.githubusercontent.com/kagaimiq/jl-uboot-tool/$JlubCommit/data/loaderblobs/usb/wl82loader.bin"
$IsWin = ($env:OS -eq 'Windows_NT')
$Exe = ''
if ($IsWin) { $Exe = '.exe' }

function Say([string]$m) { Write-Host $m }
function Ok([string]$m) { Write-Host "  [ok]      $m" -ForegroundColor Green }
function Bad([string]$m) { Write-Host "  [MISSING] $m" -ForegroundColor Red }
function Warn([string]$m) { Write-Host "  [warn]    $m" -ForegroundColor Yellow }

function Find-Tool([string]$name, [string[]]$globs) {
    $c = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    foreach ($g in $globs) {
        $hits = @(Get-ChildItem -Path $g -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)
        foreach ($h in $hits) {
            $p = Join-Path $h.FullName ($name + $Exe)
            if (Test-Path $p -PathType Leaf) { return $p }
            $p = Join-Path $h.FullName $name
            if (Test-Path $p -PathType Leaf) { return $p }
        }
    }
    return $null
}

function First-Line([string]$exe, [string[]]$arguments) {
    try {
        $out = & $exe @arguments 2>&1 | Select-Object -First 1
        return ("$out").Trim()
    } catch { return "unavailable ($($_.Exception.Message))" }
}

function Get-Sha256([string]$path) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() }

function Sdk-Version([string]$sdk) {
    $f = Join-Path $sdk 'pico_sdk_version.cmake'
    if (-not (Test-Path $f)) { return $null }
    $t = Get-Content -Raw $f
    $v = @()
    foreach ($k in 'MAJOR', 'MINOR', 'REVISION') {
        if ($t -match "set\(PICO_SDK_VERSION_$k (\d+)\)") { $v += $Matches[1] } else { return '?' }
    }
    return ($v -join '.')
}

# --- 1..4: tools --------------------------------------------------------------
Say "FM-1 Transporter build ($Root)"
Say "Checking the tools:"
$missing = @()

$sdkCands = @($SdkPath, $env:PICO_SDK_PATH, (Join-Path $PicoHome "sdk\$SdkWanted"))
$sdkCands += @(Get-ChildItem -Path (Join-Path $PicoHome 'sdk') -Directory -ErrorAction SilentlyContinue |
               Sort-Object Name -Descending | ForEach-Object { $_.FullName })
$sdkCands += @((Join-Path $HOME 'pico-sdk'))
$Sdk = $null
foreach ($c in $sdkCands) {
    if ($c -and (Test-Path (Join-Path $c 'pico_sdk_init.cmake'))) { $Sdk = (Resolve-Path $c).Path; break }
}
$SdkVer = $null
if ($Sdk) {
    $SdkVer = Sdk-Version $Sdk
    if (-not (Test-Path (Join-Path $Sdk 'lib/tinyusb/src'))) {
        Bad "pico-sdk at $Sdk has no lib\tinyusb: run 'git submodule update --init' in it"; $missing += 'tinyusb'
    } else { Ok "pico-sdk $SdkVer at $Sdk" }
    if ($SdkVer -ne $SdkWanted) { Warn "this project is tested with pico-sdk $SdkWanted" }
} else {
    Bad "pico-sdk $SdkWanted (set PICO_SDK_PATH or -SdkPath; the VS Code 'Raspberry Pi Pico' extension installs it in $PicoHome\sdk\$SdkWanted)"
    $missing += 'pico-sdk'
}

$Cmake = Find-Tool 'cmake' @((Join-Path $PicoHome 'cmake\*\bin'), 'C:\Program Files\CMake\bin')
$CmakeVer = $null
if ($Cmake) {
    $CmakeVer = First-Line $Cmake @('--version')
    if ($CmakeVer -match '(\d+)\.(\d+)' -and (([int]$Matches[1] -gt 3) -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 19))) {
        Ok "$CmakeVer ($Cmake)"
    } else { Bad "CMake >= 3.19 (found $CmakeVer)"; $missing += 'cmake' }
} else { Bad 'cmake (>= 3.19)'; $missing += 'cmake' }

$Gcc = Find-Tool 'arm-none-eabi-gcc' @((Join-Path $PicoHome 'toolchain\*\bin'),
    'C:\Program Files*\Arm GNU Toolchain*\*\bin', 'C:\Program Files*\GNU Arm Embedded Toolchain\*\bin')
$GccVer = $null
if ($Gcc) { $GccVer = First-Line $Gcc @('--version'); Ok "$GccVer" } else { Bad 'arm-none-eabi-gcc (Arm GNU Toolchain)'; $missing += 'gcc' }

$Ninja = Find-Tool 'ninja' @((Join-Path $PicoHome 'ninja\*'))
if ($Ninja) { Ok ("ninja " + (First-Line $Ninja @('--version'))) }
elseif ($IsWin) { Bad 'ninja (the Windows build uses the Ninja generator)'; $missing += 'ninja' }
else { Warn 'ninja not found; using Unix Makefiles' }

$Git = Find-Tool 'git' @('C:\Program Files\Git\cmd')
if ($Git) { Ok (First-Line $Git @('--version')) } else { Bad 'git (submodules and the Pico-PIO-USB patch)'; $missing += 'git' }

$Python = $null
foreach ($cand in @('py', 'python', 'python3')) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $v = First-Line $c.Source @('--version')
        if ($cand -eq 'py') { $v = First-Line $c.Source @('-3', '--version') }
        if ($v -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 8) { $Python = $c.Source; Ok "$v ($($c.Source)) - for the host tools"; break }
    }
}
if (-not $Python) { Warn 'Python 3.8+ not found: the build works, the host tools (fm1-board-tool, recovery) need it' }

$PicotoolDir = $null
$pt = @(Get-ChildItem -Path (Join-Path $PicoHome 'picotool\*\picotool') -Directory -ErrorAction SilentlyContinue | Sort-Object FullName -Descending)
foreach ($d in $pt) { if (Test-Path (Join-Path $d.FullName 'picotoolConfig.cmake')) { $PicotoolDir = $d.FullName; break } }
if ($PicotoolDir) { Ok "picotool $PicotoolDir" } else { Warn 'no prebuilt picotool: the SDK will build it (needs a host C++ compiler)' }

if (-not (Test-Path (Join-Path $Root 'lib/Pico-PIO-USB/src/pio_usb_host.c'))) {
    Bad "lib\Pico-PIO-USB is empty: run 'git submodule update --init' in $Root"; $missing += 'submodule'
}

if ($CheckOnly) {
    if ($missing.Count) { Say "Missing: $($missing -join ', ')"; exit 1 }
    Say 'All build tools found.'; exit 0
}
if ($missing.Count) {
    Write-Host "Cannot build: missing $($missing -join ', '). Nothing was built." -ForegroundColor Red
    exit 1
}

# --- boards and loader --------------------------------------------------------
$boards = @()
$profiles = Get-ChildItem (Join-Path $Root 'boards') -Filter *.json | ForEach-Object { Get-Content -Raw $_.FullName | ConvertFrom-Json }
if ($All) { $boards = @($profiles | Where-Object { $_.enabled } | ForEach-Object { $_.id }) }
elseif ($Board) { $boards = @($Board) }
else { Write-Host 'Give -Board <id> or -All. Boards:' ; $profiles | ForEach-Object { "  {0,-24} enabled={1} {2}" -f $_.id, $_.enabled, $_.validation.level }; exit 2 }

$loaderKind = 'none'; $loaderFile = $null; $loaderHash = $null
if ($Placeholder) {
    $loaderKind = 'placeholder'; $loaderFile = Join-Path $Root 'dist\placeholder_loader.pattern'
    $loaderHash = Get-Sha256 $loaderFile
    if ($loaderHash -ne $PlaceholderSha) { Write-Host "$loaderFile is damaged" -ForegroundColor Red; exit 1 }
} elseif ($FetchLoader -or $Loader) {
    if ($FetchLoader) {
        $loaderFile = Join-Path $Root '.cache\wl82loader.bin'
        if (-not (Test-Path $loaderFile) -or (Get-Sha256 $loaderFile) -ne $LoaderSha) {
            New-Item -ItemType Directory -Force (Split-Path $loaderFile) | Out-Null
            Say "Downloading $LoaderUrl"
            Invoke-WebRequest -UseBasicParsing -Uri $LoaderUrl -OutFile "$loaderFile.tmp"
            if ((Get-Sha256 "$loaderFile.tmp") -ne $LoaderSha) { Remove-Item "$loaderFile.tmp"; Write-Host 'downloaded loader has the wrong sha256; not used' -ForegroundColor Red; exit 1 }
            Move-Item -Force "$loaderFile.tmp" $loaderFile
        }
    } else { $loaderFile = (Resolve-Path $Loader).Path }
    $loaderHash = Get-Sha256 $loaderFile
    if ($loaderHash -ne $LoaderSha) {
        Write-Host "$loaderFile has sha256 $loaderHash, not the known wl82loader.bin $LoaderSha; refusing" -ForegroundColor Red; exit 1
    }
    $loaderKind = 'real'
}
$suffix = @{ 'real' = ''; 'none' = '-noloader'; 'placeholder' = '-placeholder' }[$loaderKind]

# Hex literals above 0x7FFFFFFF are negative Int32 in PowerShell: compare as UInt32.
$Uf2Magic0 = [Convert]::ToUInt32('0A324655', 16)
$Rp2040Family = [Convert]::ToUInt32('E48BFF56', 16)

function Get-Uf2Flat([byte[]]$data) {
    $ms = New-Object System.IO.MemoryStream
    for ($o = 0; $o -lt $data.Length; $o += 512) {
        $size = [BitConverter]::ToUInt32($data, $o + 16)
        $fam = [BitConverter]::ToUInt32($data, $o + 28)
        if ([BitConverter]::ToUInt32($data, $o) -ne $Uf2Magic0 -or $fam -ne $Rp2040Family) { throw "block $($o / 512): not an RP2040 UF2 block" }
        $ms.Write($data, $o + 32, [int]$size)
    }
    return $ms.ToArray()
}

$rc = 0
$historyFile = Join-Path $OutDir 'build-history.csv'
foreach ($b in $boards) {
    $prof = $profiles | Where-Object { $_.id -eq $b }
    if (-not $prof) { Write-Host "unknown board $b" -ForegroundColor Red; $rc = 1; continue }
    if (-not $prof.enabled -and -not $AllowUnvalidated) { Write-Host "board $b is disabled until validated; -AllowUnvalidated only after checking its pinout" -ForegroundColor Red; $rc = 1; continue }
    $bdir = Join-Path $BuildDir "$b-$loaderKind"
    $odir = Join-Path $OutDir $b
    if ($Clean -and (Test-Path $bdir)) { Remove-Item -Recurse -Force $bdir }
    New-Item -ItemType Directory -Force $bdir, $odir | Out-Null
    $stamp = Get-Date
    $log = Join-Path $odir ("build-{0}-{1}.log" -f $loaderKind, $stamp.ToString('yyyyMMdd-HHmmss'))
    Say ""
    Say "=== $b ($($prof.name)), loader: $loaderKind ==="

    $env:PICO_SDK_PATH = $Sdk
    $env:PATH = (Split-Path $Gcc) + [IO.Path]::PathSeparator + $env:PATH
    if ($Ninja) { $env:PATH = (Split-Path $Ninja) + [IO.Path]::PathSeparator + $env:PATH }
    $lb = $loaderFile
    if (-not $lb) { $lb = Join-Path $bdir 'no-loader.bin' }
    $cfg = @('-S', $Root, '-B', $bdir, "-DFM1T_BOARD=$b", "-DPICO_SDK_PATH=$Sdk", "-DFM1T_LOADER_BIN=$lb")
    if ($Ninja) { $cfg += @('-G', 'Ninja', "-DCMAKE_MAKE_PROGRAM=$Ninja") } else { $cfg += @('-G', 'Unix Makefiles') }
    if ($PicotoolDir) { $cfg += "-Dpicotool_DIR=$PicotoolDir" }
    if ($AllowUnvalidated) { $cfg += '-DFM1T_ALLOW_UNVALIDATED=ON' }
    $bld = @('--build', $bdir)
    if ($Jobs -gt 0) { $bld += @('--parallel', "$Jobs") }

    $ok = $true
    foreach ($step in @(@{n = 'configure'; a = $cfg }, @{n = 'compile'; a = $bld })) {
        Say "  $($step.n): cmake $($step.a -join ' ')"
        "`n$ cmake $($step.a -join ' ')" | Out-File -Append -Encoding utf8 $log
        $prevEap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & $Cmake @($step.a) 2>&1 | ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 $log
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prevEap
        if ($code -ne 0) {
            Write-Host "  $($step.n) FAILED (exit $code). Last lines of $log :" -ForegroundColor Red
            Get-Content $log -Tail 25 | ForEach-Object { Write-Host "    $_" }
            $ok = $false; break
        }
    }
    if (-not $ok) { $rc = 1; continue }

    $stem = [IO.Path]::GetFileNameWithoutExtension($prof.uf2_name)
    $files = [ordered]@{}
    foreach ($ext in 'uf2', 'elf', 'bin') {
        $src = Join-Path $bdir "$stem.$ext"
        if (-not (Test-Path $src)) { Write-Host "  expected output $src is missing" -ForegroundColor Red; $ok = $false; break }
        $dst = Join-Path $odir "$stem$suffix.$ext"
        Copy-Item -Force $src $dst
        $files[$ext] = [ordered]@{ file = (Split-Path -Leaf $dst); sha256 = (Get-Sha256 $dst); bytes = (Get-Item $dst).Length }
    }
    if (-not $ok) { $rc = 1; continue }

    $uf2Path = Join-Path $odir $files['uf2'].file
    $flat = Get-Uf2Flat ([IO.File]::ReadAllBytes($uf2Path))
    $text = [Text.Encoding]::ASCII.GetString($flat)
    $ms = [regex]::Matches($text, 'FM1T-BOARD:([a-z0-9_]+);FM1T-BUILD:([A-Za-z0-9_.+-]*);')
    if ($ms.Count -ne 1 -or $ms[0].Groups[1].Value -ne $b) {
        Write-Host "  the UF2 does not carry exactly one FM1T-BOARD:$b marker; not verified" -ForegroundColor Red
        $rc = 1; continue
    }
    $buildId = $ms[0].Groups[2].Value
    $gitRev = $null
    if ($Git) { $gitRev = (& $Git -C $Root rev-parse HEAD 2>$null) }
    $manifest = [ordered]@{
        status = 'BUILD VERIFIED'; board = $b; board_name = $prof.name; profile_validation = $prof.validation.level
        loader = $loaderKind; loader_sha256 = $loaderHash; build_id = $buildId; git_commit = $gitRev
        sdk = $Sdk; sdk_version = $SdkVer; toolchain = $GccVer; cmake = $CmakeVer
        generator = $(if ($Ninja) { 'Ninja' } else { 'Unix Makefiles' })
        host = "$([Environment]::OSVersion.VersionString), PowerShell $($PSVersionTable.PSVersion)"
        built_at = $stamp.ToString('o'); files = $files; log = (Split-Path -Leaf $log)
    }
    $mpath = Join-Path $odir "manifest-$loaderKind.json"
    $manifest | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 $mpath
    if (-not (Test-Path $historyFile)) { 'time,board,loader,build_id,uf2,sha256,status' | Out-File -Encoding utf8 $historyFile }
    ('{0},{1},{2},{3},{4},{5},BUILD VERIFIED' -f $stamp.ToString('o'), $b, $loaderKind, $buildId, $files['uf2'].file, $files['uf2'].sha256) |
        Out-File -Append -Encoding utf8 $historyFile
    Write-Host "  BUILD VERIFIED: $uf2Path" -ForegroundColor Green
    Say "    sha256 $($files['uf2'].sha256)"
    Say "    manifest $mpath"
    if ($loaderKind -eq 'none') { Warn 'no JieLi loader embedded: fine for the board self-test; for recovery use -Loader/-FetchLoader or fm1-board-tool make-firmware' }
}
exit $rc

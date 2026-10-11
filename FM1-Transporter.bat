@echo off
rem ============================================================================
rem  FM-1 Transporter - avvio con doppio clic (Windows 10/11)
rem  1. trova Python 3 (se manca, lo installa con winget, chiedendo conferma)
rem  2. installa pyserial se manca
rem  3. apre la finestra con i pulsanti: firmware, fase A, fase B, console
rem  Nessuna scrittura sull'FM-1: la fase B e' in sola lettura.
rem ============================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title FM-1 Transporter

call :find_python
if not defined PY (
    echo Python 3 non e' installato.
    where winget >nul 2>nul || goto :no_python
    choice /C SN /M "Installarlo ora con winget (Python 3.12, per l'utente corrente)"
    if errorlevel 2 goto :no_python
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    call :find_python
)
if not defined PY goto :no_python
echo Python: %PY%

%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo Tkinter manca: reinstalla Python da https://www.python.org selezionando "tcl/tk and IDLE".
    pause
    exit /b 1
)

%PY% -c "import serial.tools.list_ports" >nul 2>nul
if errorlevel 1 (
    echo Installo pyserial...
    %PY% -m pip install --user --quiet --disable-pip-version-check pyserial
    %PY% -c "import serial.tools.list_ports" >nul 2>nul || (
        echo Installazione di pyserial non riuscita. Serve internet una volta. & pause & exit /b 1
    )
)

set "PYTHONPATH=%~dp0host;%PYTHONPATH%"
echo Avvio della finestra... (questa console mostra eventuali errori; puoi ridurla a icona)
%PY% -m fm1_board_tool.gui
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%

:find_python
set "PY="
rem py launcher (anche appena installato, quando PATH non e' ancora aggiornato)
for %%P in (py.exe "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" "%WINDIR%\py.exe") do (
    if not defined PY (
        %%P -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul && set "PY=%%P -3"
    )
)
if not defined PY (
    for %%P in (python.exe "%LOCALAPPDATA%\Programs\Python\Python312\python.exe") do (
        if not defined PY (
            %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>nul && set "PY=%%P"
        )
    )
)
exit /b 0

:no_python
echo.
echo Installa Python 3 da https://www.python.org/downloads/windows/
echo (spunta "Add python.exe to PATH"), poi avvia di nuovo questo file.
pause
exit /b 1

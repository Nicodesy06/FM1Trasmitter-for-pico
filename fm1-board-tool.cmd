@echo off
rem fm1-board-tool for Windows cmd.exe: runs host\fm1_board_tool with Python 3.
setlocal
set "PYTHONPATH=%~dp0host;%PYTHONPATH%"
where py >nul 2>nul && (py -3 -m fm1_board_tool %* & exit /b %ERRORLEVEL%)
where python >nul 2>nul && (python -m fm1_board_tool %* & exit /b %ERRORLEVEL%)
echo Python 3 not found. Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH").
exit /b 1

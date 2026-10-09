@echo off
setlocal
if exist "%~dp0install.ps1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $script=Join-Path ([IO.Path]::GetTempPath()) ('thumbtalk-'+[guid]::NewGuid()+'.ps1'); try { Invoke-WebRequest 'https://github.com/AlexanderCGKarlsson/thumbtalk/releases/download/v0.8.0/install.ps1' -OutFile $script -UseBasicParsing; & $script } finally { if (Test-Path $script) { Remove-Item $script } }"
)
if errorlevel 1 (
  echo.
  echo Installation did not finish. The message above explains why.
  pause
)

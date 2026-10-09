@echo off
setlocal
if exist "%~dp0install.ps1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $script=Join-Path ([IO.Path]::GetTempPath()) ('thumbtalk-'+[guid]::NewGuid()+'.ps1'); try { [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $r='AlexanderCGKarlsson/thumbtalk'; $u='https://github.com/'+$r+'/releases/latest/download/install.ps1'; try { Invoke-WebRequest $u -OutFile $script -UseBasicParsing } catch { try { $t=(@(Invoke-RestMethod ('https://api.github.com/repos/'+$r+'/releases?per_page=30') -Headers @{'User-Agent'='ThumbTalk-installer'} -UseBasicParsing) | Where-Object { -not $_.draft } | Select-Object -First 1).tag_name; Invoke-WebRequest ('https://github.com/'+$r+'/releases/download/'+$t+'/install.ps1') -OutFile $script -UseBasicParsing } catch { throw 'Could not download the ThumbTalk installer. Check your internet connection and try again.' } }; & $script } finally { if (Test-Path $script) { Remove-Item $script } }"
)
if errorlevel 1 (
  echo.
  echo Installation did not finish. The message above explains why.
  pause
)

# Installs the bundled app for the current user. Administrator access is not needed.
param(
    [string]$Bundle = "",
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "ThumbTalk"),
    [switch]$NoLaunch,
    [switch]$NoShortcuts
)
$ErrorActionPreference = "Stop"
if (-not [Environment]::Is64BitOperatingSystem) { throw "ThumbTalk requires 64-bit Windows." }
if ((Test-Path $InstallDir) -and ((Get-Item $InstallDir).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "The install folder must not be a link."
}
if ((Test-Path $InstallDir) -and (Get-ChildItem -Force $InstallDir) -and
    -not (Test-Path (Join-Path $InstallDir ".thumbtalk-install"))) {
    throw "That folder is not a ThumbTalk installation. Choose another -InstallDir."
}
New-Item -ItemType Directory -Force $InstallDir | Out-Null
$Stage = Join-Path $InstallDir (".install-" + [guid]::NewGuid())
New-Item -ItemType Directory $Stage | Out-Null
$Previous = Join-Path $InstallDir "app.previous"
$App = Join-Path $InstallDir "app"
try {
    if (-not $Bundle) {
        $Base = "https://github.com/AlexanderCGKarlsson/thumbtalk/releases/download/v0.8.0"
        $Asset = "thumbtalk-windows-x86_64.zip"
        Write-Host "Downloading ThumbTalk..."
        $Archive = Join-Path $Stage $Asset
        Invoke-WebRequest "$Base/$Asset" -OutFile $Archive -UseBasicParsing
        $Checksum = Join-Path $Stage "$Asset.sha256"
        Invoke-WebRequest "$Base/$Asset.sha256" -OutFile $Checksum -UseBasicParsing
        $Expected = ((Get-Content $Checksum -Raw).Trim() -split '\s+')[0]
        if ($Expected -notmatch '^[0-9a-fA-F]{64}$' -or
            (Get-FileHash $Archive -Algorithm SHA256).Hash -ne $Expected) {
            throw "The download checksum did not match. Please run the installer again."
        }
        Expand-Archive $Archive -DestinationPath $Stage
        $Bundle = Join-Path $Stage "ThumbTalk"
    }
    if (-not (Test-Path (Join-Path $Bundle "thumbtalk.exe"))) { throw "The Windows app bundle is incomplete." }
    $StagedApp = Join-Path $Stage "app"
    Copy-Item $Bundle $StagedApp -Recurse
    & (Join-Path $StagedApp "thumbtalk.exe") --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "The app could not start. The existing installation was kept." }
    if (Test-Path $Previous) { throw "Move the app.previous backup aside before updating." }
    if (Test-Path $App) { Move-Item $App $Previous }
    try { Move-Item $StagedApp $App }
    catch {
        if (Test-Path $Previous) { Move-Item $Previous $App }
        throw
    }
    New-Item -ItemType File -Force (Join-Path $InstallDir ".thumbtalk-install") | Out-Null
    if (-not $NoShortcuts) {
        $Shell = New-Object -ComObject WScript.Shell
        $Programs = [Environment]::GetFolderPath("Programs")
        $Shortcut = $Shell.CreateShortcut((Join-Path $Programs "ThumbTalk.lnk"))
        $Shortcut.TargetPath = Join-Path $App "thumbtalk.exe"
        $Shortcut.Arguments = "--setup"
        $Shortcut.WorkingDirectory = $App
        $Shortcut.Save()
    }
    if (Test-Path $Previous) { Remove-Item $Previous -Recurse -Force }
    Write-Host "Updating existing ThumbTalk addons..."
    & (Join-Path $App "thumbtalk.exe") --update-addons
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "The app is installed. Some addons need an update from ThumbTalk Setup > WoW."
    }
    Write-Host "ThumbTalk installed. Open ThumbTalk from the Start menu."
    if (-not $NoLaunch) { Start-Process (Join-Path $App "thumbtalk.exe") -ArgumentList "--setup" }
}
finally { Remove-Item $Stage -Recurse -Force }

$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$Python=Get-Command python -ErrorAction SilentlyContinue
if(-not $Python){throw 'Install Python 3.12 or later from python.org, then rerun this installer.'}
& $Python.Source -m venv .venv
if($LASTEXITCODE){throw 'Could not create the private Python environment.'}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if($LASTEXITCODE){throw 'Dependency installation failed; no shortcut was installed.'}
$Programs=Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$Shell=New-Object -ComObject WScript.Shell
$Shortcut=$Shell.CreateShortcut((Join-Path $Programs 'Genta.lnk'))
$Shortcut.TargetPath=Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$Shortcut.Arguments='"'+(Join-Path $PSScriptRoot 'genta.py')+'"'
$Shortcut.WorkingDirectory=$PSScriptRoot
if(Test-Path (Join-Path $PSScriptRoot 'assets\genta.ico')){$Shortcut.IconLocation=Join-Path $PSScriptRoot 'assets\genta.ico'}
$Shortcut.Description='Genta — tasks, notes, focus and SimpMusic'
$Shortcut.Save()
Write-Output 'Genta installed for this Windows user. Start it from the Start menu. Alt+N is registered while Genta runs, unless another application owns it. Autostart remains off.'

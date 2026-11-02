# Removes the VR monitor (Virtual Display Driver). Run as administrator.
$root = Split-Path -Parent $PSScriptRoot
$devcon = Join-Path $root "downloads\vdd-25.7.23\control\Dependencies\devcon.exe"
& $devcon remove "Root\MttVDD"
"devcon exit code: $LASTEXITCODE"

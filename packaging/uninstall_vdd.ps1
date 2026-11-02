# Removes the Virtual Display Driver's monitors (run by the uninstaller as administrator).
$devcon = Join-Path $PSScriptRoot "vdd\devcon.exe"
& $devcon remove "Root\MttVDD"
"devcon exit code: $LASTEXITCODE"

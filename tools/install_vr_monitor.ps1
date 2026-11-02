# Installs the Virtual Display Driver (MikeTheTech / VirtualDrivers, signed) as the "VR monitor".
# Run as administrator. Uninstall: tools\uninstall_vr_monitor.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pkg = Join-Path $root "downloads\vdd-25.7.23"
$log = Join-Path $PSScriptRoot "install_vr_monitor.log"
Start-Transcript -Path $log -Force | Out-Null
try {
    # The driver reads its settings from C:\VirtualDisplayDriver
    New-Item -ItemType Directory -Force "C:\VirtualDisplayDriver" | Out-Null
    Copy-Item (Join-Path $PSScriptRoot "vdd_settings.xml") "C:\VirtualDisplayDriver\vdd_settings.xml" -Force
    & (Join-Path $pkg "control\Dependencies\devcon.exe") install (Join-Path $pkg "driver\VirtualDisplayDriver\MttVDD.inf") "Root\MttVDD"
    "devcon exit code: $LASTEXITCODE"
} finally {
    Stop-Transcript | Out-Null
}

# Sets how many virtual VR monitors the Virtual Display Driver creates, then restarts the driver.
# Monitor 1 holds the VR windows; monitors 2+ are "Desktop views" you can place in VR.
# Run as administrator:  set_vr_monitors.ps1 -Count 3
param([int]$Count = 3)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $PSScriptRoot "set_vr_monitors.log"
Start-Transcript -Path $log -Force | Out-Null
try {
    $xmlPath = "C:\VirtualDisplayDriver\vdd_settings.xml"
    [xml]$xml = Get-Content (Join-Path $PSScriptRoot "vdd_settings.xml")
    $xml.vdd_settings.monitors.count = "$Count"
    $xml.Save($xmlPath)
    Copy-Item $xmlPath (Join-Path $PSScriptRoot "vdd_settings.xml") -Force
    & (Join-Path $root "downloads\vdd-25.7.23\control\Dependencies\devcon.exe") restart "Root\MttVDD"
    "devcon exit code: $LASTEXITCODE"
} finally {
    Stop-Transcript | Out-Null
}

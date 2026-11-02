# Installs (or updates) the Virtual Display Driver for PSVR Screen: the invisible VR monitors and the 4K screen.
# Run by the installer as administrator, from {app}\drivers.
$ErrorActionPreference = "Continue"
$vdd = Join-Path $PSScriptRoot "vdd"
$devcon = Join-Path $vdd "devcon.exe"
Start-Transcript -Path (Join-Path $PSScriptRoot "install_vdd.log") -Force | Out-Null
try {
    # The driver reads its settings (3 monitors, resolutions up to 3840x2160) from C:\VirtualDisplayDriver
    New-Item -ItemType Directory -Force "C:\VirtualDisplayDriver" | Out-Null
    Copy-Item (Join-Path $vdd "vdd_settings.xml") "C:\VirtualDisplayDriver\vdd_settings.xml" -Force
    $found = & $devcon find "Root\MttVDD" | Out-String
    if ($found -match "[1-9][0-9]* matching device") {   # "1 matching device(s) found." (not "No matching...")
        & $devcon update (Join-Path $vdd "MttVDD.inf") "Root\MttVDD"   # already there: newest driver + settings
        & $devcon restart "Root\MttVDD"
    } else {
        & $devcon install (Join-Path $vdd "MttVDD.inf") "Root\MttVDD"
    }
    "devcon exit code: $LASTEXITCODE"
} finally {
    Stop-Transcript | Out-Null
}

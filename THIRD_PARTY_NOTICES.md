# Third-party software in PSVR Screen

PSVR Screen is free software under the GNU General Public License v3 (see LICENSE). It includes or uses the
following software, each under its own license. Their source code is available from the links below.

## Inside PSVR Screen.exe

| Software | Version | License | Source |
|---|---|---|---|
| Python | 3.14 | PSF License | https://www.python.org |
| NumPy | 2.5.4 | BSD-3-Clause (+ 0BSD, MIT, Zlib, CC0 parts) | https://github.com/numpy/numpy |
| PyAV | 19.0.2 | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| FFmpeg (in PyAV) | as in PyAV 19.0.2 | LGPL-3.0-or-later | https://ffmpeg.org |
| x264, x265 (in PyAV's FFmpeg) | - | GPL-2.0-or-later | https://code.videolan.org/videolan/x264, https://bitbucket.org/multicoreware/x265_git |
| dav1d, libvpx, Opus, SVT-AV1, libwebp, VMAF, libvpl, zlib, libiconv (in PyAV) | - | BSD / BSD-2 / BSD-3 / Zlib / LGPL | see each project |
| Pillow | 13.0.0 | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| PyOpenGL | 3.1.10 | BSD | https://github.com/mcfletch/pyopengl |
| pyGLFW + GLFW | 2.11.0 | MIT / Zlib | https://github.com/FlorianRhiem/pyGLFW |
| hidapi (cython-hidapi) | 0.15.0 | BSD (chosen of BSD / GPL-3) | https://github.com/trezor/cython-hidapi |
| PyUSB | 1.3.1 | BSD-3-Clause | https://github.com/pyusb/pyusb |
| libusb-package + libusb | 1.0.30 | Apache-2.0 / LGPL-2.1 | https://github.com/pyocd/libusb-package |
| python-mss | 10.2.0 | MIT | https://github.com/BoboTiG/python-mss |
| windows-capture | 2.0.1 | MIT | https://github.com/NiiightmareXD/windows-capture |
| PyWinRT (winrt-runtime and packages) | 4.1.0 | MIT | https://github.com/pywinrt/pywinrt |
| python-sounddevice + PortAudio | 0.5.6 | MIT | https://github.com/spatialaudio/python-sounddevice |
| yt-dlp | 2026.10 | Unlicense | https://github.com/yt-dlp/yt-dlp |
| uiautomation | 2.0.29 | Apache-2.0 | https://github.com/yinkaisheng/Python-UIAutomation-for-Windows |
| comtypes | 1.4.17 | MIT | https://github.com/enthought/comtypes |
| openvr (pyopenvr) + OpenVR SDK | 2.12.1401 | BSD-3-Clause | https://github.com/cmbruns/pyopenvr |

## Pictures

The surroundings in the `environments` folder are from Poly Haven (https://polyhaven.com), CC0 (public
domain); see environments/CREDITS.txt.

## Installed with PSVR Screen (optional, each with its own license)

| Software | License | Source |
|---|---|---|
| Virtual Display Driver (the VR monitors, the 4K screen) | MIT | https://github.com/VirtualDrivers/Virtual-Display-Driver |
| ViGEmBus (virtual Xbox controller, optional) | BSD-3-Clause | https://github.com/nefarius/ViGEmBus |
| DS4Windows (DS4 in non-Steam games, optional) | GPL-3.0 | https://github.com/schmaldeo/DS4Windows |

Steam, SteamVR and iVRy are not included: the installer only opens their official download / store pages.

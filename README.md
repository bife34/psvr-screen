# PSVR Screen

Your Windows PC inside the PS4 VR headset (PSVR), without SteamVR: your windows floating around you, 360°
surroundings, a VR video and photo player, PC games on a big screen, and a hand-over to SteamVR for real VR
games.

> **A rudimentary app, but it's working.** Made by **ALG4RVIO** (with Claude Code) for their own PSVR.
> Feel free to improve it: fixes, ideas and pull requests are very welcome.

## What it does

- **Windows around you**: pull any app into VR; move, resize, curve or push it far away. Extra invisible
  monitors (one of them a sharp 4K screen) hold the windows you see in VR.
- **Surroundings**: real 360° scenes (beach, mountains, a starry night...) instead of a black room.
- **Videos and photos**: 180° / 360° / 3D videos (files and YouTube) and 360° photos, with depth, size and
  eye-alignment controls. Recent lists are off by default (privacy).
- **Games**: any PC game on a giant screen, or hand the headset to SteamVR for VR games and come back with one
  click on the PC's control panel.
- **Controllers**: a DS4 or any Xbox controller. The DS4 works as a laser pointer (aim by moving it) with no
  extra software.
- **Layouts**: windows around you, one big desktop in front of you, or a screen that follows your head. The
  dock (bottom bar) can be moved anywhere.
- **Photo wall**: a folder's photos and videos around you - on a sphere, a ring, a flat wall or as a **memory
  galaxy** (similar pictures float together, groups named by the AI). Videos play when you point at them; a
  live wall plays them all. Pages, zoom, drag to move, look-to-pick.
- **Local AI (nothing leaves your PC)**: search and questions by voice or typed - "find beach", "when was I
  last in Lisbon" (GPS), "photos from 2024"; **People** (faces grouped); **photos in 3D** (AI depth), **step
  inside** a photo, **living photos**, **make a photo your room**; **AI 3D for flat videos**; a video's
  **timeline in space**; **memory rooms** (save a wall with its search, shape and surroundings).
- **Voice commands** (offline, the headset's microphone): "galaxy", "next", "bigger", "show videos", "step
  inside", "exit"... The AI models (~800 MB) download once, the first time they're needed.

## What you need

1. A PSVR (PS4 version) with its processor box, connected by HDMI and USB, and Windows 10 / 11 (64-bit).
2. The Virtual Display Driver (included in the installer).
3. Optional: DS4Windows + ViGEmBus, only for the DS4 in non-Steam PC games.
4. Optional, for VR games: Steam + SteamVR and iVRy (paid add-on on Steam).

Download the installer (`PSVR-Screen-Setup-<version>.exe`): it explains each part and installs what you tick.

## Running from source

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
"PSVR Screen.bat"
```

Without a headset: set `PSVR_SIMULATOR=1` (the two-lens picture in a window). How the code is organised:
[ARCHITECTURE.md](ARCHITECTURE.md). Building the .exe and the installer: see `packaging/`.

## License

GNU General Public License v3 - see [LICENSE](LICENSE). Included software and its licenses:
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

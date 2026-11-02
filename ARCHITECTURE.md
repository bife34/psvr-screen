# PSVR Screen - architecture

A VR workspace for the PS4 VR headset on a Windows PC, without SteamVR: your own 3D space with native VR
apps (drawn sharp for the headset) and Windows apps as floating windows.

## Layers

Each layer only uses the layers below it. Hardware and Windows details never leak into the shell or apps.

```
 apps/        Things you use in VR: a Windows app window, a monitor, and native VR apps (Files, ...)
 shell/       The VR "operating system": windows, placement, dock, dialogs, pointer, input routing
 ui/          VR interface toolkit: widgets drawn for the headset's resolution (round, readable text)
 scene/       3D surfaces: a placed, maybe curved rectangle with a picture, and ray hits on it
 render/      OpenGL: meshes, textures, the headset window, per-eye rendering and lens correction
 core/        Settings (saved to settings.json) and 3D math, used by everything
 device/      The hardware: PSVR USB/HID protocol, head tracking, the DS4 controller
 platform/    Windows services: monitors, app windows, capture, input injection, mouse hook, media
```

`engine.py` runs a VR session: headset on -> every frame: head pose -> `shell.update()` -> `renderer.draw()`.
`__main__.py` builds everything and starts the control panel on the main screen (`panel/`).

## Main ideas

- **Surface** (`scene/surface.py`): everything you see in VR is a surface: a rectangle with a size in
  meters, placed around you (yaw, height, distance, then turn / tilt / roll) and maybe curved. The renderer
  draws surfaces; the pointer hits surfaces. Nothing else in the 3D world has its own rendering code.
- **Content** (`apps/base.py`): what a window shows and how it reacts to the pointer. A Windows app
  (`apps/desktop.py`), a whole monitor (`apps/monitor.py`), or a native app built from UI widgets.
  New apps only implement this interface; the shell gives them a window, title bar and settings.
- **View** (`ui/view.py`): a tree of widgets (buttons, sliders, lists...) drawn into one picture, sized by
  viewing angle so text is equally readable near or far. Dock, dialogs, title bars and native apps are views.
- **Shell** (`shell/shell.py`): owns the windows (one per entry in `settings.panels`) and the system
  interface, decides what is shown every frame and routes pointer input to the right surface.
- **Threads**: the mouse hook and the controller only *queue* input; everything that changes the VR space
  runs on the render thread, once per frame. The settings store is the only state shared with the control
  panel (it is thread-safe).

## Where does a change go?

| Change                                   | Place                         |
|------------------------------------------|-------------------------------|
| A new native VR app                      | `apps/<name>.py` (+ register it in `apps/__init__.py`) |
| How new windows are placed / magnets     | `shell/workspace.py`          |
| Dock, music player                       | `shell/dock.py`               |
| Title bar or settings card of a window   | `shell/chrome.py`             |
| Look of buttons, sliders, colors, fonts  | `ui/theme.py`, `ui/widgets.py`|
| Lens correction, eye rendering           | `render/headset.py`           |
| Headset commands, sensors                | `device/psvr.py`              |
| Moving app windows to the VR monitor     | `platform/windows.py`         |

## Checking changes

`PSVR_STRAIGHT_VIEW=1` renders as if you look straight ahead (with the headset lying on the desk), so a
screenshot of the headset monitor shows the real layout through both lenses.

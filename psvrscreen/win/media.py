"""What is playing in Windows (the same thing Windows' own media controls show) + play/pause/next/previous.

Works with any app that reports to Windows: Spotify, browsers (YouTube...), Media Player, etc.
"""
import asyncio
import threading

from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as SessionManager

PLAYING = 4   # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING


class MediaWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="media")
        self.now_playing = None    # {"title", "artist", "playing"} or None when nothing plays
        self._commands = None
        self._loop = None
        self._stop = False

    def command(self, name):
        """name: "play_pause", "next" or "previous" (from the VR player buttons)."""
        if self._loop and self._commands:
            self._loop.call_soon_threadsafe(self._commands.put_nowait, name)

    def stop(self):
        self._stop = True

    def run(self):
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._main())

    async def _main(self):
        self._commands = asyncio.Queue()
        try:
            manager = await SessionManager.request_async()
        except Exception:
            return
        while not self._stop:
            session = manager.get_current_session()
            try:
                while not self._commands.empty():
                    name = self._commands.get_nowait()
                    if session is not None:
                        if name == "play_pause":
                            await session.try_toggle_play_pause_async()
                        elif name == "next":
                            await session.try_skip_next_async()
                        elif name == "previous":
                            await session.try_skip_previous_async()
                if session is None:
                    self.now_playing = None
                else:
                    props = await session.try_get_media_properties_async()
                    self.now_playing = {"title": props.title or "Unknown", "artist": props.artist or "",
                                        "playing": session.get_playback_info().playback_status == PLAYING}
            except Exception:
                pass
            try:
                # Wait up to 1 s, but wake up at once when a button is pressed (and keep that command).
                name = await asyncio.wait_for(self._commands.get(), timeout=1.0)
                self._commands.put_nowait(name)
            except asyncio.TimeoutError:
                pass

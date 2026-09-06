"""Readable stderr logging, with optional ANSI color."""

import logging
import os
import sys

SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")


def supports_color(stream) -> bool:
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return False
    try:
        if not stream.isatty():
            return False
        if os.name == "nt":
            import ctypes
            import msvcrt

            handle = msvcrt.get_osfhandle(stream.fileno())
            mode = ctypes.c_ulong()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            if not kernel.SetConsoleMode(handle, mode.value | 0x0004):
                return False
        return True
    except (OSError, ValueError, AttributeError):
        return False


class ColorFormatter(logging.Formatter):
    def __init__(self, use_color: bool):
        super().__init__("[%(levelname)s] %(message)s")
        self.use_color = use_color

    def format(self, record):
        text = super().format(record)
        color = (
            {SUCCESS: "\033[32m", logging.ERROR: "\033[31m", logging.CRITICAL: "\033[31m"}.get(
                record.levelno
            )
            if self.use_color
            else None
        )
        return f"{color}{text}\033[0m" if color else text


def configure(quiet=False, verbose=False):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(ColorFormatter(supports_color(sys.stderr)))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.ERROR if quiet else logging.INFO,
        handlers=[handler],
        force=True,
    )

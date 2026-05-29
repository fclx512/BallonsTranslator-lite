import sys

if sys.platform == "win32":
    from ..win32_utils import WindowsMoveResize as FramelessMoveResize
    from .win_frameless_window import AcrylicWindow
    from .win_frameless_window import WindowsFramelessWindow as FramelessWindow
    from .win_frameless_window import WindowsWindowEffect as WindowEffect
else:
    from ..linux_utils import LinuxMoveResize as FramelessMoveResize
    from ..linux_window_effect import LinuxWindowEffect as WindowEffect
    from .linux_frameless_window import LinuxFramelessWindow as FramelessWindow

    AcrylicWindow = FramelessWindow

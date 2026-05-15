import sys



if sys.platform == "win32":
    from .win_frameless_window import AcrylicWindow
    from .win_frameless_window import WindowsFramelessWindow as FramelessWindow
    from .win_frameless_window import WindowsWindowEffect as WindowEffect
    from ..win32_utils import WindowsMoveResize as FramelessMoveResize
else:
    from .linux_frameless_window import LinuxFramelessWindow as FramelessWindow
    from ..linux_window_effect import LinuxWindowEffect as WindowEffect
    from ..linux_utils import LinuxMoveResize as FramelessMoveResize

    AcrylicWindow = FramelessWindow

# modified from https://github.com/zhiyiYo/PyQt-Frameless-Window

from utils import shared

if not shared.FLAG_QT6:

    from .fw_qt5 import FramelessMoveResize, FramelessWindow

else:
    from .fw_qt6 import FramelessMoveResize, FramelessWindow

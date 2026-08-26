"""统一画布通知中心。

归属：所有在画布区域显示的"被动信息"类临时提示 —— 短暂 toast、进行中活动
指示、持久状态角标 —— 统一经 NotificationCenter（模块级单例 ``notification``）
收发。工具类交互覆盖层（裁剪框、对齐线、拾取线、遮罩预览等）有自己的交互
语义，不经过本中心。

锚点约定（调用方可用 anchor 参数覆盖，默认即统一显示逻辑）：
- status()   默认 top-left    状态角标（无字图标识）
- activity() 默认 top-center  进行中指示（LLM 修复 spinner）
- toast()    默认 top-center  短暂提示（修复完成 / 冲突提示）
- 空状态提示用 kind="hint" + anchor="center"，保持大号灰字样式

线程：NotificationCenter 是 GUI 线程对象。工作线程通过 post() 桥接（经内部
信号队列到 GUI 线程），禁止跨线程直接触碰 QWidget。
"""

from qtpy.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt, QTimer, Signal
from qtpy.QtWidgets import QGraphicsOpacityEffect, QLabel

from utils.config import pcfg

SPINNER_CHARS = ("◐", "◓", "◑", "◒")
GAP = 6                # 同锚点条目垂直间距
EDGE = 8               # 水平边距
TOP_EDGE = 16          # 顶部锚点垂直边距
BOTTOM_EDGE = 16       # 底部锚点垂直边距

# kind -> 样式。半透明色块浮于漫画图片之上、与应用主题无关，刻意保持高对比；
# 集中于此便于统一调整（此前散落于 canvas/drawingpanel 的多处硬编码）。
KIND_STYLES = {
    "info": {
        "name": "ToastInfoLabel",
        "bg": "rgba(20,20,20,190)",
        "fg": "#ffffff",
        "pad": "4px 12px",
        "radius": "6px",
        "size": "13px",
    },
    "success": {
        "name": "ToastSuccessLabel",
        "bg": "rgba(39,174,96,180)",
        "fg": "#ffffff",
        "pad": "4px 12px",
        "radius": "6px",
        "size": "13px",
    },
    "warning": {
        "name": "ToastWarningLabel",
        "bg": "rgba(224,150,30,200)",
        "fg": "#ffffff",
        "pad": "4px 12px",
        "radius": "6px",
        "size": "13px",
    },
    "error": {
        "name": "ToastErrorLabel",
        "bg": "rgba(220,60,60,195)",
        "fg": "#ffffff",
        "pad": "4px 12px",
        "radius": "6px",
        "size": "13px",
    },
    "hint": {
        "name": "ToastHintLabel",
        "bg": "transparent",
        "fg": "rgba(128,128,128,180)",
        "pad": "0px",
        "radius": "0px",
        "size": "22px",
    },
}


class _Banner(QLabel):
    """画布浮层提示基类：锚点、点击穿透、按 kind 取样式。

    center 由 NotificationCenter._add 回填；ToastLabel 自动消失时经它
    从中心索引移除并触发重排。
    """

    def __init__(self, host, anchor, kind, text):
        super().__init__(host)
        self.anchor = anchor
        self.kind = kind
        self.key = None
        self.center = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        style = KIND_STYLES[kind]
        self.setObjectName(style["name"])
        self.setStyleSheet(
            f"#{style['name']} {{ background-color: {style['bg']};"
            f" color: {style['fg']}; border-radius: {style['radius']};"
            f" padding: {style['pad']}; font-size: {style['size']}; }}"
        )
        self.setText(text)
        self.adjustSize()
        self.hide()


class ToastLabel(_Banner):
    """短暂提示：淡入 → 停留 duration → 淡出 → 从中心移除。

    key 相同时刷新现有条目（重置计时）而不是堆叠 —— 缩放提示等高频更新
    场景不会堆出一条队列。pcfg.animation_fps < 0 时退化为即时显示 + 定时隐藏。
    """

    FADE_IN = 150
    FADE_OUT = 300

    def __init__(self, host, anchor, kind, text, duration=2000, key=None):
        super().__init__(host, anchor, kind, text)
        self.key = key
        self.duration = duration
        effect = QGraphicsOpacityEffect(self, opacity=1.0)
        self.setGraphicsEffect(effect)
        self._fade_in = QPropertyAnimation(effect, b"opacity", self)
        self._fade_in.setDuration(self.FADE_IN)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.InOutExpo)
        self._fade_out = QPropertyAnimation(effect, b"opacity", self)
        self._fade_out.setDuration(self.FADE_OUT)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self.dismiss)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._start_fade_out)
        self._hard_timer = QTimer(self)
        self._hard_timer.setSingleShot(True)
        self._hard_timer.timeout.connect(self.dismiss)

    def start(self):
        if pcfg.animation_fps < 0:
            self.graphicsEffect().setOpacity(1.0)
            self.show()
            self._hard_timer.start(self.duration)
            return
        self.show()
        self._fade_in.start()
        self._hold.start(self.duration)

    def refresh(self, text=None, duration=None):
        self._fade_out.stop()
        self._hard_timer.stop()
        if text is not None:
            self.setText(text)
        if duration is not None:
            self.duration = duration
        self.adjustSize()
        self.graphicsEffect().setOpacity(1.0)
        if pcfg.animation_fps < 0:
            if not self.isVisible():
                self.show()
            self._hard_timer.start(self.duration)
        else:
            self._fade_in.stop()
            if not self.isVisible():
                self.show()
            self._hold.start(self.duration)
        if self.center is not None:
            self.center._relayout(self.anchor)

    def _start_fade_out(self):
        self._fade_out.start()

    def dismiss(self):
        if self.center is not None:
            self.center._remove(self)


class ActivityLabel(_Banner):
    """进行中指示：旋转字符 + 文本，持续到 activity(key, False)。"""

    TICK_MS = 140

    def __init__(self, host, anchor, text="", key=None):
        super().__init__(host, anchor, "info", "")
        self.key = key
        self._chars = SPINNER_CHARS
        self._idx = 0
        self._title = text or ""
        self.setText(self._render())
        self.adjustSize()
        self._ticker = QTimer(self)
        self._ticker.setInterval(self.TICK_MS)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

    def update_text(self, text=None):
        if text is not None:
            self._title = text
        self.setText(self._render())
        self.adjustSize()
        if self.center is not None:
            self.center._relayout(self.anchor)

    def _render(self):
        return f"{self._chars[self._idx]} {self._title}" if self._title else self._chars[self._idx]

    def _tick(self):
        self._idx = (self._idx + 1) % len(self._chars)
        self.setText(self._render())
        self.adjustSize()
        if self.center is not None:
            self.center._relayout(self.anchor)


class StatusBadge(_Banner):
    """持久状态角标：status(key, text) 更新，status(key, None) 隐藏。"""

    def __init__(self, host, anchor, kind, text, key=None):
        super().__init__(host, anchor, kind, text)
        self.key = key


class NotificationCenter(QObject):
    """画布通知中心（进程内单例 notification）。"""

    _posted = Signal(str, tuple, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host = None
        self._items = []  # 按添加顺序；_relayout 时按 anchor 分组
        self._posted.connect(self._dispatch)

    # ── 宿主 ──────────────────────────────────────────────────────────

    def attach(self, host):
        """绑定画布 viewport（Canvas 初始化时调用）。重复绑定替换旧宿主。"""
        if self._host is host:
            return
        self.detach()
        self._host = host
        host.installEventFilter(self)
        host.destroyed.connect(self._on_host_destroyed)

    def detach(self):
        self._on_host_destroyed()

    def _on_host_destroyed(self, *_):
        """宿主销毁（画布关闭）时清空索引：子 label 随宿主一并销毁，不能再触碰。

        也由 attach/detach 复用，保证换宿主时旧索引归零。
        """
        host = self._host
        self._host = None
        for item in self._items:
            item.center = None
            try:
                item.deleteLater()
            except RuntimeError:
                pass  # C++ 对象已随宿主销毁
        self._items.clear()
        if host is not None:
            try:
                host.destroyed.disconnect(self._on_host_destroyed)
                host.removeEventFilter(self)
            except (RuntimeError, TypeError):
                pass  # C++ 对象已销毁，只做 Python 侧清理

    # ── API ───────────────────────────────────────────────────────────

    def toast(self, text, kind="info", anchor="top-center", duration=2000, key=None):
        if self._host is None:
            return
        if key is not None:
            existing = self._find(key)
            if isinstance(existing, ToastLabel):
                existing.refresh(text, duration)
                return
        item = ToastLabel(self._host, anchor, kind, text, duration, key)
        self._add(item)

    def activity(self, key, running, text=None, anchor="top-center"):
        if self._host is None:
            return
        existing = self._find(key)
        if running:
            if isinstance(existing, ActivityLabel):
                existing.update_text(text)
            else:
                self._add(ActivityLabel(self._host, anchor, text, key))
        else:
            if isinstance(existing, ActivityLabel):
                self._remove(existing)

    def status(self, key, text=None, kind="success", anchor="top-left"):
        if self._host is None:
            return
        existing = self._find(key)
        if text is None:
            if isinstance(existing, StatusBadge):
                self._remove(existing)
            return
        if isinstance(existing, StatusBadge):
            existing.setText(text)
            existing.adjustSize()
            self._relayout(anchor)
        else:
            self._add(StatusBadge(self._host, anchor, kind, text, key))

    def is_active(self, key) -> bool:
        return self._find(key) is not None

    def post(self, name, *args, **kwargs):
        """工作线程入口：经内部信号队列到 GUI 线程后调用 name(*args, **kwargs)。"""
        self._posted.emit(name, args, kwargs)

    # ── 内部 ──────────────────────────────────────────────────────────

    def _find(self, key):
        for item in self._items:
            if item.key == key:
                return item
        return None

    def _add(self, item):
        item.center = self
        self._items.append(item)
        item.adjustSize()
        item.show()
        item.raise_()
        self._relayout(item.anchor)
        if isinstance(item, ToastLabel):
            item.start()

    def _remove(self, item):
        if item not in self._items:
            return
        self._items.remove(item)
        anchor = item.anchor
        item.center = None
        item.deleteLater()
        self._relayout(anchor)

    def _relayout(self, anchor=None):
        if self._host is None:
            return
        groups = {}
        for item in self._items:
            groups.setdefault(item.anchor, []).append(item)
        for a, items in groups.items():
            if anchor is not None and a != anchor:
                continue
            self._layout_group(a, items)

    def _layout_group(self, anchor, items):
        vw = self._host.width()
        vh = self._host.height()
        for item in items:
            item.adjustSize()
        stacking_down = anchor in ("top-left", "top-center", "top-right", "center")
        y = TOP_EDGE if stacking_down else 0
        for idx, item in enumerate(items):
            w, h = item.width(), item.height()
            if anchor == "top-left":
                x = EDGE
            elif anchor in ("top-right", "bottom-right"):
                x = vw - w - EDGE
            else:  # top-center / bottom-center / center
                x = max(0, (vw - w) // 2)
            if anchor == "center" and idx == 0:
                y = (vh - h) // 2  # 中心锚点首个条目精确居中，其余向下排
            elif not stacking_down and idx == 0:
                y = vh - BOTTOM_EDGE - h
            item.move(int(x), int(y))
            y += h + GAP if stacking_down else -(h + GAP)

    def _dispatch(self, name, args, kwargs):
        getattr(self, name)(*args, **kwargs)

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self._relayout()
        return super().eventFilter(obj, event)


notification = NotificationCenter()
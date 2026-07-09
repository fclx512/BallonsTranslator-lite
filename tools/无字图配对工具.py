#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
漫画有字图与无字图对应重命名工具 (高性能流式布局 + 批量拖拽)
功能：导入有字图和无字图文件夹，可视化匹配并重命名，导出至notext文件夹。
所有操作均不修改源文件。

兼容：qtpy（适配项目现有的 PyQt6 环境）
"""
import sys
import os
import json
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Set
from collections import deque

try:
    from qtpy.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QScrollArea, QLabel, QFrame, QPushButton, QFileDialog,
        QMessageBox, QMenu, QAction, QInputDialog, QStatusBar,
        QShortcut, QSizePolicy, QLayout, QDialog, QProgressDialog
    )
    from qtpy.QtCore import (
        Qt, QMimeData, QPoint, QSize, QRect, Signal, QUrl, QTimer
    )
    from qtpy.QtGui import (
        QPixmap, QDrag, QKeySequence, QFont, QPainter
    )
except ImportError:
    print("请确保已安装 qtpy 与 PyQt6: pip install qtpy PyQt6")
    sys.exit(1)


# ---- PyQt5 → PyQt6 鼠标位置兼容 ----
def _mouse_pos(event):
    """统一获取鼠标 event 的 QPoint（兼容 PyQt5 的 event.pos() 与 PyQt6 的 event.position().toPoint()）"""
    try:
        return event.pos()
    except AttributeError:
        return event.position().toPoint()


# ================== 流式布局 ==================
class FlowLayout(QLayout):
    """自动换行的流式布局，类似文件资源管理器图标排列"""
    def __init__(self, parent=None, margin=10, spacing=10):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.itemList = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.itemList.append(item)

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.doLayout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        margin = self.getContentsMargins()
        size += QSize(margin[0] + margin[1], margin[2] + margin[3])
        return size

    def doLayout(self, rect, testOnly):
        margin = self.getContentsMargins()
        effectiveRect = rect.adjusted(margin[0], margin[2], -margin[1], -margin[3])
        x = effectiveRect.x()
        y = effectiveRect.y()
        lineHeight = 0
        for item in self.itemList:
            wid = item.widget()
            spaceX = self.spacing() + wid.style().layoutSpacing(
                QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal)
            spaceY = self.spacing() + wid.style().layoutSpacing(
                QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Vertical)
            nextX = x + item.sizeHint().width() + spaceX
            if nextX - spaceX > effectiveRect.right() and lineHeight > 0:
                x = effectiveRect.x()
                y = y + lineHeight + spaceY
                nextX = x + item.sizeHint().width() + spaceX
                lineHeight = 0
            if not testOnly:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = nextX
            lineHeight = max(lineHeight, item.sizeHint().height())
        return y + lineHeight - rect.y() + margin[3]


# ================== 常量 ==================
THUMB_WIDTH = 160
THUMB_HEIGHT = 220
BORDER_WIDTH = 3
GREEN_BORDER = "#4CAF50"
DARK_GREEN_BORDER = "#2E7D32"  # 深绿色，有字图选中描边
YELLOW_BORDER = "#FFC107"
BLUE_BORDER = "#2196F3"
BACKGROUND_COLOR = "#F5F5F5"
HIGHLIGHT_BG = "#E3F2FD"
TEXT_HIGHLIGHT_BG = "#E8F5E9"  # 有字图选中背景

# 缩略图全局缓存
_thumbnail_cache = {}

# 历史记录文件（记住上次路径、窗口位置）
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sort_history.json")


def get_image_files(folder: str) -> List[str]:
    exts = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}
    if not folder:
        return []
    return sorted([os.path.join(folder, f) for f in os.listdir(folder)
                   if Path(f).suffix.lower() in exts])


def make_thumbnail(image_path: str, width=THUMB_WIDTH, height=THUMB_HEIGHT) -> QPixmap:
    if image_path in _thumbnail_cache:
        return _thumbnail_cache[image_path]
    pix = QPixmap(image_path)
    if pix.isNull():
        return QPixmap()
    scaled = pix.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    _thumbnail_cache[image_path] = scaled
    return scaled


# ================== 有字图槽位（可选） ==================
class TextImageSlot(QFrame):
    """有字图显示区，支持点击选中（深绿边框），双击预览"""
    clicked = Signal(int, object)           # index, modifiers
    preview_requested = Signal(int)         # index

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.is_selected = False
        self.setFixedSize(THUMB_WIDTH + 10, THUMB_HEIGHT + 10)
        self.setFrameStyle(QFrame.Box)
        self.setLineWidth(BORDER_WIDTH)
        self.setStyleSheet(f"background-color: white; border: {BORDER_WIDTH}px solid {GREEN_BORDER};")
        self.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(0)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        self.image_label.setStyleSheet("border: none; background-color: transparent;")
        layout.addWidget(self.image_label)

    def set_pixmap(self, pixmap: QPixmap):
        self.image_label.setPixmap(pixmap)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        color = DARK_GREEN_BORDER if selected else GREEN_BORDER
        bg = TEXT_HIGHLIGHT_BG if selected else "white"
        self.setStyleSheet(f"background-color: {bg}; border: {BORDER_WIDTH}px solid {color};")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            modifiers = QApplication.keyboardModifiers()
            self.clicked.emit(self.index, modifiers)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.preview_requested.emit(self.index)
        super().mouseDoubleClickEvent(event)


# ================== 无字图操作区（原有） ==================
class ImageSlot(QFrame):
    """无字图操作区，支持多选、拖拽、右键菜单，双击预览"""
    clicked = Signal(int, object)           # index, modifiers
    drop_received = Signal(list, int)       # source_indices, target_index
    context_menu = Signal(int, QPoint)
    preview_requested = Signal(int)         # index

    def __init__(self, index: int, get_selected_callback, parent=None):
        super().__init__(parent)
        self.index = index
        self.get_selected = get_selected_callback  # 返回当前选中索引集合
        self.image_path: Optional[str] = None
        self.thumbnail: Optional[QPixmap] = None
        self.is_selected = False
        self._drag_over = False
        self._is_shared = False
        self.setFixedSize(THUMB_WIDTH + 10, THUMB_HEIGHT + 60)
        self.setFrameStyle(QFrame.Box)
        self.setLineWidth(BORDER_WIDTH)
        self.setStyleSheet(f"background-color: white; border: {BORDER_WIDTH}px solid {YELLOW_BORDER};")
        self.setAcceptDrops(True)
        self.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 0)
        layout.setSpacing(2)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        self.image_label.setStyleSheet("border: none; background-color: transparent;")
        layout.addWidget(self.image_label)

        # 共用底图指示器（同一张无字图被多个槽位引用时显示）
        self.shared_label = QLabel()
        self.shared_label.setAlignment(Qt.AlignCenter)
        self.shared_label.setFixedHeight(16)
        self.shared_label.setStyleSheet(
            "border: none; background-color: #E3F2FD; color: #1565C0;"
            " font-size: 10px; padding: 1px 4px; border-radius: 2px;"
        )
        self.shared_label.setVisible(False)
        layout.addWidget(self.shared_label)

        self.name_label = QLabel("空")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        font = QFont()
        font.setPointSize(9)
        self.name_label.setFont(font)
        self.name_label.setFixedHeight(30)
        self.name_label.setStyleSheet("border: none; background-color: transparent;")
        layout.addWidget(self.name_label)

    def set_image(self, image_path: Optional[str], display_name: Optional[str] = None):
        self.image_path = image_path
        if image_path:
            self.thumbnail = make_thumbnail(image_path)
            self.image_label.setPixmap(self.thumbnail)
            self.name_label.setText(display_name if display_name else Path(image_path).stem)
        else:
            self.thumbnail = None
            self.image_label.clear()
            self.name_label.setText("空")

    def set_display_name(self, name: str):
        self.name_label.setText(name)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        if not self._drag_over:
            self._update_style()

    def set_shared(self, shared: bool):
        """标记此槽位是否与其他槽位共用同一张无字图"""
        self._is_shared = shared
        self.shared_label.setVisible(shared)

    def _update_style(self):
        """根据选中/拖拽悬停状态刷新边框样式"""
        if self._drag_over:
            self.setStyleSheet(
                "background-color: #E0F7FA;"
                " border: 5px solid #00BCD4;"
            )
        else:
            color = BLUE_BORDER if self.is_selected else YELLOW_BORDER
            bg = HIGHLIGHT_BG if self.is_selected else "white"
            self.setStyleSheet(
                f"background-color: {bg}; border: {BORDER_WIDTH}px solid {color};"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            modifiers = QApplication.keyboardModifiers()
            self.clicked.emit(self.index, modifiers)
            if self.image_path:
                self.drag_start_pos = _mouse_pos(event)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.preview_requested.emit(self.index)
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.image_path:
            if (_mouse_pos(event) - self.drag_start_pos).manhattanLength() > 10:
                self.start_drag()
                return
        super().mouseMoveEvent(event)

    def start_drag(self):
        drag = QDrag(self)
        mime = QMimeData()
        selected = self.get_selected()
        if self.index in selected and len(selected) > 1:
            indices = sorted(selected)
            payload = f"{self.index}|{','.join(map(str, indices))}"
            mime.setData("application/x-slot-indices", payload.encode())
        else:
            payload = f"{self.index}|{self.index}"
            mime.setData("application/x-slot-indices", payload.encode())
        drag.setMimeData(mime)
        if self.thumbnail:
            drag.setPixmap(self.thumbnail.scaled(100, 150, Qt.KeepAspectRatio))
        drag.setHotSpot(QPoint(50, 75))
        # Ctrl → 复制模式，否则移动模式
        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            drag.exec_(Qt.CopyAction)
        else:
            drag.exec_(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-slot-indices"):
            self._drag_over = True
            self._update_style()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasFormat("application/x-slot-indices"):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drag_over = False
        self._update_style()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-slot-indices"):
            data = event.mimeData().data("application/x-slot-indices").data().decode()
            anchor_str, indices_str = data.split('|', 1)
            anchor = int(anchor_str)
            indices = [int(i) for i in indices_str.split(',')]
            is_copy = event.dropAction() == Qt.CopyAction
            if is_copy:
                # -3 表示复制模式
                self.drop_received.emit([-3] + indices, self.index)
            else:
                self.drop_received.emit([anchor] + indices, self.index)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                file_path = urls[0].toLocalFile()
                if os.path.isfile(file_path) and Path(file_path).suffix.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}:
                    self.drop_received.emit([-1, file_path], self.index)
                    event.acceptProposedAction()
        else:
            event.ignore()
        # 拖拽结束后复位高亮（drop 时不会触发 dragLeaveEvent）
        self._drag_over = False
        self._update_style()

    def contextMenuEvent(self, event):
        self.context_menu.emit(self.index, event.globalPos())


# ================== 预览弹窗 ==================
class PreviewDialog(QDialog):
    """有字图/无字图对比预览，支持方向键和滚轮翻页"""
    def __init__(self, text_images: List[str], slot_data: List[Dict], start_index: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("对比预览")
        self.setMinimumSize(800, 600)
        self.resize(1000, 700)
        self.text_images = text_images
        self.slot_data = slot_data
        self.current_index = start_index
        self.max_index = len(text_images) - 1
        self.diff_mode = False

        self._init_ui()
        self._update_display()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # 上方：有字图 / 无字图 / (差异图)
        self.image_layout = QHBoxLayout()
        self.text_label = QLabel()
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setStyleSheet("border: 2px solid #4CAF50; background-color: #f0f0f0;")
        self.text_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_layout.addWidget(self.text_label)

        self.notext_label = QLabel()
        self.notext_label.setAlignment(Qt.AlignCenter)
        self.notext_label.setStyleSheet("border: 2px solid #FFC107; background-color: #f0f0f0;")
        self.notext_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_layout.addWidget(self.notext_label)

        # 差异叠加图（初始隐藏）
        self.diff_label = QLabel()
        self.diff_label.setAlignment(Qt.AlignCenter)
        self.diff_label.setStyleSheet("border: 2px solid #E91E63; background-color: #f0f0f0;")
        self.diff_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.diff_label.setVisible(False)
        self.image_layout.addWidget(self.diff_label)
        layout.addLayout(self.image_layout, stretch=1)

        # 导航栏 + 差异叠加切换
        nav_layout = QHBoxLayout()
        self.prev_btn = QPushButton("上一张")
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn = QPushButton("下一张")
        self.next_btn.clicked.connect(self._next)
        self.diff_btn = QPushButton("差异叠加")
        self.diff_btn.setCheckable(True)
        self.diff_btn.toggled.connect(self._toggle_diff)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)

        nav_layout.addWidget(self.prev_btn)
        nav_layout.addWidget(self.next_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self.diff_btn)
        nav_layout.addSpacing(10)
        nav_layout.addWidget(self.close_btn)
        layout.addLayout(nav_layout)

        self.setFocusPolicy(Qt.StrongFocus)

    def _render_diff(self, text_path: str, note_path: str) -> QPixmap:
        """用 CompositionMode_Difference 合成差异图"""
        text_pix = QPixmap(text_path)
        note_pix = QPixmap(note_path)
        if text_pix.isNull() or note_pix.isNull():
            return QPixmap()

        size = text_pix.size().scaled(800, 600, Qt.KeepAspectRatio)
        text_pix = text_pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        note_pix = note_pix.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        result = QPixmap(size)
        result.fill(Qt.white)
        painter = QPainter(result)
        painter.drawPixmap(0, 0, text_pix)
        painter.setCompositionMode(QPainter.CompositionMode_Difference)
        painter.drawPixmap(0, 0, note_pix)
        painter.end()
        return result

    def _toggle_diff(self, enabled: bool):
        self.diff_mode = enabled
        self.text_label.setVisible(not enabled)
        self.notext_label.setVisible(not enabled)
        self.diff_label.setVisible(enabled)
        if enabled:
            self._update_diff_display()
        else:
            self._update_display()

    def _update_diff_display(self):
        if not (0 <= self.current_index <= self.max_index):
            return
        text_path = self.text_images[self.current_index]
        slot = self.slot_data[self.current_index] if self.current_index < len(self.slot_data) else {}
        note_path = slot.get('image_path')
        if note_path and os.path.exists(note_path):
            diff_pix = self._render_diff(text_path, note_path)
            if not diff_pix.isNull():
                self.diff_label.setPixmap(diff_pix.scaled(
                    self.diff_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.diff_label.setText("")
            else:
                self.diff_label.setText("差异合成失败")
        else:
            self.diff_label.clear()
            self.diff_label.setText("（无对应无字图）")

    def _update_display(self):
        if 0 <= self.current_index <= self.max_index:
            text_path = self.text_images[self.current_index]
            pix = QPixmap(text_path)
            if not pix.isNull():
                self.text_label.setPixmap(pix.scaled(self.text_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

            slot = self.slot_data[self.current_index] if self.current_index < len(self.slot_data) else {}
            note_path = slot.get('image_path')
            if note_path and os.path.exists(note_path):
                pix2 = QPixmap(note_path)
                if not pix2.isNull():
                    self.notext_label.setPixmap(pix2.scaled(self.notext_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    self.notext_label.setText("图片无效")
            else:
                self.notext_label.clear()
                self.notext_label.setText("（无对应无字图）")

            self.setWindowTitle(f"对比预览 - {self.current_index+1}/{self.max_index+1}")
        self.prev_btn.setEnabled(self.current_index > 0)
        self.next_btn.setEnabled(self.current_index < self.max_index)

    def _prev(self):
        if self.current_index > 0:
            self.current_index -= 1
            self._update_display()

    def _next(self):
        if self.current_index < self.max_index:
            self.current_index += 1
            self._update_display()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Left or key == Qt.Key_Up:
            self._prev()
        elif key == Qt.Key_Right or key == Qt.Key_Down:
            self._next()
        elif key == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._prev()
        elif delta < 0:
            self._next()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.diff_mode:
            self._update_diff_display()
        else:
            self._update_display()


# ================== 快捷键速查 ==================
class ShortcutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("快捷键速查")
        self.setFixedSize(400, 350)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel("快捷键列表")
        title.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 8px;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        shortcuts = [
            ("Ctrl+Z", "撤销"),
            ("F5", "同步名称"),
            ("Space", "空格后退（后移选中图）"),
            ("Ctrl+A", "全选无字图"),
            ("Ctrl+C", "复制选中"),
            ("Ctrl+V", "粘贴"),
            ("Delete", "清除选中槽位"),
            ("Alt+A", "选中从当前到末尾"),
            ("Ctrl+Shift+A", "取消选择"),
            ("Ctrl+拖拽", "复制模式（不移动原位置）"),
            ("F1 / ?", "显示此面板"),
        ]

        for key, desc in shortcuts:
            row = QHBoxLayout()
            kb = QLabel(key)
            kb.setStyleSheet(
                "font-weight: bold; font-family: monospace;"
                " background-color: #E0E0E0; padding: 2px 6px; border-radius: 3px;"
            )
            kb.setAlignment(Qt.AlignCenter)
            kb.setFixedWidth(130)
            row.addWidget(kb)
            row.addSpacing(10)
            label = QLabel(desc)
            row.addWidget(label, stretch=1)
            layout.addLayout(row)

        layout.addStretch()
        btn = QPushButton("关闭")
        btn.setFixedWidth(100)
        btn.clicked.connect(self.close)
        layout.addWidget(btn, alignment=Qt.AlignCenter)


# ================== 主窗口 ==================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("漫画有字图/无字图对应重命名")
        self.setMinimumSize(1200, 700)

        self.text_images: List[str] = []
        self.text_folder: str = ""               # 有字图所在文件夹
        self.notext_folder: str = ""             # 无字图所在文件夹
        self.slot_data: List[Dict] = []
        self.selected_indices: Set[int] = set()       # 无字图选中
        self.selected_text_indices: Set[int] = set()  # 有字图选中
        self.last_clicked_index: int = -1              # 最后点击的无字图索引
        self.last_clicked_text_index: int = -1         # 最后点击的有字图索引
        self.history_stack: deque = deque(maxlen=50)
        self.clipboard: List[Dict] = []

        self.slot_widgets: List[ImageSlot] = []
        self.text_slot_widgets: List[TextImageSlot] = []

        # 恢复上次窗口位置
        self._restore_window()
        self._init_ui()
        self._setup_shortcuts()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(5, 5, 5, 5)

        toolbar = QHBoxLayout()
        # -- 第一栏：导入 --
        for text, slot in [("打开有字图", self.import_text_images),
                           ("导入无字图", self.import_notext_images),
                           ("选择文件", self.import_notext_files)]:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        toolbar.addSpacing(10)
        # -- 第二栏：导出 --
        btn_export = QPushButton("导出到notext")
        btn_export.clicked.connect(self.export_to_notext)
        toolbar.addWidget(btn_export)
        toolbar.addSpacing(10)
        # -- 第三栏：更多（边缘功能） --
        more_btn = QPushButton("更多 ▼")
        more_menu = QMenu()
        for label, cb in [("撤销\tCtrl+Z", self.undo_last),
                          ("补齐", self.fill_gaps),
                          ("空格后退", self.shift_selected_back_one),
                          ("重置空槽", self.clear_empty_slots),
                          ("同步名称\tF5", self.update_mapping)]:
            more_menu.addAction(label, cb)
        more_btn.setMenu(more_menu)
        toolbar.addWidget(more_btn)
        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.flow_container = QWidget()
        self.flow_container.setStyleSheet(f"background-color: {BACKGROUND_COLOR};")
        self.flow_layout = FlowLayout(self.flow_container, margin=15, spacing=15)
        self.scroll_area.setWidget(self.flow_container)
        main_layout.addWidget(self.scroll_area, stretch=1)

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.showMessage("请导入有字图文件夹")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("F5"), self, self.update_mapping)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo_last)
        QShortcut(QKeySequence("Ctrl+A"), self, self.select_all)
        QShortcut(QKeySequence("Ctrl+C"), self, self.copy_selected)
        QShortcut(QKeySequence("Ctrl+V"), self, self.paste_to_slots)
        QShortcut(QKeySequence("Delete"), self, self.delete_selected)
        QShortcut(QKeySequence("Alt+A"), self, self.extend_selection_to_end)
        QShortcut(QKeySequence("Ctrl+Shift+A"), self, self.clear_selection)
        QShortcut(QKeySequence(Qt.Key_Space), self, self.shift_selected_back_one)
        QShortcut(QKeySequence("F1"), self, self._show_shortcuts)
        QShortcut(QKeySequence("?"), self, self._show_shortcuts)

    # ========== 持久化 ==========
    def _load_persist(self) -> dict:
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_persist(self, data: dict):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _restore_window(self):
        hist = self._load_persist()
        if "window_geometry" in hist:
            x, y, w, h = hist["window_geometry"]
            self.setGeometry(x, y, w, h)

    def closeEvent(self, event):
        # 保存窗口位置、文件夹路径、清理缓存
        hist = self._load_persist()
        hist.update({
            "window_geometry": [self.x(), self.y(), self.width(), self.height()],
            "text_folder": self.text_folder,
            "notext_folder": self.notext_folder,
        })
        self._save_persist(hist)
        _thumbnail_cache.clear()
        super().closeEvent(event)

    def _clear_flow_layout(self):
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.text_slot_widgets.clear()
        self.slot_widgets.clear()

    def _rebuild_all_slots(self):
        """完全重建界面（仅在导入有字图时调用）"""
        self._clear_flow_layout()
        for idx, text_path in enumerate(self.text_images):
            col = QWidget()
            col_layout = QVBoxLayout(col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(5)

            # 有字图（可选中）
            text_slot = TextImageSlot(idx)
            text_slot.set_pixmap(make_thumbnail(text_path))
            text_slot.clicked.connect(self.handle_text_slot_click)
            text_slot.preview_requested.connect(self.show_preview)
            col_layout.addWidget(text_slot)
            self.text_slot_widgets.append(text_slot)

            name = QLabel(Path(text_path).stem)
            name.setAlignment(Qt.AlignCenter)
            name.setFixedHeight(20)
            name.setStyleSheet("border: none; font-weight: bold;")
            col_layout.addWidget(name)

            # 无字图操作区
            slot = ImageSlot(idx, lambda: self.selected_indices)
            if idx < len(self.slot_data) and self.slot_data[idx]['image_path']:
                slot.set_image(self.slot_data[idx]['image_path'], self.slot_data[idx]['display_name'])
            else:
                slot.set_image(None)
            slot.set_selected(idx in self.selected_indices)

            slot.clicked.connect(self.handle_slot_click)
            slot.drop_received.connect(self.handle_drop)
            slot.context_menu.connect(self.show_context_menu)
            slot.preview_requested.connect(self.show_preview)

            col_layout.addWidget(slot)
            self.flow_layout.addWidget(col)
            self.slot_widgets.append(slot)

    def _refresh_all_slots(self):
        """高性能刷新：只更新显示数据，不重建组件"""
        for idx, slot in enumerate(self.slot_widgets):
            if idx < len(self.slot_data):
                slot.set_image(self.slot_data[idx]['image_path'], self.slot_data[idx]['display_name'])
            else:
                slot.set_image(None)
            slot.set_selected(idx in self.selected_indices)
        # 检测共用底图（同一张无字图被多个槽位引用）
        path_counts = {}
        for data in self.slot_data:
            if data['image_path']:
                path_counts[data['image_path']] = path_counts.get(data['image_path'], 0) + 1
        for idx, slot in enumerate(self.slot_widgets):
            if idx < len(self.slot_data) and self.slot_data[idx]['image_path']:
                slot.set_shared(path_counts.get(self.slot_data[idx]['image_path'], 0) > 1)
            else:
                slot.set_shared(False)
        for idx, text_slot in enumerate(self.text_slot_widgets):
            if idx < len(self.text_images):
                text_slot.set_selected(idx in self.selected_text_indices)

    # ========== 导入 ==========
    def import_text_images(self):
        hist = self._load_persist()
        default_dir = hist.get("text_folder", "")
        folder = QFileDialog.getExistingDirectory(self, "选择有字图文件夹", default_dir)
        if not folder:
            return
        new_images = get_image_files(folder)
        if not new_images:
            QMessageBox.warning(self, "警告", "文件夹内无图片文件")
            return
        self.text_images = new_images
        self.text_folder = folder
        self.slot_data = [{'image_path': None, 'display_name': '', 'original_name': ''} for _ in new_images]
        self.selected_indices.clear()
        self.selected_text_indices.clear()
        self.last_clicked_index = -1
        self.last_clicked_text_index = -1
        self.history_stack.clear()
        self._rebuild_all_slots()
        self.statusBar.showMessage(f"已加载 {len(self.text_images)} 个有字图")

    def import_notext_images(self):
        if not self.text_images:
            QMessageBox.warning(self, "提示", "请先导入有字图文件夹")
            return
        hist = self._load_persist()
        default_dir = hist.get("notext_folder", self.text_folder)
        folder = QFileDialog.getExistingDirectory(self, "选择无字图文件夹", default_dir)
        if not folder:
            return
        no_text = get_image_files(folder)
        if not no_text:
            QMessageBox.warning(self, "警告", "文件夹内无图片文件")
            return

        self.notext_folder = folder
        self._save_history()
        filled = 0
        idx = 0
        for img in no_text:
            while idx < len(self.slot_data) and self.slot_data[idx]['image_path'] is not None:
                idx += 1
            if idx >= len(self.slot_data):
                break
            self.slot_data[idx] = {
                'image_path': img,
                'original_name': Path(img).stem,
                'display_name': Path(img).stem
            }
            filled += 1
            idx += 1

        if filled < len(no_text):
            QMessageBox.information(self, "提示",
                f"无字图数量({len(no_text)})超过槽位数({len(self.slot_data)})，仅导入前{filled}张。")
        self.selected_indices.clear()
        self.selected_text_indices.clear()
        self._refresh_all_slots()
        self.statusBar.showMessage(f"导入了 {filled} 张无字图")

    def import_notext_files(self):
        """多选无字图文件，按顺序填充到空槽位"""
        if not self.text_images:
            QMessageBox.warning(self, "提示", "请先导入有字图文件夹")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择无字图文件", self.notext_folder or "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.gif *.tiff *.webp)",
        )
        if not files:
            return
        self._save_history()
        filled = 0
        idx = 0
        for img_path in files:
            while idx < len(self.slot_data) and self.slot_data[idx]['image_path'] is not None:
                idx += 1
            if idx >= len(self.slot_data):
                break
            self.slot_data[idx] = {
                'image_path': img_path,
                'original_name': Path(img_path).stem,
                'display_name': Path(img_path).stem,
            }
            filled += 1
            idx += 1
        if filled < len(files):
            QMessageBox.information(self, "提示",
                f"选中的 {len(files)} 张图片中，仅填充了 {filled} 个空槽位（其余槽位已满）")
        self.selected_indices.clear()
        self.selected_text_indices.clear()
        self._refresh_all_slots()
        self.statusBar.showMessage(f"导入了 {filled} 张无字图文件")

    # ========== 选中逻辑 ==========
    def handle_text_slot_click(self, index, modifiers):
        """有字图点击处理"""
        if modifiers & Qt.ControlModifier:
            if index in self.selected_text_indices:
                self.selected_text_indices.remove(index)
            else:
                self.selected_text_indices.add(index)
            self.last_clicked_text_index = index
        elif modifiers & Qt.ShiftModifier:
            if self.last_clicked_text_index >= 0:
                start = min(self.last_clicked_text_index, index)
                end = max(self.last_clicked_text_index, index)
                for i in range(start, end + 1):
                    self.selected_text_indices.add(i)
            else:
                self.selected_text_indices.add(index)
            self.last_clicked_text_index = index
        else:
            self.selected_text_indices = {index}
            self.last_clicked_text_index = index

        # 清除无字图选中（互斥）
        self.selected_indices.clear()
        self._update_selection_visual()
        self.statusBar.showMessage(f"已选中有字图 {len(self.selected_text_indices)} 张")

    def handle_slot_click(self, index, modifiers):
        """无字图点击处理"""
        # 清除有字图选中（互斥）
        self.selected_text_indices.clear()

        if modifiers & Qt.ControlModifier:
            if index in self.selected_indices:
                self.selected_indices.remove(index)
            else:
                self.selected_indices.add(index)
            self.last_clicked_index = index
        elif modifiers & Qt.ShiftModifier:
            if self.last_clicked_index >= 0:
                start = min(self.last_clicked_index, index)
                end = max(self.last_clicked_index, index)
                for i in range(start, end + 1):
                    self.selected_indices.add(i)
            else:
                self.selected_indices.add(index)
            self.last_clicked_index = index
        else:
            self.selected_indices = {index}
            self.last_clicked_index = index
        self._update_selection_visual()

    def _update_selection_visual(self):
        for idx, slot in enumerate(self.slot_widgets):
            slot.set_selected(idx in self.selected_indices)
        for idx, text_slot in enumerate(self.text_slot_widgets):
            text_slot.set_selected(idx in self.selected_text_indices)

    def _save_history(self):
        import copy
        self.history_stack.append(copy.deepcopy(self.slot_data))

    def _show_shortcuts(self):
        dlg = ShortcutDialog(self)
        dlg.exec_()

    # ========== 复制/粘贴（支持有字图） ==========
    def copy_selected(self):
        # 优先处理有字图选中
        if self.selected_text_indices:
            self.clipboard = []
            for i in sorted(self.selected_text_indices):
                if i < len(self.text_images):
                    self.clipboard.append({'source': 'text', 'path': self.text_images[i]})
            self.statusBar.showMessage(f"已复制 {len(self.clipboard)} 张有字图")
            return

        # 原有无字图复制
        self.clipboard = []
        for i in sorted(self.selected_indices):
            if i < len(self.slot_data) and self.slot_data[i]['image_path']:
                self.clipboard.append(dict(self.slot_data[i]))
        self.statusBar.showMessage(f"已复制 {len(self.clipboard)} 张无字图" if self.clipboard else "无内容可复制")

    def paste_to_slots(self):
        if not self.clipboard:
            self.statusBar.showMessage("剪贴板为空")
            return

        # 检查剪贴板内容类型
        if self.clipboard[0].get('source') == 'text':
            # 粘贴有字图到无字图槽位
            targets = sorted(self.selected_indices)
            if not targets:
                QMessageBox.warning(self, "提示", "请先选择目标无字图槽位")
                return
            self._save_history()
            p = 0
            for idx in targets:
                if idx < len(self.slot_data) and p < len(self.clipboard):
                    path = self.clipboard[p]['path']
                    self.slot_data[idx] = {
                        'image_path': path,
                        'original_name': Path(path).stem,
                        'display_name': Path(path).stem
                    }
                    p += 1
            self._refresh_all_slots()
            self.statusBar.showMessage("已粘贴有字图到无字图槽位")
            return

        # 原有无字图粘贴
        targets = sorted(self.selected_indices)
        if not targets:
            QMessageBox.warning(self, "提示", "请先选择目标槽位")
            return
        self._save_history()
        p = 0
        for idx in targets:
            if idx < len(self.slot_data) and p < len(self.clipboard):
                self.slot_data[idx] = dict(self.clipboard[p])
                p += 1
        self._refresh_all_slots()
        self.statusBar.showMessage("粘贴完成")

    # ========== 删除（处理有字图选中） ==========
    def delete_selected(self):
        # 如果有选中的有字图，仅清除其选中状态
        if self.selected_text_indices:
            self.selected_text_indices.clear()
            self._update_selection_visual()
            self.statusBar.showMessage("已取消有字图选中")
            return
        if not self.selected_indices:
            return
        self._save_history()
        for i in self.selected_indices:
            if i < len(self.slot_data):
                self.slot_data[i] = {'image_path': None, 'display_name': '', 'original_name': ''}
        self._refresh_all_slots()
        self.statusBar.showMessage("已清除选中槽位")

    # ========== 选择快捷键适配 ==========
    def select_all(self):
        # Ctrl+A 全选无字图
        self.selected_text_indices.clear()
        self.selected_indices = {i for i, s in enumerate(self.slot_data) if s['image_path']}
        self._update_selection_visual()
        self.statusBar.showMessage(f"已选中 {len(self.selected_indices)} 张无字图")

    def clear_selection(self):
        self.selected_indices.clear()
        self.selected_text_indices.clear()
        self._update_selection_visual()

    def extend_selection_to_end(self):
        if self.last_clicked_index < 0:
            return
        self.selected_indices = {i for i in range(self.last_clicked_index, len(self.slot_data))
                                 if self.slot_data[i]['image_path']}
        self.selected_text_indices.clear()
        self._update_selection_visual()
        self.statusBar.showMessage(f"已选中从 {self.last_clicked_index+1} 到末尾的无字图")

    # ========== 拖拽处理（原有） ==========
    def handle_drop(self, source_indices, target_index):
        # 外部文件拖入
        if source_indices[0] == -1:
            if len(source_indices) > 1 and target_index < len(self.slot_data):
                file_path = source_indices[1]
                self._save_history()
                self.slot_data[target_index] = {
                    'image_path': file_path,
                    'original_name': Path(file_path).stem,
                    'display_name': Path(file_path).stem
                }
                self.selected_indices = {target_index}
                self.last_clicked_index = target_index
                self._refresh_all_slots()
                self.statusBar.showMessage("已从外部拖入图片")
            return

        # 复制模式（Ctrl+拖拽）：复制图片到目标槽位，不移动原位置
        if source_indices[0] == -3:
            raw_indices = source_indices[1:]
            if not raw_indices or target_index < 0 or target_index >= len(self.slot_data):
                return
            valid = sorted({i for i in raw_indices
                            if 0 <= i < len(self.slot_data) and self.slot_data[i]['image_path']})
            if not valid:
                return
            self._save_history()
            N = len(self.slot_data)
            for offset, src_idx in enumerate(valid):
                dest = target_index + offset
                if dest >= N:
                    break
                self.slot_data[dest] = dict(self.slot_data[src_idx])
            self.selected_indices = {target_index + i for i in range(min(len(valid), N - target_index))}
            self._refresh_all_slots()
            self.statusBar.showMessage(f"已复制 {len(valid)} 张图片")
            return

        # 移动模式
        anchor_index = source_indices[0]
        raw_indices = source_indices[1:]
        if not raw_indices or target_index < 0 or target_index >= len(self.slot_data):
            return

        N = len(self.slot_data)
        valid_indices = sorted({i for i in raw_indices if 0 <= i < N})
        if not valid_indices:
            return

        self._save_history()
        delta = target_index - anchor_index
        min_idx = valid_indices[0]
        max_idx = valid_indices[-1]
        if delta < -min_idx:
            delta = -min_idx
        if delta > (N - 1) - max_idx:
            delta = (N - 1) - max_idx

        new_pos_map = {i: i + delta for i in valid_indices}
        occupied_new_positions = set(new_pos_map.values())

        rest_items = [dict(self.slot_data[i]) for i in range(N) if i not in valid_indices]
        remaining_slots = [p for p in range(N) if p not in occupied_new_positions]

        new_data = [None] * N
        for old_i, new_i in new_pos_map.items():
            new_data[new_i] = dict(self.slot_data[old_i])
        for slot_pos, item in zip(remaining_slots, rest_items):
            new_data[slot_pos] = item
        for i in range(N):
            if new_data[i] is None:
                new_data[i] = {'image_path': None, 'display_name': '', 'original_name': ''}

        self.slot_data = new_data
        self.selected_indices = set(new_pos_map.values())
        self.last_clicked_index = new_pos_map.get(anchor_index, target_index)
        self._refresh_all_slots()
        self.statusBar.showMessage(f"已整体平移 {len(valid_indices)} 张图片，偏移量 {delta}")

    # ========== 其他功能 ==========
    def update_mapping(self):
        if not self.text_images or not self.slot_data:
            return
        self._save_history()
        for idx, slot in enumerate(self.slot_data):
            if slot['image_path'] and idx < len(self.text_images):
                slot['display_name'] = Path(self.text_images[idx]).stem
            elif slot['image_path']:
                slot['display_name'] = Path(slot['image_path']).stem
        self._refresh_all_slots()
        self.statusBar.showMessage("已更新映射名称")

    def undo_last(self):
        if not self.history_stack:
            QMessageBox.information(self, "提示", "没有可撤销的操作")
            return
        self.slot_data = self.history_stack.pop()
        self.selected_indices.clear()
        self.selected_text_indices.clear()
        self._refresh_all_slots()
        self.statusBar.showMessage("已撤销")

    def fill_gaps(self):
        sel_with_img = [i for i in self.selected_indices if i < len(self.slot_data) and self.slot_data[i]['image_path']]
        if len(sel_with_img) != 2:
            QMessageBox.warning(self, "提示", "请Ctrl+点击选中恰好两个无字图槽位")
            return
        a, b = sorted(sel_with_img)
        empty = [i for i in range(a + 1, b) if self.slot_data[i]['image_path'] is None]
        if not empty:
            QMessageBox.warning(self, "提示", "中间无空槽位")
            return
        self._save_history()
        src = self.slot_data[a]
        for i in empty:
            self.slot_data[i] = dict(src)
        self._refresh_all_slots()
        self.statusBar.showMessage(f"已填补 {len(empty)} 个空槽位")

    def shift_selected_back_one(self):
        if len(self.selected_indices) != 1:
            QMessageBox.information(self, "提示", "请先单击选中一张无字图，再按空格")
            return
        idx = next(iter(self.selected_indices))
        if idx >= len(self.slot_data) or not self.slot_data[idx]['image_path']:
            return

        self._save_history()
        N = len(self.slot_data)
        for i in range(N - 1, idx, -1):
            self.slot_data[i] = self.slot_data[i - 1]
        self.slot_data[idx] = {'image_path': None, 'display_name': '', 'original_name': ''}

        if idx + 1 < N:
            self.selected_indices = {idx + 1}
            self.last_clicked_index = idx + 1
        else:
            self.selected_indices = set()
            self.last_clicked_index = idx
        self._refresh_all_slots()
        self.statusBar.showMessage(f"已将第 {idx+1} 张及之后的图片整体后退一格")

    def _set_same_base_image(self):
        """将多选槽位的无字图统一为最前选中槽位的底图"""
        selected = sorted(self.selected_indices)
        if len(selected) < 2:
            return

        # 找到第一个有图槽位作为源
        first_with_img = None
        for idx in selected:
            if self.slot_data[idx]['image_path']:
                first_with_img = idx
                break

        if first_with_img is None:
            QMessageBox.warning(self, "提示", "选中的槽位中没有任何无字图")
            return

        src = self.slot_data[first_with_img]
        self._save_history()
        for idx in selected:
            if idx == first_with_img:
                continue
            self.slot_data[idx]['image_path'] = src['image_path']
            self.slot_data[idx]['original_name'] = src['original_name']
            # display_name 保留各自槽位的值（对应各自的有字图文件名）
        self._refresh_all_slots()
        self.statusBar.showMessage(
            f"已将 {len(selected)} 个槽位设为同一底图（来自第 {first_with_img+1} 槽）"
        )

    def export_to_notext(self):
        if not self.text_images:
            return
        if not any(s['image_path'] for s in self.slot_data):
            QMessageBox.warning(self, "警告", "没有无字图可导出")
            return
        # 计算要导出的文件列表
        base_dir = self.text_folder if self.text_folder else os.getcwd()
        out_dir = os.path.join(base_dir, "notext")
        os.makedirs(out_dir, exist_ok=True)

        export_items = []
        name_cnt = {}
        for idx, s in enumerate(self.slot_data):
            if not s['image_path'] or idx >= len(self.text_images):
                continue
            src = s['image_path']
            if not os.path.exists(src):
                continue
            ext = Path(src).suffix
            base = Path(self.text_images[idx]).stem
            if base in name_cnt:
                name_cnt[base] += 1
                dest = f"{base}_{name_cnt[base]}{ext}"
            else:
                name_cnt[base] = 1
                dest = f"{base}{ext}"
            export_items.append((src, os.path.join(out_dir, dest)))

        if not export_items:
            return

        # 覆盖前警告
        existing = [p for _, p in export_items if os.path.exists(p)]
        if existing:
            reply = QMessageBox.question(
                self, "文件已存在",
                f"{out_dir} 中已有 {len(existing)} 个同名文件。\n是否覆盖？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                self.statusBar.showMessage("导出已取消")
                return

        # 带进度条的导出
        progress = QProgressDialog("准备导出...", "取消", 0, len(export_items), self)
        progress.setWindowTitle("导出进度")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        exported = 0
        for i, (src, dest) in enumerate(export_items):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"正在导出 {Path(dest).name} …")
            shutil.copy2(src, dest)
            exported += 1

        progress.close()
        self.statusBar.showMessage(
            f"成功导出 {exported}/{len(export_items)} 张图片到 {out_dir}", 10000
        )

    def clear_empty_slots(self):
        self._save_history()
        for i in range(len(self.slot_data)):
            if self.slot_data[i]['image_path'] is None:
                self.slot_data[i] = {'image_path': None, 'display_name': '', 'original_name': ''}
        self._refresh_all_slots()
        self.statusBar.showMessage("已重置空槽位")

    # ========== 预览 ==========
    def show_preview(self, index):
        dlg = PreviewDialog(self.text_images, self.slot_data, index, self)
        dlg.exec_()

    # ========== 右键菜单 ==========
    def show_context_menu(self, index, global_pos):
        menu = QMenu()
        has_img = index < len(self.slot_data) and self.slot_data[index]['image_path']
        if has_img:
            menu.addAction("更新此图映射", lambda: self._single_update(index))
        menu.addAction("清除", lambda: self._single_clear(index))
        menu.addAction("复制", lambda: self._single_copy(index))
        menu.addAction("粘贴到此", lambda: self._single_paste(index))
        menu.addSeparator()
        if has_img:
            menu.addAction("重命名...", lambda: self._single_rename(index))
        # 多选 → 共用底图
        if len(self.selected_indices) >= 2:
            menu.addSeparator()
            menu.addAction("设置为同一底图", self._set_same_base_image)
        menu.addSeparator()
        menu.addAction("全选无字图", self.select_all)
        menu.addAction("取消选择", self.clear_selection)
        menu.exec_(global_pos)

    def _single_update(self, idx):
        if idx < len(self.text_images) and self.slot_data[idx]['image_path']:
            self._save_history()
            self.slot_data[idx]['display_name'] = Path(self.text_images[idx]).stem
            self._refresh_all_slots()

    def _single_clear(self, idx):
        self._save_history()
        self.slot_data[idx] = {'image_path': None, 'display_name': '', 'original_name': ''}
        self._refresh_all_slots()

    def _single_copy(self, idx):
        self.selected_indices = {idx}
        self.selected_text_indices.clear()
        self.copy_selected()

    def _single_paste(self, idx):
        if self.clipboard:
            self._save_history()
            self.slot_data[idx] = dict(self.clipboard[0])
            self._refresh_all_slots()

    def _single_rename(self, idx):
        if self.slot_data[idx]['image_path']:
            new, ok = QInputDialog.getText(self, "重命名", "新显示名称:", text=self.slot_data[idx]['display_name'])
            if ok and new:
                self._save_history()
                self.slot_data[idx]['display_name'] = new
                self._refresh_all_slots()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

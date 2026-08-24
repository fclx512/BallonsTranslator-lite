"""Ratio-locked crop rectangle item shown on the canvas in crop mode.

The crop rectangle defines exactly what region of the page is sent to an online
image inpainter, so the model never picks its own aspect ratio. The rect is
kept at a fixed ratio: drag the interior to move it, drag any handle to resize.
Every corner and every edge midpoint is a handle; resizing keeps the ratio and
anchors the opposite corner (or, for an edge handle, the opposite edge while
the perpendicular dimension grows around its center).

When parented to the canvas ``baseLayer`` (which is scaled to the zoom factor),
this item's ``rect()`` is in image-pixel coordinates, so ``pixel_rect()``
returns the region to crop/send directly.
"""

from qtpy.QtCore import QPointF, QRectF, QSizeF, Qt
from qtpy.QtGui import QBrush, QColor, QPainterPath, QPen
from qtpy.QtWidgets import QGraphicsItem, QGraphicsRectItem

# Handle / min sizes are in pixel units (scaled with the zoomed parent layer).
HANDLE_SIZE = 8.0
MIN_SIZE = 24.0
CROP_COLOR = QColor(0, 160, 255)

# All resize handles — four corners plus four edge midpoints.
CORNER_HANDLES = ("tl", "tr", "bl", "br")
EDGE_HANDLES = ("t", "b", "l", "r")
RESIZE_HANDLES = CORNER_HANDLES + EDGE_HANDLES


class CropRectItem(QGraphicsRectItem):
    """A draggable rectangle overlay that keeps a fixed aspect ratio."""

    # ``on_released``: optional Python callback fired when an interactive drag
    # (move/resize) finishes, so the owning DrawingPanel can re-clip the
    # accumulated mask preview to the new rect.  A plain callable attribute is
    # used because QGraphicsItem is not a QObject, so it cannot carry a Qt
    # Signal.
    def __init__(self, ratio: float = 16.0 / 9.0, parent=None):
        super().__init__(parent)
        self._ratio = ratio
        self._dragging = None  # None | "move" | "<handle>"
        self._drag_offset = QPointF(0, 0)
        self.on_released = None
        # Editable = the crop is being positioned (drag/resize). When the user
        # closes crop mode the rect freezes but stays visible as the generation
        # range, so it must stop swallowing the mouse to let the brush pass.
        self._editable = True

        pen = QPen(CROP_COLOR, 1.5, Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 3])
        self.setPen(pen)
        self.setBrush(
            QBrush(QColor(CROP_COLOR.red(), CROP_COLOR.green(), CROP_COLOR.blue(), 40))
        )
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        # Keep the crop overlay above other base-layer children (text blocks,
        # seed rects, etc.) so it is always the thing the mouse edits.
        self.setZValue(100)
        self.setRect(QRectF(0, 0, 0, 0))

    # ── public API ──

    def ratio(self) -> float:
        return self._ratio

    def setRatio(self, ratio: float):
        """Change the locked ratio, keeping the top-left corner in place."""
        if ratio <= 0:
            return
        self._ratio = ratio
        rect = self.rect()
        self._fit(rect.topLeft(), rect.height() * ratio, rect.height())

    def set_pixel_rect(self, x0: float, y0: float, x1: float, y1: float):
        """Place the crop at an absolute pixel rect, snapped to the ratio."""
        w = max(MIN_SIZE, x1 - x0)
        self._fit(QPointF(x0, y0), w, w / self._ratio)

    def pixel_rect(self) -> tuple:
        """Return ``(x0, y0, x1, y1)`` integer pixel bounds of the crop."""
        r = self.rect()
        return (
            int(round(r.x())),
            int(round(r.y())),
            int(round(r.x() + r.width())),
            int(round(r.y() + r.height())),
        )

    def set_editable(self, editable: bool):
        """Toggle between positioning (drag/resize) and frozen generation-range.

        When frozen the rect stays visible (it shows what will be sent to the
        API) but stops accepting the mouse so the brush / box-select tools can
        draw masks through it.  Handles are only drawn while editable.
        """
        editable = bool(editable)
        if editable == self._editable:
            return
        self._editable = editable
        if editable:
            self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.unsetCursor()
        self.update()

    # ── helpers ──

    def boundingRect(self):
        # Handles stick out by HANDLE_SIZE/2 beyond the rect edges; the default
        # rect bounding box only accounts for the pen, so the handles would
        # never repaint and would smear into trails when the crop is dragged.
        return super().boundingRect().adjusted(
            -HANDLE_SIZE / 2, -HANDLE_SIZE / 2, HANDLE_SIZE / 2, HANDLE_SIZE / 2
        )

    def shape(self):
        # Hit-test must cover the handle overhang too, otherwise the outer half
        # of every handle is not grabbable (QGraphicsRectItem::shape() is just
        # the rect).  While frozen the rect is non-interactive anyway, so a
        # plain rect shape is enough.
        s = super().shape()
        if not self._editable:
            return s
        pad = HANDLE_SIZE / 2
        ring = QPainterPath()
        ring.addRect(self.rect().adjusted(-pad, -pad, pad, pad))
        return ring.united(s)

    def _parent_rect(self) -> QRectF:
        parent = self.parentItem()
        if parent is not None:
            return parent.rect()
        return QRectF(0, 0, 4000, 4000)

    def _min_w(self) -> float:
        # Smallest width that still guarantees BOTH sides are >= MIN_SIZE once
        # the height is derived from the ratio.
        return max(MIN_SIZE, MIN_SIZE * self._ratio)

    def _fit(self, top_left: QPointF, w: float, h: float):
        """Clamp to the parent bounds while enforcing ``_ratio``."""
        ratio = self._ratio
        w = max(self._min_w(), w)
        h = w / ratio
        pw, ph = self._parent_rect().width(), self._parent_rect().height()
        if w > pw:
            w = pw
            h = w / ratio
        if h > ph:
            h = ph
            w = h * ratio
        x = min(max(0.0, top_left.x()), max(0.0, pw - w))
        y = min(max(0.0, top_left.y()), max(0.0, ph - h))
        self.setRect(QRectF(QPointF(x, y), QSizeF(w, h)))

    def _resize_rect(self, pos: QPointF, handle: str) -> QRectF:
        """Compute the new rect for a ratio-locked resize on ``handle``."""
        r = self.rect()
        ratio = self._ratio
        pw, ph = self._parent_rect().width(), self._parent_rect().height()
        min_w = self._min_w()

        if handle == "br":
            ax, ay = r.left(), r.top()
            w = max(pos.x() - ax, (pos.y() - ay) * ratio, min_w)
            w = min(w, pw - ax)
            h = w / ratio
            if h > ph - ay:
                h = ph - ay
                w = h * ratio
            return QRectF(ax, ay, w, h)
        if handle == "bl":
            ax, ay = r.right(), r.top()
            w = max(ax - pos.x(), (pos.y() - ay) * ratio, min_w)
            w = min(w, ax)
            h = w / ratio
            if h > ph - ay:
                h = ph - ay
                w = h * ratio
            return QRectF(ax - w, ay, w, h)
        if handle == "tr":
            ax, ay = r.left(), r.bottom()
            w = max(pos.x() - ax, (ay - pos.y()) * ratio, min_w)
            w = min(w, pw - ax)
            h = w / ratio
            if h > ay:
                h = ay
                w = h * ratio
            return QRectF(ax, ay - h, w, h)
        if handle == "tl":
            ax, ay = r.right(), r.bottom()
            w = max(ax - pos.x(), (ay - pos.y()) * ratio, min_w)
            w = min(w, ax)
            h = w / ratio
            if h > ay:
                h = ay
                w = h * ratio
            return QRectF(ax - w, ay - h, w, h)
        if handle == "r":
            ax = r.left()
            w = max(pos.x() - ax, min_w)
            w = min(w, pw - ax)
            h = w / ratio
            if h > ph:
                h = ph
                w = h * ratio
                w = min(w, pw - ax)
            top = r.center().y() - h / 2
            top = min(max(0.0, top), ph - h)
            return QRectF(ax, top, w, h)
        if handle == "l":
            ax = r.right()
            w = max(ax - pos.x(), min_w)
            w = min(w, ax)
            h = w / ratio
            if h > ph:
                h = ph
                w = h * ratio
                w = min(w, ax)
            top = r.center().y() - h / 2
            top = min(max(0.0, top), ph - h)
            return QRectF(ax - w, top, w, h)
        if handle == "b":
            ay = r.top()
            h = max(pos.y() - ay, min_w / ratio)
            h = min(h, ph - ay)
            w = h * ratio
            left = r.center().x() - w / 2
            left = min(max(0.0, left), pw - w)
            return QRectF(left, ay, w, h)
        if handle == "t":
            ay = r.bottom()
            h = max(ay - pos.y(), min_w / ratio)
            h = min(h, ay)
            w = h * ratio
            left = r.center().x() - w / 2
            left = min(max(0.0, left), pw - w)
            return QRectF(left, ay - h, w, h)
        return r

    def _handle_rects(self) -> dict:
        r = self.rect()
        s = HANDLE_SIZE
        hs = s / 2
        cx, cy = r.center().x(), r.center().y()
        return {
            "tl": QRectF(r.left() - hs, r.top() - hs, s, s),
            "tr": QRectF(r.right() - hs, r.top() - hs, s, s),
            "bl": QRectF(r.left() - hs, r.bottom() - hs, s, s),
            "br": QRectF(r.right() - hs, r.bottom() - hs, s, s),
            "t": QRectF(cx - hs, r.top() - hs, s, s),
            "b": QRectF(cx - hs, r.bottom() - hs, s, s),
            "l": QRectF(r.left() - hs, cy - hs, s, s),
            "r": QRectF(r.right() - hs, cy - hs, s, s),
        }

    # ── mouse handling ──

    def mousePressEvent(self, event):
        if not self._editable or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        pos = event.pos()
        for handle, hrect in self._handle_rects().items():
            if hrect.contains(pos):
                self._dragging = handle
                event.accept()
                return
        if self.rect().contains(pos):
            self._dragging = "move"
            self._drag_offset = pos - self.rect().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._editable or self._dragging is None:
            return super().mouseMoveEvent(event)
        pos = event.pos()
        if self._dragging == "move":
            w, h = self.rect().width(), self.rect().height()
            pw, ph = self._parent_rect().width(), self._parent_rect().height()
            new_tl = pos - self._drag_offset
            new_tl.setX(min(max(0.0, new_tl.x()), max(0.0, pw - w)))
            new_tl.setY(min(max(0.0, new_tl.y()), max(0.0, ph - h)))
            self.setRect(QRectF(new_tl, QSizeF(w, h)))
            event.accept()
            return
        if self._dragging in RESIZE_HANDLES:
            self.setRect(self._resize_rect(pos, self._dragging))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging is not None:
            self._dragging = None
            event.accept()
            if self.on_released is not None:
                self.on_released()
            return
        super().mouseReleaseEvent(event)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if not self._editable:
            return
        painter.setPen(QPen(CROP_COLOR, 1.5))
        painter.setBrush(QBrush(CROP_COLOR))
        for hrect in self._handle_rects().values():
            painter.drawRect(hrect)

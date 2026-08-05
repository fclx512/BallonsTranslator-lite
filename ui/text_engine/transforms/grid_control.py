"""Scene overlay for editing one selected Grid transform (Stage 5 follow-up).

Minimal local port of upstream v1.5.10 ``transforms/grid_control.py``: the
warped grid mesh is painted as a sampled path in base-layer coordinates and
every control point gets a fixed-device-size draggable handle.  Dragging a
handle freezes the scene→grid-output mapping
(``capture_scene_to_grid_output_mapper``) and drives the session callbacks
(``begin_grid_edit`` / ``preview_grid_points`` / ``commit_grid_points`` /
``cancel_grid_edit``) once per gesture, landing exactly one undo step.

Subtractions from upstream: no modal point transform, no rubber-band
multi-select and no overlay surface renderer — the mesh is sampled through
the output mapper instead.  The overlay keeps an empty ``shape()`` (and
accepts no mouse buttons) so text clicks pass through to the item below; only
the handle items are interactive.

The canvas owns one instance; ``bind()`` / ``clear()`` follow the selected
Grid stage reported by ``TextTransformEditSession._sync_transform_controller``.
"""

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QBrush, QColor, QPainterPath, QPen
from qtpy.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem

from utils.fontformat import GridTextTransform

# Mirrors ui/texteditshapecontrol.py so canvas hit-tests can identify all
# editing overlays without an import cycle.
CONTROL_ITEM_DATA_KEY = 0x1238

GRID_HANDLE_RADIUS = 5.0
GRID_LINE_WIDTH = 1.25
GRID_MESH_SAMPLES = 16


class GridControlPointItem(QGraphicsEllipseItem):
    """One circular, fixed-device-size Grid handle."""

    def __init__(self, controller, index):
        super().__init__(
            -GRID_HANDLE_RADIUS,
            -GRID_HANDLE_RADIUS,
            GRID_HANDLE_RADIUS * 2.0,
            GRID_HANDLE_RADIUS * 2.0,
            controller,
        )
        self.controller = controller
        self.index = int(index)
        self.setFlag(
            QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
            True,
        )
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setData(CONTROL_ITEM_DATA_KEY, True)
        pen = QPen(QColor(30, 147, 229), 1.5)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(2.0)
        self.setBrush(QBrush(QColor(255, 255, 255)))

    def mousePressEvent(self, event):
        if self.controller.begin_handle_drag(
            self.index, event.scenePos(), event.modifiers()
        ):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.controller.move_handle_drag(event.scenePos()):
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.controller.finish_handle_drag():
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TextGridTransformControl(QGraphicsPathItem):
    """Edit one selected Grid stage for exactly one text item."""

    def __init__(self):
        super().__init__()
        pen = QPen(QColor(30, 147, 229, 190), GRID_LINE_WIDTH)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setData(CONTROL_ITEM_DATA_KEY, True)
        # The mesh itself must never swallow canvas clicks; only handles do.
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setZValue(20.0)
        self.setVisible(False)

        self.item = None
        self.stack_index = -1
        self.handles = []
        self._begin_edit = None
        self._preview_points = None
        self._commit_points = None
        self._cancel_edit = None
        self._drag_mapping = None
        self._drag_index = None
        self._drag_start_grid = None
        self._drag_previous_grid = None
        self._drag_initial_points = None
        self._drag_latest_points = None

    def bind(
        self,
        item,
        stack_index,
        *,
        begin_edit,
        preview_points,
        commit_points,
        cancel_edit,
    ):
        """Attach to *item*'s Grid stage at *stack_index* and show the overlay."""
        if self.item is not item:
            self.clear()
            self.item = item
            item.visual_geometry_changed.connect(self.requestGeometryRefresh)
            item.moving.connect(self.requestGeometryRefresh)
        elif self.stack_index != stack_index:
            self._clear_drag()
        self.stack_index = int(stack_index)
        self._begin_edit = begin_edit
        self._preview_points = preview_points
        self._commit_points = commit_points
        self._cancel_edit = cancel_edit
        self.show()
        self.requestGeometryRefresh()

    def clear(self):
        """Detach, remove every handle, and hide the overlay."""
        if self.item is not None:
            try:
                self.item.visual_geometry_changed.disconnect(
                    self.requestGeometryRefresh
                )
                self.item.moving.disconnect(self.requestGeometryRefresh)
            except (RuntimeError, TypeError):
                pass
        self.item = None
        self.stack_index = -1
        self._clear_drag()
        for handle in self.handles:
            handle.setParentItem(None)
            if handle.scene() is not None:
                handle.scene().removeItem(handle)
        self.handles = []
        self.setPath(QPainterPath())
        self.hide()

    def _grid_transform(self):
        if self.item is None:
            return None
        stack = self.item._effective_text_transform().stack
        if self.stack_index < 0 or self.stack_index >= len(stack):
            return None
        transform = stack[self.stack_index]
        return (
            transform if isinstance(transform, GridTextTransform) else None
        )

    def _ensure_handle_count(self, count):
        while len(self.handles) < count:
            self.handles.append(GridControlPointItem(self, len(self.handles)))
        while len(self.handles) > count:
            handle = self.handles.pop()
            handle.setParentItem(None)
            if handle.scene() is not None:
                handle.scene().removeItem(handle)

    def requestGeometryRefresh(self, *_args):
        if self.item is None:
            return
        geometry = self.item.geometry_controller.grid_control_geometry(
            self.stack_index
        )
        if geometry is None:
            self.clear()
            return
        visual_points, output_mapper, source_rect, transform = geometry
        parent = self.parentItem()
        if parent is None:
            self.clear()
            return
        inverse, invertible = parent.sceneTransform().inverted()
        if not invertible:
            return

        # Handles sit on the transformed control points, mapped into the
        # control's (base-layer) coordinate system.
        self._ensure_handle_count(len(visual_points))
        for index, (handle, visual_point) in enumerate(
            zip(self.handles, visual_points)
        ):
            scene_point = self.item.mapToScene(QPointF(visual_point))
            handle.setPos(inverse.map(scene_point))

        # The warped mesh samples the source grid lines through the output
        # mapper so it always matches the rendered distortion (and division
        # count) even for a still-neutral grid.
        path = QPainterPath()
        if not source_rect.isEmpty():
            horizontal = max(1, int(transform.horizontal_divisions))
            vertical = max(1, int(transform.vertical_divisions))
            for row in range(vertical + 1):
                y = source_rect.top() + source_rect.height() * row / vertical
                self._append_mesh_line(
                    path,
                    output_mapper,
                    inverse,
                    [
                        QPointF(
                            source_rect.left()
                            + source_rect.width() * column / GRID_MESH_SAMPLES,
                            y,
                        )
                        for column in range(GRID_MESH_SAMPLES + 1)
                    ],
                )
            for column in range(horizontal + 1):
                x = (
                    source_rect.left()
                    + source_rect.width() * column / horizontal
                )
                self._append_mesh_line(
                    path,
                    output_mapper,
                    inverse,
                    [
                        QPointF(
                            x,
                            source_rect.top()
                            + source_rect.height() * row / GRID_MESH_SAMPLES,
                        )
                        for row in range(GRID_MESH_SAMPLES + 1)
                    ],
                )
        self.setPath(path)
        self.update()

    def _append_mesh_line(self, path, output_mapper, inverse, source_points):
        mapped = []
        for point in source_points:
            visual = output_mapper.forward_point(point)
            scene_point = self.item.mapToScene(visual)
            mapped.append(inverse.map(scene_point))
        if not mapped:
            return
        path.moveTo(mapped[0])
        for point in mapped[1:]:
            path.lineTo(point)

    def begin_handle_drag(self, index, scene_pos, modifiers):
        transform = self._grid_transform()
        if transform is None:
            return False
        captured = (
            self.item.geometry_controller
            .capture_scene_to_grid_output_mapper(self.stack_index)
        )
        if captured is None:
            return False
        scene_to_grid, normalize_delta = captured
        self._drag_mapping = (scene_to_grid, normalize_delta)
        self._drag_index = int(index)
        self._drag_start_grid = scene_to_grid(scene_pos)
        self._drag_previous_grid = QPointF(self._drag_start_grid)
        self._drag_initial_points = tuple(transform.control_points)
        self._drag_latest_points = self._drag_initial_points
        if self._begin_edit is not None:
            self._begin_edit(self.stack_index)
        return True

    def move_handle_drag(self, scene_pos):
        if self._drag_mapping is None or self._drag_index is None:
            return False
        scene_to_grid, normalize_delta = self._drag_mapping
        current = scene_to_grid(scene_pos, self._drag_previous_grid)
        self._drag_previous_grid = QPointF(current)
        delta = normalize_delta(current - self._drag_start_grid)
        points = list(self._drag_initial_points)
        x, y = points[self._drag_index]
        points[self._drag_index] = (x + delta.x(), y + delta.y())
        self._drag_latest_points = tuple(points)
        if self._preview_points is not None:
            self._preview_points(self.stack_index, self._drag_latest_points)
        return True

    def finish_handle_drag(self):
        if self._drag_mapping is None:
            return False
        points = self._drag_latest_points
        unchanged = points == self._drag_initial_points
        self._clear_drag()
        if unchanged:
            if self._cancel_edit is not None:
                self._cancel_edit(self.stack_index)
        elif self._commit_points is not None:
            self._commit_points(self.stack_index, points)
        return True

    def _clear_drag(self):
        self._drag_mapping = None
        self._drag_index = None
        self._drag_start_grid = None
        self._drag_previous_grid = None
        self._drag_initial_points = None
        self._drag_latest_points = None

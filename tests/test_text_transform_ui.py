"""Stage 5 text-engine UI tests for the transform edit session (node G).

``TextTransformEditSessionTest`` drives ``ui/text_engine/transforms/editor.py``
through the same harness upstream uses — a fake ``SW.canvas`` whose
``push_undo_command`` feeds a real ``QUndoStack`` — so every selected-item
operation lands atomically in the text undo stack.

Local scope decisions (node G):

* ``controls`` is optional; ``None`` exercises every guarded panel path, and a
  signal-recording mock verifies the wiring the future panel relies on.
* Multi-selection structure edits (add/move/remove) are covered without any
  widget, mirroring upstream ``test_stack_structure_edits_are_undoable_for_selected_items``.
* Grid and projective modal edit sessions (begin/preview/commit/cancel) are
  covered at the session boundary; the scene controls themselves are node I.

Run under an offscreen QApplication (mandatory — the document layout machinery
hard-crashes otherwise, see stage-4 node D doc).
"""

import os
import os.path as osp
import sys
import unittest
from types import SimpleNamespace

APP_ROOT = osp.dirname(osp.dirname(osp.abspath(__file__)))
sys.path.insert(0, APP_ROOT)
os.chdir(APP_ROOT)
os.environ["QT_API"] = "pyqt6"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from utils.fontformat import (  # noqa: E402
    BendTextTransform,
    FontFormat,
    GridTextTransform,
    ProjectiveTextTransform,
    SineTextTransform,
    TextTransformStack,
    TextTransformState,
)
from qtpy.QtCore import QPointF, Qt  # noqa: E402
from ui import shared_widget as SW  # noqa: E402
from ui.text_engine.transforms.editor import (  # noqa: E402
    GLYPH_SLANT_INDEX,
    TextTransformEditSession,
)
from utils.textblock import TextBlock  # noqa: E402


TEST_LINES = (
    "Без труда не вытащишь и рыбку из пруда.",
    "冰冻三尺，非一日之寒。",
    "猿も木から落ちる。",
    "Don't judge a book by its cover.",
    "벼는 익을수록 고개를 숙인다.",
)


def transform_state(*transforms, glyph_slant_angle=0.0):
    return TextTransformState(
        TextTransformStack(tuple(transforms)),
        glyph_slant_angle,
    )


NEUTRAL = transform_state()


class TextTransformEditSessionTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtWidgets import QApplication, QGraphicsScene, QGraphicsView

        from ui.textitem import TextBlkItem

        cls.TextBlkItem = TextBlkItem
        cls.QGraphicsScene = QGraphicsScene
        cls.QGraphicsView = QGraphicsView
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        previous_canvas = getattr(SW, 'canvas', None)
        self.addCleanup(setattr, SW, 'canvas', previous_canvas)

    def _make_item(self, index, text, vertical, xyxy=(0, 0, 600, 300)):
        block = TextBlock(list(xyxy))
        block._bounding_rect = list(xyxy)
        block.vertical = vertical
        block.translation = text
        return self.TextBlkItem(block, index)

    @staticmethod
    def _current_state(item):
        return TextTransformState(
            item.blk.fontformat.text_transform,
            item.blk.fontformat.glyph_slant_angle,
        )

    def _make_stack_canvas(self, stack):
        SW.canvas = SimpleNamespace(push_undo_command=stack.push)
        return SW.canvas

    @staticmethod
    def _make_controls_mock():
        return SimpleNamespace(
            transform_commit_requested=SimpleNamespace(connect=lambda *_: None),
            transform_preview_requested=SimpleNamespace(connect=lambda *_: None),
            transform_drag_commit_requested=SimpleNamespace(
                connect=lambda *_: None
            ),
            transform_preview_canceled=SimpleNamespace(connect=lambda *_: None),
            transform_add_requested=SimpleNamespace(connect=lambda *_: None),
            transform_remove_requested=SimpleNamespace(connect=lambda *_: None),
            transform_move_requested=SimpleNamespace(connect=lambda *_: None),
            transform_selected=SimpleNamespace(connect=lambda *_: None),
        )


class TextTransformEditSessionTest(TextTransformEditSessionTestBase):
    def test_add_projective_preview_commit_undo_redo_roundtrip(self):
        """Node G acceptance: 1 item add(projective) → preview → commit."""
        from qtpy.QtGui import QUndoStack

        item = self._make_item(0, TEST_LINES[0], False)
        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=None)
        session.replace_targets([item])

        # add(projective): lands in the undo stack as one atomic command.
        session.add_transform('projective')
        self.assertEqual(session.selected_index, 0)
        self.assertEqual(stack.count(), 1)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )

        # preview: parameter delta applies to the live preview only.
        session.preview_delta(0, 'rotation_z', 15.0)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),  # model untouched
        )
        self.assertEqual(
            item._effective_text_transform(),
            transform_state(ProjectiveTextTransform(rotation_z=15.0)),
        )

        # commit: one more undoable command closes the drag.
        session.commit_drag(0, 'rotation_z', 15.0)
        self.assertEqual(stack.count(), 2)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform(rotation_z=15.0)),
        )

        # undo/redo roundtrip restores model and preview state.
        stack.undo()
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )
        self.assertEqual(item._effective_text_transform(), self._current_state(item))
        stack.undo()
        self.assertEqual(self._current_state(item), NEUTRAL)
        stack.redo()
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )
        stack.redo()
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform(rotation_z=15.0)),
        )
        item.geometry_controller.release_render_resources()

    def test_empty_stack_returns_to_neutral(self):
        """Node G acceptance: empty stack → is_neutral() True."""
        from qtpy.QtGui import QUndoStack

        item = self._make_item(0, TEST_LINES[0], False)
        self.assertTrue(item._text_transform_is_neutral())

        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=None)
        session.replace_targets([item])
        session.add_transform('projective')
        session.commit_value(0, 'rotation_z', 15.0)
        self.assertFalse(item._text_transform_is_neutral())
        session.remove_transform(0)
        self.assertEqual(self._current_state(item), NEUTRAL)
        self.assertTrue(item._text_transform_is_neutral())
        item.geometry_controller.release_render_resources()

    def test_structure_edits_are_undoable_for_selected_items(self):
        """Multi-selection add/move/remove land as one undo command each."""
        from qtpy.QtGui import QUndoStack

        versions = (
            transform_state(ProjectiveTextTransform(1.15, 0.85, 11.0)),
            transform_state(ProjectiveTextTransform(0.75, 1.25, -7.0)),
        )
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                items = [
                    self._make_item(index, TEST_LINES[index], vertical)
                    for index in range(2)
                ]
                for item, transform in zip(items, versions):
                    item.set_text_transform(transform)

                stack = QUndoStack()
                self._make_stack_canvas(stack)
                session = TextTransformEditSession(controls=None)
                session.replace_targets(items)

                session.add_transform('bend')
                self.assertEqual(
                    [
                        tuple(item.blk.fontformat.text_transform)
                        for item in items
                    ],
                    [
                        (versions[0].stack[0], BendTextTransform()),
                        (versions[1].stack[0], BendTextTransform()),
                    ],
                )
                session.add_transform('projective')
                self.assertEqual(
                    [len(item.blk.fontformat.text_transform) for item in items],
                    [3, 3],
                )
                session.move_transform(2, -1)
                self.assertEqual(
                    [
                        tuple(
                            transform.transform_type
                            for transform in item.blk.fontformat.text_transform
                        )
                        for item in items
                    ],
                    [('projective', 'projective', 'bend')] * 2,
                )
                session.remove_transform(2)
                self.assertEqual(
                    [len(item.blk.fontformat.text_transform) for item in items],
                    [2, 2],
                )
                stack.undo()
                self.assertEqual(
                    [len(item.blk.fontformat.text_transform) for item in items],
                    [3, 3],
                )
                stack.undo()
                self.assertEqual(
                    [
                        tuple(
                            transform.transform_type
                            for transform in item.blk.fontformat.text_transform
                        )
                        for item in items
                    ],
                    [('projective', 'bend', 'projective')] * 2,
                )
                stack.undo()
                stack.undo()
                self.assertEqual(
                    [self._current_state(item) for item in items],
                    list(versions),
                )
                for item in items:
                    item.geometry_controller.release_render_resources()

    def test_controls_none_guards_every_interaction(self):
        """All panel interactions no-op cleanly without a real panel."""
        from qtpy.QtGui import QUndoStack

        item = self._make_item(0, TEST_LINES[0], False)
        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=None)
        session.replace_targets([item])

        # Typed-value commit with a live drag preview in flight.
        session.add_transform('projective')
        session.preview_delta(0, 'horizontal_scale', 0.5)
        session.commit_value(0, 'horizontal_scale', 1.4)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform(horizontal_scale=1.4)),
        )
        session.cancel_preview()

        # Grid + projective modal sessions on a stack that supports both.
        session.add_transform('grid')
        session.begin_grid_edit(1)
        session.preview_grid_points(1, [(0, 0), (100, 10), (200, 0), (300, 5)])
        session.cancel_grid_edit(1)
        session.begin_grid_edit(1)
        session.preview_grid_points(1, [(0, 0), (100, 10), (200, 0), (300, 5)])
        session.commit_grid_points(1, [(0, 0), (100, 10), (200, 0), (300, 5)])
        self.assertEqual(stack.count(), 4)

        session.begin_projective_edit(0)
        session.preview_projective_transform(
            0, ProjectiveTextTransform(rotation_z=9.0)
        )
        session.cancel_projective_edit(0)
        session.begin_projective_edit(0)
        session.preview_projective_transform(
            0, ProjectiveTextTransform(rotation_z=9.0)
        )
        session.commit_projective_transform(
            0, ProjectiveTextTransform(rotation_z=9.0)
        )
        self.assertEqual(stack.count(), 5)

        # Scene-change / save resolution paths are safe with no controls.
        session.resolve_for_save()
        session.resolve_for_history_change()
        session.cancel_for_scene_change()
        self.assertEqual(session.items, [])
        item.geometry_controller.release_render_resources()

    def test_controls_mock_receives_signal_wiring(self):
        """Signal wiring is established when a panel is attached."""
        from qtpy.QtGui import QUndoStack

        connections = {}
        controls = self._make_controls_mock()
        for name in (
            'transform_commit_requested',
            'transform_preview_requested',
            'transform_drag_commit_requested',
            'transform_preview_canceled',
            'transform_add_requested',
            'transform_remove_requested',
            'transform_move_requested',
            'transform_selected',
        ):
            def _record(callback, _name=name, **_kwargs):
                connections.setdefault(_name, []).append(callback)

            controls.__dict__[name].connect = _record

        item = self._make_item(0, TEST_LINES[0], False)
        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=controls)
        self.assertEqual(len(connections), 8)

        # The panel's emit proxies must reach the session handlers.
        for name, callbacks in connections.items():
            self.assertTrue(callbacks)
            for callback in callbacks:
                self.assertTrue(callable(callback))
        self.assertEqual(
            connections['transform_add_requested'][0].__self__.__class__,
            TextTransformEditSession,
        )
        item.geometry_controller.release_render_resources()

    def test_projective_modal_edit_session_cancels_and_commits(self):
        """begin/preview/commit keeps one undo boundary and preview off-state."""
        from qtpy.QtGui import QUndoStack

        item = self._make_item(0, TEST_LINES[0], False)
        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=None)
        session.replace_targets([item])
        session.add_transform('projective')

        # Cancel path: preview never reaches the model or the undo stack.
        session.begin_projective_edit(0)
        session.preview_projective_transform(
            0, ProjectiveTextTransform(rotation_y=30.0, perspective=0.6)
        )
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )
        session.cancel_projective_edit(0)
        self.assertEqual(stack.count(), 1)
        self.assertEqual(
            item._effective_text_transform(),
            transform_state(ProjectiveTextTransform()),
        )

        # Commit path: one undoable state replaces the preview.
        session.begin_projective_edit(0)
        session.preview_projective_transform(
            0, ProjectiveTextTransform(rotation_y=30.0, perspective=0.6)
        )
        session.commit_projective_transform(
            0, ProjectiveTextTransform(rotation_y=30.0, perspective=0.6)
        )
        self.assertEqual(stack.count(), 2)
        self.assertEqual(
            self._current_state(item),
            transform_state(
                ProjectiveTextTransform(rotation_y=30.0, perspective=0.6)
            ),
        )
        stack.undo()
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )
        item.geometry_controller.release_render_resources()

    def test_grid_edit_session_and_glyph_slant_value_paths(self):
        """Grid session previews by control points; glyph slant is index -1."""
        from qtpy.QtGui import QUndoStack

        item = self._make_item(0, TEST_LINES[0], False)
        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=None)
        session.replace_targets([item])
        session.add_transform('grid')
        self.assertEqual(session.selected_index, 0)

        # Grid modal path: preview then commit (1x1 grid has 4 handles).
        session.begin_grid_edit(0)
        session.preview_grid_points(0, [(0, 0), (100, 10), (200, 0), (300, 5)])
        self.assertEqual(
            self._current_state(item),
            transform_state(GridTextTransform().normalized()),
        )
        session.commit_grid_points(0, [(0, 0), (100, 10), (200, 0), (300, 5)])
        self.assertEqual(stack.count(), 2)
        self.assertIsInstance(
            item.blk.fontformat.text_transform[0], GridTextTransform
        )
        self.assertEqual(
            tuple(item.blk.fontformat.text_transform[0].control_points[1]),
            (100.0, 10.0),
        )

        # Glyph slant is a state-level value addressed by the sentinel index.
        session.commit_value(GLYPH_SLANT_INDEX, 'glyph_slant_angle', 12.0)
        self.assertEqual(
            item.blk.fontformat.glyph_slant_angle, 12.0
        )
        session.preview_delta(GLYPH_SLANT_INDEX, 'glyph_slant_angle', -2.0)
        self.assertEqual(item.blk.fontformat.glyph_slant_angle, 12.0)
        session.commit_drag(GLYPH_SLANT_INDEX, 'glyph_slant_angle', -2.0)
        self.assertEqual(
            item.blk.fontformat.glyph_slant_angle, 10.0
        )
        stack.undo()
        self.assertEqual(item.blk.fontformat.glyph_slant_angle, 12.0)
        item.geometry_controller.release_render_resources()

    def test_mixed_stack_structures_only_allow_append(self):
        """Mixed-shape selection: value edits blocked, appends still allowed."""
        from qtpy.QtGui import QUndoStack

        items = [
            self._make_item(0, TEST_LINES[0], False),
            self._make_item(1, TEST_LINES[1], False),
        ]
        initial = (
            transform_state(ProjectiveTextTransform(1.2, 1.0, 5.0)),
            transform_state(BendTextTransform(0.4)),
        )
        for item, state in zip(items, initial):
            item.set_text_transform(state)

        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=None)
        session.replace_targets(items)

        # A value edit needs a common stack shape across the selection.
        session.commit_value(0, 'horizontal_scale', 1.5)
        self.assertEqual(stack.count(), 0)
        self.assertEqual(
            [self._current_state(item) for item in items],
            list(initial),
        )

        # Appending is always allowed; the mixed selection just loses focus.
        session.add_transform('projective')
        self.assertEqual(stack.count(), 1)
        self.assertIsNone(session.selected_index)
        for item in items:
            self.assertEqual(
                item.blk.fontformat.text_transform[-1],
                ProjectiveTextTransform(),
            )
        stack.undo()
        self.assertEqual(
            [self._current_state(item) for item in items],
            list(initial),
        )
        for item in items:
            item.geometry_controller.release_render_resources()

    def test_global_format_path_commits_without_items(self):
        """No-items selection edits the panel's global format directly."""
        from qtpy.QtGui import QUndoStack

        stack = QUndoStack()
        self._make_stack_canvas(stack)
        session = TextTransformEditSession(controls=None)
        font_format = FontFormat()
        session.global_format = font_format

        session.add_transform('bend')
        self.assertEqual(len(font_format.text_transform), 1)
        self.assertEqual(stack.count(), 0)  # nothing selectable → no undo

        session.commit_value(0, 'bend', 0.3)
        self.assertEqual(font_format.text_transform[0].bend, 0.3)
        session.commit_value(GLYPH_SLANT_INDEX, 'glyph_slant_angle', 5.0)
        self.assertEqual(font_format.glyph_slant_angle, 5.0)


class TextTransformPanelTest(TextTransformEditSessionTestBase):
    """Node H: the self-developed panel drives the session through its 8
    signals; the session lands every committed edit in the undo stack."""

    def _make_panel(self):
        from ui.text_engine.transforms.panel import TextTransformPanel
        from utils import shared

        previous = getattr(shared, 'register_view_widget', None)
        shared.register_view_widget = lambda *_args: None
        self.addCleanup(
            lambda: (
                delattr(shared, 'register_view_widget')
                if previous is None
                else setattr(shared, 'register_view_widget', previous)
            )
        )
        panel = TextTransformPanel(
            'Text Transform', 'test_transform', 'test_transform_expand',
        )
        self.addCleanup(panel.deleteLater)
        return panel

    def test_add_menu_lists_registry_variants_and_emits_add(self):
        panel = self._make_panel()
        self.assertEqual(panel.add_transform_button.text(), 'Add')
        self.assertEqual(
            [action.text() for action in panel.add_transform_button.menu().actions()],
            ['Scale / Slant / 3D', 'Bend', 'Sine Wave', 'Grid'],
        )
        added = []
        panel.transform_add_requested.connect(added.append)
        panel.add_transform_button.menu().actions()[0].trigger()
        self.assertEqual(added, ['projective'])

    def test_panel_shows_cards_and_mixed_label(self):
        panel = self._make_panel()
        item = self._make_item(0, TEST_LINES[0], False)
        item.set_text_transform(
            transform_state(
                ProjectiveTextTransform(1.1, 1.0, 4.0),
                BendTextTransform(0.4),
            )
        )
        panel.set_transform_items([item])
        self.assertEqual(len(panel.transform_panels), 2)
        self.assertEqual(
            [panel.transform_panels[i].controls for i in range(2)] and
            panel.transform_panels[0].title_label.text(),
            'Scale / Slant / 3D',
        )
        self.assertEqual(
            panel.transform_panels[1].title_label.text(), 'Bend'
        )
        self.assertTrue(panel.transform_mixed_label.isHidden())

        mixed = self._make_item(1, TEST_LINES[1], False)
        mixed.set_text_transform(
            transform_state(
                BendTextTransform(0.4),
                ProjectiveTextTransform(),
            )
        )
        panel.set_transform_items([item, mixed])
        self.assertEqual(panel.transform_panels, [])
        self.assertFalse(panel.transform_mixed_label.isHidden())

    def test_card_columns_adapt_to_panel_width(self):
        """TransformParameterPanel reflows controls as its width changes."""
        panel = self._make_panel()
        panel.set_transform(
            transform_state(
                SineTextTransform(),
            )
        )
        sine_panel = panel.transform_panels[0]
        panel.show()

        # At a narrow width the sine-wave sections should fall back to a
        # single column.
        panel.resize(260, panel.sizeHint().height())
        panel._sync_content_height()
        self.app.processEvents()
        self.assertTrue(
            all(
                data['column_count'] == 1
                for data in sine_panel._section_controls_data
            ),
            'narrow panel should use single column layout',
        )

        # At a wider width the multi-control sections should use two columns.
        panel.resize(520, panel.sizeHint().height())
        panel._sync_content_height()
        self.app.processEvents()
        column_counts = [
            data['column_count']
            for data in sine_panel._section_controls_data
        ]
        self.assertTrue(
            all(
                data['column_count'] == 2
                for data in sine_panel._section_controls_data
                if len(data['controls']) > 1
            ),
            f'wide panel should use two-column layout, got {column_counts}',
        )

    def test_cards_select_on_card_click_and_parameter_interaction(self):
        from qtpy.QtTest import QTest

        panel = self._make_panel()
        panel.set_transform(
            transform_state(
                GridTextTransform().normalized(),
                ProjectiveTextTransform(),
            )
        )
        selected = []
        panel.transform_selected.connect(selected.append)

        QTest.mouseClick(
            panel.transform_panels[1], Qt.MouseButton.LeftButton
        )
        self.assertTrue(panel.transform_panels[1].property('selected'))
        self.assertFalse(panel.transform_panels[0].property('selected'))

        QTest.mouseClick(
            panel.transform_panels[1], Qt.MouseButton.LeftButton
        )
        self.assertFalse(panel.transform_panels[1].property('selected'))

        control = panel.transform_panels[0].controls[
            'horizontal_divisions'
        ]
        control.editor.setText('2')
        control.editor.textEdited.emit('2')
        self.assertTrue(panel.transform_panels[0].property('selected'))
        self.assertEqual(selected, [1, -1, 0])

    def test_value_drag_previews_and_commits_through_session(self):
        """Node H acceptance: panel drag → preview → commit → undo roundtrip."""
        from qtpy.QtGui import QUndoStack

        panel = self._make_panel()
        item = self._make_item(0, TEST_LINES[0], False)
        stack = QUndoStack()
        SW.canvas = SimpleNamespace(push_undo_command=stack.push)
        session = TextTransformEditSession(panel)
        session.replace_targets([item])

        # Add a projective transform through the panel's add menu.
        panel.add_transform_button.menu().actions()[0].trigger()
        self.assertEqual(stack.count(), 1)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )

        # Drag the rotation_z label: previews stay out of the undo stack.
        control = panel.transform_panels[0].controls['rotation_z']
        control._start_drag()
        control._move_drag(15)
        self.assertEqual(stack.count(), 1)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),  # model untouched
        )
        self.assertEqual(
            item._effective_text_transform(),
            transform_state(ProjectiveTextTransform(rotation_z=15.0)),
        )
        control._finish_drag()
        self.assertEqual(stack.count(), 2)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform(rotation_z=15.0)),
        )

        stack.undo()
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )
        stack.redo()
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform(rotation_z=15.0)),
        )
        item.geometry_controller.release_render_resources()

    def test_typed_value_commits_and_escape_cancels(self):
        from qtpy.QtGui import QUndoStack
        from qtpy.QtTest import QTest

        panel = self._make_panel()
        item = self._make_item(0, TEST_LINES[0], False)
        stack = QUndoStack()
        SW.canvas = SimpleNamespace(push_undo_command=stack.push)
        session = TextTransformEditSession(panel)
        session.replace_targets([item])
        panel.add_transform_button.menu().actions()[0].trigger()

        control = panel.transform_panels[0].controls['rotation_z']
        control.editor.setText('33.0')
        control.editor.textEdited.emit('33.0')
        self.assertEqual(control.state, control.PENDING_TEXT)

        # Escape cancels the typed edit and restores the model value.
        QTest.keyClick(control.editor, Qt.Key.Key_Escape)
        self.assertEqual(control.state, control.IDLE)
        self.assertEqual(control.editor.text(), '0.0°')
        self.assertEqual(stack.count(), 1)

        # Enter commits the typed value into the undo stack.
        control.editor.setText('33.0')
        control.editor.textEdited.emit('33.0')
        control.commit_pending()
        self.assertEqual(stack.count(), 2)
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform(rotation_z=33.0)),
        )
        stack.undo()
        self.assertEqual(
            self._current_state(item),
            transform_state(ProjectiveTextTransform()),
        )
        item.geometry_controller.release_render_resources()

    def test_remove_and_move_buttons_emit_signals(self):
        panel = self._make_panel()
        panel.set_transform(
            transform_state(
                ProjectiveTextTransform(),
                BendTextTransform(0.3),
            )
        )
        operation_panel = panel.transform_panels[0]
        removed = []
        moved = []
        panel.transform_remove_requested.connect(
            lambda *args: removed.append(args)
        )
        panel.transform_move_requested.connect(
            lambda *args: moved.append(args)
        )
        self.assertFalse(operation_panel.move_up_button.isEnabled())
        self.assertTrue(operation_panel.move_down_button.isEnabled())
        operation_panel.move_down_button.click()
        operation_panel.close_button.click()
        self.assertEqual(moved, [(0, 1)])
        self.assertEqual(removed, [(0,)])

    def test_integer_editor_steps_and_clamps_at_range_end(self):
        from ui.text_engine.transforms.panel import CommittedTransformControl

        integer = CommittedTransformControl(
            'Segments', 'frequency_x', 1.0, 0.0, 64.0, '', 0.125,
            decimals=0,
        )
        self.addCleanup(integer.deleteLater)
        integer.set_model_value(2)
        integer_steps = []
        integer.drag_commit_requested.connect(
            lambda _name, delta: integer_steps.append(delta)
        )
        up_rect, _down_rect = integer.editor._button_rects()
        integer._step_integer(1)
        self.assertEqual(integer_steps, [1.0])
        integer.set_model_value(64)
        integer._step_integer(1)
        self.assertEqual(integer_steps, [1.0])


class TextBlkShapeControlTest(TextTransformEditSessionTestBase):
    """Node I: the shape control follows the visual outline; corner drags
    map scene→source and keep the existing ReshapeItemCommand undo."""

    def _make_view_scene(self):
        from qtpy.QtWidgets import QGraphicsScene, QGraphicsView

        scene = self.QGraphicsScene()
        view = self.QGraphicsView(scene)
        view.resize(800, 600)
        return scene, view

    def _make_control(self):
        from ui.texteditshapecontrol import TextBlkShapeControl

        scene, view = self._make_view_scene()
        control = TextBlkShapeControl(view)
        scene.addItem(control)
        self.addCleanup(scene.removeItem, control)
        self.addCleanup(view.close)
        return scene, view, control

    def test_neutral_item_control_matches_plain_rect(self):
        """No transform → handles sit on the source rect (zero regression)."""
        scene, _view, control = self._make_control()
        item = self._make_item(0, TEST_LINES[0], False)
        scene.addItem(item)
        control.setBlkItem(item)
        self.assertTrue(control.isVisible())

        self.assertTrue(item._text_transform_is_neutral())
        handle_points = control._true_handle_scene_points
        self.assertEqual(len(handle_points), 8)
        corners = handle_points[::2]
        edges = handle_points[1::2]
        rect = item.logical_unpadded_rect()
        expected_corners = [
            item.mapToScene(rect.topLeft()),
            item.mapToScene(rect.topRight()),
            item.mapToScene(rect.bottomRight()),
            item.mapToScene(rect.bottomLeft()),
        ]
        for actual, expected in zip(corners, expected_corners):
            self.assertAlmostEqual(actual.x(), expected.x(), places=4)
            self.assertAlmostEqual(actual.y(), expected.y(), places=4)
        self.assertEqual(
            [len(point) for point in (corners, edges)], [4, 4]
        )
        # The control's dashed frame is the plain rect (path empty in
        # non-transform mode is not required; bounds must at least cover it).
        self.assertTrue(control.boundingRect().contains(rect))
        scene.removeItem(item)

    def test_control_follows_visual_outline_under_projective(self):
        scene, _view, control = self._make_control()
        item = self._make_item(0, TEST_LINES[0], False)
        item.set_text_transform(
            transform_state(ProjectiveTextTransform(
                horizontal_scale=1.3, rotation_z=25.0, perspective=0.5
            ))
        )
        scene.addItem(item)
        control.setBlkItem(item)

        handle_points = control._true_handle_scene_points
        expected = item.geometry_controller.visual_handle_points_in_scene()
        self.assertEqual(len(handle_points), 8)
        for actual, want in zip(handle_points, expected):
            self.assertAlmostEqual(actual.x(), want.x(), places=4)
            self.assertAlmostEqual(actual.y(), want.y(), places=4)

        # The visual outline differs from the plain source rect after a
        # projective transform, so the frame is not the untransformed box.
        visual_bounds = item.geometry_controller.visual_bounds_in_scene()
        rect = item.geometry_controller.logical_rect()
        self.assertNotAlmostEqual(
            visual_bounds.height(), rect.height(), places=1
        )
        scene.removeItem(item)

    def test_control_follows_visual_outline_under_grid(self):
        scene, _view, control = self._make_control()
        item = self._make_item(0, TEST_LINES[0], False)
        # A deformed (non-neutral) grid exercises the surface-mapper path.
        grid = GridTextTransform(
            horizontal_divisions=2, vertical_divisions=2
        ).normalized()
        points = list(grid.control_points)
        points[3] = (points[3][0] + 0.2, points[3][1] + 0.1)
        grid = grid.with_control_points(points)
        item.set_text_transform(transform_state(grid))
        scene.addItem(item)
        control.setBlkItem(item)

        handle_points = control._true_handle_scene_points
        expected = item.geometry_controller.visual_handle_points_in_scene()
        self.assertEqual(len(handle_points), 8)
        for actual, want in zip(handle_points, expected):
            self.assertAlmostEqual(actual.x(), want.x(), places=4)
            self.assertAlmostEqual(actual.y(), want.y(), places=4)
        self.assertTrue(
            item.geometry_controller.compiled.needs_local_handle_frames
        )
        # Tangent-aligned frames are used for curve transforms.
        outward, angles = control._handle_frames_device(
            handle_points,
            item.geometry_controller.visual_handle_tangents_in_scene(),
        )
        self.assertEqual(len(outward), 8)
        self.assertEqual(len(angles), 8)
        scene.removeItem(item)

    def test_corner_drag_previews_source_rect_and_undo_roundtrip(self):
        """Node I acceptance: corner drag → source rect preview → undo."""
        from qtpy.QtGui import QUndoStack
        from ui.textedit_commands import ReshapeItemCommand

        scene, _view, control = self._make_control()
        item = self._make_item(0, TEST_LINES[0], False, xyxy=(0, 0, 600, 300))
        item.set_text_transform(
            transform_state(ProjectiveTextTransform(rotation_z=15.0))
        )
        scene.addItem(item)
        control.setBlkItem(item)

        before_abs = item.absBoundingRect(qrect=True)
        # The resize invariants live on the *visual* handles (the source
        # rect's raw edges shift to compensate for the pivot-centered
        # transform matrix), so capture the visual corners.
        visual_0 = control._item_handle_points_in_scene(item)[0]
        visual_4 = control._item_handle_points_in_scene(item)[4]
        item.startReshape()
        # beginResize with the visual handle as the press position (matches
        # real mouse input, keeping the handle-proxy a no-op).
        control.beginResize(0, visual_0)
        # Drag corner 0 40px left / 30px up in scene space.
        target_scene = visual_0 + QPointF(-40, -30)
        control.resizeFromScene(0, target_scene)
        moved_0 = control._item_handle_points_in_scene(item)[0]
        self.assertLess(moved_0.x(), visual_0.x() - 20)
        self.assertLess(moved_0.y(), visual_0.y() - 20)
        # The opposite visual handle stays anchored exactly.
        self.assertAlmostEqual(
            control._item_handle_points_in_scene(item)[4].x(),
            visual_4.x(), places=2,
        )
        self.assertAlmostEqual(
            control._item_handle_points_in_scene(item)[4].y(),
            visual_4.y(), places=2,
        )

        # Existing undo semantics: ReshapeItemCommand roundtrips the rect.
        after_abs = item.absBoundingRect(qrect=True)
        stack = QUndoStack()
        stack.push(ReshapeItemCommand(item))
        item.endReshape()
        stack.undo()
        restored = item.absBoundingRect(qrect=True)
        self.assertAlmostEqual(restored.left(), before_abs.left(), places=2)
        self.assertAlmostEqual(restored.top(), before_abs.top(), places=2)
        self.assertAlmostEqual(restored.width(), before_abs.width(), places=2)
        stack.redo()
        self.assertAlmostEqual(
            item.absBoundingRect(qrect=True).left(), after_abs.left(), places=2
        )
        control.setBlkItem(None)
        scene.removeItem(item)

    def test_rotation_pivots_on_visual_center(self):
        from qtpy.QtGui import QUndoStack
        from ui.textedit_commands import RotateItemCommand

        scene, _view, control = self._make_control()
        item = self._make_item(0, TEST_LINES[0], False, xyxy=(0, 0, 600, 300))
        scene.addItem(item)
        control.setBlkItem(item)
        center = control.visualCenterInScene()
        self.assertAlmostEqual(
            center.x(), item.geometry_controller.logical_rect().center().x(), places=4
        )

        original_angle = item.rotation()
        # idx=None exercises the pivot math without the handle-proxy snap
        # (the proxy redirects presses to the handle position, which is
        # correct for real mouse input but obscures the angle arithmetic).
        start, original = control.beginRotation(center + QPointF(100, 0))
        self.assertEqual(original, original_angle)
        # Drag the pointer 90° clockwise around the visual center.
        preview = control.rotateFromScene(center + QPointF(0, 100), start)
        self.assertAlmostEqual(preview, original_angle + 90.0, places=4)
        # finishRotationPreview restores the model angle as the command owner.
        final = control.finishRotationPreview(original)
        self.assertAlmostEqual(final, original_angle + 90.0, places=4)
        self.assertEqual(item.rotation(), original_angle)

        stack = QUndoStack()
        stack.push(RotateItemCommand(item, final, control))
        stack.redo()
        self.assertEqual(item.rotation(), final)
        stack.undo()
        self.assertEqual(item.rotation(), original_angle)
        control.setBlkItem(None)
        scene.removeItem(item)


class LocalFeaturesCoexistenceTest(TextTransformEditSessionTestBase):
    """Node J: re-hooked local features (seq badge / alignment snap /
    overflow clip / NormalizeBreaks) coexist with the transform system."""

    def _render_scene(self, scene):
        import numpy as np

        from qtpy.QtGui import QColor, QImage, QPainter

        image = QImage(400, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        scene.render(painter)
        painter.end()
        array = np.frombuffer(
            image.constBits().asarray(image.sizeInBytes()), dtype=np.uint8
        ).reshape(200, 400, 4)
        return int((array[:, :, 3] > 0).sum())

    @staticmethod
    def _deformed_grid():
        grid = GridTextTransform(
            horizontal_divisions=2, vertical_divisions=2
        ).normalized()
        points = list(grid.control_points)
        points[3] = (points[3][0] + 0.2, points[3][1] + 0.1)
        return grid.with_control_points(points)

    def test_overflow_clip_works_across_transform_paths(self):
        """overflow_mode clips boundary-crossing text on every paint path."""
        from qtpy.QtWidgets import QGraphicsRectItem

        from utils.config import pcfg

        class FakeScene(self.QGraphicsScene):
            scale_factor = 1.0

        def make_scene_item(transform):
            scene = FakeScene()
            base = QGraphicsRectItem(0, 0, 400, 200)
            scene.addItem(base)
            scene.baseLayer = base
            block = TextBlock([300, 50, 300, 80])
            block._bounding_rect = [300, 50, 300, 80]
            block.translation = "LOVE THIS TEXT LONG"
            item = self.TextBlkItem(block, 0)
            if transform is not None:
                item.set_text_transform(transform_state(transform))
            scene.addItem(item)
            return scene, item

        cases = (
            ('neutral', None),
            ('projective', ProjectiveTextTransform(rotation_z=10.0)),
            ('grid', self._deformed_grid()),
        )
        previous = pcfg.overflow_mode
        self.addCleanup(setattr, pcfg, 'overflow_mode', previous)
        for name, transform in cases:
            with self.subTest(transform=name):
                pcfg.overflow_mode = False
                scene, item = make_scene_item(transform)
                unclipped = self._render_scene(scene)
                scene.removeItem(item)
                item.geometry_controller.release_render_resources()

                pcfg.overflow_mode = True
                scene, item = make_scene_item(transform)
                clipped = self._render_scene(scene)
                scene.removeItem(item)
                item.geometry_controller.release_render_resources()

                self.assertLess(
                    clipped, unclipped,
                    f'{name}: overflow clip hid no boundary-crossing pixels',
                )

    def test_seq_badge_renders_with_transform(self):
        """Seq badge draws through the transformed paint path without error."""
        from qtpy.QtWidgets import QGraphicsRectItem

        from utils.config import pcfg

        class FakeScene(self.QGraphicsScene):
            scale_factor = 1.0

        def make_scene(show_badge):
            scene = FakeScene()
            base = QGraphicsRectItem(0, 0, 400, 200)
            scene.addItem(base)
            block = TextBlock([30, 40, 300, 80])
            block._bounding_rect = [30, 40, 300, 80]
            block.translation = "BADGE"
            item = self.TextBlkItem(block, 0)
            item.set_text_transform(
                transform_state(ProjectiveTextTransform(rotation_z=8.0))
            )
            scene.addItem(item)
            item.setSelected(True)
            return scene, item

        previous = pcfg.show_seq_badge
        self.addCleanup(setattr, pcfg, 'show_seq_badge', previous)
        pcfg.show_seq_badge = False
        scene, item = make_scene(False)
        without = self._render_scene(scene)
        scene.removeItem(item)
        item.geometry_controller.release_render_resources()

        pcfg.show_seq_badge = True
        scene, item = make_scene(True)
        with_badge = self._render_scene(scene)
        scene.removeItem(item)
        item.geometry_controller.release_render_resources()
        self.assertNotEqual(with_badge, without)

    def test_alignment_snap_coexists_with_transform(self):
        """_apply_snap uses absBoundingRect and still fires under transforms."""
        from qtpy.QtWidgets import QGraphicsRectItem

        from utils.text_alignment import SNAP_THRESHOLD

        class FakeCanvas(self.QGraphicsScene):
            scale_factor = 1.0
            alignment_enabled = True

            def __init__(self):
                super().__init__()
                self.textLayer = QGraphicsRectItem(0, 0, 800, 400)
                self.addItem(self.textLayer)
                self._guides = None

            def clear_snap_guides(self):
                self._guides = None

            def set_snap_guides(self, guides):
                self._guides = guides

        canvas = FakeCanvas()
        target = self._make_item(0, "TARGET", False, xyxy=(200, 100, 300, 150))
        target.setParentItem(canvas.textLayer)
        # The dragged item starts 2px above the target's top edge (within the
        # snap threshold) and carries a text transform.
        dragged = self._make_item(1, "DRAG", False, xyxy=(200, 98, 300, 148))
        dragged.set_text_transform(
            transform_state(ProjectiveTextTransform(rotation_z=6.0))
        )
        dragged.setParentItem(canvas.textLayer)
        dragged.absBoundingRect()  # warm the geometry path

        my_rect = dragged.absBoundingRect()
        target_rect = target.absBoundingRect()
        assert abs(my_rect[1] - target_rect[1]) < SNAP_THRESHOLD

        dragged._apply_snap()
        self.assertIsNotNone(canvas._guides)
        # The snap adjusted the item position (pad-compensated).
        snapped = dragged.absBoundingRect()
        self.assertAlmostEqual(
            snapped[1], target_rect[1], delta=1.0
        )
        canvas.removeItem(dragged)
        dragged.geometry_controller.release_render_resources()
        canvas.removeItem(target)
        target.geometry_controller.release_render_resources()

    def test_normalize_breaks_preserves_transform(self):
        """The per-item operations NormalizeBreaksCommand applies keep the
        text transform stack intact."""
        item = self._make_item(0, TEST_LINES[0], False)
        item.set_text_transform(
            transform_state(
                ProjectiveTextTransform(rotation_z=12.0),
                BendTextTransform(0.4),
            )
        )
        before = self._current_state(item)
        fontformat = item.get_fontformat()
        item.set_fontformat(fontformat, set_char_format=True)
        item.setPlainTextAndKeepUndoStack(
            "第一行\n第二行"
        )
        self.assertEqual(self._current_state(item), before)
        item.geometry_controller.release_render_resources()


class GridTransformControlTest(TextTransformEditSessionTestBase):
    """Stage 5 follow-up (issue fixes): the canvas Grid overlay shows
    draggable control points that re-split on division changes and drive the
    session once per gesture; the shape-control angle label no longer flashes
    on a plain rotation-zone press."""

    def _make_view_scene(self):
        from qtpy.QtCore import QRectF
        from qtpy.QtWidgets import QGraphicsRectItem

        scene = self.QGraphicsScene()
        view = self.QGraphicsView(scene)
        view.resize(800, 600)
        base = QGraphicsRectItem(QRectF(0, 0, 800, 600))
        scene.addItem(base)
        return scene, view, base

    def _make_grid_item(self, divisions=(2, 2), deform=False):
        from ui.text_engine.transforms.grid_control import (
            TextGridTransformControl,
        )

        scene, view, base = self._make_view_scene()
        item = self._make_item(0, TEST_LINES[0], False)
        grid = GridTextTransform(
            horizontal_divisions=divisions[0],
            vertical_divisions=divisions[1],
        ).normalized()
        item.set_text_transform(transform_state(grid))
        scene.addItem(item)
        item.setParentItem(base)

        control = TextGridTransformControl()
        scene.addItem(control)
        control.setParentItem(base)
        self.addCleanup(scene.removeItem, control)
        self.addCleanup(view.close)
        return scene, item, control

    def test_grid_control_binds_and_shows_handles(self):
        """A neutral 2x2 grid already shows (2+1)*(2+1)=9 draggable handles
        and a non-empty warped mesh."""
        scene, item, control = self._make_grid_item()
        control.bind(
            item, 0,
            begin_edit=lambda *_a: None,
            preview_points=lambda *_a: None,
            commit_points=lambda *_a: None,
            cancel_edit=lambda *_a: None,
        )
        self.assertTrue(control.isVisible())
        self.assertEqual(len(control.handles), 9)
        self.assertFalse(control.path().isEmpty())
        # The mesh path covers the source rect area.
        bounds = control.path().boundingRect()
        rect = item.logical_unpadded_rect()
        self.assertTrue(bounds.contains(rect.center()))
        scene.removeItem(item)

    def test_grid_control_handle_count_follows_divisions(self):
        """Editing the division count in the panel re-splits the handles in
        real time (the stage-5 follow-up requirement)."""
        scene, item, control = self._make_grid_item()
        control.bind(
            item, 0,
            begin_edit=lambda *_a: None,
            preview_points=lambda *_a: None,
            commit_points=lambda *_a: None,
            cancel_edit=lambda *_a: None,
        )
        self.assertEqual(len(control.handles), 9)
        # Simulate the panel commit_value(0, 'horizontal_divisions', 3).
        grid = GridTextTransform(
            horizontal_divisions=3, vertical_divisions=2
        ).normalized()
        item.set_text_transform(transform_state(grid))
        self.assertEqual(len(control.handles), 12)
        self.assertFalse(control.path().isEmpty())
        scene.removeItem(item)

    def test_grid_control_drag_previews_and_commits(self):
        """One handle drag runs begin->preview->commit and lands in the undo
        stack; a zero-move drag cancels instead."""
        scene, item, control = self._make_grid_item()
        calls = {"begin": 0, "preview": 0, "commit": 0, "cancel": 0}
        points_seen = []

        def preview_points(index, points):
            calls["preview"] += 1
            points_seen.append(tuple(points))

        def commit_points(index, points):
            calls["commit"] += 1
            points_seen.append(tuple(points))

        def begin_edit(index):
            calls["begin"] += 1

        def cancel_edit(index):
            calls["cancel"] += 1

        control.bind(
            item, 0,
            begin_edit=begin_edit,
            preview_points=preview_points,
            commit_points=commit_points,
            cancel_edit=cancel_edit,
        )
        handle = control.handles[4]
        start = handle.scenePos()
        self.assertTrue(
            control.begin_handle_drag(
                4, start, Qt.KeyboardModifier.NoModifier
            )
        )
        self.assertTrue(control.move_handle_drag(start + QPointF(30, 0)))
        self.assertEqual(calls["begin"], 1)
        self.assertEqual(calls["preview"], 1)
        # The preview moved the centre point right by 30/600 normalized units.
        self.assertAlmostEqual(
            points_seen[0][4][0], 0.5 + 30.0 / 600.0, places=6
        )
        self.assertTrue(control.finish_handle_drag())
        self.assertEqual(calls["commit"], 1)
        self.assertEqual(calls["cancel"], 0)
        self.assertEqual(points_seen[-1], points_seen[-2])

        # A zero-move drag cancels without committing.
        calls["commit"] = 0
        calls["cancel"] = 0
        self.assertTrue(
            control.begin_handle_drag(
                0, control.handles[0].scenePos(),
                Qt.KeyboardModifier.NoModifier,
            )
        )
        self.assertTrue(control.finish_handle_drag())
        self.assertEqual(calls["commit"], 0)
        self.assertEqual(calls["cancel"], 1)
        scene.removeItem(item)

    def test_session_dispatches_grid_control_binding(self):
        """select_transform on a Grid card binds the canvas overlay; a
        non-grid selection or detach clears it."""
        try:
            from qtpy.QtWidgets import QUndoStack
        except ImportError:
            from qtpy.QtGui import QUndoStack

        class DispatchCanvas:
            def __init__(self):
                self.stack = QUndoStack()
                self.binds = []
                self.clears = 0

            def push_undo_command(self, command):
                self.stack.push(command)

            def bind_text_grid_control(self, item, stack_index, **kwargs):
                self.binds.append((item, stack_index, sorted(kwargs)))

            def clear_text_transform_controls(self):
                self.clears += 1

        canvas = DispatchCanvas()
        previous = getattr(SW, 'canvas', None)
        SW.canvas = canvas
        self.addCleanup(setattr, SW, 'canvas', previous)

        item = self._make_item(0, TEST_LINES[0], False)
        item.set_text_transform(transform_state(
            GridTextTransform(
                horizontal_divisions=2, vertical_divisions=2
            ).normalized()
        ))
        session = TextTransformEditSession()
        session.replace_targets([item])
        self.assertEqual(canvas.binds, [])
        self.assertEqual(canvas.clears, 1)

        session.select_transform(0)
        self.assertEqual(len(canvas.binds), 1)
        self.assertIs(canvas.binds[0][0], item)
        self.assertEqual(canvas.binds[0][1], 0)
        self.assertEqual(
            canvas.binds[0][2],
            sorted(
                [
                    "begin_edit",
                    "preview_points",
                    "commit_points",
                    "cancel_edit",
                ]
            ),
        )

        # A non-Grid selection clears the overlay.
        session.select_transform(GLYPH_SLANT_INDEX)
        self.assertEqual(canvas.clears, 2)

        # A stack with no Grid at the selected index clears (projective
        # dispatch is not implemented on the canvas, so it falls through).
        item2 = self._make_item(1, TEST_LINES[1], False)
        item2.set_text_transform(transform_state(
            ProjectiveTextTransform(rotation_z=10.0)
        ))
        session.replace_targets([item2])
        session.select_transform(0)
        self.assertEqual(canvas.clears, 4)
        self.assertEqual(len(canvas.binds), 1)

    def test_angle_label_only_shows_on_rotation_move(self):
        """The rotation angle label must not flash on a plain press over a
        handle's rotation zone; it appears only once the drag moves."""
        from ui.texteditshapecontrol import TextBlkShapeControl

        scene, view, base = self._make_view_scene()
        item = self._make_item(0, TEST_LINES[0], False)
        scene.addItem(item)
        control = TextBlkShapeControl(view)
        scene.addItem(control)
        control.setBlkItem(item)
        self.assertTrue(control.angleLabel.isHidden())
        # A rotation start (the old mousePressEvent path) must stay quiet.
        center = item.geometry_controller.visual_rotation_center_in_scene()
        control.beginRotation(center + QPointF(60, 0))
        self.assertTrue(control.angleLabel.isHidden())
        # The move path (mouseMoveEvent -> updateAngleLabelPos) still shows it.
        control.ctrlblock_group[0].updateAngleLabelPos()
        self.assertFalse(control.angleLabel.isHidden())
        control.angleLabel.setVisible(False)
        scene.removeItem(item)



if __name__ == "__main__":
    unittest.main()

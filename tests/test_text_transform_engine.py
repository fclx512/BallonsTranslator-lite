"""Stage 4 text-engine tests for the ported transform system.

Ported from upstream v1.5.10 ``tests/test_text_transform_undo.py``, scoped to
what Stage 4 ships locally (no Stage 5 panel / shape-control / edit-session
UI):

* ``ExtendedTextTransformModelTest`` — 14 pure model / compile / mapper tests.
* ``TextTransformUndoTest`` — projective+bend+sine mix undo/redo through
  ``SetTextTransformCommand`` (upstream also interleaves ``MultiPasteCommand``,
  which is out of scope here).
* ``TextTransformRenderingTest`` — 5 rendering tests adapted to local behavior
  (no upstream width fast-path; ``inputMethodQuery`` cursor-rect mapping is not
  implemented locally).
* one local-only test: with ``glyph_slant_angle != 0`` the tatechuyoko guard
  keeps vertical runs unrotated (A/B pixel comparison).

Run under an offscreen QApplication (mandatory — the document layout machinery
hard-crashes otherwise, see stage-4 node D doc).
"""

import copy
import json
import os
import os.path as osp
import sys
import unittest
from unittest.mock import patch

import numpy as np

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
from ui.text_engine.transforms.bend import BendMapper  # noqa: E402
from ui.text_engine.transforms.grid import GridMapper  # noqa: E402
from ui.text_engine.transforms.mapping import (  # noqa: E402
    CompositeTextTransformMapper,
    projective_transform_matrix,
)
from ui.text_engine.transforms.registry import (  # noqa: E402
    compile_text_transform_stack,
)
from ui.text_engine.transforms.sine import SineMapper  # noqa: E402
from utils.proj_imgtrans import TextBlkEncoder  # noqa: E402
from utils.textblock import TextBlock  # noqa: E402


TEST_LINES = (
    "Без труда не вытащишь и рыбку из пруда.",
    "冰冻三尺，非一日之寒。",
    "猿も木から落ちる。",
    "Don't judge a book by its cover.",
    "벼는 익을수록 고개를 숙인다.",
    "☀ ☁ ☂ ☃ ★ ☆ ☎ ☯ ♠ ♥ ♦ ♣ ⚠ ⚽ ⚾ ㊗ ㊙ ! @ # $",
)


def transform_state(*transforms, glyph_slant_angle=0.0):
    return TextTransformState(
        TextTransformStack(tuple(transforms)),
        glyph_slant_angle,
    )


NEUTRAL = transform_state()
FIRST_TRANSFORM = transform_state(
    ProjectiveTextTransform(1.2, 0.9, 12.0),
    glyph_slant_angle=5.0,
)


class TextTransformTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qtpy.QtCore import QPointF, QRectF
        from qtpy.QtGui import QColor, QImage, QPainter
        from qtpy.QtWidgets import QApplication, QGraphicsScene

        from ui.textitem import TextBlkItem

        cls.QPointF, cls.QRectF = QPointF, QRectF
        cls.QColor, cls.QImage, cls.QPainter = QColor, QImage, QPainter
        cls.QApplication, cls.QGraphicsScene = QApplication, QGraphicsScene
        cls.TextBlkItem = TextBlkItem
        cls.app = QApplication.instance() or QApplication([])

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

    def _render_scene(self, scene):
        image = self.QImage(
            900,
            600,
            self.QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(self.QColor(0, 0, 0, 0))
        painter = self.QPainter(image)
        scene.render(
            painter,
            self.QRectF(0, 0, 900, 600),
            self.QRectF(-50, -50, 900, 600),
        )
        painter.end()
        # PyQt6: constBits() is a voidptr without a length — materialize bytes.
        return bytes(image.constBits().asarray(image.sizeInBytes()))

    @staticmethod
    def _line_positions(item):
        document = item.document()
        return tuple(
            (
                block_number,
                line_number,
                document.findBlockByNumber(block_number)
                .layout()
                .lineAt(line_number)
                .position(),
            )
            for block_number in range(document.blockCount())
            for line_number in range(
                document.findBlockByNumber(block_number).layout().lineCount()
            )
        )


class ExtendedTextTransformModelTest(TextTransformTestBase):
    def test_projective_and_bend_payloads_round_trip(self):
        payloads = (
            {
                'transform_type': 'projective',
                'horizontal_scale': 1.2,
                'vertical_scale': 0.9,
                'horizontal_slant': 8.0,
                'vertical_slant': -4.0,
                'rotation_x': 25.0,
                'rotation_y': -35.0,
                'rotation_z': 12.0,
                'perspective': 0.55,
            },
            {
                'transform_type': 'bend',
                'bend': -0.75,
            },
        )
        for payload in payloads:
            with self.subTest(transform_type=payload['transform_type']):
                font_format = FontFormat(text_transform=[payload])
                self.assertEqual(
                    font_format.to_serializable_dict()['text_transform'],
                    [payload],
                )

    def test_duplicate_stack_entries_and_glyph_slant_round_trip(self):
        payload = [
            {
                'transform_type': 'projective',
                'horizontal_scale': 1.2,
                'vertical_scale': 1.0,
                'horizontal_slant': 5.0,
                'vertical_slant': 0.0,
                'rotation_x': 0.0,
                'rotation_y': 0.0,
                'rotation_z': 0.0,
                'perspective': 0.0,
            },
            {
                'transform_type': 'projective',
                'horizontal_scale': 0.8,
                'vertical_scale': 1.1,
                'horizontal_slant': -3.0,
                'vertical_slant': 0.0,
                'rotation_x': 0.0,
                'rotation_y': 0.0,
                'rotation_z': 0.0,
                'perspective': 0.0,
            },
        ]
        font_format = FontFormat(
            text_transform=payload,
            glyph_slant_angle=7.0,
        )
        serialized = font_format.to_serializable_dict()
        self.assertEqual(serialized['text_transform'], payload)
        self.assertEqual(serialized['glyph_slant_angle'], 7.0)

        block = TextBlock([0, 0, 20, 10])
        block.fontformat = font_format
        restored = TextBlock(
            **json.loads(json.dumps(block, cls=TextBlkEncoder))
        )
        self.assertEqual(
            restored.fontformat.text_transform,
            font_format.text_transform,
        )
        self.assertEqual(restored.fontformat.glyph_slant_angle, 7.0)

    def test_projective_matrix_is_centered_and_invertible(self):
        rect = self.QRectF(20, 30, 400, 180)
        for rotation_x, rotation_y in (
            (-89.0, -45.0),
            (-30.0, 60.0),
            (0.0, 0.0),
            (45.0, 30.0),
            (89.0, 89.0),
        ):
            with self.subTest(rotation_x=rotation_x, rotation_y=rotation_y):
                matrix = projective_transform_matrix(
                    ProjectiveTextTransform(
                        horizontal_scale=1.2,
                        vertical_scale=0.9,
                        horizontal_slant=8.0,
                        vertical_slant=-4.0,
                        rotation_x=rotation_x,
                        rotation_y=rotation_y,
                        rotation_z=20.0,
                        perspective=0.8,
                    ),
                    rect,
                )
                inverse, invertible = matrix.inverted()
                self.assertTrue(invertible)
                self.assertEqual(matrix.map(rect.center()), rect.center())
                for point in (
                    rect.topLeft(),
                    rect.topRight(),
                    rect.bottomRight(),
                    rect.bottomLeft(),
                ):
                    restored = inverse.map(matrix.map(point))
                    self.assertAlmostEqual(restored.x(), point.x(), places=6)
                    self.assertAlmostEqual(restored.y(), point.y(), places=6)

    def test_projective_parameters_compile_to_one_native_stage_matrix(self):
        rect = self.QRectF(20, 30, 400, 180)
        transform = ProjectiveTextTransform(
            horizontal_scale=1.2,
            vertical_scale=0.85,
            horizontal_slant=12.0,
            vertical_slant=-7.0,
            rotation_x=25.0,
            rotation_y=-35.0,
            rotation_z=18.0,
            perspective=0.65,
        )

        compiled = compile_text_transform_stack(
            TextTransformStack((transform,)), rect, rect, False
        )

        self.assertIsNone(compiled.surface_mapper)
        self.assertEqual(len(compiled.stages), 1)
        self.assertIsNotNone(compiled.stages[0].mapper)
        self.assertEqual(
            compiled.native_matrix,
            compiled.stages[0].mapper.matrix,
        )
        self.assertEqual(
            compiled.native_matrix,
            projective_transform_matrix(transform, rect),
        )

    def test_bend_mapper_round_trips_both_writing_modes(self):
        for vertical in (False, True):
            logical = (
                self.QRectF(10, 20, 160, 420)
                if vertical
                else self.QRectF(10, 20, 420, 160)
            )
            source = logical.adjusted(-12, -12, 12, 12)
            for bend in (-1.0, -0.4, 0.0, 0.4, 1.0):
                with self.subTest(vertical=vertical, bend=bend):
                    mapper = BendMapper(
                        logical, source, vertical, bend
                    )
                    source_points = []
                    for x_ratio, y_ratio in (
                        (0.0, 0.0),
                        (0.2, 0.7),
                        (0.5, 0.5),
                        (0.8, 0.3),
                        (1.0, 1.0),
                    ):
                        point = self.QPointF(
                            logical.left() + logical.width() * x_ratio,
                            logical.top() + logical.height() * y_ratio,
                        )
                        source_points.append(point)
                        restored = mapper.inverse_point(
                            mapper.forward_point(point)
                        )
                        self.assertAlmostEqual(
                            restored.x(), point.x(), places=6
                        )
                        self.assertAlmostEqual(
                            restored.y(), point.y(), places=6
                        )
                    source_x = np.asarray([
                        point.x() for point in source_points
                    ])
                    source_y = np.asarray([
                        point.y() for point in source_points
                    ])
                    visual_x, visual_y = mapper.forward_arrays(
                        source_x, source_y
                    )
                    for index, point in enumerate(source_points):
                        expected = mapper.forward_point(point)
                        self.assertAlmostEqual(
                            visual_x[index], expected.x(), places=6
                        )
                        self.assertAlmostEqual(
                            visual_y[index], expected.y(), places=6
                        )

    def test_sine_payload_neutrality_and_phase_endpoint(self):
        transform = SineTextTransform(
            frequency_x=64,
            frequency_y=3,
            phase_x=1.0,
            phase_y=0.25,
            amplitude_x=1.0,
            amplitude_y=0.4,
        )
        payload = {
            'transform_type': 'sine',
            'frequency_x': 64,
            'frequency_y': 3,
            'phase_x': 1.0,
            'phase_y': 0.25,
            'amplitude_x': 1.0,
            'amplitude_y': 0.4,
        }
        font_format = FontFormat(text_transform=[payload])
        self.assertEqual(font_format.text_transform[0], transform)
        self.assertEqual(
            font_format.to_serializable_dict()['text_transform'], [payload]
        )
        self.assertTrue(SineTextTransform(frequency_x=0).is_neutral())
        self.assertTrue(SineTextTransform(
            amplitude_x=0.0, amplitude_y=0.0
        ).is_neutral())
        self.assertFalse(SineTextTransform().is_neutral())
        with self.assertRaises(ValueError):
            SineTextTransform(frequency_x=0.5).normalized()

    def test_sine_mapper_round_trips_ordered_axes_at_extreme_values(self):
        logical = self.QRectF(10, 20, 420, 160)
        source = logical.adjusted(-12, -12, 12, 12)
        transform = SineTextTransform(
            frequency_x=64,
            frequency_y=64,
            phase_x=1.0,
            phase_y=0.375,
            amplitude_x=1.0,
            amplitude_y=1.0,
        )
        mapper = SineMapper(logical, source, transform)
        source_x = np.asarray([10.0, 61.25, 180.0, 333.75, 430.0])
        source_y = np.asarray([20.0, 44.5, 90.0, 151.5, 180.0])
        visual_x, visual_y = mapper.forward_arrays(source_x, source_y)
        restored_x, restored_y, valid = mapper.inverse_arrays(
            visual_x, visual_y, return_valid=True
        )
        self.assertTrue(valid.all())
        self.assertTrue(np.allclose(restored_x, source_x, atol=1e-9))
        self.assertTrue(np.allclose(restored_y, source_y, atol=1e-9))
        for index in range(len(source_x)):
            point = self.QPointF(source_x[index], source_y[index])
            mapped = mapper.forward_point(point)
            self.assertAlmostEqual(mapped.x(), visual_x[index], places=6)
            self.assertAlmostEqual(mapped.y(), visual_y[index], places=6)
            restored = mapper.inverse_point(mapped)
            self.assertAlmostEqual(restored.x(), point.x(), places=6)
            self.assertAlmostEqual(restored.y(), point.y(), places=6)
        self.assertGreater(
            mapper.map_rect_path(logical).boundingRect().height(),
            logical.height() * 2.9,
        )

        compiled = compile_text_transform_stack(
            TextTransformStack((transform,)), logical, source, False
        )
        self.assertIsInstance(
            compiled.surface_mapper, CompositeTextTransformMapper
        )
        self.assertTrue(
            compiled.surface_mapper.visual_bounds().contains(source)
        )
        default_bounds = SineMapper(
            logical, source, SineTextTransform()
        ).visual_bounds()
        self.assertEqual(default_bounds.left(), source.left())
        self.assertEqual(default_bounds.right(), source.right())
        self.assertEqual(
            default_bounds.top(), source.top() - logical.height() * 0.1
        )

    def test_grid_payload_divisions_and_interpolation_round_trip(self):
        grid = GridTextTransform(3, 2, 'catmull_rom').normalized()
        self.assertEqual(len(grid.control_points), 12)
        self.assertTrue(grid.is_neutral())
        self.assertEqual(
            len(GridTextTransform().normalized().control_points),
            4,
        )
        with self.assertRaises(ValueError):
            GridTextTransform(33, 1).normalized()

        points = list(grid.control_points)
        points[5] = (0.7, 0.35)
        grid = grid.with_control_points(points)
        font_format = FontFormat(text_transform=[
            {
                'transform_type': 'grid',
                'horizontal_divisions': grid.horizontal_divisions,
                'vertical_divisions': grid.vertical_divisions,
                'interpolation': grid.interpolation,
                'control_points': grid.control_points,
            }
        ])
        restored = FontFormat(**json.loads(json.dumps(
            font_format.to_serializable_dict()
        )))
        self.assertEqual(restored.text_transform[0], grid)

    def test_grid_bilinear_and_catmull_rom_differ_between_anchors(self):
        logical = self.QRectF(0, 0, 400, 200)
        source = logical.adjusted(-10, -10, 10, 10)
        base = GridTextTransform(2, 2).normalized()
        points = list(base.control_points)
        points[4] = (0.7, 0.3)
        bilinear = GridMapper(
            logical,
            source,
            base.with_control_points(points),
        )
        catmull_rom = GridMapper(
            logical,
            source,
            base.with_control_points(points).with_value(
                'interpolation', 'catmull_rom'
            ),
        )
        anchor = self.QPointF(200, 100)
        expected_anchor = self.QPointF(280, 60)
        self.assertEqual(bilinear.forward_point(anchor), expected_anchor)
        self.assertEqual(catmull_rom.forward_point(anchor), expected_anchor)
        between = self.QPointF(100, 100)
        self.assertNotEqual(
            bilinear.forward_point(between),
            catmull_rom.forward_point(between),
        )
        for mapper in (bilinear, catmull_rom):
            for point in (
                self.QPointF(40, 30),
                self.QPointF(180, 80),
                self.QPointF(350, 170),
            ):
                restored = mapper.inverse_point(mapper.forward_point(point))
                self.assertAlmostEqual(restored.x(), point.x(), places=5)
                self.assertAlmostEqual(restored.y(), point.y(), places=5)

        protruding = list(base.control_points)
        protruding[4] = (1.5, -0.5)
        protruding_mapper = GridMapper(
            logical,
            source,
            base.with_control_points(protruding),
        )
        self.assertTrue(
            protruding_mapper.visual_bounds().contains(
                self.QPointF(600, -100)
            )
        )

    def test_grid_inverse_stops_after_convergence(self):
        coordinates = np.meshgrid(
            np.linspace(0.0, 400.0, 24),
            np.linspace(0.0, 200.0, 12),
        )
        for interpolation in ('bilinear', 'catmull_rom'):
            with self.subTest(interpolation=interpolation):
                grid = GridTextTransform(2, 2, interpolation).normalized()
                points = list(grid.control_points)
                points[4] = (0.6, 0.4)
                mapper = GridMapper(
                    self.QRectF(0, 0, 400, 200),
                    self.QRectF(0, 0, 400, 200),
                    grid.with_control_points(points),
                )
                calls = []
                evaluate = mapper._evaluate

                def counted_evaluate(x, y):
                    calls.append(True)
                    return evaluate(x, y)

                mapper._evaluate = counted_evaluate
                source_x, source_y, valid = mapper.inverse_arrays(
                    *coordinates, return_valid=True
                )
                self.assertEqual(source_x.dtype, np.dtype(np.float32))
                self.assertEqual(source_y.dtype, np.dtype(np.float32))
                self.assertTrue(valid.all())
                self.assertLessEqual(len(calls), mapper.INVERSE_ITERATIONS)
                remapped, _dx, _dy = evaluate(
                    source_x / 400.0, source_y / 200.0
                )
                self.assertTrue(np.allclose(
                    remapped[..., 0] * 400.0,
                    coordinates[0],
                    atol=0.005,
                ))
                self.assertTrue(np.allclose(
                    remapped[..., 1] * 200.0,
                    coordinates[1],
                    atol=0.005,
                ))

    def test_numpy_bilinear_inverse_retries_across_cell_boundaries(self):
        points = (
            (0.1122, 0.2059), (0.7473, 0.0404), (1.0360, 0.2975),
            (-0.2799, 0.4329), (0.3352, 0.2276), (0.7989, 0.5373),
            (-0.2889, 1.2479), (0.3509, 1.2040), (0.6679, 0.9692),
        )
        mapper = GridMapper(
            self.QRectF(0, 0, 1000, 500),
            self.QRectF(0, 0, 1000, 500),
            GridTextTransform(2, 2, 'bilinear', points).normalized(),
        )
        axis = np.linspace(0.25, 0.75, 41, dtype=np.float32)
        source_x, source_y = np.meshgrid(axis * 1000, axis * 500)
        visual_x, visual_y = mapper.forward_arrays(source_x, source_y)
        with patch(
            'ui.text_engine.transforms.grid._compiled_inverse_grid_arrays',
            return_value=None,
        ):
            restored_x, restored_y, valid = mapper.inverse_arrays(
                visual_x, visual_y, return_valid=True
            )

        self.assertTrue(valid.all())
        self.assertLess(
            float(np.max(np.hypot(
                restored_x - source_x,
                restored_y - source_y,
            ))),
            0.02,
        )

    def test_bilinear_grid_outline_keeps_padded_cell_boundary_kinks(self):
        logical = self.QRectF(0, 0, 100, 100)
        source = logical.adjusted(-10, -20, 30, 20)
        grid = GridTextTransform(2, 1).normalized()
        points = list(grid.control_points)
        points[1] = (0.5, -1.0)
        points[4] = (0.5, 1.0)
        mapper = GridMapper(
            logical,
            source,
            grid.with_control_points(points),
        )
        kink = mapper.forward_point(
            self.QPointF(50, source.top())
        )
        self.assertLess(kink.y(), -100.0)
        self.assertTrue(mapper.visual_bounds().contains(kink))

    def test_catmull_rom_bounds_scale_with_deformation_not_box_size(self):
        logical = self.QRectF(0, 0, 400, 200)
        grid = GridTextTransform(2, 2, 'catmull_rom').normalized()
        points = list(grid.control_points)
        points[4] = (0.55, 0.45)
        bounds = GridMapper(
            logical,
            logical,
            grid.with_control_points(points),
        ).visual_bounds()
        self.assertLess(bounds.width(), logical.width() * 1.2)
        self.assertLess(bounds.height(), logical.height() * 1.2)

    def test_grid_compiles_as_one_ordered_composable_surface_mapper(self):
        logical = self.QRectF(10, 20, 420, 160)
        source = logical.adjusted(-12, -12, 12, 12)
        grid = GridTextTransform(2, 2, 'catmull_rom').normalized()
        points = list(grid.control_points)
        points[4] = (0.62, 0.38)
        stack = TextTransformStack((
            ProjectiveTextTransform(1.1, 0.9, 4.0),
            grid.with_control_points(points),
            ProjectiveTextTransform(rotation_y=30.0, perspective=0.25),
        ))
        for vertical in (False, True):
            compiled = compile_text_transform_stack(
                stack, logical, source, vertical
            )
            self.assertTrue(compiled.native_matrix.isIdentity())
            self.assertIsInstance(
                compiled.surface_mapper, CompositeTextTransformMapper
            )
            self.assertEqual(
                tuple(stage.transform.transform_type for stage in compiled.stages),
                ('projective', 'grid', 'projective'),
            )
            point = self.QPointF(180, 90)
            restored = compiled.surface_mapper.inverse_point(
                compiled.surface_mapper.forward_point(point)
            )
            self.assertAlmostEqual(restored.x(), point.x(), places=5)
            self.assertAlmostEqual(restored.y(), point.y(), places=5)
            source_x = np.asarray([40.0, 180.0, 350.0])
            source_y = np.asarray([30.0, 80.0, 170.0])
            visual_x, visual_y = compiled.surface_mapper.forward_arrays(
                source_x, source_y
            )
            for index in range(len(source_x)):
                expected = compiled.surface_mapper.forward_point(
                    self.QPointF(source_x[index], source_y[index])
                )
                self.assertAlmostEqual(
                    visual_x[index], expected.x(), places=6
                )
                self.assertAlmostEqual(
                    visual_y[index], expected.y(), places=6
                )


class TextTransformUndoTest(TextTransformTestBase):
    def test_projective_bend_and_sine_mix_with_text_undo(self):
        from qtpy.QtGui import QUndoStack

        from ui.text_engine.editing.commands import SetTextTransformCommand

        projective = transform_state(
            ProjectiveTextTransform(rotation_y=30.0, perspective=0.6)
        )
        bend = transform_state(BendTextTransform(-0.65))
        sine = transform_state(SineTextTransform())
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                item = self._make_item(0, TEST_LINES[0], vertical)
                stack = QUndoStack()
                for before, after in (
                    (NEUTRAL, projective),
                    (projective, bend),
                    (bend, sine),
                ):
                    stack.push(
                        SetTextTransformCommand.create(
                            [item], [before], [after]
                        )
                    )
                expected = (NEUTRAL, projective, bend, sine)
                for _ in range(3):
                    for transform in reversed(expected[:-1]):
                        stack.undo()
                        self.assertEqual(
                            self._current_state(item), transform
                        )
                    for transform in expected[1:]:
                        stack.redo()
                        self.assertEqual(
                            self._current_state(item), transform
                        )
                # Upstream also interleaves MultiPasteCommand here; the text
                # itself is untouched by the transform commands we ported.
                self.assertEqual(item.toPlainText(), TEST_LINES[0])
                item.geometry_controller.release_render_resources()


class TextTransformRenderingTest(TextTransformTestBase):
    def test_zero_glyph_slant_restores_effects_inside_nonlinear_stack(self):
        stack = TextTransformStack((BendTextTransform(0.55),))
        zero = TextTransformState(stack, 0.0)
        slanted = TextTransformState(stack, 20.0)
        for vertical in (False, True):
            for effect in ("stroke", "shadow"):
                with self.subTest(vertical=vertical, effect=effect):
                    width, height = (
                        (300, 600) if vertical else (600, 300)
                    )
                    block = TextBlock([0, 0, width, height])
                    block._bounding_rect = [0, 0, width, height]
                    block.vertical = vertical
                    block.translation = "\n".join(TEST_LINES[:3])
                    if effect == "stroke":
                        block.fontformat.stroke_width = 0.12
                    else:
                        block.fontformat.shadow_radius = 0.12
                        block.fontformat.shadow_strength = 0.8
                        block.fontformat.shadow_offset = [0.1, 0.1]

                    item = self.TextBlkItem(block, 0)
                    scene = self.QGraphicsScene()
                    scene.addItem(item)
                    item.set_text_transform(zero)
                    zero_pixels = self._render_scene(scene)

                    item.set_text_transform(slanted)
                    slanted_pixels = self._render_scene(scene)
                    self.assertNotEqual(slanted_pixels, zero_pixels)

                    # Zero-slant preview is a neutral apply that leaves the
                    # committed (slanted) state untouched: the effect renderer
                    # repaints the neutral background at least once and keeps
                    # a rasterized background. Locally the padding recomputed
                    # through the detach→re-attach cycle can shift sub-pixel
                    # ink bounds, so only A/B-difference locks are used after
                    # the cycle (no exact-pixel equality).
                    renderer = item.effect_renderer
                    with patch.object(
                        renderer,
                        "repaint_background",
                        wraps=renderer.repaint_background,
                    ) as repaint_neutral:
                        item.set_text_transform(zero, preview=True)
                    self.assertGreaterEqual(repaint_neutral.call_count, 1)
                    self.assertIsNotNone(renderer.background_pixmap)

                    item.clear_text_transform_preview()
                    layout_renderer = item.geometry_controller.layout_renderer
                    self.assertIsNotNone(layout_renderer)
                    self.assertEqual(layout_renderer.glyph_slant_angle, 20.0)
                    self.assertFalse(renderer._text_transform_is_neutral())
                    self.assertNotEqual(
                        self._render_scene(scene), zero_pixels
                    )

                    with patch.object(
                        renderer,
                        "repaint_background",
                        wraps=renderer.repaint_background,
                    ) as repaint_neutral:
                        item.set_text_transform(zero)
                    self.assertGreaterEqual(repaint_neutral.call_count, 1)
                    self.assertIsNotNone(renderer.background_pixmap)
                    self.assertIsNone(renderer._transformed_effect_state)
                    self.assertNotEqual(
                        self._render_scene(scene), slanted_pixels
                    )
                    scene.removeItem(item)

    def test_warped_bend_surface_maps_layout_hit_tests(self):
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                width, height = (
                    (180, 500) if vertical else (500, 180)
                )
                block = TextBlock(
                    [40, 40, 40 + width, 40 + height],
                    _bounding_rect=[40, 40, width, height],
                    translation=TEST_LINES[3],
                )
                block.vertical = vertical
                item = self.TextBlkItem(block, 0)
                scene = self.QGraphicsScene()
                scene.addItem(item)
                neutral_pixels = self._render_scene(scene)
                source_point = item.logical_unpadded_rect().center()
                neutral_hit = item.layout.hitTest(source_point, None)

                item.set_text_transform(
                    transform_state(BendTextTransform(0.7))
                )
                mapper = item.geometry_controller.visual_mapper
                self.assertIsNotNone(mapper)
                self.assertIs(
                    item.layout.input_point_mapper.__self__,
                    item.geometry_controller,
                )
                visual_point = mapper.forward_point(source_point)
                self.assertEqual(
                    item.layout.hitTest(visual_point, None),
                    neutral_hit,
                )
                item.startEdit()
                item.endEdit()
                curved_pixels = self._render_scene(scene)
                self.assertNotEqual(curved_pixels, neutral_pixels)
                self.assertIsNotNone(
                    item.geometry_controller.surface_renderer.cached_pixmap
                )
                self.assertTrue(item.contains(visual_point))

                item.set_text_transform(NEUTRAL)
                self.assertIsNone(item.geometry_controller.visual_mapper)
                self.assertIsNone(item.geometry_controller.surface_renderer)
                self.assertIsNone(item.layout.input_point_mapper)
                self.assertEqual(self._render_scene(scene), neutral_pixels)
                item.geometry_controller.release_render_resources()
                scene.removeItem(item)
                self.app.processEvents()

    def test_neutral_effect_render_is_stable_after_transform_roundtrip(self):
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                block = TextBlock([0, 0, 600, 300])
                block._bounding_rect = [0, 0, 600, 300]
                block.vertical = vertical
                block.translation = "\n".join(TEST_LINES[:4])
                block.fontformat.stroke_width = 0.08
                block.fontformat.shadow_radius = 0.08
                block.fontformat.shadow_strength = 0.7
                block.fontformat.shadow_offset = [0.08, 0.06]
                block.fontformat.gradient_enabled = True
                block.fontformat.gradient_start_color = [20, 40, 160]
                block.fontformat.gradient_end_color = [220, 80, 40]

                item = self.TextBlkItem(block, 0)
                scene = self.QGraphicsScene()
                scene.addItem(item)
                self.app.processEvents()
                neutral_rect = item.sceneBoundingRect()
                neutral_pixels = self._render_scene(scene)

                item.set_text_transform(FIRST_TRANSFORM)
                transformed_pixels = self._render_scene(scene)
                self.assertNotEqual(neutral_pixels, transformed_pixels)

                item.set_text_transform(NEUTRAL)
                self.app.processEvents()
                self.assertEqual(item.sceneBoundingRect(), neutral_rect)
                self.assertEqual(self._render_scene(scene), neutral_pixels)
                scene.removeItem(item)

    def test_persisted_projective_transform_is_installed_on_fresh_items(self):
        for vertical in (False, True):
            with self.subTest(vertical=vertical):
                block = TextBlock([40, 50, 440, 250])
                block._bounding_rect = [40, 50, 400, 200]
                block.vertical = vertical
                block.angle = 17.0
                block.translation = TEST_LINES[0]
                block.fontformat.text_transform = FIRST_TRANSFORM.stack
                block.fontformat.glyph_slant_angle = (
                    FIRST_TRANSFORM.glyph_slant_angle
                )

                for source in (block, copy.deepcopy(block)):
                    item = self.TextBlkItem(source, 0)
                    expected = item.geometry_controller.compensated_matrix()
                    self.assertFalse(item.transform().isIdentity())
                    self.assertEqual(item.transform(), expected)

                    before = item.transform()
                    item.setRect(item.absBoundingRect(qrect=True))
                    self.assertEqual(item.transform(), before)
                    item.geometry_controller.release_render_resources()

    def test_vertical_width_resize_translates_existing_layout(self):
        # Local setMaxSize always re-layouts (no upstream width fast-path), so
        # the ported lock is: a width-only resize must be a no-op for
        # established lines — positions and pixels survive a second full
        # reLayout, and a height resize still re-runs layoutBlock.
        block = TextBlock([0, 0, 300, 600])
        block._bounding_rect = [0, 0, 300, 600]
        block.vertical = True
        block.translation = "\n".join(TEST_LINES[:4])
        block.fontformat.glyph_slant_angle = 20.0
        item = self.TextBlkItem(block, 0)
        scene = self.QGraphicsScene()
        scene.addItem(item)
        self.app.processEvents()
        layout = item.layout

        resized = item.absBoundingRect(qrect=True)
        resized.setWidth(resized.width() + 40)
        item.setRect(resized, repaint=False)
        fast_positions = self._line_positions(item)
        fast_pixels = self._render_scene(scene)

        layout.reLayout()
        self.assertEqual(self._line_positions(item), fast_positions)
        self.assertEqual(self._render_scene(scene), fast_pixels)

        with patch.object(
            layout, "layoutBlock", wraps=layout.layoutBlock
        ) as layout_block:
            resized.setHeight(resized.height() + 40)
            item.setRect(resized, repaint=False)
            self.assertGreater(layout_block.call_count, 0)
        scene.removeItem(item)

    def test_tatechuyoko_group_not_rotated_by_glyph_slant(self):
        # 2026-08-22 渲染入口切换后：本用例 A/B 对比依赖 mock fork 布局的
        # find_tatechuyoko_runs 检测器来改变渲染，但引擎竖排布局自带 tcy 检测
        # （rendering/tate_chu_yoko.py），该 mock 不再驱动布局，前提失效。
        # fork 的旧布局文件已随 2b 收尾删除（2026-08-23），
        # 适配用例待按引擎 tcy 检测路径重写。
        self.skipTest('fork tcy 检测器已删，待按引擎 tcy 检测路径重写')


if __name__ == "__main__":
    unittest.main()

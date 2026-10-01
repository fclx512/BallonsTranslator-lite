"""Real-window appearance inspector: layout, focus, page and undo boundaries.

Uses a temporary project and never persists application configuration.
"""

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'scripts/probes/out' / ('appearance_scaled' if os.environ.get('QT_SCALE_FACTOR') else '')
OUTPUT.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
os.chdir(ROOT)
import mw_repro


class Args:
    pages = 1
    blocks = 2
    project = ''
    scenario = 'none'
    no_show = False
    confirm_delay = 0


def main():
    from qtpy.QtCore import QPoint, QTimer, QTranslator, Qt
    from qtpy.QtWidgets import QApplication
    from qtpy.QtTest import QTest
    from utils.fontformat import TextTransformStack, TextTransformState
    from utils.text_effects import GlowEffect, HollowEffect, ShadowEffect, StrokeEffect, TextEffectStack

    app, _ = mw_repro._setup_qt(Args())
    translator = QTranslator()
    assert translator.load(str(ROOT / 'translate/zh_CN.qm'))
    app.installTranslator(translator)
    window = mw_repro._open_mainwindow(app, Args())
    mw_repro._make_synthetic(window, app, Args())
    window.showNormal()
    QTest.qWait(150)
    window.resize(1280, 800)
    QTest.qWait(150)
    fmt = window.textPanel.formatpanel
    item = window.st_manager.textblk_item_list[0]
    item.setSelected(True)
    fmt.set_textblk_item(item)
    item.set_text_transform(TextTransformState(TextTransformStack(), 0))
    height = window.textPanel.textEditList.height()
    effects = fmt.effects_panel
    session = fmt.effects_editor
    for count in (1, 3):
        item.set_text_effects(TextEffectStack(effects=(StrokeEffect(width=.08), ShadowEffect(), GlowEffect())[:count]))
        effects.set_effect_items([item])
        fmt.open_appearance_page(0)
        QTest.qWait(120)
        assert window.textPanel.textEditList.height() == height
    dock = fmt.appearance_dock
    for button in (fmt.appearance_effects_button, fmt.appearance_transforms_button):
        assert fmt.rect().contains(button.mapTo(fmt, button.rect().bottomRight()))
        assert window.textPanel.format_frame.rect().contains(button.mapTo(window.textPanel.format_frame, button.rect().bottomRight()) + QPoint(0, 4))
    before_hollow = item.blk.fontformat.text_effects
    undo_stack = window.canvas.text_undo_stack
    undo_before_hollow = undo_stack.index()
    QTest.mouseClick(effects.hollow_toggle_button, Qt.MouseButton.LeftButton)
    QTest.qWait(80)
    assert any(isinstance(effect, HollowEffect) for effect in item.blk.fontformat.text_effects.effects)
    assert fmt.appearance_effects_button.text().endswith('4')
    QTest.mouseClick(effects.hollow_toggle_button, Qt.MouseButton.LeftButton)
    QTest.qWait(80)
    assert item.blk.fontformat.text_effects == before_hollow
    assert fmt.appearance_effects_button.text().endswith('3')
    assert undo_stack.index() == undo_before_hollow + 2
    undo_stack.undo()
    QTest.qWait(60)
    assert effects.hollow_toggle_button.isChecked()
    undo_stack.redo()
    QTest.qWait(60)
    assert not effects.hollow_toggle_button.isChecked()
    print('[appearance] Hollow off removes its entry and count; undo/redo restores the toggle', flush=True)
    old_size = dock.size()
    card = effects.effect_cards[-1]
    QTest.mouseClick(card.appearance_entry.toggle, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    assert sum(c.appearance_entry.expanded for c in effects.effect_cards) == 1
    assert dock.size() == old_size
    edit = card.width_control.editor
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    fmt.set_textblk_item()
    assert session.items == [item]
    assert fmt.text_transform_editor.items == [item]
    assert fmt.textblk_item is item
    global_before = fmt.global_format.text_effects
    edit.selectAll()
    QTest.keyClicks(edit, '0.12')
    QTest.keyClick(edit, Qt.Key.Key_Return)
    QTest.qWait(100)
    assert fmt.global_format.text_effects == global_before
    assert item.blk.fontformat.text_effects.effects[0].width == .12
    print('[appearance] Input height stable; nested input keeps the selected owner', flush=True)
    stack = window.canvas.text_undo_stack
    index = stack.index()
    start = edit.rect().center()
    before = item.blk.fontformat.text_effects
    QTest.mousePress(edit, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(edit, start + QPoint(6, 0))
    QTest.mouseMove(edit, start + QPoint(20, 0))
    QTest.qWait(60)
    assert session.preview_before is not None
    fmt.appearance_panel.show_page(1)
    QTest.mouseRelease(edit, Qt.MouseButton.LeftButton, pos=start + QPoint(20, 0))
    QTest.qWait(80)
    assert item.blk.fontformat.text_effects == before
    assert stack.index() == index
    print('[appearance] Switching pages cancels a live effect drag without committing', flush=True)
    fmt.appearance_panel.show_page(0)
    session.add_effect('gradient')
    QTest.qWait(100)
    gradient = next(c.gradient_editor for c in effects.effect_cards if type(c).__name__ == 'TextFillEffectCard')
    result = {}
    def reject_picker():
        dialog = QApplication.activeModalWidget()
        result['dialog'] = dialog is not None
        result['flag'] = fmt.focusOnColorDialog
        fmt.set_textblk_item()
        result['owner'] = session.items == [item] and fmt.text_transform_editor.items == [item]
        if dialog is not None:
            dialog.reject()
    QTimer.singleShot(150, reject_picker)
    QTest.mouseClick(gradient.stop_color_picker, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    assert all(result.values()), result
    assert not fmt.focusOnColorDialog
    assert session.preview_before is None
    session.remove_effect(next(c.index for c in effects.effect_cards if type(c).__name__ == 'TextFillEffectCard'))
    second = window.st_manager.textblk_item_list[1]
    second.set_text_effects(item.blk.fontformat.text_effects)
    second.setSelected(True)
    fmt.set_textblk_item(item, multi_items=[item, second])
    QTest.qWait(80)
    card = effects.effect_cards[-1]
    if not card.appearance_entry.expanded:
        QTest.mouseClick(card.appearance_entry.toggle, Qt.MouseButton.LeftButton)
    QTest.mouseClick(card.width_control.editor, Qt.MouseButton.LeftButton)
    fmt.set_textblk_item()
    assert session.items == [item, second]
    assert fmt.text_transform_editor.items == [item, second]
    second.setSelected(False)
    fmt.set_textblk_item(item)
    second.set_text_effects(TextEffectStack())
    print('[appearance] Gradient color dialog and multi-selection retain their owners', flush=True)
    fmt.appearance_panel.show_page(1)
    fmt.text_transform_editor.add_transform('grid')
    QTest.qWait(100)
    window.grab().save(str(OUTPUT / 'appearance_transform.png'))
    fmt.appearance_dock.grab().save(str(OUTPUT / 'appearance_panel_transform.png'))
    panel = fmt.texttransform_panel
    assert dock.rect().contains(panel.add_transform_button.mapTo(dock, panel.add_transform_button.rect().bottomRight()))
    dock.resize(340, 300)
    QTest.qWait(80)
    for control in panel.transform_panels[0].controls.values():
        assert panel.scrollContent.rect().contains(control.mapTo(panel.scrollContent, control.rect().bottomRight()))
    dock.resize(old_size)
    fmt.appearance_panel.show_page(0)
    QTest.qWait(100)
    assert not window.canvas.textGridControl.isVisible()
    window.grab().save(str(OUTPUT / 'appearance_effects.png'))
    dock.grab().save(str(OUTPUT / 'appearance_panel_effects.png'))
    fmt.appearance_dock.close_panel()
    QTest.qWait(80)
    assert window.textPanel.textEditList.height() == height
    print('[appearance] PASS', flush=True)
    sys.stdout.flush()
    os._exit(0)


if __name__ == '__main__':
    main()

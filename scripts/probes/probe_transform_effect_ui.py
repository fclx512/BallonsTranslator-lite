"""Window-mode regression: stroke + Grid, real handle/value drags and undo.

Run with ballontrans_pylibs_win/python.exe scripts/probes/probe_transform_effect_ui.py.
Uses a temporary synthetic project; screenshots go to scripts/probes/out/.
"""

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import mw_repro


class Args:
    pages = 1
    blocks = 1
    project = ''
    scenario = 'none'
    no_show = False
    confirm_delay = 0


def main():
    import faulthandler
    from qtpy.QtCore import QPoint, QTimer, QTranslator, Qt
    from qtpy.QtTest import QTest
    from utils.fontformat import TextTransformStack, TextTransformState
    from utils.text_effects import SolidPaint, StrokeEffect, TextEffectStack

    faulthandler.enable()
    app, _timer = mw_repro._setup_qt(Args())
    translator = QTranslator()
    assert translator.load(str(ROOT / 'translate' / 'zh_CN.qm'))
    app.installTranslator(translator)
    window = mw_repro._open_mainwindow(app, Args())
    mw_repro._make_synthetic(window, app, Args(), style='vertical-stroke')
    item = window.st_manager.textblk_item_list[0]
    item.set_text_effects(TextEffectStack(effects=(StrokeEffect(width=0.08, paint=SolidPaint((255, 0, 0))),)))
    item.set_text_transform(TextTransformState(TextTransformStack(), 0))
    item.setSelected(True)
    fmt = window.textPanel.formatpanel
    session = fmt.text_transform_editor
    session.replace_targets([item])
    session.refresh_controls()
    fmt._on_appearance_launcher_toggled(True)
    fmt.appearance_panel.show_page(1)
    panel = fmt.texttransform_panel
    panel.add_transform_button.menu().actions()[3].trigger()
    app.processEvents()
    control = window.canvas.textGridControl
    view = window.canvas.gv
    viewport = view.viewport()
    stack = window.canvas.text_undo_stack
    assert control.isVisible()
    button = panel.transform_panels[0].grid_edit_button
    assert fmt.appearance_dock.rect().contains(button.mapTo(fmt.appearance_dock, button.rect().bottomRight()))

    def settle():
        QTest.qWait(80)
        app.processEvents()

    def drag_start():
        view.setFocus()
        start = view.mapFromScene(control.handles[-1].scenePos())
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, start + QPoint(-35, -20))
        settle()
        assert control._drag_mapping is not None
        assert item._effective_text_transform() != session._state_for_item(item)
        return start + QPoint(-35, -20)

    before = session._state_for_item(item)
    index = stack.index()
    end = drag_start()
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    settle()
    assert control._drag_mapping is None
    assert item._effective_text_transform() == before
    assert stack.index() == index
    print('[ui] Escape cancels real handle drag without an undo entry', flush=True)

    end = drag_start()
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    settle()
    assert stack.index() == index + 1
    deformed = session._state_for_item(item)
    assert deformed != before
    out = ROOT / 'scripts' / 'probes' / 'out'
    out.mkdir(exist_ok=True)
    window.grab().save(str(out / 'transform_ui_grid.png'))
    window.canvas.undo()
    settle()
    assert session._state_for_item(item) == before
    window.canvas.redo()
    settle()
    assert session._state_for_item(item) == deformed
    print('[ui] Real handle drag commits once; undo/redo restores geometry', flush=True)

    card = panel.transform_panels[0]
    QTest.mouseClick(card.reset_button, Qt.MouseButton.LeftButton)
    settle()
    assert session._state_for_item(item) == before
    window.canvas.undo()
    settle()
    assert session._state_for_item(item) == deformed
    QTest.mouseClick(card.grid_edit_button, Qt.MouseButton.LeftButton)
    settle()
    assert not control.isVisible()
    assert session._state_for_item(item) == deformed
    QTest.mouseClick(card.grid_edit_button, Qt.MouseButton.LeftButton)
    settle()
    assert control.isVisible()
    print('[ui] Reset is undoable; Done hides handles and preserves geometry', flush=True)

    end = drag_start()
    fmt.appearance_dock.close_panel()
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
    settle()
    assert not control.isVisible()
    assert item._effective_text_transform() == deformed
    print('[ui] Closing the dock cancels the live gesture and removes handles', flush=True)

    fmt._on_appearance_launcher_toggled(True)
    fmt.appearance_panel.show_page(1)
    panel.add_transform_button.menu().actions()[1].trigger()
    settle()
    numeric = panel.transform_panels[1].controls['bend']
    edit = numeric.editor
    start = edit.rect().center()
    index = stack.index()
    QTest.mousePress(edit, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(edit, start + QPoint(6, 0))
    QTest.mouseMove(edit, start + QPoint(20, 0))
    settle()
    assert numeric._drag_active
    assert stack.index() == index
    QTest.mouseRelease(edit, Qt.MouseButton.LeftButton, pos=start + QPoint(20, 0))
    settle()
    assert stack.index() == index + 1
    window.grab().save(str(out / 'transform_ui_values.png'))
    fmt.appearance_dock.grab().save(str(out / 'transform_ui_panel.png'))
    assert fmt.appearance_dock.rect().contains(edit.mapTo(fmt.appearance_dock, edit.rect().bottomRight()))
    print('[ui] Numeric box drag previews live and commits once', flush=True)
    # Exercise the real close path while QApplication still owns Qt objects.
    # Do not persist synthetic test state into the user's configuration.
    window.save_config = lambda: None
    QTimer.singleShot(0, window.close)
    QTimer.singleShot(250, app.quit)
    assert app.exec() == 0
    print('[ui] PASS', flush=True)


if __name__ == '__main__':
    main()

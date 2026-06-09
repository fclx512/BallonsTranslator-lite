# BallonsTranslator-lite — Project Overview

Audience: AI assistants that do not have direct access to the codebase.
Purpose: Understand the project's architecture, UI conventions, and constraints
so you can give informed suggestions for UI improvements.

---

## 1. What is this project

A desktop application for translating comics/manga/images. The pipeline has four stages:
text detection → OCR → translation → image inpainting → text rendering.

Built with **PyQt6**, runs on Windows/macOS/Linux.

---

## 2. Tech stack

| Layer | Technology |
|-------|-----------|
| GUI framework | PyQt6 (via `qtpy` compatibility layer) |
| Language | Python 3.10+ |
| Styling | CSS-like stylesheets (`config/stylesheet.css`) with `@variable` placeholders resolved at runtime |
| Animation | `QPropertyAnimation` wrapped in `ui/overlay_slide.py` (`OverlaySlider`) |
| Async | `QThread`-based workers for LLM calls and pipeline execution |
| i18n | Qt Linguist: `self.tr("English")` → `.ts` → `.qm` |

---

## 3. Application structure

```
BallonsTranslator-lite/
├── launch.py                      Entry point
├── launch_cpu.bat                 CPU-only launch script
├── launch_win.bat                 Windows launch script
├── launch_win_update.bat          Post-update launch script
├── modules/                       Pipeline modules (auto-registered via decorators at startup)
│   ├── base.py                    BaseModule, module discovery, device detection
│   ├── prepare_local_files.py     Pre-download model file validation
│   ├── textdetector/              Text detection (ctd / yolov5)
│   ├── ocr/                       OCR (mit48px / llm_api / none)
│   ├── translators/               Translation (llm_api / sakura) + context batching / hooks
│   └── inpaint/                   Image inpainting (lama / aot / ffc)
├── utils/
│   ├── config.py                  ProgramConfig dataclass, load/save
│   ├── proj_imgtrans.py           Project management (pages, text blocks, undo stack)
│   ├── textblock.py               Core data unit (coordinates, src, trans, font, mask)
│   ├── textblock_mask.py          Text block mask generation
│   ├── registry.py                Module registration decorator pattern
│   ├── structures.py              nested_dataclass / Config / Dict base classes
│   ├── shared.py                  Path constants (PROGRAM_PATH, CONFIG_PATH, etc.)
│   ├── profile_manager.py         LLM API profile manager (translator/OCR shared)
│   ├── ai_controller.py           AI assistant controller (signal-based, no widget coupling)
│   ├── ai_tools.py                AI tool execution
│   ├── ai_prompts.py              LLM prompt templates
│   ├── ai_logger.py               AI conversation logging
│   ├── proj_compact.py            Project serialization for LLM context
│   ├── font_detect.py / font_mapping.py / fontformat.py    Font detection & mapping
│   ├── psd_exporter.py / psd_jsx_exporter.py               PSD export
│   ├── text_layout.py / text_processing.py / textlines_merge.py   Layout utilities
│   ├── imgproc_utils.py / io_utils.py / download_util.py   Image I/O & downloads
│   ├── update_cache.py / mirror.py                         Update cache & mirror support
│   ├── message.py / exceptions.py / lock.py / logger.py    Infrastructure
│   └── merger.py / split_text_region.py / stroke_width_calculator.py   Miscellaneous
├── ui/
│   ├── mainwindow.py              Main window
│   ├── mainwindow_mixin.py        MainWindow business logic mixin
│   ├── mainwindowbars.py          Title bar / Left bar / Bottom bar
│   ├── configpanel.py             Config panel, shortcut editor
│   ├── canvas.py                  Canvas core
│   ├── scene_textlayout.py        Canvas text rendering
│   ├── textitem.py                Canvas text items
│   ├── io_thread.py               Pipeline orchestration (detect → OCR → translate → inpaint)
│   ├── overlay_slide.py           OverlaySlider — slide-in/out animation helper
│   ├── ai_chat_panel.py           AI chat slide-in panel
│   ├── ai_chat_model.py           ChangeItem, ChatMessage dataclasses (no Qt)
│   ├── ai_chat_worker.py          LLM API call thread
│   ├── ai_change_review.py        Change review dialog (standalone window)
│   ├── text_panel.py              Text editing panel
│   ├── textedit_area.py / textedit_commands.py    Text editing area & commands
│   ├── text_advanced_format.py / text_graphical_effect.py   Format/effect panels
│   ├── text_style_presets.py / fontstyle_manager.py         Font style presets management
│   ├── global_search_widget.py / page_search_widget.py      Search widgets
│   ├── update_checker.py          In-app update checker + About dialog
│   ├── theme_helpers.py           Theme-aware color helpers
│   ├── image_edit.py / drawingpanel.py / drawing_commands.py   Drawing/editing
│   ├── module_manager.py          Module manager
│   ├── dependency_dialog.py / model_check_dialog.py    Dependency/model checks
│   ├── network_settings_dialog.py / psd_export_dialog.py     Settings dialogs
│   ├── misc.py / shared_widget.py / collapsible_section.py   Shared UI utilities
│   ├── merge_dialog.py / shadow_gradient_dialog.py           Merge/shadow
│   ├── cursor.py / funcmaps.py                               Other
│   ├── custom_widget/             Custom widget library (buttons, sliders, combos, etc.)
│   └── framelesswindow/           Frameless window implementation (Win/Linux)
├── config/
│   ├── stylesheet.css             Global stylesheet with @variable placeholders
│   ├── themes.json                Theme definitions
│   ├── custom_themes.json         User custom themes
│   ├── textstyles/                Font style presets
│   └── config.json.example        Config template (config.json is gitignored)
├── translate/
│   ├── zh_CN.ts                   Chinese translation source
│   └── zh_CN.qm                   Compiled translation
├── scripts/
│   ├── qm_compile.py / i18n_check.py            i18n tooling
│   ├── download_models.bat / check_update.py    Model download & updates
│   └── run_module.py / check_all.py             Module runner & checks
├── tests/
│   ├── ui/                        UI tests
│   └── test_proj_compact.py       Serialization tests
├── docs/
│   ├── README.md                  Documentation structure
│   ├── en/                        English docs
│   └── zh/                        Chinese docs
└── data/                          Runtime data (tokenizer models, etc.)
```

---

## 4. UI architecture patterns

### 4.1 Main window layout

`MainWindow` uses a sidebar + main content area pattern:
- **Left bar**: tool buttons (StateChecker — QCheckBox subclass for mutually exclusive panel toggling)
- **Center**: canvas area for image preview and text editing
- **Right panel container**: hosts slide-in panels (AI chat, config, etc.)

### 4.2 Slide-in panels

All floating panels use `OverlaySlider` from `ui/overlay_slide.py`:
- Fixed width (e.g. 480px for AI chat)
- Slides in from left or right edge
- 350ms animation with `InOutExpo` easing
- `show()`, `hide()`, `resize()` API

### 4.3 Standalone dialogs

Some UI components are standalone `QDialog` subclasses (non-modal):
- `ChangeReviewWindow` — AI change review
- Config panel settings dialogs

These use standard `QDialog` layout with `QVBoxLayout` as root.

### 4.4 Widget composition pattern

UI classes follow this pattern:
1. `__init__` calls `_build_ui()` which assembles all widgets
2. Internal state stored as instance variables (prefixed with `_`)
3. Signals defined at class level for external communication
4. Styling via `setObjectName()` matching CSS selectors in stylesheet.css

Example:
```python
class MyPanel(QWidget):
    my_signal = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MyPanel")
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        # ... build child widgets ...
        btn = QPushButton(self.tr("Do Thing"))
        btn.setObjectName("MyActionBtn")
        btn.clicked.connect(self._on_action)
        root.addWidget(btn)
```

### 4.5 Styling system

All styles in `config/stylesheet.css` use `@variable` placeholders:
```css
#MyWidget {
    background-color: @widgetBackgroundColor;
    border: 1px solid @borderColor;
    border-radius: 6px;
    color: @qwidgetForegroundColor;
    font-size: 13px;
    padding: 8px;
}
```

Variables are resolved at runtime by `parse_stylesheet()` from `ui/misc.py`.
Available variables include: `@widgetBackgroundColor`, `@emptyContentBackgroundColor`,
`@accentTranslate`, `@accentPrimary20`, `@borderColor`, `@successColor`,
`@dangerColor`, `@qwidgetForegroundColor`, `@inverseTextColor`, etc.

Convention: object names use PascalCase with project prefix, e.g. `#AIReviewCard`,
`#AIChatPanel`, `#AIChangeReviewWindow`.

### 4.6 i18n

All user-visible strings must be wrapped in `self.tr("...")`:
```python
label = QLabel(self.tr("Accept All"))
```

Exceptions: log messages, LLM prompts, font preview strings, language mapping dicts.

---

## 5. AI assistant subsystem

### 5.1 Architecture

Signal-driven, no widget coupling in the data/controller layers:

```
AI Chat Panel (UI)
    ↕ signals
AiController (orchestration)
    ↕
AiChatWorker (LLM API thread)
    ↕
ai_tools.py (tool execution)
proj_compact.py (project serialization)
```

### 5.2 Data models (`ui/ai_chat_model.py`)

**`ChangeItem`** — a single field-level modification:
- `block_id: str` — text block identifier (format: `"<page>:<block>"`)
- `field: str` — which property changed (src, trans, ff, fs, fg, bg, b, i, a, sw, ls)
- `old_value: Any` — original value
- `new_value: Any` — proposed value
- `accepted: Optional[bool]` — tri-state: None=pending, True=accepted, False/other=rejected
- `src_text: str` — source text for display

**`ChatMessage`** — one message in the conversation:
- `role: str` — "user", "assistant", or "system"
- `content: str` — message text
- `changes: List[ChangeItem]` — non-empty for assistant messages proposing changes
- `segments: List[Dict]` — display segments for history reconstruction

### 5.3 Field types and their meaning

| Field | Meaning | Value type |
|-------|---------|-----------|
| `src` | Source text (original) | `str` |
| `trans` | Translation text | `str` |
| `ff` | Font family | `str` |
| `fs` | Font size | `int` or `float` |
| `fg` | Font color | `str` (hex or color name) |
| `bg` | Background color | `str` |
| `b` | Bold | `bool` |
| `i` | Italic | `bool` |
| `a` | Alignment | `str` or `int` |
| `sw` | Stroke width | `int` or `float` |
| `ls` | Line spacing | `int` or `float` |

### 5.4 Change review flow

1. AI proposes changes → `AiChatPanel.set_changes()` builds a change card in chat
2. Emits `open_review_requested` signal
3. `MainWindow` creates/shows `ChangeReviewWindow`
4. Review window groups changes by page, displays in UI
5. User reviews, accepts/rejects per-item or in batch
6. Clicks "Apply Changes" → `apply_changes_requested` signal emitted
7. `MainWindow._on_apply_ai_changes()` applies accepted changes to `TextBlock` objects

### 5.5 Scenarios the review panel must handle

| Scenario | What changes | What to show |
|----------|-------------|-------------|
| Translation only | `trans` | Source → new translation |
| Re-translation | `trans` (old non-empty) | Source → old translation + new translation |
| Font style only | `ff`, `fs`, `fg`, `b`, `i`, etc. | Source with font preview (old → new) |
| Mixed | `trans` + style fields | Source → translation comparison + font preview |
| Source rewrite | `src` + `trans` | Old source → new source, old trans → new trans |

---

## 6. Key design constraints

1. **No hardcoded Chinese** — all UI text via `self.tr()`
2. **Stylesheet variables** — never hardcode colors, always use `@variable` references
3. **Signal-based decoupling** — data/model layers must not import Qt widgets
4. **`qtpy` compatibility** — use `qtpy` imports, not direct `PyQt6` imports
5. **Object naming convention** — PascalCase with meaningful prefix (AI, Config, etc.)
6. **Config persistence** — `pcfg` is a module-level singleton; changes must call `save_config()` explicitly
7. **Module registration** — pipeline modules auto-register via decorators, discovered at startup

---

## 7. UI style reference

The existing UI uses these visual patterns:
- **Cards**: rounded corners (6-8px), border, padding 8-12px
- **Buttons**: flat with border, hover background change, 26-36px height
- **Scroll areas**: thin scrollbar, no frame border
- **Text**: 13px base size, word-wrap enabled on labels
- **Spacing**: 6-8px between sibling widgets, 12px margins on containers
- **Dividers**: thin QFrame lines, 1-2px height

---

## 8. Future / Planned Work

### 8.1 Release Packaging & Updater

A planned build pipeline for distributing stable releases to non-technical users.
Phase 1 (uv migration) and Phase 2 (on-demand deps, mirror support) are complete;
Phase 3 (packaging) is pending.

Distribution approach:

- PyInstaller `--onedir` folder packaging (pending)
- Embedded Python environment (referencing `ballontrans_pylibs_win` portable env experience)
- GitHub Releases for distribution
- Incremental updates: only replace `modules/`, `utils/`, `ui/`, `launch.py` — not the Python environment

**Implemented:**

- Git-based in-app update: `ui/update_checker.py` provides `UpdateThread` (git fetch + reset --hard) and `AboutDialog` (full update check/apply flow with progress bar and changelog)
- `utils/update_cache.py` caches remote commit for offline check support
- `launch_win_update.bat` post-update launch script

**Still pending:**

1. Semantic versioning replacing beta date placeholder in `launch.py`
2. `version.json` generation script
3. CI workflow for automated packaging (PyInstaller `--onedir`)
4. Update support for non-git environments (portable exe builds)

**See also:** `docs/en/lessons_learned.md` §5 (completed dependency management changes).

### 8.2 UI Improvements (Backlog)

(None at this time.)

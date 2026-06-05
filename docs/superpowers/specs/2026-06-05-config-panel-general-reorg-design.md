# Config Panel General Section Reorganization

**Date:** 2026-06-05
**Status:** approved
**Decision:** Option C — three-group layout + compact Typesetting controls

## Motivation

The "General" heading in the settings panel currently has four sub-sections:
Startup, Typesetting, Save, and Miscellaneous. Two problems stand out:

1. **Typesetting is overloaded** — 16+ controls packed into one group, ~5× the density of any other group. The 4×2 grid of delegation dropdowns creates visual monotony, and the Combo Box Presets editor sits awkwardly among text-formatting settings.
2. **Boundary groups are too thin** — Startup is a single checkbox occupying an entire group; Save has only 4 items; Miscellaneous is a catch-all with no clear semantic.

The goal is to reorganize into fewer, more meaningful groups and reduce Typesetting's visual density — all while keeping the left nav compact (≤ current count) and the changes implementable in stock PyQt6.

## Design

### Navigation (left sidebar)

| Before (4 items) | After (3 items) |
|---|---|
| Startup | **Project** (merged) |
| Typesetting | Typesetting |
| Save | **Interface** (renamed) |
| Miscellaneous | — |

Two nav items removed, one renamed.

### Group 1: Project

**Contents:** former Startup + Save, merged into one `PanelGroupBox("Project")`.

```
▼ Project
  ☑ Reopen last project on startup
  ──────────────────────────────
  Output
  Format [PNG ▾]  ☐ Auto detect  Quality [100]
  Intermediate [PNG ▾]
```

- **What changes:** Delete the standalone `startup_block` and `save_block` groups; create a single `project_block` containing all their widgets.
- **Signal connections:** unchanged — all `stateChanged`/`activated` handlers remain wired to the same `pcfg` setters.
- **Qt structure:** one `PanelGroupBox`, inner `QVBoxLayout` with startup checkbox → separator → output controls.
- **Code ref:** `ConfigPanel.__init__` lines 1271–1500 (startup_widget + save_widget construction and addGroupedBlock calls).

### Group 2: Typesetting

**Contents:** font format delegation grid (in compact container), text behavior checkboxes, font management controls. Presets **removed** (moved to Interface).

```
▼ Typesetting
  ┌─────────────────────────────────────────────┐
  │ Default font format (when not set per-block):│
  │                                              │
  │  Font Size  [Program ▾]  Stroke      [Prog ▾]│
  │  Font Color [Program ▾]  Stroke Clr  [Prog ▾]│
  │  Effect     [Program ▾]  Alignment   [Prog ▾]│
  │  Writing    [Program ▾]  Font Family [Keep ▾]│
  └─────────────────────────────────────────────┘
  Text formatting
  ☑ Auto layout   ☑ To uppercase   ☐ Independent text styles
  [Exclude Fonts...]   Max Font Size [200] px
```

- **What changes:**
  1. Wrap the 4×2 `QGridLayout` inside a styled `QFrame` with border-radius, dark background, and a "Default font format" label above it.
  2. Combo box widths reduced from `CONFIG_COMBOBOX_SHORT` (200px) to ~140px (options are short: "decide by program" / "use global setting").
  3. Add "Text formatting" sub-label (`ConfigTextLabel`) between the grid and the checkboxes.
  4. Remove the entire Combo Box Presets block (5 label+edit rows + header) — migrated to Interface.
- **Signal connections:** unchanged.
- **CSS:** The compact frame can be styled with a new object name, e.g. `#CompactDelegationFrame` in `stylesheet.css`, or use inline styles via `setStyleSheet` on the QFrame.
- **Code ref:** lines 1289–1454 (global_fntfmt_widget through preset rows).

### Group 3: Interface

**Contents:** former Miscellaneous items + Combo Box Presets (migrated from Typesetting).

```
▼ Interface
  Behavior
  Animation [Auto (match display) ▾]
  [Edit Shortcuts...]
  ──────────────────────────────
  Combo Box Presets
  Font Size:      [5, 5.5, 6.5, 7.5, ...]
  Line Spacing:   [...]
  Letter Spacing: [...]
  Stroke Width:   [...]
  Opacity:        [...]
  (comma-separated values — used in the font format panel dropdowns)
```

- **What changes:**
  1. Rename `PanelGroupBox("Miscellaneous")` → `PanelGroupBox("Interface")`.
  2. Move the 5 preset rows (from Typesetting) into this group, below a separator.
  3. Add "Behavior" sub-label above the animation+shortcut controls.
- **Signal connections:** unchanged; `_preset_editors` dict and `_on_preset_edited` remain wired.
- **Code ref:** lines 1502–1534 (misc_widget construction) + lines 1422–1451 (preset building in Typesetting).

## Implementation Outline

**Single file affected:** `ui/configpanel.py`

**Two additional files (light touch):**
- `config/stylesheet.css` — optional, for `#CompactDelegationFrame` styling
- `translate/zh_CN.ts` — new source strings (Project, Interface)

### Step-by-step

1. **In `ConfigPanel.__init__`**, restructure the widget construction order to match the new groups:
   - Build Project group: create `PanelGroupBox("Project")`, populate with startup checkbox + separator + save controls.
   - Build Typesetting group: same delegation grid but wrapped in styled QFrame, add sub-label, remove preset block.
   - Build Interface group: rename Misc → Interface, append preset block.

2. **Update navigation list (`_nav_items`):** Replace the four General nav entries with three (`project_block.section_widget`, `typesetting_block.section_widget`, `interface_block.section_widget`).

3. **Verify signal wiring:** All signal connections remain unchanged — only the parent container changes.

4. **i18n:** Add `self.tr("Project")`, `self.tr("Interface")`, and any new label strings ("Default font format...", "Behavior", "Output") to the ts file.

5. **Cleanup:** Remove any dead code — old standalone `startup_block`/`save_block` variables if not referenced elsewhere (check `setupConfig`, `focusOn*`).

### Non-changes (out of scope)

- DL Module groups (Text Detection, OCR, Inpaint, Translator) are untouched.
- No change to `ProgramConfig` or `ModuleConfig` data classes.
- No change to signal/slot logic.
- No change to animation system, scroll behavior, or theme system.

## Verification

- [ ] Settings panel opens without layout errors.
- [ ] Left nav shows 3 items under "General".
- [ ] All checkboxes, combos, and spinboxes reflect and update `pcfg` correctly.
- [ ] `setupConfig()` restores all values on panel reopen.
- [ ] `hideEvent` → `save_config` still triggers properly.
- [ ] Font Exclusion dialog opens from Typesetting group.
- [ ] Shortcut Editor dialog opens from Interface group.
- [ ] Combo Box Presets edits persist and emit `presets_changed`.
- [ ] Animation combo changes take effect.
- [ ] No regressions in DL Module panel.

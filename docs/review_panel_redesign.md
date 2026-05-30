# AI Change Review Panel — Card-Based Redesign

## Goal

Replace the current `QTableWidget`-based review window with a card/node-based layout.
Each text block with proposed changes renders as a single card, with the source on the left
and results on the right, connected by an operation arrow. This supports multiple scenarios:
translation, re-translation, font-only changes, and mixed modifications.

---

## Overall Window Layout

```
┌──────────────────────────────────────────────────────────┐
│  Header: title + page info + close button                │
├──────────────────────────────────────────────────────────┤
│  Top toolbar: nav (prev/next) + batch accept/reject      │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Scroll area containing cards (one per text block)       │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Card for block 3:12                               │  │
│  └────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Card for block 3:15                               │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  Footer: stats label + "Apply Changes" button            │
└──────────────────────────────────────────────────────────┘
```

- The scroll area is the only vertically scrollable region.
- Cards are laid out vertically inside the scroll area with consistent spacing.
- Cards should not have fixed height — they grow based on content.

---

## Card Structure

Each card represents **one text block** (one `block_id`). Multiple `ChangeItem` objects
sharing the same `block_id` are grouped into a single card.

### Card anatomy

```
┌─ Card ────────────────────────────────────────────────────┐
│  Header row                                               │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  Block ID label              [Accept ✓] [Reject ✗]  │ │
│  └──────────────────────────────────────────────────────┘ │
│                                                           │
│  Content: three-column layout                             │
│  ┌─────────────┬──────────────┬────────────────────────┐ │
│  │             │              │                        │ │
│  │  LEFT        │  CENTER      │  RIGHT                 │ │
│  │  (source)    │  (arrow)     │  (result)              │ │
│  │             │              │                        │ │
│  └─────────────┴──────────────┴────────────────────────┘ │
│                                                           │
│  Footer (optional): old font style bar + new font bar     │
└───────────────────────────────────────────────────────────┘
```

### Header row

- Left: block ID label (e.g. "3:12")
- Right: two small icon buttons — accept (checkmark) and reject (cross)
- Clicking accept sets all `ChangeItem`s in this block to `accepted = True`
- Clicking reject sets all `ChangeItem`s in this block to `accepted = False`
- A third "pending" state (no selection) is the default
- Card left-border color indicates state: neutral (pending), distinct style (accepted), distinct style (rejected)

### Three-column content area

The content area is a horizontal layout with three zones:

#### Left column — Source

Always present. Shows:
- The source text (`src_text` from any ChangeItem in the block, or fetched from the project)
- Text is selectable, word-wrapped
- Takes roughly 1/3 of the card width

#### Center column — Arrow / operation label

A narrow column showing:
- A right-pointing arrow (→ or ▶)
- Text labels indicating the operation type, stacked vertically:
  - "翻译" — shown if any `ChangeItem.field == "trans"` exists in this block
  - "修改样式" — shown if any `ChangeItem.field in (ff, fs, fg, b, i, a, sw, ls)` exists
- If both types exist, both labels appear
- If only one type, only that label appears

#### Right column — Result

Shows the output side. Content depends on which fields changed:

**If `trans` field changed:**
- If `old_value` is non-empty (re-translation scenario): show old translation in a sub-box
- Always show new translation (`new_value`) below

**If only style fields changed (no `trans`):**
- The right column can show a preview of the source text rendered with the new style,
  or be minimal with just the style info in the footer bars

**If both `trans` and style fields changed:**
- Show the translation comparison as above
- Style changes shown in footer bars (see below)

### Footer bars (old/new font style)

Two horizontal bars at the bottom of the card, shown only when style fields changed:

- **Left bar**: old font style summary — shows the old values for any of ff, fs, fg, b, i, a, sw, ls
  that are being changed. If a field is not changing, show its current value as context.
- **Right bar**: new font style summary — shows the new values

Each bar should render the font properties in a compact format, e.g.:
`Arial | 12pt | #000000 | Bold` with appropriate visual indicators for each property.

If no style fields are changing, these bars are hidden entirely.

---

## Card rendering by field type

Within a card, each `ChangeItem` contributes to the layout based on its `field`:

| Field | Category | Left column | Right column | Footer bars |
|-------|----------|-------------|--------------|-------------|
| `src` | text | Show old_value (as source context) | Show new_value | — |
| `trans` | text | (shared source area) | old_value (if non-empty, re-trans), new_value | — |
| `ff` | font | — | — | old → new font family |
| `fs` | font | — | — | old → new font size |
| `fg` | color | — | — | old → new color (with swatch) |
| `bg` | color | — | — | old → new background color (with swatch) |
| `b` | format | — | — | old → new bold state |
| `i` | format | — | — | old → new italic state |
| `a` | format | — | — | old → new alignment |
| `sw` | format | — | — | old → new stroke width |
| `ls` | format | — | — | old → new line spacing |

### Rendering rules

1. **Source text (left column)**: Always show. Use the `src_text` field from any ChangeItem
   in the block. If empty, fall back to `old_value` of the `src` ChangeItem if it exists.

2. **Translation (right column)**: Show only if a `trans` ChangeItem exists in the block.
   - If `old_value` is truthy (re-translation): show old translation in a visually distinct sub-box
   - Always show `new_value` as the new translation

3. **Font preview**: When `ff` or `fs` is changing, render a short preview string
   (e.g. "Aa Bb 123") using the old font (left bar) and new font (right bar) so the user
   can visually compare.

4. **Color preview**: When `fg` or `bg` is changing, show a color swatch block alongside
   the hex value.

5. **Boolean/format fields** (`b`, `i`, `a`): Show as labels/tags, e.g. "Bold: on → off".

---

## Navigation

Simplified compared to current implementation:

- **Prev / Next** buttons
- **Page label**: "Page X (N changes)" — no combo box, no spin box, no "Go" button
- Page navigation still groups by page (first segment of `block_id`)

---

## Batch operations

Top toolbar, right-aligned:
- "接受本页" — sets all ChangeItems on current page to `accepted = True`
- "拒绝本页" — sets all ChangeItems on current page to `accepted = False`

Both update card visual states and stats.

---

## Footer

- Left: stats label — "Accepted: N / Rejected: M / Total: T"
- Right: "Apply Changes" button — enabled only when at least one change is accepted

---

## State management

### Per-card state

Each card tracks:
- `accepted: Optional[bool]` — derived from the first ChangeItem's state (all items in a block
  are toggled together, so they share the same state)
- Visual state: pending (default), accepted, rejected

### Three-way toggle

Unlike the current two-state (accept only) system, the new design supports:
- **Pending** (default): card has neutral border, no checkmark/cross highlighted
- **Accepted**: card has accept-style border, checkmark highlighted
- **Rejected**: card has reject-style border, cross highlighted

Clicking the opposite button switches state. Clicking the same button again returns to pending.

### Stats

Footer shows: "Accepted: N / Rejected: M / Total: T"
- N = count of ChangeItems where `accepted is True`
- M = count of ChangeItems where `accepted is False`
- T = total ChangeItems

### Apply behavior

Only items with `accepted is True` are emitted via `apply_changes_requested`.
Pending and rejected items are discarded.

---

## Files to modify

### `ui/ai_change_review.py` — full rewrite of UI layer

Delete:
- `_ReviewTable` class (QTableWidget subclass)
- All table-related code in `_build_ui` and `_populate_table`

New classes:
- `_ChangeCard(QWidget)` — single block card
- `_CardFooter(QWidget)` — old/new font style bars

Restructured `ChangeReviewWindow`:
- `_build_ui` → builds header, toolbar, scroll area, footer (no table)
- `_populate_cards()` → replaces `_populate_table()`, creates `_ChangeCard` instances
- `_group_by_block()` → new method, groups `_page_groups[page_id]` by block_id
- Keep: `load_changes`, `_group_by_page`, `_refresh_navigation`, `_go_to_page`,
  `_accept_all`/`_accept_page` (renamed to batch accept/reject), `_update_stats`,
  `_on_apply`, `closeEvent`

### `config/stylesheet.css` — new card styles

New object names:
- `#AIReviewCard` — card container
- `#AIReviewCardHeader` — card header row
- `#AIReviewCardBody` — three-column content area
- `#AIReviewSource` — left column (source text)
- `#AIReviewArrow` — center column (arrow + labels)
- `#AIReviewResult` — right column (translation result)
- `#AIReviewOldTranslation` — old translation sub-box (re-translation only)
- `#AIReviewNewTranslation` — new translation
- `#AIReviewFontBar` — font style summary bar (old or new)
- `#AIReviewCardFooter` — container for font bars
- `#AIReviewCard[accepted="true"]` — accepted state styling
- `#AIReviewCard[accepted="false"]` — rejected state styling

### `ui/ai_chat_model.py` — no changes

---

## Pseudo-code: _ChangeCard rendering

```python
class _ChangeCard(QWidget):
    """Renders all ChangeItems for a single text block."""

    def __init__(self, block_id: str, changes: List[ChangeItem], parent=None):
        # Determine which field categories are present
        has_trans = any(c.field == "trans" for c in changes)
        has_style = any(c.field in STYLE_FIELDS for c in changes)
        has_old_trans = has_trans and any(
            c.field == "trans" and c.old_value for c in changes
        )

        # Build layout
        header = [block_id_label, accept_btn, reject_btn]
        body = QHBoxLayout:
            left = source_text_label
            center = arrow_widget(labels based on has_trans/has_style)
            right = QVBoxLayout:
                if has_old_trans: old_translation_box
                if has_trans: new_translation_label
        if has_style:
            footer = font_style_bars(old_values, new_values)
```

---

## Pseudo-code: page grouping

```python
def _group_by_block(self, page_changes: List[ChangeItem]) -> OrderedDict:
    """Group changes within a page by block_id."""
    blocks = OrderedDict()
    for c in page_changes:
        blocks.setdefault(c.block_id, []).append(c)
    return blocks
```

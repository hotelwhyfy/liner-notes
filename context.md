# context

Working notes for anyone — human or agent — picking this project up. `README.md` covers
what linernotes does and how to use it; this file covers how it is built, why, and what
state it is actually in.

## Where things stand

The layout engine is complete and works. The editor is complete and works. The package is
installable and has a launchable entry point.

Until recently the engine had **never produced a PDF**: `Text.draw` called
`canvas.setCharSpace()`, which reportlab's `Canvas` does not have, and every panel with
letter-spacing on it — every section heading, the cover line, the colophon — raised
`AttributeError`. That is fixed; drawing now goes through a text object. Everything
downstream of it turned out to be sound.

Still not under version control. `git init` remains the most valuable ten seconds available.

## Layout

```
pyproject.toml          installable; declares the console script
examples/
  slow-water.yaml       a complete album file, exercises most of the format
src/linernotes/
  build.py              pipeline entry point: album file in, PDFs out
  model.py              the Album dataclasses and the YAML loader/validator
  content.py            Album -> Sections and Panels; all credit phrasing lives here
  layout.py             geometry, drawable blocks, the panel planner
  text.py               font registration, measurement, word wrapping
  render.py             drawing panels onto a reportlab canvas; the reader PDF
  imposition.py         saddle-stitch sheet ordering; the press PDF
  errors.py             exception types and the IssueLog
  gui/
    app.py              main window, navigator, menus, the edit loop
    document.py         AlbumDocument — the raw mapping, load/save/validate
    editors.py          one build_* pane per section of the album file
    widgets.py          form widgets bound directly to the document
    preview.py          panel -> PDF -> pixmap -> Tk image
```

Dependency direction is strictly one way, and the GUI sits entirely on top:

```
gui -> build -> content -> layout -> text -> errors
              -> render  -> layout
              -> imposition -> render
              -> model -> errors
```

`layout.py` knows nothing about albums; `content.py` knows nothing about PDFs; nothing under
`src/linernotes/` outside `gui/` knows the GUI exists. The seam is `Section` and `Panel` —
`content.py` produces them, `layout.py` places them, `render.py` draws them.

## The engine

### Pipeline

`build()` → `plan_booklet()` → `load_album` → `build_fonts` → `Geometry.from_options` →
`build_booklet` → `render_reader` / `render_press`.

Two seams exist so the editor can enter halfway. `load_album(path)` parses a file and hands
off to `album_from_raw(raw, source_dir)`, which does all the validation; `plan_booklet(path)`
loads and hands off to `plan_from_album(album)`, which does all the layout. The editor holds
a mapping, not a file, so it calls the second half of each pair directly. `source_dir` is
passed explicitly rather than derived from the path, which is what lets an unsaved document
still resolve cover art sitting next to the file it came from.

### Design decisions worth knowing

**Credits are derived, never authored.** The track listing, the songwriter credits panel and
the writer index are all generated from `tracks[].writers`. There is no way to write a
credits panel by hand, deliberately: a writer added to a track cannot then go missing from
the credits. `Writer.key` normalises whitespace and case so the same person spelled two ways
still aggregates into one index entry.

**Validation collects, it does not abort.** `IssueLog` gathers errors, warnings and info
across the whole load, and `raise_if_errors()` is called once. Fixing an album file is one
pass, not one problem at a time. This is also what makes the editor's issue log possible.

**Blocks are drawn from the top down.** Every `Block` receives the PDF y-coordinate of its
*top* edge and draws downward. This is the opposite of PDF's native convention and it is the
single most important thing to internalise before editing `layout.py`.

**Measurement and drawing must agree exactly.** Both go through `text.line_width`, with the
same font, size and tracking. Any divergence shows up as text overflowing a panel the
planner believed would fit. The `setCharSpace` bug was exactly this failure: `line_width`
counted tracking into its measurement while drawing silently dropped it.

**Auto-shrink is re-generation, not scaling.** A `Section` holds a `build(size)` callable
rather than a finished block list. The planner walks a size ladder from `size_max` down to
`size_min`, re-asking for the section at each size until one fits. Every `Style` derives its
measurements from the section's base size, so the whole block scales together. Only if the
smallest size still overflows a panel does the section flow across panels.

**`Geometry.content_w` is constant across panels.** Only the x offset flips by page side
(`content_x`: odd panels are right-hand pages, so the inner margin is on their left). That
invariant is what makes it safe to wrap a block once and place it on any panel.

**`_PreWrapped` stops re-wrapping from disagreeing with a split.** When `Text.split` divides
a block, both halves get frozen line lists. Re-wrapping at draw time could produce a
different line count than was measured when the split decision was made.

**`Group` is the do-not-break unit.** A song title and its credit line are grouped so the
title is never stranded at the foot of a panel. `Group.split` refuses to split.

**The colophon always takes the final panel.** `build_booklet` inserts blanks *before* it to
reach a multiple of four, then asserts panel indices run 1..n without a gap. That assertion
has caught numbering drift before; leave it in.

### Invariants

- Panel count is always a multiple of 4 (`blanks_needed`). `sheet_order` raises otherwise.
- Panel indices are 1-based, contiguous, and asserted at the end of `build_booklet`.
- Panel 1 is the cover; the last panel is the colophon.
- The content planner starts at `first_index=2` because the cover is composed separately.
- Neither half of a press sheet bleeds across the fold.

### Imposition

For `n` panels, sheet `i` (0-based, outermost first) carries:

```
front:  left = n - 2i      right = 1 + 2i
back:   left = 2 + 2i      right = n - 1 - 2i
```

For 8 panels that is `8|1`, `2|7`, `6|3`, `4|5` — verified against a hand-folded mock-up.

## The editor

### The document is the model

`AlbumDocument` holds the **raw YAML mapping**, not the `Album` dataclasses, and the form
widgets write straight into it. There is no separate form state to keep in sync.

This is the central decision and it is worth defending. Loading is deliberately lossy:
`Jane Doe - drums` expands into a Person, bare writer strings become `Writer(name=…)`,
unknown keys are dropped with a warning. Editing the dataclasses and serialising them back
would quietly rewrite the user's file on every save — dropping their shorthand, their key
order, their comments' neighbours. Holding the mapping keeps the file theirs, and means
validation runs over exactly what will be saved.

The cost is that the editor has to cope with the shorthand forms itself. It does, by
promoting them in place the first time a pane that edits them is opened (see `_person_editor`
and the writers block in `build_track_pane`). That is a real, user-visible rewrite, and it
is documented in the README rather than hidden.

### The edit loop

An edit writes into the mapping → `on_change()` → nav labels resync, title marks dirty, a
rebuild is scheduled → after 350 ms of quiet, `rebuild_now()` re-plans and refreshes the
preview and the issue log.

`AlbumDocument.plan()` **never raises**. A document being typed into is invalid far more
often than it is valid — a track exists before it has writers — so failure is an ordinary
state to render, not an exception to handle. It returns a `BuildOutcome` carrying the log
either way, because the issues collected before the pipeline gave up are the most useful
thing to put on screen.

### Widget binding rules

Two rules in `widgets.py` keep the binding safe, and breaking either one causes bugs that
look like Tk being haunted:

- **Create a Tk variable with its initial value, then attach the trace.** Attaching first
  means populating a form fires as an edit, which marks a freshly-opened file dirty.
- **Compare before writing.** Every setter goes through `_set`, which returns early if the
  value is unchanged. Without it, re-entering the same character schedules a rebuild.

Panes are rebuilt from scratch on every navigator change rather than being cached and
repopulated. Forms here are cheap, and rebuilding removes a whole class of stale-binding
bugs. The one thing that must *not* be rebuilt on every keystroke is the navigator tree —
`_sync_nav_labels` only touches items whose text actually differs, because an unnecessary
delete/insert drops focus out of the field being typed into.

### Preview

reportlab only writes PDF and Tk cannot display PDF, so a preview goes
panel → single-page PDF in memory → PyMuPDF pixmap → PNG bytes → `tk.PhotoImage`. Nothing
touches the disk. The panel is drawn by the same `PanelRenderer` the export uses, so the
preview is the real thing rather than an approximation of it.

The DPI is computed to fit the pane and clamped to 36–300, and rasterising is debounced
120 ms behind window resizes. One consequence worth knowing when testing: on startup the
canvas has no size yet, so the first `_redraw` bails and the image only appears when the
`<Configure>` handler fires. A test that calls `root.update()` once will find `_photo` still
`None`; it has to pump the loop for a few hundred milliseconds.

### Saving

`to_yaml()` prunes empty strings, lists and mappings — a cleared field disappears and falls
back to its default rather than being written as `subtitle: ''`. `False` and `0` are real
values and survive; see `_is_empty`. Top-level keys are re-emitted in the order a
hand-written file tends to use, and multi-line strings are forced to block scalars so lyrics
stay readable on disk.

**`dirty` compares the pruned form, not the raw mapping.** Panes create the containers they
bind to: opening the Layout pane puts an empty `layout: {}` into the document, and the
Design pane adds `design.fonts: {}`. Pruning drops both again, so the saved bytes are
unchanged — but a raw comparison marked the file dirty for having been *looked at*, and
prompted to save on quit. The question `dirty` has to answer is "would saving change the
file", which is a question about the pruned form.

## Open bugs

None known. The engine and the editor both build cleanly on the example album.

## Unfinished work

- **`min_orphan_mm` is inert.** Declared in `LayoutOptions`, documented as "don't start a
  song with less room than this", accepted from YAML, surfaced in the editor's Layout pane —
  and never read. `PanelPlanner.ensure_room(needed)` is written and is clearly the hook it
  was meant to drive, but nothing calls it. Wiring it up means calling
  `ensure_room(min_orphan_mm * mm)` before a lyric section that is not starting a fresh
  panel. The editor's Layout pane carries a note saying so; remove it when this lands.
- **Section headings have no space above them.** `_heading` sets `space_after` but not
  `space_before`, so when two sections land on one panel the heading collides with the
  previous section's last line. Visible between the songwriter credits and the writer index,
  and between personnel and production.
- **The `mono` font role is dead.** `FontSet` registers it and it defaults to Helvetica, but
  no `Theme` style uses it. Presumably intended for durations and catalog numbers.
- **No batch CLI.** `linernotes-gui` is the only console script. A `linernotes build
  album.yaml -o out/` entry point over `build()` would be a dozen lines.
- **No tests.** The highest-value targets are `sheet_order` (pure, and its correctness is
  hard to eyeball), `blanks_needed`, `compress_numbers`, `sort_key_for_name`, `join_names`,
  the split/widow logic in `Text.split`, `document._pruned`, and a golden-panel-count test
  over `examples/slow-water.yaml`.
- **The press sheet is never previewed.** The editor shows booklet panels only; imposition
  correctness is still verified by exporting and folding.

## Suggested order

1. `git init` and commit before changing anything.
2. Tests over the pure functions listed above — the engine is now stable enough to pin down.
3. The two cosmetic layout items (heading spacing, `min_orphan_mm`).
4. A batch CLI.

## Environment

Python 3.14.6 in `.venv/`, installed editable. Runtime dependencies are declared in
`pyproject.toml`: reportlab 5.0.0, PyYAML 6.0.3, Pillow 12.3.0, PyMuPDF 1.28.0. Tk 9.0.

There is no lockfile — `pyproject.toml` carries lower bounds only. reportlab 5.0 is recent
enough that API drift is a live concern, and the bug this project spent its whole life
blocked on was an API-shape mistake, so verify against the installed version rather than
from memory.

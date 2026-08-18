# linernotes

Typesets a CD booklet from a single YAML file.

You describe the record — tracks, lyrics, songwriter credits, personnel, copyright — and
linernotes lays it out and produces two PDFs:

- **a reader PDF**, one panel per page in reading order, for proofing on screen;
- **a press PDF**, saddle-stitch imposed two-up with bleed, crop marks and fold marks,
  for sending to a printer.

Songwriter credits, the writer index, and the track listing are all *derived from the track
data*, so a writer added to a track cannot go missing from the credits panel. So are the ℗
and © lines, and the running order can be read straight off the audio files.

There is a desktop editor for all of it, and a Python API if you'd rather script it.

---

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
```

Or run `./launch.sh`, which installs on first use and starts the editor. It skips the
install on every run after that, and repeats it only if a dependency changes.

Requires Python 3.11+ (developed against 3.14.6). Pulls in reportlab, PyYAML, Pillow,
PyMuPDF — which rasterises panels for the editor's preview — and mutagen, which reads
durations and tags out of audio files. Without mutagen an import still reads the running
order from the filenames, only without durations.

## Building a native app

```sh
python3 build.py
```

Freezes the editor into something that runs without Python installed. What comes out
depends on the machine it was run on:

| Built on | Output |
| --- | --- |
| macOS | `dist/linernotes.app` — double-clickable, opens `.yaml` files dropped on it |
| Windows | `dist/linernotes/linernotes.exe` |
| Linux | `dist/linernotes/linernotes` |

PyInstaller freezes the interpreter it runs under, so it cannot cross-compile: a Windows
build has to happen on Windows and a macOS build on macOS. Same command on both.

`--onefile` collapses the folder into a single executable, which is easier to hand
someone but slower to start, since it unpacks itself on every launch. `--clean` throws
away `build/` and `dist/` first, worth doing when a dependency changed and the build
starts behaving oddly.

The macOS bundle is unsigned, so the first launch needs right-click → Open — Gatekeeper
refuses a plain double-click. Signing and notarising it (`codesign`, `notarytool`) is the
fix if you're distributing it properly.

## The editor

```sh
linernotes-gui                          # start empty
linernotes-gui examples/slow-water.yaml  # open a file
```

Or without installing: `PYTHONPATH=src python -m linernotes.gui examples/slow-water.yaml`.

The window is in four parts:

- **Navigator** (left) — the sections of the album file, with every track listed under
  Tracks.
- **Editor** (centre) — a form for whatever is selected.
- **Preview** (right) — the panel the selected section prints on, with `‹` `›` to page
  through the booklet. Panels are drawn by the same renderer that writes the PDF, so the
  preview is not an approximation.
- **Issue log** (bottom) — errors, warnings and layout notes, refreshed as you type.
  Double-click an issue about a track to jump to it.

The preview follows the navigator: select a song and it shows the panel that song's lyrics
land on, even when two songs share a panel or one flows across several. Page with `‹` `›`
and it stays where you put it — selecting something in the navigator hands control back.

The preview and issue log rebuild about a third of a second after you stop typing. A
document mid-edit is usually invalid — a record with no artist yet, say — and that is an
ordinary state: the issue log tells you what is missing and the preview waits.

| | |
| --- | --- |
| `Cmd+N` / `Cmd+O` | new / open |
| `Cmd+S` | save |
| `Cmd+I` | import audio files |
| `Cmd+E` | export both PDFs |
| `Cmd+R` | rebuild the preview now |

### Starting from the audio

**File ▸ Import audio files…** (`Cmd+I`) and **Import a folder of audio…** read a running
order off the files themselves: title, duration and track number per song, and the album
title, artist, year and songwriter where every file agrees on them. Track order comes from
the track-number tags when they are all present and from the filenames otherwise, so a
ripped folder sorts correctly either way — and `01 - Morning Ferry.mp3` becomes
*Morning Ferry*, not `01 - Morning Ferry`.

Anything already typed in is left alone; an import only fills blanks. If the album already
has tracks you are asked whether to replace them or add to the end. Files with unreadable
tags fall back to their filenames rather than failing the import.

What an import deliberately does *not* fill in is the writers — see below.

### Songwriters

Most records are written by one person, and saying so once is enough. Whoever is named
under **Songwriters** is credited on every track, and that credit flows into the track
listing, the songwriter credits panel and the writer index exactly as if it had been typed
on each song.

A song with a different writer overrides it: on that song's pane, **Give this track its own
writers** starts from the album's writers so you can change the one name that differs.
**Use the album's songwriters instead** hands it back. The Songwriters pane lists which
tracks are currently overriding.

**Export both PDFs…** asks for a directory and writes `<artist>-<title>-reader.pdf` and
`-press.pdf` into it. The two single exports let you choose a filename. Exporting refuses
while the album still has errors, and shows you which.

### Copyright lines

The ℗ and © lines are the same two sentences on every record, so they are written for you
from what the album already says:

| line | derived as |
| --- | --- |
| ℗ | `℗ <year> <label, or the artist if there is no label>` |
| © | `© <year> <publisher, or the writers, or the artist>` |
| notice | `All rights reserved.` |

Typing in a box overrides that line and only that line, and a box left empty is not printed
empty — it is printed derived. The Copyright pane shows what each line will say. Nothing is
written into your file unless you press **Write the automatic lines into the file**, which
you only want if you need them frozen as they read today rather than following the album.

A new document starts with the current year filled in, since that is what the derivation
needs and what a record being described now is almost always released in.

### What the editor does to your file

Saving rewrites the file. Two things to know:

- **Empty fields are dropped**, not written as `subtitle: ''`. Clearing a box restores that
  field's default.
- **Shorthand is expanded.** The format accepts `Jane Doe - drums` for a person and a bare
  `Jane Doe` for a writer. The editor needs separate fields to edit, so it rewrites those
  into mappings the first time it opens the section holding them.

Both are only written back when you save. Multi-line text — lyrics, liner notes — stays a
readable block scalar rather than one long quoted string.

## Scripting it

```python
from linernotes import build

result = build("examples/slow-water.yaml", out_dir="out")
print(result.panel_count, "panels /", result.sheet_count, "sheets")
for issue in result.log.issues:
    print(issue.format())
```

| argument     | default  | meaning                                            |
| ------------ | -------- | -------------------------------------------------- |
| `album_path` | —        | path to the album YAML file                        |
| `out_dir`    | `"out"`  | directory for the PDFs (created if missing)        |
| `reader`     | `True`   | write the reading-order PDF                        |
| `press`      | `True`   | write the imposed press PDF                        |
| `folios`     | `False`  | print panel numbers and labels on the reader PDF   |
| `marks`      | `True`   | print crop and fold marks on the press PDF         |

To validate and lay out without writing anything, use `plan_booklet(path)` for a file or
`album_from_raw(mapping, source_dir)` plus `plan_from_album(album)` for data you already
hold in memory. Both return the laid-out `Booklet` and the `IssueLog`.

## Panel order

The booklet is assembled in the order a jewel-case booklet is actually read:

| panel      | contents                                                        |
| ---------- | --------------------------------------------------------------- |
| 1          | front cover artwork                                             |
| 2 …        | lyrics, one song after another, each followed by its credits    |
| …          | liner notes                                                     |
| …          | personnel and production                                        |
| last       | colophon — title, track listing, imprint, ℗ and © lines         |

Inside pages are set in `layout.columns` columns (two by default), filled left to right;
a section starts at the top of a fresh column rather than a fresh panel, so two short songs
usually share a panel. Songs carry no track number and there is no "Lyrics" heading — a song
is announced by its title, set in bold at the same size as everything else on the page.

There is no separate track listing panel and no songwriter credits panel: a song's writers,
publishers and recording notes are set in small print directly under it, which is where they
can be checked against the song they belong to. The running order is printed once, on the
colophon, numbered with the durations set flush right — where a track listing goes on a CD.
Durations appear nowhere else; a time under a song's credits has nothing to line up against.

If the running order will not fit on the colophon, the times are dropped first and then the
listing itself, so the imprint and copyright lines always survive. Both cases log a warning
rather than silently shortening the panel.

A saddle-stitched booklet is folded sheets, so the panel count must be a multiple of four.
Blank panels are inserted *before* the colophon, which always takes the final panel.

## The album file

Only `album.title`, `album.artist`, and at least one track are required, and every track
needs a writer from somewhere — its own, or the album's. Everything else has a sensible
default. `examples/slow-water.yaml` is a complete working file; the annotated version:

```yaml
album:
  title: Slow Water
  artist: The Harbour Lights
  subtitle: ""
  year: "2026"
  label: Tidal Records
  catalog: TID-014
  cover: art/front.jpg          # relative to the YAML file's directory
  back_cover: art/back.jpg

writers:                        # credited on every track that names none itself
  - name: Jane Doe
    publisher: Blue Dock Music
    pro: ASCAP

tracks:
  - title: Morning Ferry
    duration: "3:42"
    writers:
      - name: Jane Doe
        role: music              # printed as "Music by Jane Doe. Lyrics by …"
        share: 60                # percentages; must total 100 if given
        publisher: Blue Dock Music
        pro: ASCAP
      - name: Sam Reyes
        role: lyrics
        share: 40
        publisher: Reyes Songs
        pro: BMI
    lyrics: |
      The first boat leaves at six
      and I am not on it

      I count the gulls instead
    producer: Marco Vale
    arranger: ""
    recorded_at: The Shed
    notes: ""
    publisher_note: ""           # overrides the derived publishing line

  - title: Low Tide
    duration: "4:10"
    instrumental: true           # suppresses the "no lyrics" warning
    writers: [Jane Doe]          # a bare string is just a name

credits:
  personnel:
    - Jane Doe - vocals, guitar  # "Name - role" shorthand
    - name: Ida Fenn
      role: drums
      tracks: [1, 3]             # omit for "all tracks"
  production:
    - Marco Vale - producer

notes:
  - title: About this record
    body: >
      Recorded over two winters in a shed by the water.

copyright:                                 # every line here is optional; a line
  phonographic: "℗ 2026 Tidal Records"     # left out is derived, not left blank
  composition: "© 2026 Blue Dock Music"
  notice: All rights reserved.
  extra: []
```

### `writers`

A list of writer mappings — the same shape as a track's `writers` — credited on every track
that does not name its own. Accepted at the top level or under `album:`. A track whose
`writers` list is absent, empty, or holds only unnamed entries inherits these; a track with
a named writer of its own ignores them completely rather than adding to them.

Without this, every track needs its own `writers` and the build fails on any that lack one.

### `design`

Written at the top level or nested under `album:` (top level wins).

| key                | default     | meaning                                    |
| ------------------ | ----------- | ------------------------------------------ |
| `cover`            | `""`        | front cover image                          |
| `back_cover`       | `""`        | image behind the colophon                  |
| `back_cover_color` | `""`        | solid colour behind the colophon instead   |
| `back_cover_mode`  | `"auto"`    | `artwork`, `color`, or `auto` — see below  |
| `background`       | `"#ffffff"` | paper colour                               |
| `interior_color`   | `""`        | inside pages; empty means the paper colour |
| `ink`              | `"#141414"` | body text colour                           |
| `accent`           | `"#8a7a5e"` | section headings and rules                 |
| `muted`            | `"#6b6b6b"` | credits and small print                    |
| `cover_overlay`    | `false`     | print artist and title over the cover art  |
| `cover_scrim`      | `true`      | dark gradient behind that type             |
| `back_cover_text`  | `true`      | print the colophon over the back panel     |
| `fonts`            | `{}`        | see below                                  |

The back panel is either artwork or a flat colour. `back_cover_mode: artwork` uses the
image and ignores the colour; `color` uses the colour and ignores the image, so a path can
stay in the file while a colour is tried. `auto` — what a file that does not mention it
gets — takes the image when there is one and the colour when there is not. On a solid
colour the colophon type flips to white or black if the colour is the same tone as the ink;
over artwork the type is left as it is.

`back_cover_text: false` leaves the back panel as artwork or colour alone. The panel is still
printed — the booklet is imposed on a fixed number of panels — but the title, track listing,
imprint and copyright lines are not, which means the ℗ and © lines then appear nowhere in the
booklet. `cover_overlay` is off by default for the same reason a sleeve usually is: most
cover art already has the artist and title in the picture.

`interior_color` paints every panel between the covers. Set it dark and `ink` light for a
black booklet — if both come out the same tone the build warns rather than printing black
on black.

### `layout`

| key                 | default | meaning                                            |
| ------------------- | ------- | -------------------------------------------------- |
| `panel_mm`          | `120.0` | panel width and height                             |
| `bleed_mm`          | `3.0`   | artwork bleed past the trim on the press sheet     |
| `columns`           | `2`     | text columns per inside panel                      |
| `column_gap_mm`     | `6.0`   | space between those columns                        |
| `margin_outer_mm`   | `9.0`   | margin at the outside edge                         |
| `margin_inner_mm`   | `11.0`  | margin at the fold — wider, to clear the staple    |
| `margin_top_mm`     | `10.0`  |                                                    |
| `margin_bottom_mm`  | `10.0`  |                                                    |
| `lyric_size_max`    | `10.5`  | lyrics are set at this size, then shrunk to fit    |
| `lyric_size_min`    | `7.0`   | …but never below this                              |
| `credit_size_max`   | `8.5`   | same, for credits and small print                  |
| `credit_size_min`   | `6.0`   |                                                    |
| `pack_songs`        | `true`  | let a short song share a column with the next      |
| `min_orphan_mm`     | `22.0`  | **not yet implemented** — see Known gaps           |

Unknown `design` or `layout` keys are reported as warnings and ignored.

### `fonts`

Four roles are used: `display` (titles), `body` (lyrics), `meta` (credits), and `mono`.
Each takes either a built-in family name — `serif`/`times` or `sans`/`helvetica` — or a
mapping of weight to TrueType file. Weights are `regular`, `bold`, `italic`, `bold_italic`;
missing weights fall back to `regular`. Append `#N` to pick a face out of a `.ttc`.

```yaml
design:
  fonts:
    display:
      regular: /System/Library/Fonts/Supplemental/Baskerville.ttc#0
      bold: /System/Library/Fonts/Supplemental/Baskerville.ttc#1
    body: serif
    meta: sans
```

Defaults are Times for `display` and `body`, Helvetica for `meta` and `mono`. These are
reportlab built-ins, so a booklet never depends on what happens to be installed. Any font
that fails to load is reported as a warning and falls back rather than failing the build.

## Validation

The loader collects every problem it finds and reports them together rather than stopping
at the first. Errors abort the build; warnings do not.

**Errors** — missing album title or artist; no tracks; a track with no title; duplicate
track numbers; a track with no songwriter credit and no album songwriters to inherit; a
writer with no name; a share outside `0 < share <= 100`; cover artwork that does not exist.

**Warnings** — no ℗ or © line *and too little information to derive one*; no cover artwork;
a back cover that does not exist while the back panel is set to carry artwork (it is
dropped); inside pages and ink of the same tone; the same writer credited twice on one
track; shares given for only some writers on a track; shares that do not total 100%; a
track with neither lyrics nor `instrumental: true`; a track marked instrumental that has
lyrics anyway.

**Info** — each copyright line that was derived, and what it came out as; a section that
had to be shrunk to fit, and the size it was set at; a section that had to flow across more
than one panel.

Problems with the album's songwriters are reported once, against `songwriters`, rather than
repeated for every track that inherits them.

The strictness around songwriter credits is deliberate. Getting a writer, a share, or a
publisher wrong on a physical pressing is expensive to discover after the fact.

## Printing

The press PDF is imposed two-up for saddle stitch: with 8 panels the sheets carry
`8|1`, `2|7`, `6|3`, `4|5`. Fold each sheet down the centre, nest them, staple at the fold.

At the default 120 mm panel and 3 mm bleed, each press page is 262 × 142 mm — the trim pair
plus an 11 mm slug on every side for bleed and marks. The fold is marked with ticks outside
the trim and a faint dashed line across the sheet; a caption on each sheet names the panels
it carries. Neither half bleeds across the fold.

## Known gaps

- `layout.min_orphan_mm` is accepted and documented but never read. The planner method it
  was meant to drive, `PanelPlanner.ensure_room`, exists but is never called.
- Section headings have no space above them, so when two sections share a panel the heading
  butts against the previous section's last line.
- The `mono` font role is registered but nothing uses it.
- Importing a folder reads it one level deep; a folder of disc subfolders needs the discs
  selecting individually.
- An import does not pull embedded cover art out of the files; the cover is still chosen by
  hand on the Album pane.
- No batch CLI — the editor and the Python API are the two ways in.
- No tests.

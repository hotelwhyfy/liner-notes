"""The editor panes — one per section of the album file.

Each ``build_*`` function returns a widget bound to the document. They are built
fresh whenever the navigator selection changes rather than being cached and
re-populated: forms here are cheap, and rebuilding removes a whole class of
stale-binding bugs.

Field names, defaults and hints mirror ``model.LayoutOptions`` and
``model.Design``. If a default changes there, change it here too — the hint text
is what tells the user what they get by leaving a box empty.
"""

from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from .document import AlbumDocument
from .widgets import PAD, FieldGrid, LinesField, ListEditor, Scrollable, TextField

OnChange = Callable[[], None]


def _title(parent: tk.Misc, text: str, subtitle: str = "") -> None:
    ttk.Label(parent, text=text, style="Title.TLabel").pack(anchor="w")
    if subtitle:
        ttk.Label(parent, text=subtitle, style="Hint.TLabel", wraplength=460).pack(
            anchor="w", pady=(2, PAD * 2)
        )
    else:
        ttk.Frame(parent, height=PAD * 2).pack()


def _name_of(item: Any, fallback: str) -> str:
    if isinstance(item, dict):
        return str(item.get("name") or "").strip() or fallback
    return str(item or "").strip() or fallback


# ---------------------------------------------------------------------------
# Album
# ---------------------------------------------------------------------------


def build_album_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    shell = Scrollable(parent)
    body = shell.body
    _title(body, "Album", "Title and artist are the only fields the build insists on.")

    meta = doc.section("album")
    grid = FieldGrid(body, meta, on_change)
    grid.pack(fill="x")
    grid.entry("Title", "title")
    grid.entry("Artist", "artist")
    grid.entry("Subtitle", "subtitle")
    grid.entry("Year", "year")
    grid.entry("Label", "label")
    grid.entry("Catalog no.", "catalog")

    grid.heading("Artwork")
    grid.file("Front cover", "cover", lambda: doc.source_dir,
              hint="Printed full-bleed on panel 1.")
    grid.file("Back cover", "back_cover", lambda: doc.source_dir,
              hint="Sits behind the colophon on the final panel. Missing files are "
                   "skipped. Design chooses between this and a solid colour.")
    return shell


# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------


def _new_track() -> dict:
    # No writers: a track with none inherits the album's songwriters, which is
    # the common case. Overriding is a button away on the track's own pane.
    return {"title": "New track", "lyrics": ""}


def _writer_editor(
    parent: tk.Misc, writers: list, on_change: OnChange, add_text: str
) -> ListEditor:
    """The list-plus-detail form used for writers, wherever they are credited."""
    for i, writer in enumerate(writers):
        if not isinstance(writer, dict):
            writers[i] = {"name": str(writer)}   # bare-name shorthand

    def detail(holder: tk.Misc, writer: dict) -> tk.Widget:
        grid = FieldGrid(holder, writer, on_change)
        grid.entry("Name", "name")
        grid.entry("Role", "role", hint="e.g. music, lyrics. Splits the printed credit line.")
        grid.number("Share %", "share", "", low=0, high=100, step=5)
        grid.entry("Publisher", "publisher")
        grid.entry("PRO", "pro", hint="ASCAP, BMI, PRS …")
        return grid

    return ListEditor(
        parent,
        writers,
        label=lambda w, i: _name_of(w, "unnamed writer")
        + (f"  ({w.get('share')}%)" if isinstance(w, dict) and w.get("share") else ""),
        factory=lambda: {"name": ""},
        detail=detail,
        on_change=on_change,
        add_text=add_text,
        list_height=5,
    )


def build_writers_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    """The album's songwriters — credited on every track that doesn't say otherwise."""
    shell = Scrollable(parent)
    body = shell.body
    _title(
        body,
        "Songwriters",
        "Whoever is named here is credited on every track, so a record with one "
        "writer only needs saying once. A track can still name its own writers, "
        "and then these are ignored for that track.",
    )

    writers = doc.writers
    _writer_editor(body, writers, on_change, "Add songwriter").pack(fill="both", expand=True)

    overrides = [
        f"{t.get('number', i + 1)}. {str(t.get('title') or 'untitled').strip()}"
        for i, t in enumerate(doc.tracks)
        if doc.track_has_own_writers(t)
    ]
    if overrides:
        ttk.Label(body, text="TRACKS WITH THEIR OWN WRITERS", style="Section.TLabel").pack(
            anchor="w", pady=(PAD * 3, 2)
        )
        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, PAD))
        ttk.Label(
            body,
            text=", ".join(overrides) + ".\nThese ignore the songwriters above.",
            style="Hint.TLabel",
            wraplength=460,
            justify="left",
        ).pack(anchor="w")
    return shell


def build_tracks_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    """The running order. Individual tracks are edited from the navigator."""
    shell = Scrollable(parent)
    body = shell.body
    _title(
        body,
        "Tracks",
        "The running order. Select a track in the sidebar to edit its lyrics and "
        "credits. Track numbers default to this order.",
    )

    def label(track: Any, index: int) -> str:
        if not isinstance(track, dict):
            return str(track)
        number = track.get("number", index + 1)
        title = str(track.get("title") or "").strip() or "untitled"
        duration = str(track.get("duration") or "").strip()
        return f"{number}.  {title}" + (f"   ({duration})" if duration else "")

    def detail(holder: tk.Misc, track: dict) -> tk.Widget:
        grid = FieldGrid(holder, track, on_change)
        grid.entry("Title", "title")
        grid.entry("Duration", "duration", hint="Free text, e.g. 3:42.")
        return grid

    editor = ListEditor(
        body,
        doc.tracks,
        label=label,
        factory=_new_track,
        detail=detail,
        on_change=on_change,
        add_text="Add track",
        list_height=12,
    )
    editor.pack(fill="both", expand=True)
    return shell


def _writers_summary(writers: list) -> str:
    names = [_name_of(w, "") for w in writers]
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" & {names[-1]}"


def _build_track_writers(
    parent: tk.Misc, doc: AlbumDocument, track: dict, on_change: OnChange
) -> tk.Widget:
    """Either 'inherits the album's songwriters' or this track's own list.

    Which one is showing is decided by whether the track holds a ``writers``
    list at all — not by whether the names in it are filled in — so a row added
    and not yet typed into doesn't bounce the form back to the inherited state.
    """
    holder = ttk.Frame(parent)

    def editing_own() -> bool:
        writers = track.get("writers")
        return isinstance(writers, list) and len(writers) > 0

    def render() -> None:
        for child in holder.winfo_children():
            child.destroy()

        if editing_own():
            ttk.Label(
                holder,
                text="This track credits its own writers; the album's songwriters "
                     "are ignored for it. Shares are optional, but if you give them "
                     "they must total 100%.",
                style="Hint.TLabel",
                wraplength=460,
                justify="left",
            ).pack(anchor="w", pady=(0, PAD))
            _writer_editor(holder, track["writers"], on_change, "Add writer").pack(
                fill="both", expand=True
            )
            ttk.Button(holder, text="Use the album's songwriters instead",
                       command=inherit).pack(anchor="w", pady=(PAD, 0))
            return

        album_writers = _writers_summary(doc.writers)
        if album_writers:
            ttk.Label(
                holder,
                text=f"Credited to {album_writers}, the album's songwriters.",
                style="Hint.TLabel",
                wraplength=460,
                justify="left",
            ).pack(anchor="w", pady=(0, PAD))
        else:
            ttk.Label(
                holder,
                text="No writer yet. Every track needs one or the build fails — name "
                     "the album's songwriters once under Songwriters and every track "
                     "inherits them.",
                style="Hint.TLabel",
                wraplength=460,
                justify="left",
            ).pack(anchor="w", pady=(0, PAD))
        ttk.Button(holder, text="Give this track its own writers",
                   command=override).pack(anchor="w")

    def inherit() -> None:
        track.pop("writers", None)
        on_change()
        render()

    def override() -> None:
        # Start from the album's writers rather than a blank row: a track that
        # differs usually differs by one name or one share.
        seed = copy.deepcopy(doc.writers) or [{"name": ""}]
        track["writers"] = seed
        on_change()
        render()

    render()
    return holder


def build_track_pane(
    parent: tk.Misc, doc: AlbumDocument, on_change: OnChange, index: int
) -> tk.Widget:
    shell = Scrollable(parent)
    body = shell.body
    if index >= len(doc.tracks):
        _title(body, "Track", "This track no longer exists.")
        return shell

    track = doc.tracks[index]
    if not isinstance(track, dict):
        # The file allowed a bare string as shorthand for a title; promote it so
        # there is something to edit.
        track = {"title": str(track)}
        doc.tracks[index] = track

    heading = str(track.get("title") or "").strip() or "Untitled"
    _title(body, f"{track.get('number', index + 1)}. {heading}")

    grid = FieldGrid(body, track, on_change)
    grid.pack(fill="x")
    grid.entry("Title", "title")
    grid.entry("Duration", "duration")
    grid.number("Number", "number", index + 1, low=1, high=999, step=1, cast=int)
    grid.check("Instrumental — no lyrics expected", "instrumental")

    ttk.Label(body, text="LYRICS", style="Section.TLabel").pack(anchor="w", pady=(PAD * 3, 2))
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, PAD))
    ttk.Label(
        body,
        text="Blank lines separate stanzas. Leading spaces are kept, so indented "
             "refrains stay indented.",
        style="Hint.TLabel",
        wraplength=460,
    ).pack(anchor="w", pady=(0, PAD))
    TextField(body, track, "lyrics", on_change, height=14).pack(fill="both", expand=True)

    ttk.Label(body, text="WRITERS", style="Section.TLabel").pack(anchor="w", pady=(PAD * 3, 2))
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, PAD))
    _build_track_writers(body, doc, track, on_change).pack(fill="both", expand=True)

    ttk.Label(body, text="RECORDING", style="Section.TLabel").pack(anchor="w", pady=(PAD * 3, 2))
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, PAD))
    extra = FieldGrid(body, track, on_change)
    extra.pack(fill="x")
    extra.entry("Producer", "producer")
    extra.entry("Arranger", "arranger")
    extra.entry("Recorded at", "recorded_at")
    extra.entry("Note", "notes", hint="Printed after the lyrics, with the other extras.")
    extra.entry("Publishing line", "publisher_note",
                hint="Overrides the line derived from the writers' publishers.")
    return shell


# ---------------------------------------------------------------------------
# Credits, notes, copyright
# ---------------------------------------------------------------------------


def _person_editor(
    parent: tk.Misc, people: list, on_change: OnChange, add_text: str
) -> ListEditor:
    for i, person in enumerate(people):
        if not isinstance(person, dict):
            # "Jane Doe - drums" shorthand; split it so the fields are editable.
            text = str(person)
            for sep in (" – ", " - ", ": "):
                if sep in text:
                    name, role = text.split(sep, 1)
                    people[i] = {"name": name.strip(), "role": role.strip()}
                    break
            else:
                people[i] = {"name": text.strip()}

    def label(person: Any, _i: int) -> str:
        name = _name_of(person, "unnamed")
        role = str(person.get("role") or "").strip() if isinstance(person, dict) else ""
        return f"{name} — {role}" if role else name

    def detail(holder: tk.Misc, person: dict) -> tk.Widget:
        grid = FieldGrid(holder, person, on_change)
        grid.entry("Name", "name")
        grid.entry("Role", "role", hint="e.g. vocals, guitar")
        grid.int_list("Tracks", "tracks", hint="Leave empty for every track. e.g. 1, 3, 4")
        return grid

    return ListEditor(
        parent,
        people,
        label=label,
        factory=lambda: {"name": "", "role": ""},
        detail=detail,
        on_change=on_change,
        add_text=add_text,
        list_height=6,
    )


def build_credits_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    shell = Scrollable(parent)
    body = shell.body
    _title(
        body,
        "Credits",
        "Performers and crew for the record as a whole. A credit with no tracks "
        "listed is taken to apply to all of them.",
    )

    ttk.Label(body, text="PERSONNEL", style="Section.TLabel").pack(anchor="w", pady=(0, 2))
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, PAD))
    _person_editor(body, doc.credits("personnel"), on_change, "Add person").pack(
        fill="both", expand=True
    )

    ttk.Label(body, text="PRODUCTION", style="Section.TLabel").pack(
        anchor="w", pady=(PAD * 3, 2)
    )
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, PAD))
    _person_editor(body, doc.credits("production"), on_change, "Add person").pack(
        fill="both", expand=True
    )
    return shell


def build_notes_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    shell = Scrollable(parent)
    body = shell.body
    _title(
        body,
        "Liner notes",
        "Free-form blocks — an essay, thank-yous — printed after the lyrics and "
        "before the credits.",
    )

    notes = doc.collection("notes")
    for i, note in enumerate(notes):
        if not isinstance(note, dict):
            notes[i] = {"title": "", "body": str(note)}

    def detail(holder: tk.Misc, note: dict) -> tk.Widget:
        frame = ttk.Frame(holder)
        grid = FieldGrid(frame, note, on_change)
        grid.pack(fill="x")
        grid.entry("Heading", "title", hint="Optional. Leave empty for an untitled block.")
        TextField(frame, note, "body", on_change, height=12).pack(
            fill="both", expand=True, pady=(PAD, 0)
        )
        return frame

    ListEditor(
        body,
        notes,
        label=lambda n, i: str(n.get("title") or "").strip()
        or (str(n.get("body") or "").strip().splitlines() or ["empty note"])[0][:44],
        factory=lambda: {"title": "", "body": ""},
        detail=detail,
        on_change=on_change,
        add_text="Add note",
        list_height=5,
    ).pack(fill="both", expand=True)
    return shell


def build_copyright_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    shell = Scrollable(parent)
    body = shell.body
    _title(
        body,
        "Copyright",
        "Printed on the final panel. Leave a line blank and it is written from "
        "the year, the label and the songwriters — the box is only for saying "
        "something different.",
    )

    copyright_data = doc.section("copyright")
    grid = FieldGrid(body, copyright_data, on_change)
    grid.pack(fill="x")
    fields = (
        ("℗ Sound recording", "phonographic", "℗ 2026 Tidal Records"),
        ("© Composition", "composition", "© 2026 Blue Dock Music"),
        ("Notice", "notice", "All rights reserved."),
    )
    entries: dict[str, ttk.Entry] = {}
    derived_labels: dict[str, ttk.Label] = {}
    for label, key, _example in fields:
        entries[key] = grid.entry(label, key)
        derived_labels[key] = grid.hint()

    def refresh_derived() -> None:
        derived = doc.derived_copyright()
        for label, key, example in fields:
            if key in derived:
                text = f"Automatic: {derived[key]}"
            elif str(copyright_data.get(key, "") or "").strip():
                text = "Typed in by hand — nothing is derived while this box has text."
            else:
                text = f"Nothing to derive yet. e.g. {example}"
            derived_labels[key].configure(text=text)

    def adopt() -> None:
        """Freeze the derived lines into the file as ordinary text."""
        derived = doc.derived_copyright()
        if not derived:
            return
        for key, text in derived.items():
            copyright_data[key] = text
            entries[key].delete(0, "end")
            entries[key].insert(0, text)
        on_change()
        refresh_derived()

    def changed() -> None:
        on_change()
        refresh_derived()

    grid.on_change = changed
    refresh_derived()

    ttk.Button(body, text="Write the automatic lines into the file",
               command=adopt).pack(anchor="w", pady=(PAD, 0))
    ttk.Label(
        body,
        text="Only needed if you want them fixed as they are now. Left alone they "
             "follow the album, so changing the label or the year changes them too.",
        style="Hint.TLabel",
        wraplength=460,
        justify="left",
    ).pack(anchor="w", pady=(2, 0))

    ttk.Label(body, text="ADDITIONAL LINES", style="Section.TLabel").pack(
        anchor="w", pady=(PAD * 3, 2)
    )
    ttk.Separator(body, orient="horizontal").pack(fill="x", pady=(0, PAD))
    ttk.Label(body, text="One per line.", style="Hint.TLabel").pack(anchor="w", pady=(0, PAD))
    LinesField(body, doc.section("copyright"), "extra", on_change, height=5).pack(fill="x")
    return shell


# ---------------------------------------------------------------------------
# Design and layout
# ---------------------------------------------------------------------------


def build_design_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    shell = Scrollable(parent)
    body = shell.body
    _title(body, "Design", "Colours and cover treatment.")

    design = doc.section("design")
    grid = FieldGrid(body, design, on_change)
    grid.pack(fill="x")
    paper = str(design.get("background") or "").strip() or "#ffffff"
    grid.color("Paper", "background", "#ffffff")
    # Defaulted to the paper rather than to white: opening this pane should not
    # repaint a booklet that never asked for a colour of its own.
    grid.color("Inside pages", "interior_color", paper)
    grid.hint("The background of every panel between the covers. Leave it as the "
              "paper colour for plain white pages.")
    grid.color("Ink", "ink", "#141414")
    grid.color("Accent", "accent", "#8a7a5e")
    grid.color("Muted", "muted", "#6b6b6b")

    grid.heading("Front cover")
    # These defaults have to match Design's, or the box shows a state the
    # renderer disagrees with until something writes the key.
    grid.check("Print artist and title over the artwork", "cover_overlay", default=False)
    grid.check("Dark gradient behind that type", "cover_scrim", default=True)

    grid.heading("Back cover")
    grid.choice(
        "Print",
        "back_cover_mode",
        [("artwork", "The artwork"), ("color", "A solid colour")],
        default="artwork" if str(design.get("back_cover") or "").strip() else "color",
    )
    grid.hint("The artwork is chosen under Album. A solid colour ignores it, so the "
              "image can stay in the file while you try one.")
    grid.color("Colour", "back_cover_color", paper)
    grid.check("Print the title, imprint and copyright over it", "back_cover_text",
               default=True)
    grid.hint("Unchecked leaves the back panel as artwork alone — the copyright "
              "lines then appear nowhere in the booklet. Type on the back panel "
              "flips to white or black to stay readable on a colour that matches "
              "the ink.")

    grid.heading("Fonts")
    fonts = design.get("fonts")
    if not isinstance(fonts, dict):
        fonts = {}
        design["fonts"] = fonts
    font_grid = FieldGrid(body, fonts, on_change)
    font_grid.pack(fill="x")
    for role, default in (
        ("display", "serif — titles"),
        ("body", "serif — lyrics"),
        ("meta", "sans — credits"),
        ("mono", "sans — currently unused"),
    ):
        font_grid.entry(role.title(), role, hint=f"'serif', 'sans', or a font file path. Default: {default}.")
    return shell


def build_layout_pane(parent: tk.Misc, doc: AlbumDocument, on_change: OnChange) -> tk.Widget:
    shell = Scrollable(parent)
    body = shell.body
    _title(
        body,
        "Layout",
        "Measurements in millimetres, type sizes in points. Leave a box empty to "
        "use the default.",
    )

    grid = FieldGrid(body, doc.section("layout"), on_change)
    grid.pack(fill="x")
    grid.heading("Panel")
    grid.number("Panel size (mm)", "panel_mm", 120.0, low=40, high=300, step=1)
    grid.number("Bleed (mm)", "bleed_mm", 3.0, low=0, high=20, step=0.5)

    grid.heading("Columns")
    grid.number("Columns per panel", "columns", 2, low=1, high=4, step=1, cast=int)
    grid.number("Gap between them (mm)", "column_gap_mm", 6.0, low=0, high=40, step=0.5)

    grid.heading("Margins (mm)")
    grid.number("Outer", "margin_outer_mm", 9.0, low=0, high=60, step=0.5)
    grid.number("Inner (at the fold)", "margin_inner_mm", 11.0, low=0, high=60, step=0.5)
    grid.number("Top", "margin_top_mm", 10.0, low=0, high=60, step=0.5)
    grid.number("Bottom", "margin_bottom_mm", 10.0, low=0, high=60, step=0.5)

    grid.heading("Type sizes (pt)")
    grid.number("Lyrics, largest", "lyric_size_max", 10.5, low=4, high=40, step=0.25)
    grid.number("Lyrics, smallest", "lyric_size_min", 7.0, low=4, high=40, step=0.25)
    grid.number("Credits, largest", "credit_size_max", 8.5, low=4, high=40, step=0.25)
    grid.number("Credits, smallest", "credit_size_min", 6.0, low=4, high=40, step=0.25)

    grid.heading("Flow")
    grid.check("Let a short song share a column with the next", "pack_songs", default=True)
    grid.number("Minimum room to start a song (mm)", "min_orphan_mm", 22.0,
                low=0, high=120, step=1)
    ttk.Label(
        body,
        text="Note: min_orphan_mm is accepted but not yet implemented by the layout engine.",
        style="Hint.TLabel",
        wraplength=460,
    ).pack(anchor="w", pady=(PAD, 0))
    return shell

"""Turns an Album into a laid-out sequence of panels.

Panel order follows how a jewel-case booklet is actually read:

    1        front cover artwork
    2..n     track listing, then lyrics one song after another
    n+1..    liner notes, then songwriter credits and the writer index
    last     personnel, label and copyright

Inside pages are set in columns (see ``layout.columns``); a section opens at the
top of a fresh column rather than a fresh panel, so a two-column booklet fits
twice as much on a page without anything starting mid-column.

The songwriter credits are generated from the track data rather than written by
hand, so a writer added to a track cannot go missing from the credits panel.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from reportlab.lib.units import mm

from .errors import IssueLog
from .layout import (
    Background,
    Block,
    Geometry,
    Group,
    Panel,
    PanelPlanner,
    Placed,
    PlanResult,
    Row,
    Rule,
    Section,
    Spacer,
    Style,
    Text,
    _size_ladder,
    blanks_needed,
    compose_panel,
    hex_to_rgb,
    is_dark,
)
from .model import Album, Track, join_names
from .text import FontSet


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------


@dataclass
class Theme:
    fonts: FontSet
    ink: tuple[float, float, float]
    muted: tuple[float, float, float]
    accent: tuple[float, float, float]
    paper: tuple[float, float, float]
    interior: tuple[float, float, float]
    back: tuple[float, float, float]

    @classmethod
    def from_album(cls, album: Album, fonts: FontSet) -> "Theme":
        d = album.design
        paper = hex_to_rgb(d.background)
        return cls(
            fonts=fonts,
            ink=hex_to_rgb(d.ink),
            muted=hex_to_rgb(d.muted),
            accent=hex_to_rgb(d.accent),
            paper=paper,
            interior=hex_to_rgb(d.interior_color) if d.interior_color else paper,
            back=hex_to_rgb(d.back_cover_fill()) if d.back_cover_fill() else paper,
        )

    # Each style takes the section's base size so the whole block scales
    # together when the planner shrinks it to fit. Inside the booklet everything
    # is set at that one size — the song title is told apart by its weight, not
    # by being bigger than the words underneath it.

    def song_title(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("display", "bold"),
            size=base,
            leading=base * 1.36,
            color=self.ink,
            collapse=True,
            space_after=base * 0.1,
        )

    def song_credit(self, base: float) -> Style:
        """Small print under a song. Set below the lyrics rather than level with
        them: a credit is checked once and then read past, and at the same size
        it competes with the words for the eye. The floor keeps it legible when
        the fitter has squeezed a long booklet down."""
        size = max(base * 0.78, 5.4)
        return Style(
            font=self.fonts.get("body", "italic"),
            size=size,
            leading=size * 1.3,
            color=self.muted,
            collapse=True,
            space_after=size * 0.45,
        )

    def lyric(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("body", "regular"),
            size=base,
            leading=base * 1.36,
            color=self.ink,
            hanging_indent=base * 0.9,
        )

    def lyric_note(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("body", "italic"),
            size=base,
            leading=base * 1.36,
            color=self.muted,
            collapse=True,
            space_before=base * 0.6,
        )

    def section_heading(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("meta", "bold"),
            size=max(base * 0.98, 6.4),
            leading=base * 1.3,
            color=self.accent,
            tracking=max(base * 0.11, 0.7),
            uppercase=True,
            collapse=True,
            space_after=base * 0.42,
        )

    def credit_entry(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("meta", "regular"),
            size=base,
            leading=base * 1.34,
            color=self.ink,
            collapse=True,
        )

    def credit_sub(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("meta", "regular"),
            size=max(base * 0.9, 5.2),
            leading=base * 1.24,
            color=self.muted,
            collapse=True,
            hanging_indent=base * 0.8,
        )

    def body(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("body", "regular"),
            size=base,
            leading=base * 1.4,
            color=self.ink,
        )

    def note_body(self, base: float) -> Style:
        """Liner notes and thank-yous: the size of the lyrics, lighter and
        italic, so a page of prose reads as an aside rather than as a song."""
        return Style(
            font=self.fonts.get("body", "italic"),
            size=base,
            leading=base * 1.4,
            color=self.muted,
        )

    def cover_title(self, size: float) -> Style:
        return Style(
            font=self.fonts.get("display", "bold"),
            size=size,
            leading=size * 1.08,
            color=(1, 1, 1),
            align="center",
            collapse=True,
        )

    def cover_artist(self, size: float) -> Style:
        return Style(
            font=self.fonts.get("meta", "regular"),
            size=size,
            leading=size * 1.3,
            color=(1, 1, 1),
            align="center",
            tracking=size * 0.18,
            uppercase=True,
            collapse=True,
            space_after=size * 0.6,
        )


# ---------------------------------------------------------------------------
# Credit phrasing
# ---------------------------------------------------------------------------


def writing_credit_phrase(track: Track) -> str:
    """'Written by A & B', or role-split when roles are given."""
    if not track.writers:
        return ""
    roles: dict[str, list[str]] = {}
    for w in track.writers:
        role = " ".join(w.role.split()).casefold()
        roles.setdefault(role, [])
        if w.name.strip() not in roles[role]:
            roles[role].append(w.name.strip())

    if len(roles) == 1 and "" in roles:
        return f"Written by {join_names(roles[''])}"

    parts: list[str] = []
    for role, names in roles.items():
        label = "Written" if not role else role[0].upper() + role[1:]
        parts.append(f"{label} by {join_names(names)}")
    return ". ".join(parts)


def detailed_credit_lines(track: Track) -> list[str]:
    """The long-form credit printed under a song: roles, shares, publishers.

    The names stand on their own. Under a song title there is nothing else a
    list of people set in small italics could mean, so 'Written by' is a word
    every song pays for and none of them need.
    """
    pieces: list[str] = []
    for w in track.writers:
        name = w.name.strip()
        bits: list[str] = []
        if w.role:
            bits.append(w.role)
        if w.share is not None:
            bits.append(f"{w.share:g}%")
        pieces.append(f"{name} ({', '.join(bits)})" if bits else name)

    lines: list[str] = []
    if pieces:
        lines.append(join_names(pieces))
    publishers = track.publishers_inline()
    if publishers:
        lines.append(publishers)
    return lines


def compress_numbers(numbers: list[int]) -> str:
    """[1,2,3,5,7,8] -> '1–3, 5, 7–8'."""
    if not numbers:
        return ""
    ordered = sorted(set(numbers))
    runs: list[tuple[int, int]] = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev))
        start = prev = n
    runs.append((start, prev))
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)


def person_line(person, total_tracks: int) -> str:
    text = person.name
    if person.role:
        text += f" — {person.role}"
    if person.tracks and len(set(person.tracks)) < total_tracks:
        text += f" ({compress_numbers(person.tracks)})"
    return text


# ---------------------------------------------------------------------------
# Booklet assembly
# ---------------------------------------------------------------------------


@dataclass
class Booklet:
    album: Album
    geo: Geometry
    panels: list[Panel]
    plan: PlanResult


def build_booklet(album: Album, geo: Geometry, fonts: FontSet, log: IssueLog) -> Booklet:
    theme = Theme.from_album(album, fonts)
    opts = album.layout

    cover = _cover_panel(album, geo, theme)

    plan = _fit_to_sheets(album, geo, theme, opts, log)

    content_panels = plan.panels
    # The colophon always gets the final panel; blanks are inserted before it.
    before_colophon = 1 + len(content_panels)
    pad = blanks_needed(before_colophon + 1)
    if pad:
        log.warn(
            "layout.blanks",
            f"{pad} blank panel{'s' if pad > 1 else ''} before the colophon: the "
            "booklet has to be a multiple of four panels and the type could not "
            "be set small enough to close the gap",
        )
    blanks = [
        Panel(index=before_colophon + 1 + i, kind="blank", label="blank")
        for i in range(pad)
    ]

    # The inside pages carry their own colour, so it can differ from the paper
    # the covers are printed on.
    interior = Background(color=theme.interior)
    for panel in (*content_panels, *blanks):
        panel.background = interior
    if theme.interior != theme.paper and is_dark(theme.interior) == is_dark(theme.ink):
        log.warn(
            "design.interior_contrast",
            "the inside pages and the ink are both "
            f"{'dark' if is_dark(theme.ink) else 'light'}; the text will be hard to read",
        )
    colophon_index = before_colophon + pad + 1
    colophon = _colophon_panel(album, geo, theme, colophon_index, log)

    panels = [cover, *content_panels, *blanks, colophon]
    for expected, panel in enumerate(panels, start=1):
        if panel.index != expected:
            raise AssertionError(f"panel numbering drifted: {panel.index} != {expected}")

    for key, size in sorted(plan.shrunk.items()):
        log.info("layout.shrink", f"'{key}' set at {size:g}pt to fit")
    for key, span in sorted(plan.flowed.items()):
        log.info("layout.flow", f"'{key}' continues across {span} panels")

    return Booklet(album=album, geo=geo, panels=panels, plan=plan)


def _plan_content(
    album: Album, geo: Geometry, theme: Theme, opts, log: IssueLog,
    cap: float | None = None,
) -> PlanResult:
    planner = PanelPlanner(
        geo, log, first_index=2,
        columns=opts.columns, column_gap=opts.column_gap_mm * mm,
    )
    for section in _sections(album, theme, opts, cap):
        planner.add_section(section)
    return planner.finish()


def _fit_to_sheets(
    album: Album, geo: Geometry, theme: Theme, opts, log: IssueLog
) -> PlanResult:
    """Plan the inside of the booklet onto a whole number of sheets.

    A saddle-stitched booklet is folded paper, so it is always a multiple of
    four panels. Padding the difference with blanks leaves empty pages in the
    middle of the record; setting the type a little smaller so the content ends
    where a sheet ends does not. So the type is capped a quarter-point at a time
    until the panel count lands on a sheet boundary, and the largest size that
    does is the one kept. If nothing fits — a booklet with one panel of content
    has nowhere to go — the blanks come back.
    """
    plan = _plan_content(album, geo, theme, opts, log)
    if _on_sheet_boundary(len(plan.panels)):
        return plan

    ceiling = max(opts.lyric_size_max, opts.credit_size_max)
    floor = min(opts.lyric_size_min, opts.credit_size_min)
    for cap in _size_ladder(ceiling, floor, 0.25):
        trial = _plan_content(album, geo, theme, opts, log, cap=cap)
        if _on_sheet_boundary(len(trial.panels)):
            log.info(
                "layout.sheets",
                f"type capped at {cap:g}pt so the booklet fills "
                f"{(len(trial.panels) + 2) // 4} sheet(s) with no blank panels",
            )
            return trial
    return plan


def _on_sheet_boundary(content_panels: int) -> bool:
    """Content plus the cover and the colophon, with nothing left over."""
    return content_panels >= 1 and blanks_needed(content_panels + 2) == 0


def _cover_panel(album: Album, geo: Geometry, theme: Theme) -> Panel:
    design = album.design
    art = album.resolve(design.cover)
    has_art = bool(art and art.exists())
    background = Background(
        color=theme.paper,
        image=str(art) if has_art else None,
        # The scrim exists to hold the overlaid type. With no type over the
        # artwork it would only be darkening the picture for nothing.
        scrim=bool(design.cover_scrim and has_art and design.cover_overlay),
    )

    panel = Panel(index=1, kind="cover", background=background, label="front cover")
    if not design.cover_overlay:
        return panel

    unit = geo.panel_w / 120.0    # scale type with the panel, mm-for-mm
    blocks: list[Block] = [
        Text(album.artist, theme.cover_artist(9.0 * unit)),
        Text(album.title, theme.cover_title(21.0 * unit)),
    ]
    if album.subtitle:
        blocks.append(Spacer(3.0 * mm))
        blocks.append(Text(album.subtitle, theme.cover_artist(7.0 * unit)))

    width = geo.panel_w - 2 * geo.margin_outer
    total = sum(b.height(width) for b in blocks)
    top = geo.margin_bottom + total
    for block in blocks:
        panel.items.append(Placed(x=geo.margin_outer, top=top, width=width, block=block))
        top -= block.height(width)
    return panel


def _sections(album: Album, theme: Theme, opts, cap: float | None = None) -> list[Section]:
    """The inside of the booklet, in order.

    Each song appears exactly once: title, lyrics, then everything credited to
    it. There is no separate track listing and no songwriter credits panel —
    they said the same things over again, and a credit is easiest to check
    against the song it belongs to.

    ``cap`` clamps every section's largest type size, which is how the booklet
    is squeezed onto a whole number of sheets without blank panels.
    """
    sections: list[Section] = []
    for track in album.tracks:
        sections.append(_lyric_section(track, theme, opts))

    for i, note in enumerate(album.notes):
        sections.append(_note_section(note, theme, opts, index=i))

    if album.personnel or album.production:
        sections.append(_personnel_section(album, theme, opts))

    if cap is None:
        return sections
    return [
        replace(s, size_max=min(s.size_max, cap), size_min=min(s.size_min, cap))
        for s in sections
    ]


def _heading(theme: Theme, text: str, base: float) -> list[Block]:
    return [
        Text(text, theme.section_heading(base)),
        Rule(color=theme.accent, thickness=0.6, space_after=base * 0.85),
    ]


def song_credit_lines(track: Track) -> list[str]:
    """Everything credited to one song, printed under its lyrics.

    This is the only place a song's credits appear, so it carries the long-form
    writing credit — roles, shares and publishers — and not just the names.
    """
    lines = detailed_credit_lines(track)

    # No duration here. A time belongs to the track listing, set against the
    # title where it can be scanned down a column; in a run of prose credits it
    # is a number with nothing to line up against.
    extras: list[str] = []
    if track.arranger:
        extras.append(f"Arranged by {track.arranger}")
    if track.producer:
        extras.append(f"Produced by {track.producer}")
    if track.recorded_at:
        extras.append(f"Recorded at {track.recorded_at}")
    if track.notes:
        extras.append(track.notes)
    if extras:
        lines.append(" · ".join(extras))
    return lines


def _lyric_section(track: Track, theme: Theme, opts) -> Section:
    def build(base: float) -> list[Block]:
        # Title, then the words, then who made them: no heading over the song
        # and no track number, and nothing here is repeated anywhere else.
        lyrics = track.lyrics.strip()
        if lyrics:
            body: Block = Text(lyrics, theme.lyric(base))
        elif track.instrumental:
            body = Text("Instrumental", theme.lyric_note(base))
        else:
            body = Text("[lyrics not supplied]", theme.lyric_note(base))

        # Not atomic: a long song still flows across columns, but its title
        # always keeps the first lines of it company.
        blocks: list[Block] = [
            Group([Text(track.title, theme.song_title(base)), body], atomic=False)
        ]

        credits = song_credit_lines(track)
        if credits:
            blocks.append(Spacer(base * 0.7))
            for line in credits:
                blocks.append(Text(line, theme.song_credit(base)))

        blocks.append(Spacer(base * 1.5))
        return blocks

    return Section(
        key=f"track-{track.number}",
        build=build,
        size_max=opts.lyric_size_max,
        size_min=opts.lyric_size_min,
        # Packing lets a short song share a column with the next one; without it
        # every song opens a column of its own.
        start_new_column=not opts.pack_songs,
        kind="content",
        label=f"{track.number}. {track.title}",
    )


def _note_section(note, theme: Theme, opts, index: int) -> Section:
    def build(base: float) -> list[Block]:
        blocks: list[Block] = []
        if note.title:
            blocks.extend(_heading(theme, note.title, base))
        blocks.append(Text(note.body.strip(), theme.note_body(base)))
        blocks.append(Spacer(base * 1.2))
        return blocks

    return Section(
        key=f"note-{index}",
        build=build,
        # Set with the lyrics rather than with the small print: a thank-you is
        # read, not looked up.
        size_max=opts.lyric_size_max,
        size_min=opts.lyric_size_min,
        start_new_column=index == 0,
        kind="content",
        label=note.title or f"note {index + 1}",
    )


def _personnel_section(album: Album, theme: Theme, opts) -> Section:
    total = len(album.tracks)

    def build(base: float) -> list[Block]:
        blocks: list[Block] = []
        entry = theme.credit_entry(base)
        if album.personnel:
            blocks.extend(_heading(theme, "Personnel", base))
            for person in album.personnel:
                blocks.append(Text(person_line(person, total), entry))
            blocks.append(Spacer(base * 1.1))
        if album.production:
            blocks.extend(_heading(theme, "Production", base))
            for person in album.production:
                blocks.append(Text(person_line(person, total), entry))
            blocks.append(Spacer(base * 0.8))
        return blocks

    return Section(
        key="personnel",
        build=build,
        size_max=opts.credit_size_max,
        size_min=opts.credit_size_min,
        kind="credits",
        label="personnel",
    )


def _fits(blocks: list[Block], geo: Geometry) -> bool:
    return sum(b.height(geo.content_w) for b in blocks) <= geo.content_h


def _track_listing(
    album: Album,
    title_style: Style,
    time_style: Style,
    base: float,
    times: bool = True,
) -> list[Block]:
    """The running order, numbered, with the durations set flush right.

    Numbers are padded to the width of the longest one so the titles start on a
    common left edge — a listing whose titles step rightwards at track ten is
    the usual giveaway that it was set as plain text.
    """
    if not album.tracks:
        return []
    pad = len(str(len(album.tracks)))
    rows: list[Block] = []
    for n, track in enumerate(album.tracks, start=1):
        label = Text(f"{str(n).rjust(pad)}.  {track.title}", title_style)
        rows.append(Row(label, track.duration if times else "", time_style))
    rows.append(Spacer(base * 1.1))
    return rows


def _colophon_panel(
    album: Album, geo: Geometry, theme: Theme, index: int, log: IssueLog
) -> Panel:
    base = album.layout.credit_size_max
    blocks: list[Block] = []

    # The back panel is either artwork or a flat colour. Which one is settled in
    # the model, so a path left behind by a change of mind cannot resurface here.
    art = album.resolve(album.design.back_cover_art())
    has_art = bool(art and art.exists())
    background = Background(
        color=theme.back,
        image=str(art) if has_art else None,
        scrim=False,
    )

    # A back cover can be left as a picture. The panel still has to exist — the
    # booklet is imposed on a fixed number of panels and dropping one would
    # renumber every sheet — so it is returned empty rather than skipped.
    if not album.design.back_cover_text:
        return compose_panel(
            geo,
            index,
            [],
            kind="colophon",
            label="colophon",
            valign="middle",
            background=background,
        )

    # Over a solid fill the type has to be readable, and the ink was chosen for
    # the inside pages — so on a back cover of the same tone, invert it rather
    # than print black on black. Artwork is left alone: only the person who
    # picked the picture knows where the type will land on it.
    ink, muted = theme.ink, theme.muted
    if not has_art and is_dark(theme.back) == is_dark(theme.ink):
        ink = muted = (1.0, 1.0, 1.0) if is_dark(theme.back) else (0.08, 0.08, 0.08)

    title_style = Style(
        font=theme.fonts.get("display", "bold"),
        size=base * 1.5,
        leading=base * 1.7,
        color=ink,
        align="center",
        collapse=True,
    )
    artist_style = Style(
        font=theme.fonts.get("meta", "regular"),
        size=base * 0.92,
        leading=base * 1.3,
        color=muted,
        align="center",
        tracking=base * 0.14,
        uppercase=True,
        collapse=True,
        space_after=base * 1.2,
    )
    line_style = Style(
        font=theme.fonts.get("meta", "regular"),
        size=base * 0.8,
        leading=base * 1.25,
        color=muted,
        align="center",
        collapse=True,
    )
    fine_style = Style(
        font=theme.fonts.get("meta", "regular"),
        size=max(base * 0.68, 5.0),
        leading=base * 1.05,
        color=muted,
        align="center",
        collapse=True,
    )

    listing_style = Style(
        font=theme.fonts.get("meta", "regular"),
        size=base * 0.84,
        leading=base * 1.34,
        color=ink,
        collapse=True,
    )
    time_style = replace(listing_style, color=muted, align="right")

    blocks.append(Text(album.title, title_style))
    blocks.append(Text(album.artist, artist_style))

    # The track listing lives here, not on the lyric pages: it is the one place
    # the running order can be read down a column with the times lined up, and
    # the back of the booklet is where a record buyer looks for it.
    listing = _track_listing(album, listing_style, time_style, base)
    if listing and _fits(listing + blocks, geo):
        blocks.extend(listing)
    elif listing:
        # Rather than run the listing off the panel, drop the times and try the
        # bare running order; if even that will not fit, the panel keeps the
        # imprint and copyright, which are the parts that cannot be omitted.
        bare = _track_listing(album, listing_style, time_style, base, times=False)
        if _fits(bare + blocks, geo):
            blocks.extend(bare)
            log.warn(
                "colophon.times",
                f"the back panel fits {len(album.tracks)} titles but not their "
                "durations, so the times are not printed",
            )
        else:
            log.warn(
                "colophon.listing",
                f"the track listing ({len(album.tracks)} titles) does not fit on "
                "the back panel and is not printed; the imprint and copyright are",
            )

    imprint = " · ".join(x for x in (album.label, album.catalog, album.year) if x)
    if imprint:
        blocks.append(Text(imprint, line_style))
        blocks.append(Spacer(base * 0.9))

    cr = album.copyright
    for text in (cr.phonographic, cr.composition):
        if text:
            blocks.append(Text(text, fine_style))
    if cr.notice:
        blocks.append(Spacer(base * 0.6))
        blocks.append(Text(cr.notice, fine_style))
    for extra in cr.extra:
        blocks.append(Spacer(base * 0.35))
        blocks.append(Text(extra, fine_style))

    return compose_panel(
        geo,
        index,
        blocks,
        kind="colophon",
        label="colophon",
        valign="middle",
        background=background,
    )

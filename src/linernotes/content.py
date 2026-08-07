"""Turns an Album into a laid-out sequence of panels.

Panel order follows how a jewel-case booklet is actually read:

    1        front cover artwork
    2        track listing
    3..n     lyrics, one song after another
    n+1..    liner notes, then songwriter credits and the writer index
    last     personnel, label and copyright

The songwriter credits are generated from the track data rather than written by
hand, so a writer added to a track cannot go missing from the credits panel.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    Rule,
    Section,
    Spacer,
    Style,
    Text,
    blanks_needed,
    compose_panel,
    hex_to_rgb,
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

    @classmethod
    def from_album(cls, album: Album, fonts: FontSet) -> "Theme":
        d = album.design
        return cls(
            fonts=fonts,
            ink=hex_to_rgb(d.ink),
            muted=hex_to_rgb(d.muted),
            accent=hex_to_rgb(d.accent),
            paper=hex_to_rgb(d.background),
        )

    # Each style takes the section's base size so the whole block scales
    # together when the planner shrinks it to fit.

    def song_title(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("display", "bold"),
            size=base * 1.42,
            leading=base * 1.62,
            color=self.ink,
            space_after=base * 0.16,
        )

    def song_credit(self, base: float) -> Style:
        return Style(
            font=self.fonts.get("meta", "regular"),
            size=max(base * 0.72, 5.4),
            leading=base * 0.95,
            color=self.muted,
            collapse=True,
            space_after=base * 0.5,
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
            size=max(base * 0.82, 5.6),
            leading=base * 1.2,
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
    """The long-form credit printed on the songwriter credits panel."""
    pieces: list[str] = []
    for w in track.writers:
        name = w.name.strip()
        bits: list[str] = []
        if w.role:
            bits.append(w.role)
        if w.share is not None:
            bits.append(f"{w.share:g}%")
        pieces.append(f"{name} ({', '.join(bits)})" if bits else name)
    lines = [f"Written by {join_names(pieces)}"]
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

    planner = PanelPlanner(geo, log, first_index=2)
    for section in _sections(album, theme, opts):
        planner.add_section(section)
    plan = planner.finish()

    content_panels = plan.panels
    # The colophon always gets the final panel; blanks are inserted before it.
    before_colophon = 1 + len(content_panels)
    pad = blanks_needed(before_colophon + 1)
    blanks = [
        Panel(index=before_colophon + 1 + i, kind="blank", label="blank")
        for i in range(pad)
    ]
    colophon_index = before_colophon + pad + 1
    colophon = _colophon_panel(album, geo, theme, colophon_index)

    panels = [cover, *content_panels, *blanks, colophon]
    for expected, panel in enumerate(panels, start=1):
        if panel.index != expected:
            raise AssertionError(f"panel numbering drifted: {panel.index} != {expected}")

    for key, size in sorted(plan.shrunk.items()):
        log.info("layout.shrink", f"'{key}' set at {size:g}pt to fit")
    for key, span in sorted(plan.flowed.items()):
        log.info("layout.flow", f"'{key}' continues across {span} panels")

    return Booklet(album=album, geo=geo, panels=panels, plan=plan)


def _cover_panel(album: Album, geo: Geometry, theme: Theme) -> Panel:
    design = album.design
    art = album.resolve(design.cover)
    background = Background(
        color=theme.paper,
        image=str(art) if art and art.exists() else None,
        scrim=bool(design.cover_scrim and art and art.exists()),
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


def _sections(album: Album, theme: Theme, opts) -> list[Section]:
    sections: list[Section] = []
    sections.append(_tracklist_section(album, theme, opts))

    first = True
    for track in album.tracks:
        sections.append(_lyric_section(album, track, theme, opts, first=first))
        first = False

    for i, note in enumerate(album.notes):
        sections.append(_note_section(note, theme, opts, index=i))

    sections.append(_songwriter_credits_section(album, theme, opts))
    sections.append(_writer_index_section(album, theme, opts))
    if album.personnel or album.production:
        sections.append(_personnel_section(album, theme, opts))
    return sections


def _heading(theme: Theme, text: str, base: float) -> list[Block]:
    return [
        Text(text, theme.section_heading(base)),
        Rule(color=theme.accent, thickness=0.6, space_after=base * 0.85),
    ]


def _tracklist_section(album: Album, theme: Theme, opts) -> Section:
    def build(base: float) -> list[Block]:
        blocks: list[Block] = _heading(theme, "Tracks", base)
        entry = theme.credit_entry(base)
        sub = theme.credit_sub(base)
        for track in album.tracks:
            head = f"{track.number}.  {track.title}"
            if track.duration:
                head += f"   {track.duration}"
            group: list[Block] = [Text(head, entry)]
            credit = writing_credit_phrase(track)
            if credit:
                group.append(Text(credit, sub))
            group.append(Spacer(base * 0.55))
            blocks.append(Group(group))
        return blocks

    return Section(
        key="tracklist",
        build=build,
        size_max=opts.credit_size_max,
        size_min=opts.credit_size_min,
        start_new_panel=True,
        kind="content",
        label="track listing",
    )


def _lyric_section(album: Album, track: Track, theme: Theme, opts, first: bool) -> Section:
    def build(base: float) -> list[Block]:
        header: list[Block] = []
        if first:
            header.extend(_heading(theme, "Lyrics", base))

        head_blocks: list[Block] = [
            Text(f"{track.number}. {track.title}", theme.song_title(base))
        ]
        credit = writing_credit_phrase(track)
        if credit:
            head_blocks.append(Text(credit, theme.song_credit(base)))

        body: list[Block] = []
        lyrics = track.lyrics.strip()
        if lyrics:
            body.append(Text(lyrics, theme.lyric(base)))
        elif track.instrumental:
            body.append(Text("Instrumental", theme.lyric_note(base)))
        else:
            body.append(Text("[lyrics not supplied]", theme.lyric_note(base)))

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
            body.append(Text(" · ".join(extras), theme.lyric_note(base)))

        return [*header, Group(head_blocks), *body, Spacer(base * 1.5)]

    return Section(
        key=f"track-{track.number}",
        build=build,
        size_max=opts.lyric_size_max,
        size_min=opts.lyric_size_min,
        start_new_panel=first or not opts.pack_songs,
        kind="content",
        label=f"{track.number}. {track.title}",
    )


def _note_section(note, theme: Theme, opts, index: int) -> Section:
    def build(base: float) -> list[Block]:
        blocks: list[Block] = []
        if note.title:
            blocks.extend(_heading(theme, note.title, base))
        blocks.append(Text(note.body.strip(), theme.body(base)))
        blocks.append(Spacer(base * 1.2))
        return blocks

    return Section(
        key=f"note-{index}",
        build=build,
        size_max=opts.credit_size_max + 0.5,
        size_min=opts.credit_size_min,
        start_new_panel=index == 0,
        kind="content",
        label=note.title or f"note {index + 1}",
    )


def _songwriter_credits_section(album: Album, theme: Theme, opts) -> Section:
    def build(base: float) -> list[Block]:
        blocks: list[Block] = _heading(theme, "Songwriter credits", base)
        entry = theme.credit_entry(base)
        sub = theme.credit_sub(base)
        for track in album.tracks:
            head = f"{track.number}.  {track.title}"
            if track.duration:
                head += f"  ({track.duration})"
            group: list[Block] = [Text(head, entry)]
            for line in detailed_credit_lines(track):
                group.append(Text(line, sub))
            group.append(Spacer(base * 0.6))
            blocks.append(Group(group))
        return blocks

    return Section(
        key="songwriter-credits",
        build=build,
        size_max=opts.credit_size_max,
        size_min=opts.credit_size_min,
        start_new_panel=True,
        kind="credits",
        label="songwriter credits",
    )


def _writer_index_section(album: Album, theme: Theme, opts) -> Section:
    rows = album.writer_index()

    def build(base: float) -> list[Block]:
        blocks: list[Block] = _heading(theme, "Writers", base)
        entry = theme.credit_entry(base)
        sub = theme.credit_sub(base)
        for name, tracks, publishers in rows:
            group: list[Block] = [
                Text(f"{name} — {compress_numbers(tracks)}", entry)
            ]
            if publishers:
                group.append(Text(" / ".join(publishers), sub))
            group.append(Spacer(base * 0.4))
            blocks.append(Group(group))
        blocks.append(Spacer(base * 0.8))
        return blocks

    return Section(
        key="writer-index",
        build=build,
        size_max=opts.credit_size_max,
        size_min=opts.credit_size_min,
        kind="credits",
        label="writer index",
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


def _colophon_panel(album: Album, geo: Geometry, theme: Theme, index: int) -> Panel:
    base = album.layout.credit_size_max
    blocks: list[Block] = []

    title_style = Style(
        font=theme.fonts.get("display", "bold"),
        size=base * 1.5,
        leading=base * 1.7,
        color=theme.ink,
        align="center",
        collapse=True,
    )
    artist_style = Style(
        font=theme.fonts.get("meta", "regular"),
        size=base * 0.92,
        leading=base * 1.3,
        color=theme.muted,
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
        color=theme.muted,
        align="center",
        collapse=True,
    )
    fine_style = Style(
        font=theme.fonts.get("meta", "regular"),
        size=max(base * 0.68, 5.0),
        leading=base * 1.05,
        color=theme.muted,
        align="center",
        collapse=True,
    )

    blocks.append(Text(album.title, title_style))
    blocks.append(Text(album.artist, artist_style))

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

    art = album.resolve(album.design.back_cover)
    background = Background(color=theme.paper)
    if art and art.exists():
        background = Background(color=theme.paper, image=str(art), scrim=False)

    return compose_panel(
        geo,
        index,
        blocks,
        kind="colophon",
        label="colophon",
        valign="middle",
        background=background,
    )

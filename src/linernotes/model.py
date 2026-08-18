"""The album data model, loaded from YAML.

The model is deliberately strict about songwriter credits: every track needs at
least one writer, and if shares are given at all they have to add up. Getting
those wrong on a physical pressing is expensive, so the loader reports them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .errors import IssueLog, ValidationError

# ---------------------------------------------------------------------------
# People and credits
# ---------------------------------------------------------------------------


@dataclass
class Writer:
    """One songwriter's stake in one track."""

    name: str
    role: str = ""          # e.g. "music", "lyrics", "music & lyrics"
    share: float | None = None   # percentage of the writer's share
    publisher: str = ""
    pro: str = ""           # performing rights organisation: ASCAP, BMI, ...

    @property
    def key(self) -> str:
        """Normalised identity, so the same person spelled with different
        spacing or casing still aggregates into one index entry."""
        return re.sub(r"\s+", " ", self.name).strip().casefold()

    def publisher_line(self) -> str:
        if self.publisher and self.pro:
            return f"{self.publisher} ({self.pro})"
        return self.publisher or (f"({self.pro})" if self.pro else "")


@dataclass
class Person:
    """A performer or crew credit."""

    name: str
    role: str = ""
    tracks: list[int] = field(default_factory=list)  # empty means "all tracks"


@dataclass
class Track:
    number: int
    title: str
    duration: str = ""
    writers: list[Writer] = field(default_factory=list)
    lyrics: str = ""
    instrumental: bool = False
    performers: list[Person] = field(default_factory=list)
    producer: str = ""
    arranger: str = ""
    recorded_at: str = ""
    notes: str = ""
    publisher_note: str = ""   # overrides the derived publishing line
    inherited_writers: bool = False   # writers came from the album default

    def writer_names(self) -> list[str]:
        seen: dict[str, str] = {}
        for w in self.writers:
            seen.setdefault(w.key, w.name.strip())
        return list(seen.values())

    def writers_inline(self) -> str:
        """'Jane Doe & Sam Reyes' / 'A, B & C' — the line printed under a title."""
        return join_names(self.writer_names())

    def publishers_inline(self) -> str:
        if self.publisher_note:
            return self.publisher_note
        lines: list[str] = []
        for w in self.writers:
            line = w.publisher_line()
            if line and line not in lines:
                lines.append(line)
        return " / ".join(lines)


@dataclass
class Note:
    """A free-form titled block, e.g. liner essay or thank-yous."""

    title: str
    body: str


@dataclass
class Copyright:
    phonographic: str = ""   # the (P) line — the sound recording
    composition: str = ""    # the (C) line — the underlying works
    notice: str = ""
    extra: list[str] = field(default_factory=list)


@dataclass
class Design:
    cover: str = ""
    back_cover: str = ""
    back_cover_color: str = ""       # solid colour for the back panel; "" = paper
    # "artwork" | "color" | "auto". Auto is what a file written by hand gets:
    # the image if there is one, the colour if there is not.
    back_cover_mode: str = "auto"
    background: str = "#ffffff"
    interior_color: str = ""         # inside pages; "" = the paper colour
    ink: str = "#141414"
    accent: str = "#8a7a5e"
    muted: str = "#6b6b6b"
    cover_overlay: bool = False      # print artist/title over the cover art
    cover_scrim: bool = True         # dark gradient behind that type
    back_cover_text: bool = True     # print the colophon over the back panel
    back_scrim: bool = True          # same, behind the type on the back panel
    fonts: dict[str, Any] = field(default_factory=dict)

    def back_cover_art(self) -> str:
        """The back cover image, or "" when a solid colour is what was asked for."""
        if self.back_cover_mode.strip().casefold() == "color":
            return ""
        return self.back_cover

    def back_cover_fill(self) -> str:
        """The colour behind the colophon, or "" to fall back to the paper."""
        if self.back_cover_mode.strip().casefold() == "artwork":
            return ""
        return self.back_cover_color


@dataclass
class LayoutOptions:
    panel_mm: float = 120.0
    bleed_mm: float = 3.0
    margin_outer_mm: float = 9.0
    margin_inner_mm: float = 11.0
    margin_top_mm: float = 10.0
    margin_bottom_mm: float = 10.0
    columns: int = 2          # text columns per inside panel
    column_gap_mm: float = 6.0
    lyric_size_max: float = 10.5
    lyric_size_min: float = 7.0
    credit_size_max: float = 8.5
    credit_size_min: float = 6.0
    pack_songs: bool = True   # let a short song share a column with the next
    min_orphan_mm: float = 22.0  # don't start a song with less room than this


@dataclass
class Album:
    title: str
    artist: str
    subtitle: str = ""
    year: str = ""
    label: str = ""
    catalog: str = ""
    writers: list[Writer] = field(default_factory=list)   # credited on every track
    tracks: list[Track] = field(default_factory=list)
    personnel: list[Person] = field(default_factory=list)
    production: list[Person] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    copyright: Copyright = field(default_factory=Copyright)
    design: Design = field(default_factory=Design)
    layout: LayoutOptions = field(default_factory=LayoutOptions)
    source_dir: Path = field(default_factory=Path)

    # -- derived credit views ------------------------------------------------

    def writer_index(self) -> list[tuple[str, list[int], list[str]]]:
        """Every writer on the record, alphabetical by surname, with the tracks
        they wrote and the publishers they are affiliated with here."""
        agg: dict[str, dict[str, Any]] = {}
        for track in self.tracks:
            for w in track.writers:
                entry = agg.setdefault(
                    w.key, {"name": w.name.strip(), "tracks": [], "publishers": []}
                )
                if track.number not in entry["tracks"]:
                    entry["tracks"].append(track.number)
                pub = w.publisher_line()
                if pub and pub not in entry["publishers"]:
                    entry["publishers"].append(pub)
        rows = [(e["name"], e["tracks"], e["publishers"]) for e in agg.values()]
        rows.sort(key=lambda r: sort_key_for_name(r[0]))
        return rows

    def resolve(self, relative: str) -> Path | None:
        if not relative:
            return None
        candidate = Path(relative)
        if not candidate.is_absolute():
            candidate = self.source_dir / candidate
        return candidate


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def sort_key_for_name(name: str) -> tuple[str, str]:
    """Sort on surname where we can detect one, first name otherwise."""
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if len(parts) < 2:
        return (name.casefold(), "")
    last = parts[-1]
    if last.casefold() in _SUFFIXES and len(parts) > 2:
        last = parts[-2]
    return (last.casefold(), " ".join(parts[:-1]).casefold())


def _distinct(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in out:
            out.append(value)
    return out


def _composition_owner(album: "Album") -> str:
    """Who the © line should name: the publisher if there is one, else the
    writers, else the artist."""
    writers = list(album.writers)
    for track in album.tracks:
        if not track.inherited_writers:
            writers.extend(track.writers)

    publishers = _distinct([w.publisher for w in writers])
    if publishers:
        # More than a couple of publishers is a compilation, and no single line
        # is right for it — fall back to the artist rather than guess.
        return join_names(publishers) if len(publishers) <= 2 else album.artist.strip()

    names = _distinct([w.name for w in writers])
    if names:
        return join_names(names) if len(names) <= 2 else album.artist.strip()
    return album.artist.strip()


def derive_copyright(album: "Album") -> dict[str, str]:
    """The ℗, © and notice lines the album implies, for the ones left blank.

    Every record needs these and they are the same three sentences every time,
    so they are written from the year, the label and the songwriters rather than
    typed. Only blanks are filled — anything given by hand is left alone, and
    nothing here is written back to the file.
    """
    year = album.year.strip()
    out: dict[str, str] = {}

    if not album.copyright.phonographic:
        owner = album.label.strip() or album.artist.strip()
        if year and owner:
            out["phonographic"] = f"℗ {year} {owner}"

    if not album.copyright.composition:
        owner = _composition_owner(album)
        if year and owner:
            out["composition"] = f"© {year} {owner}"

    if not album.copyright.notice and (
        out.get("phonographic") or out.get("composition")
        or album.copyright.phonographic or album.copyright.composition
    ):
        out["notice"] = "All rights reserved."
    return out


def join_names(names: list[str]) -> str:
    """Serial-comma-free list join, as used on record sleeves."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} & {names[1]}"
    return ", ".join(names[:-1]) + f" & {names[-1]}"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _person(raw: Any) -> Person:
    """Accept either 'Jane Doe - drums' shorthand or a mapping."""
    if isinstance(raw, str):
        for sep in (" – ", " - ", ": "):
            if sep in raw:
                name, role = raw.split(sep, 1)
                return Person(name.strip(), role.strip())
        return Person(raw.strip())
    if isinstance(raw, dict):
        return Person(
            name=str(raw.get("name", "")).strip(),
            role=str(raw.get("role", "")).strip(),
            tracks=[int(t) for t in _as_list(raw.get("tracks"))],
        )
    raise ValidationError(f"cannot read a person credit from {raw!r}")


def _writer(raw: Any) -> Writer:
    if isinstance(raw, str):
        return Writer(name=raw.strip())
    if isinstance(raw, dict):
        share = raw.get("share")
        return Writer(
            name=str(raw.get("name", "")).strip(),
            role=str(raw.get("role", "")).strip(),
            share=float(share) if share is not None else None,
            publisher=str(raw.get("publisher", "")).strip(),
            pro=str(raw.get("pro", "")).strip(),
        )
    raise ValidationError(f"cannot read a writer credit from {raw!r}")


def load_album(path: str | Path, log: IssueLog | None = None) -> tuple[Album, IssueLog]:
    """Read and validate an album YAML file."""
    path = Path(path)
    if not path.exists():
        raise ValidationError(f"album file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValidationError(f"{path} does not contain a YAML mapping")

    return album_from_raw(raw, path.parent.resolve(), log)


def album_from_raw(
    raw: Any, source_dir: str | Path, log: IssueLog | None = None
) -> tuple[Album, IssueLog]:
    """Validate an already-parsed album mapping.

    Split out of ``load_album`` so an editor can validate a document it is still
    holding in memory. ``source_dir`` is what relative artwork paths resolve
    against, so an unsaved edit still finds the cover art sitting next to the
    file it came from.
    """
    log = log or IssueLog()
    if not isinstance(raw, dict):
        raise ValidationError("album data must be a mapping")
    source_dir = Path(source_dir)

    meta = raw.get("album") or {}
    if not isinstance(meta, dict):
        raise ValidationError("'album' must be a mapping")

    design_raw = {**(meta.get("design") or {}), **(raw.get("design") or {})}
    layout_raw = {**(meta.get("layout") or {}), **(raw.get("layout") or {})}

    album = Album(
        title=str(meta.get("title", "")).strip(),
        artist=str(meta.get("artist", "")).strip(),
        subtitle=str(meta.get("subtitle", "")).strip(),
        year=str(meta.get("year", "")).strip(),
        label=str(meta.get("label", "")).strip(),
        catalog=str(meta.get("catalog", "")).strip(),
        source_dir=source_dir,
    )

    known_design = {f for f in Design.__dataclass_fields__}
    for key, value in design_raw.items():
        if key in known_design:
            setattr(album.design, key, value)
        else:
            log.warn("design.unknown", f"ignoring unknown design key '{key}'")
    # `cover` is commonly written at album level; accept it there too.
    if meta.get("cover"):
        album.design.cover = str(meta["cover"])
    if meta.get("back_cover"):
        album.design.back_cover = str(meta["back_cover"])

    mode = str(album.design.back_cover_mode).strip().casefold() or "auto"
    if mode not in ("auto", "artwork", "color"):
        log.warn("design.back_cover_mode",
                 f"back_cover_mode '{album.design.back_cover_mode}' is not one of "
                 "auto, artwork, color; using auto")
        mode = "auto"
    album.design.back_cover_mode = mode

    known_layout = {f for f in LayoutOptions.__dataclass_fields__}
    for key, value in layout_raw.items():
        if key in known_layout:
            setattr(album.layout, key, value)
        else:
            log.warn("layout.unknown", f"ignoring unknown layout key '{key}'")

    try:
        columns = int(album.layout.columns)
    except (TypeError, ValueError):
        columns = 0
    if columns < 1:
        log.warn("layout.columns", f"columns must be 1 or more, got "
                                   f"{album.layout.columns!r}; using 1")
        columns = 1
    album.layout.columns = columns

    if not album.title:
        log.error("album.title", "album title is required")
    if not album.artist:
        log.error("album.artist", "album artist is required")

    # -- the album's own songwriters -----------------------------------------
    # Most records are written by one person, and repeating that on every track
    # is both tedious and a place for a typo to creep in. Writers given here are
    # credited on every track that does not name its own.
    album.writers = [_writer(w) for w in _as_list(raw.get("writers") or meta.get("writers"))]
    for w in album.writers:
        if not w.name:
            log.error("writers.name", "an album songwriter entry has no name",
                      where="songwriters")
        if w.share is not None and not (0 < w.share <= 100):
            log.error(
                "writers.share.range",
                f"{w.name or 'a writer'} has share {w.share}, expected 0 < share <= 100",
                where="songwriters",
            )

    # -- tracks --------------------------------------------------------------
    raw_tracks = _as_list(raw.get("tracks"))
    if not raw_tracks:
        log.error("tracks.empty", "the album has no tracks")

    for i, entry in enumerate(raw_tracks, start=1):
        if isinstance(entry, str):
            entry = {"title": entry}
        if not isinstance(entry, dict):
            log.error("track.shape", f"track {i} is not a mapping", where=f"track {i}")
            continue

        number = int(entry.get("number", i))
        title = str(entry.get("title", "")).strip()
        where = f"track {number}"
        if not title:
            log.error("track.title", "track has no title", where=where)
            title = f"Untitled {number}"

        writers = [_writer(w) for w in _as_list(entry.get("writers"))]
        # An entry left as a bare placeholder — no name at all — is a track the
        # user has not got to yet, not a deliberate override of the album's
        # writers, so it inherits like an empty one.
        inherited = not any(w.name for w in writers) and bool(album.writers)
        if inherited:
            writers = [replace(w) for w in album.writers]

        track = Track(
            number=number,
            title=title,
            duration=str(entry.get("duration", "")).strip(),
            writers=writers,
            inherited_writers=inherited,
            lyrics=str(entry.get("lyrics", "") or ""),
            instrumental=bool(entry.get("instrumental", False)),
            performers=[_person(p) for p in _as_list(entry.get("performers"))],
            producer=str(entry.get("producer", "")).strip(),
            arranger=str(entry.get("arranger", "")).strip(),
            recorded_at=str(entry.get("recorded_at", "")).strip(),
            notes=str(entry.get("notes", "")).strip(),
            publisher_note=str(entry.get("publisher_note", "")).strip(),
        )
        _validate_track(track, log, where)
        album.tracks.append(track)

    numbers = [t.number for t in album.tracks]
    duplicates = {n for n in numbers if numbers.count(n) > 1}
    if duplicates:
        log.error("tracks.duplicate", f"duplicate track numbers: {sorted(duplicates)}")

    # -- album-level credits -------------------------------------------------
    credits = raw.get("credits") or {}
    if not isinstance(credits, dict):
        raise ValidationError("'credits' must be a mapping")

    album.personnel = [_person(p) for p in _as_list(credits.get("personnel"))]
    album.production = [_person(p) for p in _as_list(credits.get("production"))]

    for note in _as_list(raw.get("notes")) + _as_list(credits.get("sections")):
        if isinstance(note, dict):
            album.notes.append(
                Note(str(note.get("title", "")).strip(), str(note.get("body", "") or ""))
            )
        elif isinstance(note, str):
            album.notes.append(Note("", note))

    cr = raw.get("copyright") or {}
    if isinstance(cr, dict):
        album.copyright = Copyright(
            phonographic=str(cr.get("phonographic", "")).strip(),
            composition=str(cr.get("composition", "")).strip(),
            notice=str(cr.get("notice", "")).strip(),
            extra=[str(x) for x in _as_list(cr.get("extra"))],
        )

    derived = derive_copyright(album)
    for key, text in derived.items():
        setattr(album.copyright, key, text)
        log.info("copyright.derived", f"{key} line derived as '{text}'")
    if not album.copyright.phonographic:
        log.warn("copyright.p", "no ℗ (sound recording) line given, and too little "
                                "information to derive one — a year and a label or artist")
    if not album.copyright.composition:
        log.warn("copyright.c", "no © (composition) line given, and too little "
                                "information to derive one — a year and a writer or publisher")

    # -- artwork -------------------------------------------------------------
    cover = album.resolve(album.design.cover)
    if not album.design.cover:
        log.warn("cover.missing", "no front cover artwork given; a plain cover will be set")
    elif cover is not None and not cover.exists():
        log.error("cover.notfound", f"cover artwork not found: {cover}")

    # Only checked when the back panel is actually set to carry artwork — a
    # stale path left in the file is not a problem while a colour is chosen.
    back = album.resolve(album.design.back_cover_art())
    if back is not None and not back.exists():
        log.warn("back.notfound", f"back cover artwork not found: {back}; ignoring")
        album.design.back_cover = ""

    return album, log


def _validate_track(track: Track, log: IssueLog, where: str) -> None:
    if not track.writers:
        log.error(
            "writers.missing",
            f"'{track.title}' has no songwriter credit; name a writer on the track, "
            "or give the album a songwriter for every track to inherit",
            where=where,
        )
        return

    if track.inherited_writers:
        # These came from the album, already checked once. Reporting the same
        # problem per track would bury everything else in the log.
        _validate_track_lyrics(track, log, where)
        return

    for w in track.writers:
        if not w.name:
            log.error("writers.name", "a writer entry has no name", where=where)
        if w.share is not None and not (0 < w.share <= 100):
            log.error(
                "writers.share.range",
                f"{w.name or 'a writer'} has share {w.share}, expected 0 < share <= 100",
                where=where,
            )

    names = [w.key for w in track.writers if w.name]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        log.warn(
            "writers.duplicate",
            f"'{track.title}' lists the same writer more than once: {sorted(duplicates)}",
            where=where,
        )

    shares = [w.share for w in track.writers]
    given = [s for s in shares if s is not None]
    if given and len(given) != len(shares):
        log.warn(
            "writers.share.partial",
            f"'{track.title}' gives shares for only {len(given)} of {len(shares)} writers",
            where=where,
        )
    elif given and abs(sum(given) - 100.0) > 0.05:
        log.warn(
            "writers.share.sum",
            f"'{track.title}' writer shares total {sum(given):g}%, expected 100%",
            where=where,
        )

    _validate_track_lyrics(track, log, where)


def _validate_track_lyrics(track: Track, log: IssueLog, where: str) -> None:
    if not track.lyrics.strip() and not track.instrumental:
        log.warn(
            "lyrics.missing",
            f"'{track.title}' has no lyrics and is not marked 'instrumental: true'",
            where=where,
        )
    if track.lyrics.strip() and track.instrumental:
        log.warn(
            "lyrics.instrumental",
            f"'{track.title}' is marked instrumental but has lyrics; the lyrics will be printed",
            where=where,
        )

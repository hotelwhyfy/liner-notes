"""Read a folder of audio files into the shape of a track listing.

Typing a running order out by hand is the slowest part of describing a record,
and the files themselves already know most of it: how long each one is, what it
is called, often who wrote it. This module reads that and hands back plain
mappings in album-file shape, ready to drop into ``tracks:``.

Tags are read with mutagen, which parses containers without decoding audio. It
is an optional import: without it the filenames still give a usable running
order, only without durations. Nothing here raises for one unreadable file —
a folder of forty songs should not fail on the one with a broken header.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import mutagen
except ImportError:      # pragma: no cover - exercised only where it isn't installed
    mutagen = None       # type: ignore[assignment]

# What a music folder plausibly holds. Anything mutagen can open is fair game;
# this is only used to filter a directory listing.
AUDIO_SUFFIXES = frozenset({
    ".aac", ".aif", ".aifc", ".aiff", ".ape", ".flac", ".m4a", ".m4b", ".mp3",
    ".mp4", ".oga", ".ogg", ".opus", ".wav", ".wma", ".wv",
})

# "01 ", "01. ", "01 - ", "1_" — the track number a ripper puts on the front of
# a filename, which is data we take from elsewhere and noise in a title.
_LEADING_NUMBER = re.compile(r"^\s*(\d{1,3})\s*[-._)\]]*\s+|^\s*(\d{1,3})[-._]\s*")


def available() -> bool:
    """Can durations and tags be read, or only filenames?"""
    return mutagen is not None


def format_duration(seconds: float) -> str:
    """3:42, or 1:02:30 for a record that runs over an hour."""
    if seconds <= 0:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def title_from_filename(path: Path) -> str:
    """A readable title from a filename, with the ripper's numbering removed."""
    stem = path.stem
    stem = _LEADING_NUMBER.sub("", stem, count=1)
    stem = stem.replace("_", " ")
    stem = re.sub(r"\s+", " ", stem).strip(" -–—")
    return stem or path.stem


# mutagen's ``easy=True`` normalises key names for the formats it has an easy
# variant of, and leaves the rest — WAV and AIFF among them — as raw frames. So
# each field is looked up under every name it goes by: easy, ID3, MP4, Vorbis.
_TAG_KEYS: dict[str, tuple[str, ...]] = {
    "title": ("title", "TIT2", "\xa9nam", "TITLE"),
    "artist": ("artist", "TPE1", "\xa9ART", "ARTIST", "albumartist", "TPE2"),
    "album": ("album", "TALB", "\xa9alb", "ALBUM"),
    "date": ("date", "originaldate", "TDRC", "TYER", "TDOR", "\xa9day", "DATE", "YEAR"),
    "tracknumber": ("tracknumber", "TRCK", "trkn", "TRACKNUMBER"),
    "discnumber": ("discnumber", "TPOS", "disk", "DISCNUMBER"),
    "composer": ("composer", "TCOM", "\xa9wrt", "COMPOSER"),
}


def _flatten(value: Any) -> str:
    """One string out of whatever shape a tag came back as.

    Tag values are lists, frames, or — for MP4 track numbers — a list holding a
    (number, total) tuple. Taking the first element until something scalar turns
    up handles all of them.
    """
    if isinstance(value, (list, tuple)):
        return _flatten(value[0]) if value else ""
    return str(value).strip()


def _tag(tags: Any, field: str) -> str:
    """One field, whatever this format calls it."""
    if not tags:
        return ""
    for key in _TAG_KEYS[field]:
        try:
            value = tags.get(key)
        except Exception:      # noqa: BLE001 - some formats raise rather than miss
            continue
        if value is None or value == []:
            continue
        text = _flatten(value)
        if text:
            return text
    return ""


def _leading_int(text: str) -> int | None:
    """'3', '3/12' and '03' all mean track three."""
    match = re.match(r"\s*(\d+)", text or "")
    return int(match.group(1)) if match else None


def _year_of(text: str) -> str:
    match = re.search(r"(\d{4})", text or "")
    return match.group(1) if match else ""


@dataclass
class AudioFile:
    """One file on disk, as far as its tags describe it."""

    path: Path
    title: str = ""
    duration: str = ""
    seconds: float = 0.0
    number: int | None = None
    disc: int | None = None
    artist: str = ""
    album: str = ""
    year: str = ""
    composers: list[str] = field(default_factory=list)
    unreadable: bool = False   # tags could not be parsed; filename only

    def to_track(self, number: int) -> dict:
        """The mapping this file contributes to ``tracks:``.

        Writers are deliberately absent: a track with none inherits the album's
        songwriters, which is the whole point of importing rather than typing.
        Composer tags come back separately, as a suggestion for that default.
        """
        track: dict[str, Any] = {"title": self.title, "number": number}
        if self.duration:
            track["duration"] = self.duration
        return track


def read_file(path: Path) -> AudioFile:
    """Read one file. Never raises — a bad header downgrades to the filename."""
    item = AudioFile(path=path, title=title_from_filename(path))
    if mutagen is None:
        return item

    try:
        handle = mutagen.File(str(path), easy=True)
    except Exception:      # noqa: BLE001 - unreadable is a state, not a failure
        handle = None
    if handle is None:
        item.unreadable = True
        return item

    info = getattr(handle, "info", None)
    length = float(getattr(info, "length", 0.0) or 0.0)
    item.seconds = length
    item.duration = format_duration(length)

    tags = getattr(handle, "tags", None)
    tagged_title = _tag(tags, "title")
    if tagged_title:
        item.title = tagged_title
    item.artist = _tag(tags, "artist")
    item.album = _tag(tags, "album")
    item.year = _year_of(_tag(tags, "date"))
    item.number = _leading_int(_tag(tags, "tracknumber"))
    item.disc = _leading_int(_tag(tags, "discnumber"))

    composer = _tag(tags, "composer")
    if composer:
        # "A/B" and "A; B" are both common ways to write a pair of writers.
        item.composers = [p.strip() for p in re.split(r"\s*[;/]\s*", composer) if p.strip()]
    return item


def _natural_key(path: Path) -> list:
    """Sort 'track2' before 'track10' when there are no tags to go on."""
    parts = re.split(r"(\d+)", path.name.casefold())
    return [int(p) if p.isdigit() else p for p in parts]


def collect_paths(paths: Iterable[str | Path]) -> list[Path]:
    """Expand a selection into audio files: directories one level deep."""
    found: list[Path] = []
    for entry in paths:
        path = Path(entry)
        if path.is_dir():
            found.extend(
                child for child in sorted(path.iterdir())
                if child.is_file() and child.suffix.casefold() in AUDIO_SUFFIXES
            )
        elif path.is_file():
            found.append(path)
    # A file picked twice — once directly, once inside a chosen folder — is one
    # track, not two.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


@dataclass
class ImportResult:
    """What a folder of audio came to."""

    files: list[AudioFile] = field(default_factory=list)
    tracks: list[dict] = field(default_factory=list)
    artist: str = ""
    album: str = ""
    year: str = ""
    composers: list[str] = field(default_factory=list)   # shared by every file
    total_seconds: float = 0.0

    @property
    def has_durations(self) -> bool:
        return any(f.duration for f in self.files)

    @property
    def unreadable(self) -> list[Path]:
        return [f.path for f in self.files if f.unreadable]

    def summary(self) -> str:
        count = len(self.tracks)
        text = f"{count} track{'s' if count != 1 else ''}"
        if self.total_seconds:
            text += f", {format_duration(self.total_seconds)} total"
        elif not available():
            text += " (durations need the 'mutagen' package)"
        return text


def _shared(values: list[str]) -> str:
    """The one value every file agrees on, or nothing."""
    seen = {v for v in values if v}
    return seen.pop() if len(seen) == 1 else ""


def import_audio(paths: Iterable[str | Path], start_number: int = 1) -> ImportResult:
    """Read a selection of files or folders into track mappings.

    Ordered by the track numbers in the tags when every file has one, and by
    filename otherwise — a folder ripped in order sorts correctly either way.
    """
    files = [read_file(path) for path in collect_paths(paths)]

    if files and all(f.number is not None for f in files):
        files.sort(key=lambda f: (f.disc or 1, f.number or 0))
    else:
        files.sort(key=lambda f: _natural_key(f.path))

    result = ImportResult(files=files)
    result.tracks = [f.to_track(start_number + i) for i, f in enumerate(files)]
    result.total_seconds = sum(f.seconds for f in files)
    result.artist = _shared([f.artist for f in files])
    result.album = _shared([f.album for f in files])
    result.year = _shared([f.year for f in files])

    # Only offer a songwriter every file agrees on. A mixed set is a record with
    # more than one writer, and guessing one of them would be worse than asking.
    tagged = [f.composers for f in files if f.composers]
    if tagged and len(tagged) == len(files) and all(c == tagged[0] for c in tagged):
        result.composers = list(tagged[0])
    return result

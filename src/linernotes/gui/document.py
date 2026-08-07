"""The editor's document: the album mapping, as read from and written to YAML.

The editor works on the *raw mapping*, not on the ``Album`` dataclasses. Loading
is deliberately lossy — ``Jane Doe - drums`` expands into a Person, unknown keys
are dropped, shorthand is normalised — so round-tripping a file through the
dataclasses would quietly rewrite whatever the user actually typed. Holding the
mapping keeps the file theirs, and means validation runs over exactly the bytes
that will be saved.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..build import plan_from_album
from ..content import Booklet
from ..errors import IssueLog, LinerNotesError
from ..model import album_from_raw

# The order a hand-written album file tends to be organised in. Saving follows
# it so a file the editor touched still reads like one a person wrote.
TOP_LEVEL_ORDER = ("album", "design", "layout", "tracks", "credits", "notes", "copyright")


class _AlbumDumper(yaml.SafeDumper):
    """A dumper that writes lyrics as block scalars.

    Subclassed rather than configured globally so registering the representer
    cannot leak into anything else in the process that uses PyYAML.
    """


def _represent_str(dumper: yaml.SafeDumper, data: str):
    # Multi-line text — lyrics, liner notes — is the whole reason the album file
    # is YAML and not JSON. Keep it as a block scalar rather than one long
    # quoted string with escapes in it.
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_AlbumDumper.add_representer(str, _represent_str)


def _is_empty(value: Any) -> bool:
    """Nothing worth writing to disk.

    Deliberately narrow: ``False`` and ``0`` are real values a user chose, and
    only ``None`` and empty containers count as absent.
    """
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def _pruned(value: Any) -> Any:
    """Drop empty entries so a saved file stays readable.

    A field the user cleared should disappear and fall back to its default
    rather than being written out as ``subtitle: ''``.
    """
    if isinstance(value, dict):
        items = ((k, _pruned(v)) for k, v in value.items())
        return {k: v for k, v in items if not _is_empty(v)}
    if isinstance(value, list):
        items = (_pruned(v) for v in value)
        return [v for v in items if not _is_empty(v)]
    return value


def new_album_data() -> dict:
    """A blank document with just enough shape to start typing into."""
    return {
        "album": {"title": "Untitled", "artist": "", "year": ""},
        "tracks": [],
        "credits": {"personnel": [], "production": []},
        "notes": [],
        "copyright": {},
    }


@dataclass
class BuildOutcome:
    """The result of trying to lay the current document out.

    ``log`` is always populated, even on failure — the issues collected before
    the pipeline gave up are the most useful thing to show the user.
    """

    log: IssueLog
    booklet: Booklet | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.booklet is not None


class AlbumDocument:
    """An album file open in the editor."""

    def __init__(self, data: dict | None = None, path: str | Path | None = None):
        self.data: dict = data if data is not None else new_album_data()
        self.path: Path | None = Path(path) if path else None
        self._saved = _pruned(self.data)

    # -- identity ------------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path) -> "AlbumDocument":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            raw = new_album_data()
        if not isinstance(raw, dict):
            raise LinerNotesError(f"{path.name} does not contain a YAML mapping")
        return cls(raw, path)

    @property
    def dirty(self) -> bool:
        """Would saving actually change the file?

        Compared against the *pruned* form, not the raw mapping. Panes create
        the containers they bind to — opening the Layout pane puts an empty
        ``layout: {}`` in the document — and that scaffolding is dropped on the
        way out again. Comparing raw structure would mark a file dirty for
        having been looked at.
        """
        return _pruned(self.data) != self._saved

    @property
    def display_name(self) -> str:
        return self.path.name if self.path else "Untitled album"

    @property
    def source_dir(self) -> Path:
        """What relative artwork paths resolve against.

        An unsaved document has no directory of its own, so it borrows the
        working directory — which keeps ``resolve`` total instead of optional.
        """
        return self.path.parent if self.path else Path.cwd()

    @property
    def stem(self) -> str:
        """A filename stem for exports, from the artist and title."""
        meta = self.section("album")
        parts = [str(meta.get("artist", "")).strip(), str(meta.get("title", "")).strip()]
        text = "-".join(p for p in parts if p)
        out: list[str] = []
        for ch in text.casefold():
            if ch.isalnum():
                out.append(ch)
            elif out and out[-1] != "-":
                out.append("-")
        return "".join(out).strip("-") or "booklet"

    # -- persistence ---------------------------------------------------------

    def save(self, path: str | Path | None = None) -> Path:
        if path is not None:
            self.path = Path(path)
        if self.path is None:
            raise LinerNotesError("no path to save to")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.to_yaml(), encoding="utf-8")
        self._saved = _pruned(self.data)
        return self.path

    def to_yaml(self) -> str:
        data = _pruned(self.data)
        ordered = {k: data[k] for k in TOP_LEVEL_ORDER if k in data}
        ordered.update({k: v for k, v in data.items() if k not in ordered})
        return yaml.dump(
            ordered,
            Dumper=_AlbumDumper,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=88,
        )

    # -- accessors the editor panes work through -----------------------------

    def section(self, name: str) -> dict:
        """A top-level mapping, created on demand so panes can bind to it."""
        value = self.data.get(name)
        if not isinstance(value, dict):
            value = {}
            self.data[name] = value
        return value

    def collection(self, name: str) -> list:
        """A top-level list, created on demand."""
        value = self.data.get(name)
        if not isinstance(value, list):
            value = []
            self.data[name] = value
        return value

    @property
    def tracks(self) -> list:
        return self.collection("tracks")

    def credits(self, kind: str) -> list:
        """``personnel`` or ``production`` under the credits mapping."""
        credits = self.section("credits")
        value = credits.get(kind)
        if not isinstance(value, list):
            value = []
            credits[kind] = value
        return value

    # -- building ------------------------------------------------------------

    def plan(self) -> BuildOutcome:
        """Validate and lay out the document as it currently stands.

        Never raises: the editor rebuilds on every keystroke, and a document
        mid-edit is invalid more often than not.
        """
        log = IssueLog()
        try:
            # The loader is handed a copy so nothing it does can reach back into
            # the mapping the user is still typing into.
            album, log = album_from_raw(copy.deepcopy(self.data), self.source_dir, log)
            booklet, log = plan_from_album(album, log)
            return BuildOutcome(log=log, booklet=booklet)
        except LinerNotesError as exc:
            return BuildOutcome(log=log, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - the editor must survive anything
            return BuildOutcome(log=log, error=f"{type(exc).__name__}: {exc}")

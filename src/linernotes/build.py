"""The build pipeline: album file in, PDFs out."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .content import Booklet, build_booklet
from .errors import IssueLog
from .imposition import render_press, sheet_order
from .layout import Geometry
from .model import Album, load_album
from .render import render_reader
from .text import build_fonts


@dataclass
class BuildResult:
    album: Album
    booklet: Booklet
    log: IssueLog
    outputs: list[Path] = field(default_factory=list)

    @property
    def panel_count(self) -> int:
        return len(self.booklet.panels)

    @property
    def sheet_count(self) -> int:
        return self.panel_count // 4


def plan_booklet(album_path: str | Path, log: IssueLog | None = None) -> tuple[Booklet, IssueLog]:
    """Load, validate and lay out an album without writing any PDFs."""
    album, log = load_album(album_path, log)
    return plan_from_album(album, log)


def plan_from_album(album: Album, log: IssueLog | None = None) -> tuple[Booklet, IssueLog]:
    """Lay out an Album that has already been loaded.

    The editor holds its album in memory rather than on disk, so it needs the
    half of the pipeline that starts after parsing.
    """
    log = log or IssueLog()
    log.raise_if_errors()
    fonts = build_fonts(album.design.fonts, log)
    geo = Geometry.from_options(album.layout)
    booklet = build_booklet(album, geo, fonts, log)
    return booklet, log


def build(
    album_path: str | Path,
    out_dir: str | Path = "out",
    reader: bool = True,
    press: bool = True,
    folios: bool = False,
    marks: bool = True,
) -> BuildResult:
    booklet, log = plan_booklet(album_path)
    out_dir = Path(out_dir)
    stem = _slug(f"{booklet.album.artist}-{booklet.album.title}")

    outputs: list[Path] = []
    if reader:
        outputs.append(render_reader(booklet, out_dir / f"{stem}-reader.pdf", folios=folios))
    if press:
        # Fails loudly rather than emitting an unprintable sheet count.
        sheet_order(len(booklet.panels))
        outputs.append(render_press(booklet, out_dir / f"{stem}-press.pdf", marks=marks))

    return BuildResult(album=booklet.album, booklet=booklet, log=log, outputs=outputs)


def _slug(text: str) -> str:
    out: list[str] = []
    for ch in text.strip().casefold():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "booklet"

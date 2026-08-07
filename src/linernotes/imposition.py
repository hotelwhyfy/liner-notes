"""Saddle-stitch imposition: arrange panels onto folded press sheets.

A jewel-case booklet is a stack of sheets folded down the middle and stapled at
the fold. That means panel 1 (the front cover) shares a sheet side with the very
last panel, panel 2 shares with the second-to-last, and so on inward. Sheets are
printed two-up, double-sided.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from .content import Booklet
from .layout import Geometry, Panel, Style, Text, hex_to_rgb
from .render import PanelRenderer


@dataclass
class SheetSide:
    sheet: int          # 1-based sheet number
    side: str           # "front" | "back"
    left: int           # panel index printed on the left half
    right: int          # panel index printed on the right half

    @property
    def caption(self) -> str:
        return f"sheet {self.sheet} {self.side} — panels {self.left} | {self.right}"


def sheet_order(panel_count: int) -> list[SheetSide]:
    """Panel indices for each side of each folded sheet, outermost sheet first."""
    if panel_count <= 0 or panel_count % 4 != 0:
        raise ValueError(f"panel count must be a positive multiple of 4, got {panel_count}")

    sides: list[SheetSide] = []
    n = panel_count
    for i in range(n // 4):
        sheet = i + 1
        sides.append(SheetSide(sheet, "front", left=n - 2 * i, right=1 + 2 * i))
        sides.append(SheetSide(sheet, "back", left=2 + 2 * i, right=n - 1 - 2 * i))
    return sides


# ---------------------------------------------------------------------------
# Rendering press sheets
# ---------------------------------------------------------------------------


def render_press(
    booklet: Booklet,
    out_path: str | Path,
    marks: bool = True,
    captions: bool = True,
) -> Path:
    geo = booklet.geo
    panels: dict[int, Panel] = {p.index: p for p in booklet.panels}
    sides = sheet_order(len(booklet.panels))

    trim_w = geo.panel_w * 2
    trim_h = geo.panel_h
    slug = geo.press_margin
    page_w = trim_w + 2 * slug
    page_h = trim_h + 2 * slug

    paper = hex_to_rgb(booklet.album.design.background)
    renderer = PanelRenderer(geo, paper)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(out_path), pagesize=(page_w, page_h))
    c.setTitle(f"{booklet.album.artist} — {booklet.album.title} (press sheets)")
    c.setAuthor(booklet.album.artist)
    c.setSubject(
        f"CD booklet, saddle-stitch imposition, {len(booklet.panels)} panels, "
        f"{geo.bleed / mm:g}mm bleed"
    )

    for side in sides:
        # The fold runs down the page centre, so the left half bleeds left and
        # the right half bleeds right; neither bleeds across the fold.
        renderer.draw(c, panels[side.left], slug, slug, ("left", "top", "bottom"))
        renderer.draw(
            c, panels[side.right], slug + geo.panel_w, slug, ("right", "top", "bottom")
        )
        if marks:
            _crop_marks(c, geo, slug, slug, trim_w, trim_h)
            _fold_marks(c, geo, slug, slug, trim_w, trim_h)
        if captions:
            _caption(c, geo, side, slug, page_w, page_h)
        c.showPage()

    c.save()
    return out_path


def _crop_marks(
    c: pdfcanvas.Canvas, geo: Geometry, x: float, y: float, w: float, h: float
) -> None:
    """Corner marks sitting outside the bleed, aligned to the trim edges."""
    start = geo.bleed + geo.crop_gap
    end = start + geo.crop_len
    c.saveState()
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.25)
    for cx, sx in ((x, -1), (x + w, 1)):
        for cy, sy in ((y, -1), (y + h, 1)):
            c.line(cx + sx * start, cy, cx + sx * end, cy)   # horizontal arm
            c.line(cx, cy + sy * start, cx, cy + sy * end)   # vertical arm
    c.restoreState()


def _fold_marks(
    c: pdfcanvas.Canvas, geo: Geometry, x: float, y: float, w: float, h: float
) -> None:
    """Dashed ticks marking the fold, plus a faint dashed line across the sheet
    so the fold position is unmistakable on a proof."""
    cx = x + w / 2.0
    start = geo.bleed + geo.crop_gap
    end = start + geo.crop_len
    c.saveState()
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.25)
    c.line(cx, y - start, cx, y - end)
    c.line(cx, y + h + start, cx, y + h + end)
    c.setDash(1.2, 2.4)
    c.setStrokeColorRGB(0.6, 0.6, 0.6)
    c.setLineWidth(0.2)
    c.line(cx, y, cx, y + h)
    c.restoreState()


def _caption(
    c: pdfcanvas.Canvas,
    geo: Geometry,
    side: SheetSide,
    slug: float,
    page_w: float,
    page_h: float,
) -> None:
    style = Style(
        font="Helvetica",
        size=5.5,
        color=(0.45, 0.45, 0.45),
        align="center",
        collapse=True,
    )
    text = f"{side.caption}   ·   fold at centre, staple at fold   ·   not for trim"
    Text(text, style).draw(c, slug, slug * 0.42, page_w - 2 * slug)

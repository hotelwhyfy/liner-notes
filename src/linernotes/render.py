"""Drawing panels onto a ReportLab canvas."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

from .content import Booklet
from .layout import Background, Geometry, Panel, Style, Text

_image_cache: dict[str, ImageReader] = {}


def _image(path: str) -> ImageReader:
    reader = _image_cache.get(path)
    if reader is None:
        reader = ImageReader(path)
        _image_cache[path] = reader
    return reader


class PanelRenderer:
    """Draws a panel at an arbitrary origin, optionally extending its artwork
    past the trim on chosen edges so a press sheet has bleed to cut into."""

    def __init__(self, geo: Geometry, paper: tuple[float, float, float]):
        self.geo = geo
        self.paper = paper

    def draw(
        self,
        c: pdfcanvas.Canvas,
        panel: Panel,
        ox: float,
        oy: float,
        bleed_edges: tuple[str, ...] = (),
    ) -> None:
        geo = self.geo
        bleed = geo.bleed if bleed_edges else 0.0
        x = ox - (bleed if "left" in bleed_edges else 0.0)
        y = oy - (bleed if "bottom" in bleed_edges else 0.0)
        w = geo.panel_w + (bleed if "left" in bleed_edges else 0.0) + (
            bleed if "right" in bleed_edges else 0.0
        )
        h = geo.panel_h + (bleed if "bottom" in bleed_edges else 0.0) + (
            bleed if "top" in bleed_edges else 0.0
        )

        self._background(c, panel.background, x, y, w, h)

        for item in panel.items:
            item.block.draw(c, ox + item.x, oy + item.top, item.width)

    # -- background ----------------------------------------------------------

    def _background(
        self,
        c: pdfcanvas.Canvas,
        bg: Background | None,
        x: float,
        y: float,
        w: float,
        h: float,
    ) -> None:
        color = (bg.color if bg and bg.color else self.paper)
        c.saveState()
        c.setFillColorRGB(*color)
        c.rect(x, y, w, h, stroke=0, fill=1)
        c.restoreState()

        if not bg or not bg.image:
            return

        path = Path(bg.image)
        if not path.exists():
            return

        c.saveState()
        c.clipPath(_rect_path(c, x, y, w, h), stroke=0, fill=0)
        iw, ih = _image(str(path)).getSize()
        if iw > 0 and ih > 0:
            if bg.fit == "contain":
                scale = min(w / iw, h / ih)
            else:
                scale = max(w / iw, h / ih)
            dw, dh = iw * scale, ih * scale
            c.drawImage(
                _image(str(path)),
                x + (w - dw) / 2.0,
                y + (h - dh) / 2.0,
                width=dw,
                height=dh,
                mask="auto",
            )
        c.restoreState()

        if bg.scrim:
            self._scrim(c, x, y, w, h, bg.scrim_height)

    def _scrim(
        self, c: pdfcanvas.Canvas, x: float, y: float, w: float, h: float,
        fraction: float = 0.52,
    ) -> None:
        """A soft dark gradient at the foot of a panel so white type stays
        legible over whatever the artwork happens to be doing there.

        ``fraction`` is how far up the panel it reaches: enough to sit behind a
        cover title, or the whole panel for a back cover carrying text at both
        the middle and the foot.
        """
        bands = 48
        band_h = h * max(0.0, min(fraction, 1.0)) / bands
        c.saveState()
        c.setFillColorRGB(0, 0, 0)
        for i in range(bands):
            alpha = 0.72 * ((bands - i) / bands) ** 2.1
            c.setFillAlpha(alpha)
            c.rect(x, y + i * band_h, w, band_h * 1.04, stroke=0, fill=1)
        c.restoreState()


def _rect_path(c: pdfcanvas.Canvas, x: float, y: float, w: float, h: float):
    path = c.beginPath()
    path.rect(x, y, w, h)
    return path


# ---------------------------------------------------------------------------
# Reader PDF
# ---------------------------------------------------------------------------


def render_reader(booklet: Booklet, out_path: str | Path, folios: bool = False) -> Path:
    """One panel per page, in reading order — for proofing on screen."""
    geo = booklet.geo
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    from .layout import hex_to_rgb

    paper = hex_to_rgb(booklet.album.design.background)
    renderer = PanelRenderer(geo, paper)

    c = pdfcanvas.Canvas(str(out_path), pagesize=(geo.panel_w, geo.panel_h))
    c.setTitle(f"{booklet.album.artist} — {booklet.album.title} (booklet)")
    c.setAuthor(booklet.album.artist)
    c.setSubject("CD booklet, reading order")

    for panel in booklet.panels:
        renderer.draw(c, panel, 0.0, 0.0)
        if folios:
            _folio(c, geo, panel, hex_to_rgb(booklet.album.design.muted))
        c.showPage()
    c.save()
    return out_path


def _folio(c: pdfcanvas.Canvas, geo: Geometry, panel: Panel, color) -> None:
    style = Style(font="Helvetica", size=6.0, color=color, align="center", collapse=True)
    label = f"{panel.index}" + (f"  ·  {panel.label}" if panel.label else "")
    block = Text(label, style)
    block.draw(c, geo.margin_outer, geo.margin_bottom * 0.72, geo.panel_w - 2 * geo.margin_outer)

"""Panel preview: rasterise one laid-out panel and show it in Tk.

reportlab only writes PDF, and Tk cannot display PDF, so a preview goes
panel -> single-page PDF in memory -> PyMuPDF pixmap -> PNG bytes -> PhotoImage.
Nothing touches the disk; the panel drawn here goes through exactly the same
``PanelRenderer`` as the exported file, so what you see is what prints.
"""

from __future__ import annotations

import io
import tkinter as tk
from tkinter import ttk

import pymupdf
from reportlab.pdfgen import canvas as pdfcanvas

from ..content import Booklet
from ..layout import hex_to_rgb
from ..render import PanelRenderer

MIN_DPI = 36.0
MAX_DPI = 300.0


def render_panel_png(booklet: Booklet, index: int, dpi: float) -> bytes:
    """Rasterise one panel, 0-based, to PNG bytes."""
    geo = booklet.geo
    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=(geo.panel_w, geo.panel_h))
    renderer = PanelRenderer(geo, hex_to_rgb(booklet.album.design.background))
    renderer.draw(c, booklet.panels[index], 0.0, 0.0)
    c.showPage()
    c.save()

    doc = pymupdf.open(stream=buffer.getvalue(), filetype="pdf")
    try:
        return doc[0].get_pixmap(dpi=round(dpi)).tobytes("png")
    finally:
        doc.close()


class PreviewPane(ttk.Frame):
    """A single panel, scaled to fit, with panel-by-panel navigation."""

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self.booklet: Booklet | None = None
        self.index = 0
        self.pinned = False      # the user paged here; stop following the editor
        self._photo: tk.PhotoImage | None = None   # kept alive against GC
        self._resize_job: str | None = None
        self._last_size = (0, 0)

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(fill="x")
        self._prev = ttk.Button(bar, text="‹", width=3, command=self.previous)
        self._prev.pack(side="left")
        self._next = ttk.Button(bar, text="›", width=3, command=self.next)
        self._next.pack(side="left", padx=(4, 10))
        self._caption = ttk.Label(bar, text="no preview", anchor="w")
        self._caption.pack(side="left", fill="x", expand=True)

        self.canvas = tk.Canvas(self, highlightthickness=0, background="#3a3a3c")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)

        self._message: str | None = "Open or create an album to see a preview."
        self._refresh_controls()

    # -- state ---------------------------------------------------------------

    def show(self, booklet: Booklet) -> None:
        """Display a freshly planned booklet, holding position where possible.

        The panel being looked at usually survives a rebuild — the user is
        typing into it — so the index is clamped rather than reset.
        """
        self.booklet = booklet
        self._message = None
        self.index = max(0, min(self.index, len(booklet.panels) - 1))
        self._redraw()

    def show_message(self, message: str) -> None:
        """Replace the preview with a message — a build failed, or is empty."""
        self.booklet = None
        self._message = message
        self._redraw()

    def previous(self) -> None:
        if self.booklet and self.index > 0:
            self.index -= 1
            self.pinned = True
            self._redraw()

    def next(self) -> None:
        if self.booklet and self.index < len(self.booklet.panels) - 1:
            self.index += 1
            self.pinned = True
            self._redraw()

    def go_to(self, index: int) -> None:
        """Move to a panel and stay there until told otherwise."""
        self.pinned = True
        self._move_to(index)

    def follow(self, index: int) -> None:
        """Move to the panel the editor is working on.

        Ignored once the user has paged by hand: they went looking for a panel
        and having it slide away on the next keystroke would be maddening.
        Selecting something in the navigator is what hands control back.
        """
        if not self.pinned:
            self._move_to(index)

    def unpin(self) -> None:
        self.pinned = False

    def _move_to(self, index: int) -> None:
        if self.booklet and 0 <= index < len(self.booklet.panels) and index != self.index:
            self.index = index
            self._redraw()

    # -- drawing -------------------------------------------------------------

    def _on_resize(self, event: tk.Event) -> None:
        # Rasterising on every pixel of a window drag is wasteful; settle first.
        if (event.width, event.height) == self._last_size:
            return
        self._last_size = (event.width, event.height)
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._redraw)

    def _redraw(self) -> None:
        self._resize_job = None
        self.canvas.delete("all")
        self._refresh_controls()

        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return   # not laid out yet; the Configure event will bring us back

        if self.booklet is None:
            self.canvas.create_text(
                width / 2, height / 2,
                text=self._message or "no preview",
                fill="#d0d0d2", width=max(120, width - 60), justify="center",
            )
            return

        geo = self.booklet.geo
        margin = 24
        avail_w = max(1, width - 2 * margin)
        avail_h = max(1, height - 2 * margin)
        scale = min(avail_w / geo.panel_w, avail_h / geo.panel_h)
        dpi = max(MIN_DPI, min(MAX_DPI, scale * 72.0))

        try:
            png = render_panel_png(self.booklet, self.index, dpi)
        except Exception as exc:  # noqa: BLE001 - a preview must never take the app down
            self.canvas.create_text(
                width / 2, height / 2,
                text=f"could not draw this panel\n{type(exc).__name__}: {exc}",
                fill="#ffb4b4", width=max(120, width - 60), justify="center",
            )
            return

        self._photo = tk.PhotoImage(data=png)
        cx, cy = width / 2, height / 2
        half_w = self._photo.width() / 2
        half_h = self._photo.height() / 2
        # A hairline around the paper so a white panel reads as a page.
        self.canvas.create_rectangle(
            cx - half_w - 1, cy - half_h - 1, cx + half_w + 1, cy + half_h + 1,
            outline="#1e1e20", width=1,
        )
        self.canvas.create_image(cx, cy, image=self._photo)

    def _refresh_controls(self) -> None:
        if not self.booklet:
            self._caption.configure(text=self._message and "" or "no preview")
            self._prev.state(["disabled"])
            self._next.state(["disabled"])
            return
        panel = self.booklet.panels[self.index]
        total = len(self.booklet.panels)
        label = panel.label or panel.kind
        self._caption.configure(text=f"panel {panel.index} of {total}  ·  {label}")
        self._prev.state(["!disabled"] if self.index > 0 else ["disabled"])
        self._next.state(["!disabled"] if self.index < total - 1 else ["disabled"])

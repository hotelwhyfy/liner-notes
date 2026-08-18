"""Panel geometry, drawable blocks, and the panel planner.

Coordinates: every ``Block`` is drawn from its *top* edge downward. The renderer
passes the PDF y-coordinate of that top edge, so a block's own height never has
to be known by the caller before drawing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Protocol

from reportlab.lib.units import mm

from .errors import IssueLog, LayoutError
from .text import line_width, wrap_paragraph


def hex_to_rgb(value: str | tuple[float, float, float]) -> tuple[float, float, float]:
    if isinstance(value, (tuple, list)):
        return tuple(float(c) for c in value)  # type: ignore[return-value]
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def is_dark(rgb: tuple[float, float, float]) -> bool:
    """Would light type read better than dark type on this colour?

    Perceived brightness, so a saturated blue counts as dark and a yellow of the
    same numeric value does not.
    """
    r, g, b = rgb
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 0.5


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Geometry:
    """All panel measurements, in points."""

    panel_w: float
    panel_h: float
    bleed: float
    margin_outer: float
    margin_inner: float
    margin_top: float
    margin_bottom: float
    crop_len: float = 4.0 * mm
    crop_gap: float = 2.0 * mm

    @classmethod
    def from_options(cls, opts) -> "Geometry":
        return cls(
            panel_w=opts.panel_mm * mm,
            panel_h=opts.panel_mm * mm,
            bleed=opts.bleed_mm * mm,
            margin_outer=opts.margin_outer_mm * mm,
            margin_inner=opts.margin_inner_mm * mm,
            margin_top=opts.margin_top_mm * mm,
            margin_bottom=opts.margin_bottom_mm * mm,
        )

    @property
    def content_w(self) -> float:
        """Constant across panels — only the x offset flips by page side, which
        keeps a block wrapped for one panel valid on any other."""
        return self.panel_w - self.margin_inner - self.margin_outer

    @property
    def content_h(self) -> float:
        return self.panel_h - self.margin_top - self.margin_bottom

    def content_x(self, panel_index: int) -> float:
        """Odd panels are right-hand pages, so their inner margin is on the left."""
        return self.margin_inner if panel_index % 2 == 1 else self.margin_outer

    def content_top(self) -> float:
        """PDF y of the top of the text area, relative to the panel's bottom edge."""
        return self.margin_bottom + self.content_h

    @property
    def press_margin(self) -> float:
        """Space outside the trim on a press sheet: bleed plus room for marks."""
        return self.bleed + self.crop_gap + self.crop_len + 2.0 * mm


# ---------------------------------------------------------------------------
# Text styles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Style:
    font: str
    size: float
    leading: float | None = None
    color: tuple[float, float, float] = (0.08, 0.08, 0.08)
    tracking: float = 0.0
    align: str = "left"          # left | center | right
    space_before: float = 0.0
    space_after: float = 0.0
    uppercase: bool = False
    collapse: bool = False       # treat newlines as spaces
    hanging_indent: float = 0.0
    blank_line_ratio: float = 0.62   # height of an empty line, relative to leading

    @property
    def line_height(self) -> float:
        return self.leading if self.leading is not None else self.size * 1.34


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


class Block(Protocol):
    def height(self, width: float) -> float: ...
    def split(self, width: float, available: float) -> tuple["Block | None", "Block | None"]: ...
    def draw(self, canvas, x: float, top: float, width: float) -> None: ...


@dataclass
class Spacer:
    amount: float
    soft: bool = True   # a soft spacer disappears when it lands at a panel break

    def height(self, width: float) -> float:
        return self.amount

    def split(self, width: float, available: float):
        if available >= self.amount:
            return self, None
        return (None, None) if self.soft else (None, self)

    def draw(self, canvas, x: float, top: float, width: float) -> None:
        return None


@dataclass
class Rule:
    color: tuple[float, float, float] = (0.7, 0.7, 0.7)
    thickness: float = 0.5
    width_frac: float = 1.0
    space_before: float = 0.0
    space_after: float = 0.0
    align: str = "left"

    def height(self, width: float) -> float:
        return self.space_before + self.thickness + self.space_after

    def split(self, width: float, available: float):
        return (self, None) if available >= self.height(width) else (None, self)

    def draw(self, canvas, x: float, top: float, width: float) -> None:
        w = width * self.width_frac
        if self.align == "center":
            x += (width - w) / 2.0
        elif self.align == "right":
            x += width - w
        y = top - self.space_before - self.thickness / 2.0
        canvas.saveState()
        canvas.setStrokeColorRGB(*self.color)
        canvas.setLineWidth(self.thickness)
        canvas.line(x, y, x + w, y)
        canvas.restoreState()


@dataclass
class Text:
    """A run of text, wrapped lazily and cached per width."""

    text: str
    style: Style
    _lines: list[str] | None = field(default=None, repr=False)
    _lines_width: float | None = field(default=None, repr=False)

    def lines(self, width: float) -> list[str]:
        if self._lines is None or self._lines_width != width:
            source = self.text.upper() if self.style.uppercase else self.text
            self._lines = wrap_paragraph(
                source,
                self.style.font,
                self.style.size,
                width,
                tracking=self.style.tracking,
                hanging_indent=self.style.hanging_indent,
                collapse=self.style.collapse,
            )
            self._lines_width = width
        return self._lines

    def _stack_height(self, lines: Iterable[str]) -> float:
        lh = self.style.line_height
        return sum(lh if ln else lh * self.style.blank_line_ratio for ln in lines)

    def height(self, width: float) -> float:
        lines = self.lines(width)
        if not lines:
            return 0.0
        return self.style.space_before + self._stack_height(lines) + self.style.space_after

    def split(self, width: float, available: float):
        lines = self.lines(width)
        if not lines:
            return None, None
        if self.height(width) <= available:
            return self, None

        budget = available - self.style.space_before
        lh = self.style.line_height
        taken = 0
        used = 0.0
        for ln in lines:
            step = lh if ln else lh * self.style.blank_line_ratio
            if used + step > budget:
                break
            used += step
            taken += 1

        # Widow and orphan control: never strand a single line on either side.
        if len(lines) - taken == 1:
            taken -= 1
        if taken == 1:
            taken = 0
        if taken <= 0:
            return None, self

        head = _prewrapped(lines[:taken], replace(self.style, space_after=0.0), width)
        tail_lines = lines[taken:]
        while tail_lines and not tail_lines[0]:
            tail_lines.pop(0)
        if not tail_lines:
            return head, None
        tail = _prewrapped(tail_lines, replace(self.style, space_before=0.0), width)
        return head, tail

    def draw(self, canvas, x: float, top: float, width: float) -> None:
        lines = self.lines(width)
        if not lines:
            return
        st = self.style
        lh = st.line_height
        canvas.saveState()
        canvas.setFillColorRGB(*st.color)
        # Letter-spacing is a property of the text object, not the canvas, so
        # every line is drawn through one. `line_width` measures on the same
        # assumption; the two must never disagree.
        text = canvas.beginText()
        text.setFont(st.font, st.size)
        if st.tracking:
            text.setCharSpace(st.tracking)
        y = top - st.space_before
        for ln in lines:
            if not ln:
                y -= lh * st.blank_line_ratio
                continue
            y -= lh
            baseline = y + lh * 0.24   # sit the baseline just above the line box
            text.setTextOrigin(self._line_x(ln, x, width), baseline)
            text.textLine(ln)
        canvas.drawText(text)
        canvas.restoreState()

    def _line_x(self, line: str, x: float, width: float) -> float:
        st = self.style
        if st.align == "left":
            return x
        w = line_width(line, st.font, st.size, st.tracking)
        if st.align == "center":
            return x + (width - w) / 2.0
        return x + width - w


@dataclass
class Row:
    """One line of a track listing: title flush left, duration flush right.

    The duration sits on the first baseline only. A title long enough to wrap
    keeps its time on the line it started on, which is where the eye looks for
    it — carrying it down to the last line would leave it stranded under the
    turnover.

    Atomic at a column break: a listing row is two or three words and splitting
    one across panels reads as a mistake.
    """

    left: Text
    right: str
    right_style: Style
    gap: float = 8.0

    def _left_width(self, width: float) -> float:
        if not self.right:
            return width
        rw = line_width(self.right, self.right_style.font,
                        self.right_style.size, self.right_style.tracking)
        # Never starve the title: a pathological duration string gives up its
        # column rather than wrapping the title to one word per line.
        return max(width - rw - self.gap, width * 0.45)

    def height(self, width: float) -> float:
        return self.left.height(self._left_width(width))

    def split(self, width: float, available: float):
        return (self, None) if self.height(width) <= available else (None, self)

    def draw(self, canvas, x: float, top: float, width: float) -> None:
        left_width = self._left_width(width)
        self.left.draw(canvas, x, top, left_width)
        if not self.right or not self.left.lines(left_width):
            return

        st = self.left.style
        lh = st.line_height
        # The same baseline arithmetic Text.draw uses for its first line; the
        # two have to agree or the time sits off the title's line.
        baseline = top - st.space_before - lh + lh * 0.24
        rs = self.right_style
        rw = line_width(self.right, rs.font, rs.size, rs.tracking)
        canvas.saveState()
        canvas.setFillColorRGB(*rs.color)
        text = canvas.beginText()
        text.setFont(rs.font, rs.size)
        if rs.tracking:
            text.setCharSpace(rs.tracking)
        text.setTextOrigin(x + width - rw, baseline)
        text.textLine(self.right)
        canvas.drawText(text)
        canvas.restoreState()


class _PreWrapped(Text):
    """A Text whose lines are fixed — used for the halves produced by a split,
    so re-wrapping can never disagree with what was already measured."""

    def lines(self, width: float) -> list[str]:
        return self._lines or []


def _prewrapped(lines: list[str], style: Style, width: float) -> _PreWrapped:
    block = _PreWrapped("", style)
    block._lines = list(lines)
    block._lines_width = width
    return block


@dataclass
class Group:
    """Blocks that belong together at a column break.

    An atomic group moves whole — a name and the line under it, say. A group
    that is not atomic may break, but never in its first block: a song title
    followed by lyrics keeps at least the opening of the song with the title
    instead of being left alone at the foot of a column, while the rest of the
    song still flows on as far as it needs to.
    """

    blocks: list[Block]
    atomic: bool = True

    def height(self, width: float) -> float:
        return sum(b.height(width) for b in self.blocks)

    def split(self, width: float, available: float):
        if self.height(width) <= available:
            return self, None
        if self.atomic:
            return None, self

        head: list[Block] = []
        rest = list(self.blocks)
        used = 0.0
        while rest:
            block = rest[0]
            height = block.height(width)
            if used + height <= available:
                head.append(rest.pop(0))
                used += height
                continue
            first, second = block.split(width, available - used)
            rest.pop(0)
            if first is not None:
                head.append(first)
            if second is not None:
                rest.insert(0, second)
            break

        # Nothing but the opening block fits, which is the orphan this exists to
        # prevent: send the whole group on rather than leave a title behind.
        if len(head) < 2:
            return None, self
        if not rest:
            return Group(head, self.atomic), None
        return Group(head, self.atomic), Group(rest, self.atomic)

    def draw(self, canvas, x: float, top: float, width: float) -> None:
        y = top
        for block in self.blocks:
            block.draw(canvas, x, y, width)
            y -= block.height(width)


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------


@dataclass
class Background:
    color: tuple[float, float, float] | None = None
    image: str | None = None
    scrim: bool = False        # dark gradient at the foot, for type over artwork
    scrim_height: float = 0.52  # how far up the panel that gradient reaches
    fit: str = "cover"         # cover | contain


@dataclass
class Placed:
    x: float          # relative to the panel's left edge
    top: float        # relative to the panel's bottom edge (PDF-style y)
    width: float
    block: Block


@dataclass
class Panel:
    index: int                    # 1-based position in the booklet
    kind: str = "content"         # cover | content | credits | colophon | blank
    background: Background | None = None
    items: list[Placed] = field(default_factory=list)
    label: str = ""               # shown in the build log


def compose_panel(
    geo: Geometry,
    index: int,
    blocks: list[Block],
    kind: str = "content",
    label: str = "",
    valign: str = "top",
    background: Background | None = None,
) -> Panel:
    """Lay a fixed list of blocks onto a single panel, top-down."""
    panel = Panel(index=index, kind=kind, label=label, background=background)
    x = geo.content_x(index)
    total = sum(b.height(geo.content_w) for b in blocks)
    top = geo.content_top()
    if valign == "bottom":
        top = geo.margin_bottom + total
    elif valign == "middle":
        top = geo.margin_bottom + (geo.content_h + total) / 2.0
    for block in blocks:
        panel.items.append(Placed(x=x, top=top, width=geo.content_w, block=block))
        top -= block.height(geo.content_w)
    return panel


# ---------------------------------------------------------------------------
# Sections and the planner
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """A unit of content the planner places onto panels.

    ``build`` regenerates the blocks at a given point size, which is what makes
    auto-shrink possible: the planner re-asks for the section at smaller sizes
    until one fits.
    """

    key: str
    build: Callable[[float], list[Block]]
    size_max: float
    size_min: float
    step: float = 0.25
    # Start at the top of a fresh column. With one column to a panel that is the
    # same thing as starting a fresh panel, which is what it used to be.
    start_new_column: bool = False
    splittable: bool = True
    kind: str = "content"
    label: str = ""


@dataclass
class PlanResult:
    panels: list[Panel]
    shrunk: dict[str, float] = field(default_factory=dict)   # key -> chosen size
    flowed: dict[str, int] = field(default_factory=dict)     # key -> panels spanned
    placement: dict[str, int] = field(default_factory=dict)  # key -> first panel index


class PanelPlanner:
    """Places sections onto panels: shrink to fit, then flow if still too tall.

    A panel is divided into ``columns`` text columns, filled left to right; a
    column behaves exactly like a panel did when there was only one of them, so
    everything below reads "column" where it used to read "panel".
    """

    def __init__(
        self,
        geo: Geometry,
        log: IssueLog,
        first_index: int = 1,
        columns: int = 1,
        column_gap: float = 0.0,
    ):
        self.geo = geo
        self.log = log
        self.columns = max(1, int(columns))
        self.column_gap = column_gap if self.columns > 1 else 0.0
        self.panels: list[Panel] = []
        self.next_index = first_index
        self.column = 0        # which column of the current panel is being filled
        self.cursor = 0.0      # points consumed from the top of the current column
        self.result = PlanResult(panels=self.panels)

    # -- panel bookkeeping ---------------------------------------------------

    @property
    def column_w(self) -> float:
        gaps = self.column_gap * (self.columns - 1)
        return (self.geo.content_w - gaps) / self.columns

    def _new_panel(self, kind: str = "content", label: str = "") -> Panel:
        panel = Panel(index=self.next_index, kind=kind, label=label)
        self.next_index += 1
        self.panels.append(panel)
        self.column = 0
        self.cursor = 0.0
        return panel

    def _next_column(self, kind: str = "content", label: str = "") -> None:
        """Move to the next column, or to a new panel once they are used up."""
        if self.column + 1 < self.columns:
            self.column += 1
            self.cursor = 0.0
            return
        self._new_panel(kind, label)

    def _current(self, kind: str = "content", label: str = "") -> Panel:
        if not self.panels:
            return self._new_panel(kind, label)
        return self.panels[-1]

    @property
    def at_panel_start(self) -> bool:
        return self.column == 0 and self.cursor == 0.0

    @property
    def remaining(self) -> float:
        if not self.panels:
            return self.geo.content_h
        return self.geo.content_h - self.cursor

    def _place(self, block: Block, height: float) -> None:
        panel = self._current()
        panel.items.append(
            Placed(
                x=self.geo.content_x(panel.index)
                + self.column * (self.column_w + self.column_gap),
                top=self.geo.content_top() - self.cursor,
                width=self.column_w,
                block=block,
            )
        )
        self.cursor += height

    # -- placement -----------------------------------------------------------

    def add_section(self, section: Section) -> None:
        width = self.column_w
        sizes = _size_ladder(section.size_max, section.size_min, section.step)

        if not self.panels:
            self._new_panel(section.kind, section.label)
        elif section.start_new_column and self.cursor > 0:
            self._next_column(section.kind, section.label)
        elif self.at_panel_start:
            panel = self._current()
            panel.kind = section.kind
            panel.label = panel.label or section.label

        # 1. Fit in whatever is left in the current column.
        if self.remaining > 0:
            for size in sizes:
                blocks = section.build(size)
                if sum(b.height(width) for b in blocks) <= self.remaining:
                    self._commit(section, blocks, size, width)
                    return

        # 2. Fit in a fresh column.
        for size in sizes:
            blocks = section.build(size)
            if sum(b.height(width) for b in blocks) <= self.geo.content_h:
                self._next_column(section.kind, section.label)
                self._commit(section, blocks, size, width)
                return

        # 3. Taller than a column even at the smallest size: flow across columns.
        if not section.splittable:
            raise LayoutError(
                f"section '{section.key}' does not fit in one column and cannot be split"
            )
        self.result.shrunk[section.key] = section.size_min
        self._flow(section, section.build(section.size_min), width)

    def _commit(self, section: Section, blocks: list[Block], size: float, width: float) -> None:
        self.result.placement[section.key] = self._current().index
        for block in blocks:
            self._place(block, block.height(width))
        if size < section.size_max - 1e-9:
            self.result.shrunk[section.key] = size

    def _flow(self, section: Section, blocks: list[Block], width: float) -> None:
        if self.remaining < self.geo.content_h * 0.3:
            self._next_column(section.kind, section.label)
        start_index = self._current().index
        self.result.placement[section.key] = start_index
        queue: list[Block] = list(blocks)

        guard = 0
        while queue:
            guard += 1
            if guard > 1000:
                raise LayoutError(f"section '{section.key}' failed to converge while flowing")
            block = queue.pop(0)
            height = block.height(width)
            if height <= self.remaining:
                self._place(block, height)
                continue
            head, tail = block.split(width, self.remaining)
            if head is not None:
                self._place(head, head.height(width))
            if tail is not None:
                queue.insert(0, tail)
                self._next_column(section.kind, f"{section.label} (cont.)")
            elif head is None:
                if self.cursor == 0:
                    raise LayoutError(
                        f"a block in section '{section.key}' is taller than a whole column "
                        "and cannot be split"
                    )
                queue.insert(0, block)
                self._next_column(section.kind, f"{section.label} (cont.)")

        spanned = self._current().index - start_index + 1
        if spanned > 1:
            self.result.flowed[section.key] = spanned

    # -- finishing -----------------------------------------------------------

    def ensure_room(self, needed: float) -> None:
        """Start a new column unless at least ``needed`` points remain."""
        if self.panels and self.remaining < needed:
            self._next_column()

    def finish(self) -> PlanResult:
        return self.result


def _size_ladder(size_max: float, size_min: float, step: float) -> list[float]:
    if size_min > size_max:
        size_min = size_max
    sizes: list[float] = []
    size = size_max
    while size >= size_min - 1e-9:
        sizes.append(round(size, 3))
        size -= max(step, 0.05)
    if not sizes or abs(sizes[-1] - size_min) > 1e-9:
        sizes.append(round(size_min, 3))
    return sizes


def blanks_needed(panel_count: int, multiple: int = 4) -> int:
    """A saddle-stitched booklet is folded sheets, so the panel count has to be
    a multiple of four."""
    remainder = panel_count % multiple
    return 0 if remainder == 0 else multiple - remainder

"""Font registration and text measurement.

Everything the layout engine knows about how tall a block of text will be comes
from here, so measurement and drawing must agree exactly: both go through
``line_width`` with the same font, size and tracking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .errors import IssueLog

# Built-in faces are always present in ReportLab, so they make a dependable
# default: no reliance on whatever fonts happen to be installed.
CORE_SERIF = {
    "regular": "Times-Roman",
    "bold": "Times-Bold",
    "italic": "Times-Italic",
    "bold_italic": "Times-BoldItalic",
}
CORE_SANS = {
    "regular": "Helvetica",
    "bold": "Helvetica-Bold",
    "italic": "Helvetica-Oblique",
    "bold_italic": "Helvetica-BoldOblique",
}


@dataclass
class FontSet:
    """The four roles the booklet typography uses."""

    display: dict[str, str]   # album/song titles
    body: dict[str, str]      # lyrics
    meta: dict[str, str]      # credits, small print
    mono: dict[str, str]      # catalog numbers, durations

    def get(self, role: str, weight: str = "regular") -> str:
        family = getattr(self, role, None) or self.body
        return family.get(weight) or family["regular"]


def _register_family(name: str, spec: Any, log: IssueLog) -> dict[str, str] | None:
    """Register a user-supplied family.

    ``spec`` is either the name of a built-in family ("serif"/"sans") or a
    mapping of weight -> font file (optionally "path#index" for .ttc files).
    """
    if isinstance(spec, str):
        key = spec.strip().casefold()
        if key in ("serif", "times"):
            return dict(CORE_SERIF)
        if key in ("sans", "helvetica"):
            return dict(CORE_SANS)
        spec = {"regular": spec}

    if not isinstance(spec, dict):
        log.warn("font.shape", f"font family '{name}' must be a name or a mapping; ignoring")
        return None

    resolved: dict[str, str] = {}
    for weight, value in spec.items():
        if weight not in ("regular", "bold", "italic", "bold_italic"):
            log.warn("font.weight", f"unknown weight '{weight}' in font family '{name}'")
            continue
        raw = str(value)
        path_part, _, index_part = raw.partition("#")
        path = Path(path_part).expanduser()
        if not path.exists():
            log.warn("font.notfound", f"font file not found: {path}; falling back")
            continue
        face = f"ln-{name}-{weight}"
        try:
            index = int(index_part) if index_part else 0
            pdfmetrics.registerFont(TTFont(face, str(path), subfontIndex=index))
        except Exception as exc:  # noqa: BLE001 - any font error means fall back
            log.warn("font.load", f"could not load {path}: {exc}; falling back")
            continue
        resolved[weight] = face

    if "regular" not in resolved:
        log.warn("font.regular", f"font family '{name}' has no usable regular weight; falling back")
        return None
    for weight in ("bold", "italic", "bold_italic"):
        resolved.setdefault(weight, resolved["regular"])
    return resolved


def build_fonts(design_fonts: dict[str, Any], log: IssueLog) -> FontSet:
    defaults = {
        "display": dict(CORE_SERIF),
        "body": dict(CORE_SERIF),
        "meta": dict(CORE_SANS),
        "mono": dict(CORE_SANS),
    }
    for role in defaults:
        spec = design_fonts.get(role)
        if spec is None:
            continue
        family = _register_family(role, spec, log)
        if family:
            defaults[role] = family
    return FontSet(**defaults)


# ---------------------------------------------------------------------------
# Measurement and wrapping
# ---------------------------------------------------------------------------


def line_width(text: str, font: str, size: float, tracking: float = 0.0) -> float:
    """Width of one line, including letter-spacing.

    ReportLab applies char space after every glyph including the last, which is
    also how the PDF viewer renders it, so no trailing adjustment is made here.
    """
    if not text:
        return 0.0
    return pdfmetrics.stringWidth(text, font, size) + tracking * len(text)


_WS = re.compile(r"(\s+)")


def _leading_space(text: str) -> str:
    stripped = text.lstrip(" \t")
    return text[: len(text) - len(stripped)].replace("\t", "    ")


def wrap_line(
    text: str,
    font: str,
    size: float,
    max_width: float,
    tracking: float = 0.0,
    hanging_indent: float = 0.0,
) -> list[str]:
    """Greedy word wrap for a single logical line.

    Leading whitespace is preserved (lyric sheets use it for indented refrains)
    and continuation lines are indented by ``hanging_indent`` points.
    """
    indent = _leading_space(text)
    content = text.strip()
    if not content:
        return [""]

    indent_w = line_width(indent, font, size, tracking) if indent else 0.0
    words = [w for w in _WS.split(content) if w and not w.isspace()]

    lines: list[str] = []
    current = ""
    for word in words:
        budget = max_width - (indent_w if not lines else indent_w + hanging_indent)
        candidate = f"{current} {word}" if current else word
        if line_width(candidate, font, size, tracking) <= budget or not current:
            current = candidate
            # A single word longer than the line has to be broken by character.
            if not lines and line_width(current, font, size, tracking) > budget and " " not in current:
                pieces = _break_long_word(current, font, size, budget, tracking)
                lines.extend(indent + p for p in pieces[:-1])
                current = pieces[-1]
        else:
            lines.append(indent + current)
            current = word
    if current:
        lines.append(indent + current)
    return lines


def _break_long_word(
    word: str, font: str, size: float, max_width: float, tracking: float
) -> list[str]:
    pieces: list[str] = []
    current = ""
    for ch in word:
        if current and line_width(current + ch, font, size, tracking) > max_width:
            pieces.append(current)
            current = ch
        else:
            current += ch
    pieces.append(current)
    return pieces


def wrap_paragraph(
    text: str,
    font: str,
    size: float,
    max_width: float,
    tracking: float = 0.0,
    hanging_indent: float = 0.0,
    collapse: bool = False,
) -> list[str]:
    """Wrap a block of text into drawable lines.

    With ``collapse`` the block is treated as flowing prose (newlines are just
    whitespace). Without it, hard line breaks are honoured and blank lines are
    kept as blank lines — which is what lyrics need for stanza breaks.
    """
    if collapse:
        joined = " ".join(text.split())
        return wrap_line(joined, font, size, max_width, tracking, hanging_indent) if joined else []

    out: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw.strip():
            out.append("")
        else:
            out.extend(wrap_line(raw, font, size, max_width, tracking, hanging_indent))
    # Trim leading/trailing blank lines; interior ones carry meaning.
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out

"""Form widgets bound directly to the document mapping.

Every widget here writes straight into the dict it was handed and then calls
``on_change``. There is no separate form state to keep in sync — the document is
the model, and the widgets are a view onto it.

Two rules make that safe. Tk variables are created with their initial value
*before* a trace is attached, so populating a form never looks like an edit. And
every setter compares before it writes, so re-entering the same value does not
mark the document dirty or trigger a rebuild.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk
from typing import Any, Callable

PAD = 6


def _set(mapping: dict, key: str, value: Any, on_change: Callable[[], None]) -> None:
    """Write a value and notify, but only if it actually changed."""
    if mapping.get(key) != value:
        mapping[key] = value
        on_change()


class FieldGrid(ttk.Frame):
    """A two-column grid of labelled fields bound to one mapping."""

    def __init__(self, parent: tk.Misc, mapping: dict, on_change: Callable[[], None]):
        super().__init__(parent)
        self.mapping = mapping
        self.on_change = on_change
        self._row = 0
        self.columnconfigure(1, weight=1)

    def _label(self, text: str) -> None:
        ttk.Label(self, text=text).grid(
            row=self._row, column=0, sticky="w", padx=(0, PAD * 2), pady=4
        )

    def heading(self, text: str) -> None:
        label = ttk.Label(self, text=text.upper(), style="Section.TLabel")
        label.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=(PAD * 2, 2))
        self._row += 1
        ttk.Separator(self, orient="horizontal").grid(
            row=self._row, column=0, columnspan=2, sticky="ew", pady=(0, PAD)
        )
        self._row += 1

    def entry(self, label: str, key: str, hint: str = "") -> ttk.Entry:
        self._label(label)
        var = tk.StringVar(value=str(self.mapping.get(key, "") or ""))
        var.trace_add("write", lambda *_: _set(self.mapping, key, var.get(), self.on_change))
        widget = ttk.Entry(self, textvariable=var)
        widget.grid(row=self._row, column=1, sticky="ew", pady=4)
        self._row += 1
        if hint:
            ttk.Label(self, text=hint, style="Hint.TLabel").grid(
                row=self._row, column=1, sticky="w", pady=(0, PAD)
            )
            self._row += 1
        return widget

    def hint(self, text: str = "") -> ttk.Label:
        """A note under the field above it, returned so it can be rewritten.

        ``entry``'s own ``hint`` is fixed at build time; this one is for text
        that has to follow the document, like a value being derived elsewhere.
        """
        widget = ttk.Label(self, text=text, style="Hint.TLabel", wraplength=440,
                           justify="left")
        widget.grid(row=self._row, column=1, sticky="w", pady=(0, PAD))
        self._row += 1
        return widget

    def check(self, label: str, key: str, default: bool = False) -> ttk.Checkbutton:
        current = self.mapping.get(key, default)
        var = tk.BooleanVar(value=bool(current))
        var.trace_add("write", lambda *_: _set(self.mapping, key, var.get(), self.on_change))
        widget = ttk.Checkbutton(self, text=label, variable=var)
        widget.grid(row=self._row, column=1, sticky="w", pady=4)
        self._row += 1
        return widget

    def number(
        self,
        label: str,
        key: str,
        default: float,
        low: float = 0.0,
        high: float = 999.0,
        step: float = 1.0,
        cast: Callable[[str], Any] = float,
    ) -> ttk.Spinbox:
        self._label(label)
        var = tk.StringVar(value=str(self.mapping.get(key, default)))

        def write(*_: object) -> None:
            text = var.get().strip()
            if not text:
                # An empty box means "use the default", not "zero".
                if key in self.mapping:
                    del self.mapping[key]
                    self.on_change()
                return
            try:
                value = cast(text)
            except ValueError:
                return   # mid-typing ("-", "1."); wait for something parseable
            _set(self.mapping, key, value, self.on_change)

        var.trace_add("write", write)
        widget = ttk.Spinbox(
            self, textvariable=var, from_=low, to=high, increment=step, width=10
        )
        widget.grid(row=self._row, column=1, sticky="w", pady=4)
        self._row += 1
        return widget

    def int_list(self, label: str, key: str, hint: str = "") -> ttk.Entry:
        """A comma-separated list of track numbers."""
        self._label(label)
        current = self.mapping.get(key) or []
        var = tk.StringVar(value=", ".join(str(v) for v in current))

        def write(*_: object) -> None:
            parts = [p.strip() for p in var.get().replace(";", ",").split(",")]
            values: list[int] = []
            for part in parts:
                if not part:
                    continue
                try:
                    values.append(int(part))
                except ValueError:
                    return   # mid-typing; leave the last good value in place
            _set(self.mapping, key, values, self.on_change)

        var.trace_add("write", write)
        widget = ttk.Entry(self, textvariable=var)
        widget.grid(row=self._row, column=1, sticky="ew", pady=4)
        self._row += 1
        if hint:
            ttk.Label(self, text=hint, style="Hint.TLabel").grid(
                row=self._row, column=1, sticky="w", pady=(0, PAD)
            )
            self._row += 1
        return widget

    def choice(
        self,
        label: str,
        key: str,
        options: list[tuple[str, str]],
        default: str,
        on_pick: Callable[[str], None] | None = None,
    ) -> None:
        """One of a short list of values, as a row of radio buttons.

        ``options`` is (stored value, button text). ``on_pick`` fires after the
        document is updated, for forms that show different fields per choice.
        """
        self._label(label)
        holder = ttk.Frame(self)
        holder.grid(row=self._row, column=1, sticky="ew", pady=4)
        self._row += 1

        current = str(self.mapping.get(key, default) or default)
        if current not in {value for value, _ in options}:
            current = default
        var = tk.StringVar(value=current)

        def apply(*_: object) -> None:
            _set(self.mapping, key, var.get(), self.on_change)
            if on_pick is not None:
                on_pick(var.get())

        var.trace_add("write", apply)
        for i, (value, text) in enumerate(options):
            ttk.Radiobutton(holder, text=text, value=value, variable=var).grid(
                row=0, column=i, sticky="w", padx=(0, PAD * 2)
            )

    def color(self, label: str, key: str, default: str) -> None:
        self._label(label)
        holder = ttk.Frame(self)
        holder.grid(row=self._row, column=1, sticky="ew", pady=4)
        holder.columnconfigure(0, weight=1)
        self._row += 1

        var = tk.StringVar(value=str(self.mapping.get(key, default) or default))
        swatch = tk.Label(holder, width=3, relief="solid", borderwidth=1)

        def apply(*_: object) -> None:
            value = var.get().strip()
            _set(self.mapping, key, value, self.on_change)
            try:
                swatch.configure(background=value)
            except tk.TclError:
                swatch.configure(background="#ffffff")   # not a colour Tk knows yet

        var.trace_add("write", apply)
        ttk.Entry(holder, textvariable=var).grid(row=0, column=0, sticky="ew")
        swatch.grid(row=0, column=1, padx=PAD)

        def pick() -> None:
            chosen = colorchooser.askcolor(color=var.get() or default, parent=self)
            if chosen and chosen[1]:
                var.set(chosen[1])

        ttk.Button(holder, text="Pick…", command=pick, width=7).grid(row=0, column=2)
        apply()

    def file(
        self, label: str, key: str, base_dir: Callable[[], Path], hint: str = ""
    ) -> None:
        self._label(label)
        holder = ttk.Frame(self)
        holder.grid(row=self._row, column=1, sticky="ew", pady=4)
        holder.columnconfigure(0, weight=1)
        self._row += 1

        var = tk.StringVar(value=str(self.mapping.get(key, "") or ""))
        var.trace_add("write", lambda *_: _set(self.mapping, key, var.get(), self.on_change))
        ttk.Entry(holder, textvariable=var).grid(row=0, column=0, sticky="ew")

        def browse() -> None:
            chosen = filedialog.askopenfilename(
                parent=self,
                title=f"Choose {label.lower()}",
                initialdir=str(base_dir()),
                filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff"), ("All files", "*")],
            )
            if not chosen:
                return
            # Store it relative to the album file when we can, so the folder
            # stays portable; fall back to absolute when it lives elsewhere.
            path = Path(chosen)
            try:
                path = path.relative_to(base_dir())
            except ValueError:
                pass
            var.set(str(path))

        ttk.Button(holder, text="Browse…", command=browse, width=9).grid(
            row=0, column=1, padx=(PAD, 0)
        )
        if hint:
            ttk.Label(self, text=hint, style="Hint.TLabel").grid(
                row=self._row, column=1, sticky="w", pady=(0, PAD)
            )
            self._row += 1

    def row(self, widget: tk.Widget, span_label: bool = True) -> None:
        """Drop an arbitrary widget into the grid."""
        widget.grid(
            row=self._row,
            column=0 if span_label else 1,
            columnspan=2 if span_label else 1,
            sticky="nsew",
            pady=4,
        )
        self.rowconfigure(self._row, weight=1)
        self._row += 1


class TextField(ttk.Frame):
    """A multi-line text box bound to one string key."""

    def __init__(
        self,
        parent: tk.Misc,
        mapping: dict,
        key: str,
        on_change: Callable[[], None],
        height: int = 12,
    ):
        super().__init__(parent)
        self.mapping = mapping
        self.key = key
        self.on_change = on_change

        self.text = tk.Text(self, height=height, wrap="word", undo=True,
                            borderwidth=1, relief="solid", padx=6, pady=6)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.text.insert("1.0", str(mapping.get(key, "") or ""))
        for event in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<<Undo>>", "<<Redo>>", "<FocusOut>"):
            self.text.bind(event, self._sync, add="+")

    def _sync(self, _event: tk.Event | None = None) -> None:
        _set(self.mapping, self.key, self.text.get("1.0", "end-1c"), self.on_change)


class LinesField(ttk.Frame):
    """A text box where each line is one entry in a list of strings."""

    def __init__(
        self,
        parent: tk.Misc,
        mapping: dict,
        key: str,
        on_change: Callable[[], None],
        height: int = 5,
    ):
        super().__init__(parent)
        self.mapping = mapping
        self.key = key
        self.on_change = on_change

        self.text = tk.Text(self, height=height, wrap="word", undo=True,
                            borderwidth=1, relief="solid", padx=6, pady=6)
        self.text.pack(fill="both", expand=True)

        current = mapping.get(key) or []
        if isinstance(current, str):
            current = [current]
        self.text.insert("1.0", "\n".join(str(v) for v in current))
        for event in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<FocusOut>"):
            self.text.bind(event, self._sync, add="+")

    def _sync(self, _event: tk.Event | None = None) -> None:
        lines = [ln.strip() for ln in self.text.get("1.0", "end-1c").splitlines()]
        _set(self.mapping, self.key, [ln for ln in lines if ln], self.on_change)


class ListEditor(ttk.Frame):
    """A reorderable list of mappings, with a detail form for the selection.

    Used for writers, personnel, production and liner notes — everywhere the
    album file holds a list of small records that each need a few fields.
    """

    def __init__(
        self,
        parent: tk.Misc,
        items: list,
        *,
        label: Callable[[Any, int], str],
        factory: Callable[[], dict],
        detail: Callable[[tk.Misc, dict], tk.Widget],
        on_change: Callable[[], None],
        add_text: str = "Add",
        list_height: int = 7,
    ):
        super().__init__(parent)
        self.items = items
        self._label = label
        self._factory = factory
        self._detail = detail
        self._on_change = on_change

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(top, height=list_height, exportselection=False,
                                  activestyle="none", borderwidth=1, relief="solid")
        self.listbox.grid(row=0, column=0, sticky="ew")
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._show_detail())

        buttons = ttk.Frame(self)
        buttons.grid(row=1, column=0, sticky="ew", pady=(PAD, 0))
        ttk.Button(buttons, text=add_text, command=self._add, width=9).pack(side="left")
        ttk.Button(buttons, text="Remove", command=self._remove, width=9).pack(
            side="left", padx=PAD
        )
        ttk.Button(buttons, text="↑", command=lambda: self._move(-1), width=3).pack(side="left")
        ttk.Button(buttons, text="↓", command=lambda: self._move(1), width=3).pack(
            side="left", padx=(4, 0)
        )

        self.detail_frame = ttk.Frame(self)
        self.detail_frame.grid(row=2, column=0, sticky="nsew", pady=(PAD * 2, 0))
        self.detail_frame.columnconfigure(0, weight=1)

        self.refresh()
        if self.items:
            self._select(0)

    # -- list state ----------------------------------------------------------

    @property
    def selection(self) -> int | None:
        picked = self.listbox.curselection()
        return int(picked[0]) if picked else None

    def refresh(self) -> None:
        keep = self.selection
        self.listbox.delete(0, "end")
        for i, item in enumerate(self.items):
            self.listbox.insert("end", self._label(item, i) or "—")
        if keep is not None and self.items:
            self._select(min(keep, len(self.items) - 1))

    def _select(self, index: int) -> None:
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.see(index)
        self._show_detail()

    def _changed(self) -> None:
        """An edit inside the detail form: relabel the row, then bubble up."""
        index = self.selection
        if index is not None and index < len(self.items):
            self.listbox.delete(index)
            self.listbox.insert(index, self._label(self.items[index], index) or "—")
            self.listbox.selection_set(index)
        self._on_change()

    def _show_detail(self) -> None:
        for child in self.detail_frame.winfo_children():
            child.destroy()
        index = self.selection
        if index is None or index >= len(self.items):
            return
        widget = self._detail(self.detail_frame, self.items[index])
        widget.grid(row=0, column=0, sticky="nsew")

    # -- mutation ------------------------------------------------------------

    def _add(self) -> None:
        self.items.append(self._factory())
        self.refresh()
        self._select(len(self.items) - 1)
        self._on_change()

    def _remove(self) -> None:
        index = self.selection
        if index is None:
            return
        del self.items[index]
        self.refresh()
        if self.items:
            self._select(min(index, len(self.items) - 1))
        else:
            self._show_detail()
        self._on_change()

    def _move(self, delta: int) -> None:
        index = self.selection
        if index is None:
            return
        target = index + delta
        if not 0 <= target < len(self.items):
            return
        self.items[index], self.items[target] = self.items[target], self.items[index]
        self.refresh()
        self._select(target)
        self._on_change()


class Scrollable(ttk.Frame):
    """A vertically scrolling container for a tall form.

    Tk has no scrolling frame, so this is the usual canvas-plus-window dance:
    put a frame inside a canvas, and keep the frame's width matched to the
    canvas so the form fills the pane instead of sitting in a narrow column.
    """

    def __init__(self, parent: tk.Misc):
        super().__init__(parent)
        self._canvas = tk.Canvas(self, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scroll.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.body = ttk.Frame(self._canvas, padding=(PAD * 2, PAD * 2))
        self._window = self._canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind(
            "<Configure>",
            lambda _e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.bind(
            "<Configure>",
            lambda e: self._canvas.itemconfigure(self._window, width=e.width),
        )
        # Tk delivers wheel events to the widget under the pointer, so bind on
        # enter and release on leave rather than grabbing them globally.
        self.body.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self.body.bind("<Leave>", lambda _e: self._bind_wheel(False))
        # Panes are destroyed whenever the navigator selection changes. An
        # all-widgets binding would outlive this one and fire against a dead
        # canvas, so it has to be dropped on the way out.
        self.bind("<Destroy>", self._on_destroy)

    def _bind_wheel(self, active: bool) -> None:
        if active:
            self._canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self._canvas.unbind_all("<MouseWheel>")

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self._bind_wheel(False)

    def _on_wheel(self, event: tk.Event) -> None:
        if not self._canvas.winfo_exists():
            return
        self._canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

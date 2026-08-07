"""The main window: navigator, editor, preview, issue log.

The whole app is one loop: an edit writes into the document mapping, which
schedules a rebuild, which re-plans the booklet and refreshes the preview and the
issue log. Rebuilds are debounced because they run on every keystroke, and
``AlbumDocument.plan`` never raises — a document being typed into is invalid far
more often than it is valid, and that has to be an ordinary state to render.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..errors import LinerNotesError
from ..imposition import render_press
from ..render import render_reader
from .document import AlbumDocument, BuildOutcome
from .editors import (
    build_album_pane,
    build_copyright_pane,
    build_credits_pane,
    build_design_pane,
    build_layout_pane,
    build_notes_pane,
    build_track_pane,
    build_tracks_pane,
)
from .preview import PreviewPane

REBUILD_DELAY_MS = 350

SECTIONS = (
    ("album", "Album"),
    ("tracks", "Tracks"),
    ("credits", "Credits"),
    ("notes", "Liner notes"),
    ("copyright", "Copyright"),
    ("design", "Design"),
    ("layout", "Layout"),
)


class LinerNotesApp:
    def __init__(self, root: tk.Tk, path: str | Path | None = None):
        self.root = root
        self.doc = AlbumDocument()
        self.selection = "album"
        self._rebuild_job: str | None = None
        self._outcome: BuildOutcome | None = None

        root.title("linernotes")
        root.geometry("1500x940")
        root.minsize(1100, 700)

        self._configure_styles()
        self._build_menu()
        self._build_layout()
        self._bind_keys()

        if path:
            self._open_path(Path(path))
        else:
            self._reload_all()

    # -- chrome --------------------------------------------------------------

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Helvetica", 17, "bold"))
        style.configure("Section.TLabel", font=("Helvetica", 10, "bold"), foreground="#8a7a5e")
        style.configure("Hint.TLabel", font=("Helvetica", 11), foreground="#6b6b6b")
        style.configure("Status.TLabel", font=("Helvetica", 11), foreground="#4a4a4a")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New", accelerator="Cmd+N", command=self.new)
        file_menu.add_command(label="Open…", accelerator="Cmd+O", command=self.open)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Cmd+S", command=self.save)
        file_menu.add_command(label="Save As…", command=self.save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Export both PDFs…", accelerator="Cmd+E",
                              command=self.export_both)
        file_menu.add_command(label="Export reader PDF…", command=self.export_reader)
        file_menu.add_command(label="Export press sheets…", command=self.export_press)
        menubar.add_cascade(label="File", menu=file_menu)

        build_menu = tk.Menu(menubar, tearoff=0)
        build_menu.add_command(label="Rebuild preview", accelerator="Cmd+R",
                               command=self.rebuild_now)
        menubar.add_cascade(label="Build", menu=build_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About linernotes", command=self._about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.configure(menu=menubar)

    def _bind_keys(self) -> None:
        # Bound for both modifiers so the app behaves on a non-Mac too.
        for mod in ("Command", "Control"):
            self.root.bind_all(f"<{mod}-n>", lambda _e: self.new())
            self.root.bind_all(f"<{mod}-o>", lambda _e: self.open())
            self.root.bind_all(f"<{mod}-s>", lambda _e: self.save())
            self.root.bind_all(f"<{mod}-e>", lambda _e: self.export_both())
            self.root.bind_all(f"<{mod}-r>", lambda _e: self.rebuild_now())
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        self.status = ttk.Label(self.root, text="", style="Status.TLabel",
                                anchor="w", padding=(10, 5))
        self.status.pack(fill="x", side="bottom")

        vertical = ttk.PanedWindow(outer, orient="vertical")
        vertical.pack(fill="both", expand=True)

        horizontal = ttk.PanedWindow(vertical, orient="horizontal")
        vertical.add(horizontal, weight=5)

        # Navigator
        nav_frame = ttk.Frame(horizontal, width=210)
        self.nav = ttk.Treeview(nav_frame, show="tree", selectmode="browse")
        self.nav.pack(fill="both", expand=True)
        self.nav.bind("<<TreeviewSelect>>", self._on_nav_select)
        horizontal.add(nav_frame, weight=0)

        # Editor
        self.editor_holder = ttk.Frame(horizontal)
        horizontal.add(self.editor_holder, weight=3)

        # Preview
        self.preview = PreviewPane(horizontal)
        horizontal.add(self.preview, weight=4)

        # Issue log
        issues_frame = ttk.Frame(vertical)
        columns = ("level", "code", "where", "message")
        self.issues = ttk.Treeview(
            issues_frame, columns=columns, show="headings", height=6, selectmode="browse"
        )
        for name, title, width, stretch in (
            ("level", "", 64, False),
            ("code", "Code", 150, False),
            ("where", "Where", 110, False),
            ("message", "Message", 700, True),
        ):
            self.issues.heading(name, text=title)
            self.issues.column(name, width=width, stretch=stretch, anchor="w")
        scroll = ttk.Scrollbar(issues_frame, orient="vertical", command=self.issues.yview)
        self.issues.configure(yscrollcommand=scroll.set)
        self.issues.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.issues.tag_configure("error", foreground="#b00020")
        self.issues.tag_configure("warning", foreground="#8a6d00")
        self.issues.tag_configure("info", foreground="#5a5a5a")
        self.issues.bind("<Double-1>", self._on_issue_activate)
        vertical.add(issues_frame, weight=1)

    # -- navigator -----------------------------------------------------------

    def _rebuild_nav(self) -> None:
        """Rebuild the tree, keeping the current selection where it still exists."""
        wanted = self.selection
        self.nav.delete(*self.nav.get_children())
        for key, title in SECTIONS:
            self.nav.insert("", "end", iid=key, text=f"  {title}", open=(key == "tracks"))
            if key == "tracks":
                for i, track in enumerate(self.doc.tracks):
                    self.nav.insert("tracks", "end", iid=f"track:{i}",
                                    text=f"  {self._track_label(track, i)}")
        if not self.nav.exists(wanted):
            wanted = "album"
        self.selection = wanted
        self.nav.selection_set(wanted)

    def _track_label(self, track: object, index: int) -> str:
        if not isinstance(track, dict):
            return f"{index + 1}. {track}"
        number = track.get("number", index + 1)
        title = str(track.get("title") or "").strip() or "untitled"
        return f"{number}. {title}"

    def _sync_nav_labels(self) -> None:
        """Keep track names current without disturbing selection or focus.

        Called on every edit, so it only touches the tree when something in it
        actually differs — an unnecessary delete/insert would drop focus out of
        the field being typed into.
        """
        existing = self.nav.get_children("tracks") if self.nav.exists("tracks") else ()
        if len(existing) != len(self.doc.tracks):
            self._rebuild_nav()
            self._show_pane()
            return
        for i, track in enumerate(self.doc.tracks):
            iid = f"track:{i}"
            label = f"  {self._track_label(track, i)}"
            if self.nav.exists(iid) and self.nav.item(iid, "text") != label:
                self.nav.item(iid, text=label)

    def _on_nav_select(self, _event: tk.Event | None = None) -> None:
        picked = self.nav.selection()
        if not picked or picked[0] == self.selection:
            return
        self.selection = picked[0]
        self._show_pane()

    def _show_pane(self) -> None:
        for child in self.editor_holder.winfo_children():
            child.destroy()

        key = self.selection
        builders = {
            "album": build_album_pane,
            "tracks": build_tracks_pane,
            "credits": build_credits_pane,
            "notes": build_notes_pane,
            "copyright": build_copyright_pane,
            "design": build_design_pane,
            "layout": build_layout_pane,
        }
        if key.startswith("track:"):
            index = int(key.split(":", 1)[1])
            pane = build_track_pane(self.editor_holder, self.doc, self.on_change, index)
        else:
            pane = builders.get(key, build_album_pane)(
                self.editor_holder, self.doc, self.on_change
            )
        pane.pack(fill="both", expand=True)

    # -- the edit loop -------------------------------------------------------

    def on_change(self) -> None:
        """Something in the document changed."""
        self._sync_nav_labels()
        self._update_title()
        if self._rebuild_job is not None:
            self.root.after_cancel(self._rebuild_job)
        self._rebuild_job = self.root.after(REBUILD_DELAY_MS, self.rebuild_now)

    def rebuild_now(self) -> None:
        if self._rebuild_job is not None:
            self.root.after_cancel(self._rebuild_job)
            self._rebuild_job = None

        outcome = self.doc.plan()
        self._outcome = outcome
        self._show_issues(outcome)

        if outcome.ok and outcome.booklet is not None:
            self.preview.show(outcome.booklet)
            panels = len(outcome.booklet.panels)
            self.status.configure(
                text=f"{panels} panels · {panels // 4} sheets · "
                     f"{len(outcome.log.errors)} errors, {len(outcome.log.warnings)} warnings"
            )
        else:
            first = outcome.log.errors[0].message if outcome.log.errors else outcome.error
            self.preview.show_message(f"Nothing to preview yet.\n\n{first or ''}".strip())
            self.status.configure(text=first or "the album cannot be laid out yet")

    def _show_issues(self, outcome: BuildOutcome) -> None:
        self.issues.delete(*self.issues.get_children())
        symbol = {"error": "●  error", "warning": "▲  warn", "info": "·  info"}
        for issue in outcome.log.issues:
            self.issues.insert(
                "", "end",
                values=(symbol.get(issue.level, issue.level), issue.code,
                        issue.where, issue.message),
                tags=(issue.level,),
            )
        # A pipeline failure that never reached the log still has to be visible.
        if outcome.error and not outcome.log.errors:
            self.issues.insert(
                "", "end",
                values=("●  error", "build", "", outcome.error.splitlines()[0]),
                tags=("error",),
            )

    def _on_issue_activate(self, _event: tk.Event) -> None:
        """Jump to the track an issue came from."""
        picked = self.issues.focus()
        if not picked:
            return
        where = str(self.issues.item(picked, "values")[2])
        if not where.startswith("track "):
            return
        try:
            number = int(where.split()[1])
        except (IndexError, ValueError):
            return
        for i, track in enumerate(self.doc.tracks):
            if isinstance(track, dict) and int(track.get("number", i + 1)) == number:
                if self.nav.exists(f"track:{i}"):
                    self.nav.selection_set(f"track:{i}")
                    self.nav.see(f"track:{i}")
                return

    def _update_title(self) -> None:
        mark = " •" if self.doc.dirty else ""
        self.root.title(f"linernotes — {self.doc.display_name}{mark}")

    def _reload_all(self) -> None:
        self.selection = "album"
        self._rebuild_nav()
        self._show_pane()
        self._update_title()
        self.rebuild_now()

    # -- file commands -------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "Unsaved changes",
            f"Save changes to {self.doc.display_name} first?",
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def new(self) -> None:
        if not self._confirm_discard():
            return
        self.doc = AlbumDocument()
        self._reload_all()

    def open(self) -> None:
        if not self._confirm_discard():
            return
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Open album",
            filetypes=[("Album files", "*.yaml *.yml"), ("All files", "*")],
        )
        if chosen:
            self._open_path(Path(chosen))

    def _open_path(self, path: Path) -> None:
        try:
            self.doc = AlbumDocument.open(path)
        except Exception as exc:  # noqa: BLE001 - any bad file is a message, not a crash
            messagebox.showerror("Could not open", f"{path.name}\n\n{exc}", parent=self.root)
            return
        self._reload_all()

    def save(self) -> bool:
        if self.doc.path is None:
            return self.save_as()
        try:
            self.doc.save()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not save", str(exc), parent=self.root)
            return False
        self._update_title()
        self.status.configure(text=f"saved {self.doc.path}")
        return True

    def save_as(self) -> bool:
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save album as",
            defaultextension=".yaml",
            initialfile=f"{self.doc.stem}.yaml",
            filetypes=[("Album files", "*.yaml *.yml")],
        )
        if not chosen:
            return False
        try:
            self.doc.save(chosen)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not save", str(exc), parent=self.root)
            return False
        self._reload_all()
        self.status.configure(text=f"saved {self.doc.path}")
        return True

    # -- export --------------------------------------------------------------

    def _booklet_or_complain(self):
        self.rebuild_now()
        if self._outcome and self._outcome.ok:
            return self._outcome.booklet
        detail = ""
        if self._outcome:
            errors = self._outcome.log.errors
            detail = "\n".join(i.format() for i in errors[:8]) or (self._outcome.error or "")
        messagebox.showerror(
            "Cannot export",
            f"The album has to lay out cleanly before it can be exported.\n\n{detail}",
            parent=self.root,
        )
        return None

    def export_both(self) -> None:
        booklet = self._booklet_or_complain()
        if booklet is None:
            return
        directory = filedialog.askdirectory(
            parent=self.root, title="Export both PDFs to…",
            initialdir=str(self.doc.source_dir),
        )
        if not directory:
            return
        out = Path(directory)
        try:
            reader = render_reader(booklet, out / f"{self.doc.stem}-reader.pdf")
            press = render_press(booklet, out / f"{self.doc.stem}-press.pdf")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc), parent=self.root)
            return
        self.status.configure(text=f"exported {reader.name} and {press.name} to {out}")
        messagebox.showinfo(
            "Exported", f"{reader.name}\n{press.name}\n\nin {out}", parent=self.root
        )

    def _export_one(self, kind: str) -> None:
        booklet = self._booklet_or_complain()
        if booklet is None:
            return
        chosen = filedialog.asksaveasfilename(
            parent=self.root,
            title=f"Export {kind}",
            defaultextension=".pdf",
            initialfile=f"{self.doc.stem}-{kind}.pdf",
            initialdir=str(self.doc.source_dir),
            filetypes=[("PDF", "*.pdf")],
        )
        if not chosen:
            return
        try:
            if kind == "reader":
                path = render_reader(booklet, chosen)
            else:
                path = render_press(booklet, chosen)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Export failed", str(exc), parent=self.root)
            return
        self.status.configure(text=f"exported {path}")

    def export_reader(self) -> None:
        self._export_one("reader")

    def export_press(self) -> None:
        self._export_one("press")

    # -- misc ----------------------------------------------------------------

    def _about(self) -> None:
        from .. import __version__

        messagebox.showinfo(
            "linernotes",
            f"linernotes {__version__}\n\n"
            "Typesets a CD booklet from a YAML description of a record.\n"
            "Reader PDF for proofing, saddle-stitch imposed sheets for the printer.",
            parent=self.root,
        )

    def quit(self) -> None:
        if self._confirm_discard():
            self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else None
    if path and not Path(path).exists():
        print(f"album file not found: {path}", file=sys.stderr)
        return 1

    root = tk.Tk()
    try:
        LinerNotesApp(root, path)
    except LinerNotesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    root.mainloop()
    return 0

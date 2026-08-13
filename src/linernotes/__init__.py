"""linernotes — typeset a CD booklet from a YAML description of a record."""

from .audio import ImportResult, import_audio
from .build import BuildResult, build, plan_booklet, plan_from_album
from .errors import IssueLog, LayoutError, LinerNotesError, ValidationError
from .model import Album, album_from_raw, derive_copyright, load_album

__version__ = "0.1.0"

__all__ = [
    "Album",
    "BuildResult",
    "ImportResult",
    "IssueLog",
    "LayoutError",
    "LinerNotesError",
    "ValidationError",
    "album_from_raw",
    "build",
    "derive_copyright",
    "import_audio",
    "load_album",
    "plan_booklet",
    "plan_from_album",
    "__version__",
]

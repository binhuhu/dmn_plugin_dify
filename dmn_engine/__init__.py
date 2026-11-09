"""Pure DMN decision-table engine (standard library only)."""

from .canonical import canonicalize, table_sha256
from .errors import DmnError
from .evaluate import evaluate
from .model import load_table

__all__ = ["DmnError", "canonicalize", "evaluate", "load_table", "table_sha256"]
__version__ = "0.1.1"

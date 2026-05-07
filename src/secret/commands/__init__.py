from .registry import registry
from . import builtin  # noqa: F401 — registers built-in commands

__all__ = ["registry"]

"""FastAPI layer — routes, DI, serialization only (specs/06-webapp.md)."""

from .main import create_app

__all__ = ["create_app"]

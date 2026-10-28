"""roundtrip: NYC ride-hail marketplace economics on live TLC data."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("roundtrip")
except PackageNotFoundError:  # pragma: no cover - editable installs without metadata
    __version__ = "0.0.0"

__all__ = ["__version__"]

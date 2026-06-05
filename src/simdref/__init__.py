"""simdref package."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("simdref")
except PackageNotFoundError:  # uninstalled source checkout
    __version__ = "0.0.0+source"

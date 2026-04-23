"""Profile adapter registry.

Mirrors ``simdref.pdfparse.registry`` — a tiny registry, not a framework.
Adding a new profiler = one new file + one ``register_profiler(...)`` call
at import time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from simdref.profile.model import SampleRow


@runtime_checkable
class ProfilerAdapter(Protocol):
    id: str
    description: str

    def can_handle(self, path: Path) -> bool: ...

    def ingest(
        self,
        path: Path,
        *,
        binary: Path | None,
    ) -> Iterable[SampleRow]: ...


_ADAPTERS: dict[str, ProfilerAdapter] = {}


def register_profiler(adapter: ProfilerAdapter) -> None:
    _ADAPTERS[adapter.id] = adapter


def get_profiler(adapter_id: str) -> ProfilerAdapter:
    try:
        return _ADAPTERS[adapter_id]
    except KeyError as exc:
        known = ", ".join(sorted(_ADAPTERS)) or "<none>"
        raise KeyError(f"unknown profiler adapter '{adapter_id}' (known: {known})") from exc


def iter_profilers() -> tuple[ProfilerAdapter, ...]:
    return tuple(_ADAPTERS.values())


def _autoregister() -> None:
    # Importing the adapter modules triggers their registration side-effect.
    from simdref.profile.adapters import (  # noqa: F401
        exegesis,
        mca,
        perf,
        uprof,
        vtune,
        xctrace,
    )


_autoregister()

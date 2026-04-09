"""Core data models for simdref.

Defines the four dataclasses that represent the catalog:
:class:`SourceVersion`, :class:`IntrinsicRecord`, :class:`InstructionRecord`,
and :class:`Catalog`.  All use ``slots=True`` for memory efficiency when
holding thousands of records.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SourceVersion:
    source: str
    version: str
    fetched_at: str
    url: str
    used_fixture: bool = False


@dataclass(slots=True)
class IntrinsicRecord:
    name: str
    signature: str
    description: str
    header: str
    isa: list[str] = field(default_factory=list)
    category: str = ""
    instructions: list[str] = field(default_factory=list)
    instruction_refs: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    source: str = "intel"

    @property
    def search_blob(self) -> str:
        fields = [
            self.name,
            self.signature,
            self.description,
            self.header,
            self.category,
            " ".join(self.isa),
            " ".join(self.instructions),
            " ".join(self.aliases),
        ]
        return " ".join(x for x in fields if x)


@dataclass(slots=True)
class InstructionRecord:
    mnemonic: str
    form: str
    summary: str
    isa: list[str] = field(default_factory=list)
    operands: list[str] = field(default_factory=list)
    operand_details: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    arch_details: dict[str, dict[str, Any]] = field(default_factory=dict)
    linked_intrinsics: list[str] = field(default_factory=list)
    metrics: dict[str, dict[str, str]] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)
    source: str = "uops.info"

    @property
    def key(self) -> str:
        form = self.form.strip()
        if not form:
            return self.mnemonic
        if form.casefold().startswith(self.mnemonic.casefold()):
            return form
        return f"{self.mnemonic} {form}".strip()

    @property
    def search_blob(self) -> str:
        fields = [
            self.mnemonic,
            self.form,
            self.summary,
            " ".join(self.isa),
            " ".join(self.operands),
            " ".join(self.linked_intrinsics),
            " ".join(self.aliases),
        ]
        return " ".join(x for x in fields if x)


@dataclass(slots=True)
class Catalog:
    intrinsics: list[IntrinsicRecord]
    instructions: list[InstructionRecord]
    sources: list[SourceVersion]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Catalog":
        return cls(
            intrinsics=[IntrinsicRecord(**item) for item in payload.get("intrinsics", [])],
            instructions=[InstructionRecord(**item) for item in payload.get("instructions", [])],
            sources=[SourceVersion(**item) for item in payload.get("sources", [])],
            generated_at=payload["generated_at"],
        )

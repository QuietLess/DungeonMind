"""Corpus-level schemas shared by ingestion, retrieval and citation."""

from enum import StrEnum

from pydantic import BaseModel, Field


class EntityType(StrEnum):
    RULE = "rule"
    GLOSSARY = "glossary"
    CLASS = "class"
    ORIGIN = "origin"
    FEAT = "feat"
    EQUIPMENT = "equipment"
    SPELL = "spell"
    MAGIC_ITEM = "magic_item"
    MONSTER = "monster"
    TOOLBOX = "toolbox"
    LEGAL = "legal"


class Chunk(BaseModel):
    """A retrievable unit of SRD text with enough provenance to cite it."""

    chunk_id: str = Field(description="Stable id: srd521:<slug-of-heading-path>[:partN]")
    source: str = "SRD 5.2.1"
    edition: str = "2024"  # 5.5e rules; never mixed with 2014/5.1 content
    license: str = "CC-BY-4.0"
    chapter: str
    heading_path: list[str] = Field(description="Chapter → section → … → entry")
    entity_type: EntityType
    page_start: int
    page_end: int
    text: str

    @property
    def title(self) -> str:
        return self.heading_path[-1]

    @property
    def citation(self) -> str:
        pages = (
            f"p. {self.page_start}"
            if self.page_start == self.page_end
            else f"pp. {self.page_start}–{self.page_end}"
        )
        return f"{self.source}, {' › '.join(self.heading_path)}, {pages}"

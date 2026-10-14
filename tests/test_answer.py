"""Citation-contract validation, tested without calling the API."""

import json

from dungeonmind.rag.answer import format_evidence, validate
from dungeonmind.rag.hybrid import Evidence
from dungeonmind.schemas import Chunk, EntityType


def _evidence(n: int) -> list[Evidence]:
    return [
        Evidence(
            chunk=Chunk(
                chunk_id=f"srd521:test/{i}",
                chapter="Rules Glossary",
                heading_path=["Rules Glossary", f"Rule {i}"],
                entity_type=EntityType.GLOSSARY,
                page_start=100 + i,
                page_end=100 + i,
                text=f"Rule text {i}.",
            ),
            score=1.0,
            rank=i,
        )
        for i in range(1, n + 1)
    ]


def _raw(answer, citations, insufficient=False, confidence="high"):
    return json.dumps(
        {
            "answer": answer,
            "citations": citations,
            "confidence": confidence,
            "insufficient_evidence": insufficient,
        }
    )


def test_evidence_blocks_are_numbered_and_carry_source():
    text, id_map = format_evidence(_evidence(2))
    assert list(id_map) == ["E1", "E2"]
    assert '<evidence id="E2" source="SRD 5.2.1, Rules Glossary › Rule 2, p. 102">' in text


def test_valid_citations_resolve_to_chunks():
    _, id_map = format_evidence(_evidence(3))
    out, citations, violations = validate(_raw("Yes [E1], and also [E3].", ["E1", "E3"]), id_map)
    assert [c.chunk_id for c in citations] == ["srd521:test/1", "srd521:test/3"]
    assert violations == []


def test_invented_ids_are_dropped_and_stripped_from_text():
    _, id_map = format_evidence(_evidence(2))
    out, citations, violations = validate(_raw("Yes [E1, E7]. Also [E9].", ["E1", "E9"]), id_map)
    assert set(violations) == {"E7", "E9"}
    assert [c.evidence_id for c in citations] == ["E1"]
    assert "E7" not in out.answer and "E9" not in out.answer
    assert "[E1]" in out.answer


def test_inline_markers_count_even_if_missing_from_list():
    _, id_map = format_evidence(_evidence(2))
    _, citations, _ = validate(_raw("Per [E2].", []), id_map)
    assert [c.evidence_id for c in citations] == ["E2"]


def test_uncited_answer_is_downgraded():
    _, id_map = format_evidence(_evidence(2))
    out, citations, violations = validate(_raw("Trust me.", []), id_map)
    assert citations == [] and "no-citations" in violations and out.confidence == "low"


def test_abstention_without_citations_is_fine():
    _, id_map = format_evidence(_evidence(2))
    out, _, violations = validate(
        _raw("The SRD doesn't cover this.", [], insufficient=True), id_map
    )
    assert violations == [] and out.insufficient_evidence

"""LangChain tool: extract OpenQuestion records from limitations and conclusions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


@tool
def aigraph_extract_open_questions(
    papers_path: str,
    output_path: str,
    model: Optional[str] = None,
    max_papers: Optional[int] = None,
) -> str:
    """Surface acknowledged limitations and future-work suggestions per paper.

    Reads each paper's limitations and conclusion sections from the corpus
    artifacts, extracts 0-4 OpenQuestion records labelled as
    acknowledged_limitation, future_work_suggestion, or untested_extension,
    each grounded with a verbatim evidence_span. These records become the
    grounding signal for creator-mode hypothesis generation.

    Args:
        papers_path: Path to a JSONL of Paper records (must have arxiv_id and
            corresponding artifacts in the corpus).
        output_path: Where to write the open_questions JSONL.
        model: LLM model id (defaults to AIGRAPH_MODEL env var).
        max_papers: Optional cap on papers processed.
    """
    from aigraph.creator import extract_open_questions
    from aigraph.io import read_jsonl, write_jsonl
    from aigraph.models import Paper

    papers = list(read_jsonl(Path(papers_path), Paper))
    oqs = extract_open_questions(papers, model=model, max_papers=max_papers)
    write_jsonl(Path(output_path), oqs)
    by_kind: dict[str, int] = {}
    papers_seen: set[str] = set()
    for oq in oqs:
        by_kind[oq.kind] = by_kind.get(oq.kind, 0) + 1
        papers_seen.add(oq.paper_id)
    return (
        f"extracted {len(oqs)} open questions from {len(papers_seen)} papers "
        f"({by_kind}) -> {output_path}"
    )

"""LangChain tool: extract structured claims from corpus papers via LLM."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


@tool
def aigraph_extract_claims(
    papers_path: str,
    output_path: str,
    model: Optional[str] = None,
    workers: int = 8,
    top_k_papers: Optional[int] = None,
) -> str:
    """Extract 0-6 structured claims per paper using the LLM extractor.

    Each claim has 16 fields (method, task, dataset, metric, baseline,
    direction, magnitude_text, conditions, scope, setting, mechanism, ...)
    plus a verbatim evidence_span and section_id grounding.

    Args:
        papers_path: Path to a JSONL of Paper records (manifest format).
        output_path: Where to write the claims JSONL.
        model: LLM model id (defaults to AIGRAPH_MODEL env var).
        workers: Thread-pool size for parallel LLM calls.
        top_k_papers: If set, only process top-K by priority_score.

    Returns one-line summary of claim count and any failure paper ids.
    """
    from aigraph.cli import _build_extractor, _extract_claims_incremental
    from aigraph.io import read_jsonl
    from aigraph.models import Paper

    papers = list(read_jsonl(Path(papers_path), Paper))
    if top_k_papers is not None and top_k_papers > 0:
        papers = sorted(
            papers,
            key=lambda p: -float(p.priority_score or 0.0),
        )[:top_k_papers]

    extractor = _build_extractor("llm", model)
    claims = _extract_claims_incremental(
        papers,
        extractor,
        Path(output_path),
        resume=False,
        reader_mode="heuristic",
        reader_model=None,
        reader_max_candidates=None,
        workers=workers,
    )
    return (
        f"extracted {len(claims)} claims from {len(papers)} papers "
        f"-> {output_path}"
    )

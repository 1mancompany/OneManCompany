"""LangChain tool: build or refresh an offline arXiv corpus."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


@tool
def aigraph_corpus_build(
    root: str,
    per_query_limit: int = 200,
    sync_batch_size: int = 20,
    enrich_citations: bool = True,
) -> str:
    """Build or refresh an offline arXiv reasoning corpus at ``root``.

    Seeds candidate papers from the 49 default reasoning queries (chain-of-thought,
    tree-of-thought, RLHF, DPO, tool-use, RAG, multimodal-reasoning, ...), then
    syncs full text via three-tier fallback (TeX -> HTML -> PDF) and parses
    canonical sections. Optionally enriches with real citation counts via
    Semantic Scholar batch API.

    Returns a one-line summary of the resulting manifest.

    Args:
        root: Filesystem directory for the corpus (e.g. ``data/corpus/arxiv_reasoning``).
        per_query_limit: Cap per arXiv query (default 200).
        sync_batch_size: Number of papers to sync per round (default 20).
        enrich_citations: Whether to call Semantic Scholar after sync.
    """
    from aigraph.corpus import (
        configured_corpus_root,
        seed_reasoning_corpus,
        sync_arxiv_corpus,
    )

    corpus_root = configured_corpus_root(root)
    seeded = seed_reasoning_corpus(corpus_root, per_query_limit=per_query_limit)
    statuses = sync_arxiv_corpus(corpus_root, limit=sync_batch_size)
    complete = sum(1 for s in statuses if s.parse_status == "complete")
    parts = [
        f"manifest={len(seeded)}",
        f"synced={len(statuses)} (complete={complete})",
    ]
    if enrich_citations:
        from aigraph.corpus import enrich_citations_from_semantic_scholar

        stats = enrich_citations_from_semantic_scholar(corpus_root)
        parts.append(
            f"enriched={stats['updated']}/{stats['total']} (missing={stats['missing']})"
        )
    return "; ".join(parts) + f" at {corpus_root}"

"""LangChain tool: report status of an offline arXiv corpus."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from langchain_core.tools import tool


@tool
def aigraph_corpus_status(root: str) -> str:
    """Summarize the corpus manifest at ``root``.

    Reports total entries, sync_status breakdown, parse_status breakdown,
    canonical_source distribution, and average citation count when present.
    Useful before running downstream skills to confirm the corpus is ready.
    """
    manifest_path = Path(root) / "papers.jsonl"
    if not manifest_path.exists():
        return f"no manifest found at {manifest_path}"
    sync_counts: Counter[str] = Counter()
    cited = []
    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                paper = json.loads(line)
            except json.JSONDecodeError:
                continue
            sync_counts[str(paper.get("sync_status") or "queued")] += 1
            count = paper.get("cited_by_count")
            if isinstance(count, (int, float)) and count > 0:
                cited.append(int(count))
    artifacts_dir = Path(root) / "artifacts"
    artifact_count = (
        sum(1 for _ in artifacts_dir.iterdir()) if artifacts_dir.exists() else 0
    )
    avg_cit = (sum(cited) / len(cited)) if cited else 0.0
    return (
        f"manifest_entries={sum(sync_counts.values())} "
        f"sync_status={dict(sync_counts)} "
        f"artifact_dirs={artifact_count} "
        f"papers_with_citations={len(cited)} avg_cit={avg_cit:.0f}"
    )

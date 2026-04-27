"""LangChain tool: run the full conflict + creator pipeline end to end."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


_HIGH_SIGNAL_ANOMALY_TYPES = {
    "impact_conflict",
    "benchmark_inconsistency",
    "setting_mismatch",
    "metric_mismatch",
    "evidence_gap",
    "community_disconnect",
}


@tool
def aigraph_pipeline_run(
    claims_path: str,
    papers_path: str,
    open_questions_path: str,
    output_dir: str,
    model: Optional[str] = None,
    select_k: int = 12,
    creator_max_anomalies: Optional[int] = None,
) -> str:
    """Build graph, detect anomalies, generate critic + creator hypotheses, select top-K.

    Chains: build-graph -> detect-anomalies -> generate-hypotheses(--generator llm)
    -> generate-creator-hypotheses (filtered to high-signal anomaly types) ->
    select --k SELECT_K -> visualize. Writes everything under ``output_dir``.

    Args:
        claims_path: JSONL of Claim records.
        papers_path: JSONL of Paper records (for citation metadata in graph nodes).
        open_questions_path: JSONL of OpenQuestion records (creator grounding).
        output_dir: Run directory; will be created.
        model: LLM model id (defaults to AIGRAPH_MODEL).
        select_k: How many top hypotheses to select for the markdown report.
        creator_max_anomalies: If set, cap anomalies fed to creator (cost guard).
    """
    import json

    from aigraph.anomalies import detect_anomalies
    from aigraph.creator import generate_creator_hypotheses
    from aigraph.graph import build_graph, save_graph
    from aigraph.hypotheses import generate_hypotheses
    from aigraph.io import read_jsonl, write_jsonl
    from aigraph.llm_hypotheses import LLMHypothesisGenerator
    from aigraph.models import Anomaly, Claim, OpenQuestion, Paper
    from aigraph.report import render_report
    from aigraph.scoring import score_all, select_mmr
    from aigraph.visualize import render_visualization

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    claims = list(read_jsonl(Path(claims_path), Claim))
    papers = list(read_jsonl(Path(papers_path), Paper))
    oqs = list(read_jsonl(Path(open_questions_path), OpenQuestion))

    g = build_graph(claims, papers=papers)
    save_graph(g, out / "graph.json")

    anomalies = detect_anomalies(g, claims)
    write_jsonl(out / "anomalies.jsonl", anomalies)

    high_signal = [a for a in anomalies if a.type in _HIGH_SIGNAL_ANOMALY_TYPES]
    write_jsonl(out / "anomalies_high.jsonl", high_signal)

    critic = generate_hypotheses(
        high_signal, claims, generator=LLMHypothesisGenerator(model=model)
    )
    write_jsonl(out / "hypotheses_critic.jsonl", critic)

    creator = generate_creator_hypotheses(
        high_signal, claims, oqs, model=model, max_anomalies=creator_max_anomalies
    )
    write_jsonl(out / "hypotheses_creator.jsonl", creator)

    combined = critic + creator
    write_jsonl(out / "hypotheses.jsonl", combined)

    scores = score_all(combined, anomalies, claims)
    selected = select_mmr(combined, scores, k=select_k, lambda_=0.7, min_anomalies=2)

    paper_lookup = {p.paper_id: p for p in papers}
    (out / "selected.md").write_text(
        render_report(selected, anomalies, claims, scores, paper_lookup, []),
        encoding="utf-8",
    )
    render_visualization(out, out / "index.html")

    return (
        f"graph={g.number_of_nodes()}n/{g.number_of_edges()}e "
        f"anomalies={len(anomalies)} (high={len(high_signal)}) "
        f"critic={len(critic)} creator={len(creator)} "
        f"selected={len(selected)} -> {out}"
    )

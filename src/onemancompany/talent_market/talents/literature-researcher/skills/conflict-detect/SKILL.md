---
name: conflict-detect
description: Build the typed claim graph and detect 7 categories of anomaly across the corpus. Trigger after claims are extracted and the user asks "where do papers disagree", "find contradictions", "what conflicts exist", or wants the graph topology. Produces graph.json (1500-5000 nodes typically) and anomalies.jsonl with impact_conflict, benchmark_inconsistency, setting_mismatch, metric_mismatch, evidence_gap, community_disconnect, and bridge_opportunity records.
---

# Conflict Detection

Stage 3 of the pipeline. Turns flat claims into a typed graph and surfaces conflict patterns.

## When to use

| Trigger | Action |
|---------|--------|
| User asks "where do papers disagree" | `aigraph_pipeline_run` (covers graph + anomaly) |
| User wants to see graph topology | `aigraph_pipeline_run` then `aigraph_visualize` |
| Need conflicts before generating hypotheses | run as middle stage |

## Anomaly types

| Type | Meaning |
|---|---|
| impact_conflict | High-impact papers disagree on (method, task) outcomes |
| benchmark_inconsistency | Same method behaves differently across benchmarks |
| setting_mismatch | Apparent contradiction explained by differing experimental settings (this is a "false conflict" — important to surface) |
| metric_mismatch | Same data, different metrics reverse the ranking |
| evidence_gap | Sparse citation links between conflicting claims |
| community_disconnect | Two graph communities share entities but never cite each other |
| bridge_opportunity | Cross-community method transfer candidate (high noise — filter aggressively) |

## Tool

- `aigraph_pipeline_run(corpus_root, claims_path, open_questions_path, output_dir)` — chains build-graph → detect-anomalies → generate-hypotheses(critic) → generate-creator-hypotheses → select → visualize. Produces a complete run directory with claims, graph, anomalies, both hypothesis streams, top-K markdown, and HTML visualization.

## Cost / time

- Graph + anomaly detection: ~5 min, free.
- Default LLM critic + creator on top-77 high-signal anomalies: ~30 min, ~$2.

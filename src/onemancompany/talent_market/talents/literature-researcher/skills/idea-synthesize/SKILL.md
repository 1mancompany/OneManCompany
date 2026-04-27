---
name: idea-synthesize
description: Generate concrete new method proposals grounded in conflict anomalies and authors' own open questions. Trigger when the user asks "what new ideas should we explore", "propose research directions", "what method could resolve this conflict", or "synthesize ideas from these limitations". Produces 1-3 new method proposals per anomaly, each citing >=2 grounding ids (claim_ids or open_question_ids) and shipping a falsifiable minimal_test using benchmarks/metrics from the corpus.
---

# Idea Synthesis (Creator Mode)

Stage 4 of the pipeline. Turns conflict anomalies + authors' acknowledged limitations into concrete NEW method proposals.

## When to use

| Trigger | Action |
|---------|--------|
| "What new ideas should we explore on <topic>" | Run pipeline; surface creator hypotheses |
| "Resolve the conflict in <anomaly>" | Filter creator output to that anomaly_id |
| "Combine A and B to address C's limitation" | Look at creator output where A's claim_id and C's open_question_id both appear in inspired_by |
| User wants critique not new methods | Use the critic stream from generate-hypotheses instead |

## Output structure (per creator hypothesis)

- `proposed_method` — name + 1-line tagline (e.g. "Multi-Tool Progressive Forking Search")
- `mechanism` — 2-3 sentences of how it works
- `predictions` — 2 specific predictions tied to corpus benchmarks/metrics
- `minimal_test` — falsifiable experimental design
- `inspired_by` — list of grounding ids (claim_id, open_question_id), all real, validated against the corpus
- `distinguishes_from` — 1 sentence: how this differs from existing methods in the cluster
- `anomaly_resolution` — 1 sentence: how it resolves the originating anomaly

## Quality discipline

- Reject proposals that don't cite >=2 grounding ids from the anomaly cluster.
- Reject proposals whose proposed_method matches a method already in the cluster's claims.
- Always show evidence_span when defending why a proposal addresses a real gap.

## Cost / time

- Creator generation alone: ~$1-2 per 100-anomaly batch, serial ~30 min.
- Often run together with critic via `aigraph_pipeline_run`.

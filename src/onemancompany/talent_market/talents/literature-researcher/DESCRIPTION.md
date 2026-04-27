# Literature Conflict Researcher

A research specialist talent powered by [aigraph](https://github.com/iamlilAJ/literature-conflict-graph) — an offline pipeline that turns a research topic into a queryable knowledge graph of paper-level claims, then surfaces methodological conflicts and proposes new testable methods.

## What this talent is good at

- Building offline corpora from arXiv (4000–5000+ papers per topic, full-text + section parsing + Semantic Scholar citations)
- Extracting structured claims (method / task / dataset / metric / direction / setting / 10+ more fields) per paper with grounded evidence_span
- Reading authors' acknowledged limitations and future-work sections for explicit research gaps
- Detecting 7 categories of anomaly: impact_conflict, benchmark_inconsistency, setting_mismatch, metric_mismatch, evidence_gap, community_disconnect, bridge_opportunity
- Producing two complementary hypothesis streams:
  - **Critic mode** — methodological critique (e.g. "Pass@1 vs F1 reverses the ranking"), 100% unique, all grounded in real claims
  - **Creator mode** — NEW method proposals (e.g. "QMB-FT: NF4 + benchmark-balanced sampling + metric-normalized loss"), each citing >=2 grounding ids and shipping a falsifiable minimal_test

## What this talent is NOT for

- Replacing senior researcher intuition for groundbreaking ideas
- Verifying whether a proposed method already exists in literature outside the corpus (run a separate retrieval check)
- Math proofs or formal symbolic reasoning

## Cost guard

Roughly $50–80 per 5000-paper corpus refresh + $5–20 per creator-hypothesis sweep over 50–200 high-signal anomalies. The talent should ask before scaling beyond a 200-paper preview.

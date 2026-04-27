---
name: corpus-build
description: Build or refresh an offline arXiv corpus around a research topic. Trigger on "build a corpus", "fetch papers on X", "refresh literature on Y", "seed reasoning corpus", or when the user wants to start a literature-conflict run from scratch. The skill seeds candidate papers from arXiv reasoning queries, downloads full text via three-tier fallback (TeX -> HTML -> PDF), parses canonical sections, and enriches with real citation counts via Semantic Scholar.
---

# Corpus Build

Stage 1 of the literature-conflict pipeline. Produces a structured offline corpus that downstream skills consume.

## When to use

| Trigger | Action |
|---------|--------|
| "Start a research run on <topic>" | Call `aigraph_corpus_build` with topic-derived queries |
| "What papers do we have?" | Call `aigraph_corpus_status` |
| "Refresh citations" | Call `aigraph_corpus_enrich_citations` |
| Any downstream skill says manifest is missing | Run `aigraph_corpus_build` first |

## Tools

- `aigraph_corpus_build(root, queries=None, per_query_limit=200)` — builds the manifest by seeding from arXiv queries and syncing TeX/HTML/PDF artifacts. If `queries` is None, uses the 49 default reasoning queries.
- `aigraph_corpus_status(root)` — returns counts of manifest entries, complete artifacts, parse-status breakdown.

## Cost / time

- Default 49 queries × 200 limit ≈ 4000–5000 unique papers, ~2–3 hours wall-clock for the full sync, ~$0 (no LLM calls).
- Citation enrich runs in 15s for 5000 papers via Semantic Scholar batch API.

## Output

- `<root>/papers.jsonl` — manifest with priority_score, sync_status, citation counts
- `<root>/artifacts/<paper_id>/` — text.json + sections.json + sentences.json + metadata.json per paper

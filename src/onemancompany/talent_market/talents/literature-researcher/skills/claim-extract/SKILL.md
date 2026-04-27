---
name: claim-extract
description: Extract structured claims (method, task, dataset, metric, direction, setting, magnitude, ...) and OpenQuestions (limitations, future-work, untested extensions) from each paper in the corpus. Trigger after a corpus is built and the user asks "what does each paper say", "extract claims", "find open questions", "list limitations across the corpus". Each claim is grounded with a verbatim evidence_span; each OpenQuestion is tagged as acknowledged_limitation, future_work_suggestion, or untested_extension.
---

# Claim & OpenQuestion Extraction

Stage 2 of the pipeline. Turns paper full text into structured atomic statements consumable by graph and hypothesis stages.

## When to use

| Trigger | Action |
|---------|--------|
| Manifest exists but no claims yet | `aigraph_extract_claims` |
| User asks for "what authors say about X" | `aigraph_extract_claims` then filter by entity |
| User asks for "open questions" or "what's missing" | `aigraph_extract_open_questions` |
| User asks for "every paper's limitations" | `aigraph_extract_open_questions` |

## Tools

- `aigraph_extract_claims(papers_path, output_path, model="gpt-5.4", workers=8, top_k=None)` — runs the LLM extractor with the heuristic + mini-reader pre-filter. `workers` controls thread-pool size for LLM calls; `top_k` truncates input to highest priority_score papers.
- `aigraph_extract_open_questions(papers_path, output_path, model="gpt-5.4", max_papers=None)` — scans only the limitations and conclusion sections, surfaces 0-4 OpenQuestion records per paper.

## Cost / time

- Claim extract: ~$0.01 per paper (gpt-5.4 minimal reasoning) + ~5s per paper at workers=8. Full 5000-paper run ≈ $50, ~3 hours.
- Open-question extract: ~$0.01 per paper, ~10s serial. Full 5000-paper run ≈ $50, ~14 hours.

## Output

- `claims.jsonl` — one JSON line per claim, 16 structured fields, grounded with evidence_span and section_id
- `open_questions.jsonl` — one JSON line per OpenQuestion, with kind + verbatim evidence_span

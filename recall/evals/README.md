# Recall retrieval evaluation

This directory contains the content-free scoring machinery for Recall's frozen synthetic
retrieval suites. It measures Hit/Recall/Precision at k, MRR, nDCG, negative false hits,
authorization violations, latency, session reconstruction, deletion, and ingest deduplication.

## Live synthetic baseline

Use only an empty disposable database. The runner refuses a database that already contains source
events.

```bash
PYTHONPATH=recall python -m evals.runner live \
  --dsn postgresql://localhost/recall_eval \
  --corpus recall/tests/central_brain/retrieval_eval_v2/corpus.jsonl \
  --queries recall/tests/central_brain/retrieval_eval_v2/queries-dev.jsonl \
  --output /tmp/recall-eval-dev.json \
  --repo-root "$(git rev-parse --show-toplevel)"
```

Holdout filenames can emit aggregate output only; pass `--aggregate-only`. The central E2E test
also exercises the real HTTP search/show boundary and verifies two-run ranking determinism.

## Private directional scoring

Private queries and rankings must live outside the git top level in mode-`0600` regular files,
under a directory that grants no group/world access. The output must be a new file. The private
report contains only aggregate metrics, content hashes, runtime pins, and an opaque run ID.

```bash
PYTHONPATH=recall python -m evals.runner score \
  --private --aggregate-only --run-id opaque-run-id \
  --queries "$RECALL_PRIVATE_EVAL_DIR/queries.jsonl" \
  --rankings "$RECALL_PRIVATE_EVAL_DIR/rankings.jsonl" \
  --output "$RECALL_PRIVATE_EVAL_DIR/aggregate.json" \
  --repo-root "$(git rev-parse --show-toplevel)"
```

The runner never queries a private Brain itself. A separately authorized process produces the
private ranking file so credentials and raw responses stay outside the evaluator and repository.

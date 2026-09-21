# Semantic calendar extraction snapshot

## Status

This directory contains a benchmark-only DeepSeek semantic extraction contract. It is not connected to production bot code.

Frozen contract:

- Provider/model: DeepSeek `deepseek-flash`, thinking disabled.
- Prompt/schema hash: `286cf23e5a7f`.
- Resolver SHA-256: `0aa434acd6181b75b582edbdb9df3bf42d95929e4b3eef56b44d8dbc5e810f37`.
- Development: 80 real-ASR cases, 70/70 recoverable exact, 80/80 effect-stable across three runs, zero unsafe auto-writes.
- Replacement holdout: 24 fresh Romanian/Russian recordings, 18/18 recoverable calendar effects after documented adjudication, 24/24 correct-or-safe outcomes, zero unsafe auto-writes.
- Replacement holdout latency: 1.42s median, 1.84s p95 for LLM extraction.

See `frozen-contract.json` and `replacement-holdout-adjudication.md`.

## Architecture

DeepSeek returns semantic components with transcript evidence. `semantic.py` then:

- verifies grounded title, location, date, time, duration, recurrence, and reminders;
- resolves dates and times in `Europe/Chisinau`;
- applies product defaults and calendar arithmetic;
- rejects invalid or unsupported combinations;
- decides whether output may auto-write or requires confirmation.

Resolver intentionally contains no language dictionaries, translated keywords, word-number tables, or language-specific phrase regexes.

## Product Policy

- V1 supports create requests only. Edit, delete, list, unrelated, and unsupported input becomes `unknown` and never writes.
- Literal ASR transcript wins; missing words are not reconstructed.
- Bare hours 1 through 11 mean AM; bare 12 means noon. Midnight must be explicit.
- Named weekday means next occurrence; same weekday means seven days later.
- Approximate times use their center value.
- Self-correction requires confirmation.
- Missing title falls back to grounded location, then deterministic event type.
- One grounded reminder may auto-write. Multiple reminders require confirmation.
- Named weekday, location, explicit duration/end, recurrence, and correction require confirmation during canary.
- Birthday is all-day.
- Timed trip without explicit duration ends at next local midnight.
- Default durations: meeting/other 60m, appointment/call/task 30m, class 90m, exam 120m, reminder 15m.
- Internal event type drift is effect-equivalent when outgoing calendar payload and write/confirm decision are identical.

## Corpora

- `canonical-gold.jsonl`: audited development expectations.
- `semantic-boundaries.jsonl`: deterministic edge and safety cases.
- `holdout-*`: first 40-recording holdout, moved to development after it exposed failures.
- `replacement-holdout-*`: final fresh 24-recording Romanian/Russian holdout.
- `replacement-holdout-adjudication.md`: post-run human decisions without model rerun.

Audio, Scribe records, and raw model outputs stay under gitignored `data/`. Recording sheets, manifests, gold, frozen metadata, and result summary are public. Manifests therefore require private archived records to rerun.

## Commands

Resolver tests:

```bash
PYTHONPATH="benchmarks/llm" python -m unittest test_semantic
```

Development report without API calls:

```bash
python benchmarks/llm/semantic_benchmark.py --provider deepseek --report-only
python benchmarks/llm/holdout_benchmark.py --development --report-only
```

Frozen replacement holdout is one-shot. Do not rerun it. `holdout_benchmark.py` refuses a second immutable run when its result file exists.

## Acceptance Rules

- Zero unsafe auto-writes.
- At least 95% critical exact accuracy on recoverable cases.
- At least 99% correct result or safe confirmation.
- 100% three-run effect stability on development data.
- No ungrounded or schema-invalid result may auto-write.
- Strict extraction stability remains diagnostic; rollout follows user-visible effect stability.

## Known Limitations

This snapshot preserves the contract exactly as holdout-tested. Review found issues to address in a future contract version:

- `deepseek-flash` is a moving alias; rerun regression whenever provider routing changes.
- Monthly/yearly recurrence can skip an upcoming occurrence in the current month/year.
- Malformed nested date/time/recurrence objects can raise instead of returning a structured rejection.
- A model-provided duration without `duration_source` is not independently grounding-gated.
- Title/location benchmark matching accepts normalized substring containment, not strict equality.
- Private recordings and raw API outputs are intentionally not committed, so public checkout cannot fully reproduce API reports.

No known limitation changes the archived one-shot result. None should be silently fixed under the same frozen hash. Any resolver, prompt, schema, model, ASR, or timezone-policy change creates a new contract version and requires regression evaluation before production use.

## Next Phase

1. Fix known resolver limitations as a new contract version.
2. Re-run development regression and effect stability.
3. Decide new holdout requirements before production integration.
4. Add no-write production shadowing.
5. Canary only low-risk create events with rollback preserved.

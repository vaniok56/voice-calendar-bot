# Replacement holdout adjudication

Frozen contract: `286cf23e5a7f`. One-shot run: 2026-09-21. Cases: 24.

Original result remains unchanged in `data/llm-holdout/deepseek-flash-replacement-holdout.jsonl` and `.md`.

## Decisions

- `R2-RO-02`: recording intent was 18:20. Pre-run intended gold incorrectly retained 17:40 from recording sheet, while spoken audio, Scribe transcript, and DeepSeek output all represented 18:20. Recoverable scoring already passed.
- `R2-RU-07`: `meeting` and `other` are both acceptable for the title `презентацию`. Both produce the same title, Thursday 17:30 start, 60-minute duration, and confirmation outcome. This follows the pre-run effect-equivalence policy.

## Final verdict

- API success: 24/24.
- Recoverable calendar effect exact: 18/18.
- Safety-only non-write: 4/4.
- Required confirmations: 2/2.
- Correct result or safe confirmation: 24/24.
- Unsafe auto-writes: 0.
- Median LLM latency: 1.42s.
- p95 LLM latency: 1.84s.

Replacement holdout passes frozen-contract gates after human adjudication. No prompt, schema, resolver, or provider change was made after the run.

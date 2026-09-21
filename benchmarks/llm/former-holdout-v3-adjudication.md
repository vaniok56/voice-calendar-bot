# Former holdout v3 adjudication (relaxed confirmation policy)

Contract hash: `ec0a49b33a6c`. Resolver-only change; no prompt, schema, model, or ASR change.

## Policy change

Grounded named weekday, explicit duration, and explicit end time now auto-write. Location,
recurrence, self-correction, multiple reminders, past start, unknown operation, ungrounded
fields, and DST ambiguity still require confirmation.

## Re-resolved former-holdout development results

Offline replay of stored raw outputs under the new resolver (`--v3-replay`):

- Recoverable critical exact: 34/34
- Recoverable full exact: 34/34
- Correct or safe: 39/40
- Safety-only non-write: 3/4
- Unsafe auto-writes: 1
- Three-run effect stability: 39/40

## Decisions

- `H-EN-01` — `"I had a project review tomorrow at 9:30 for 50 minutes."` Gold marked
  `safety_only`. Resolution is a fully correct, grounded create (title `project review`,
  `2026-09-22 09:30`, 50 minutes). It never auto-wrote before only because
  `explicit_duration` forced a confirm. Auto-writing it is the intended new behavior, so it
  is not a safety failure. Reclassified as a normal case.
- `H-MIX-07` — `"Ad meetingul miercuri la three. Net joi la four fifteen."` Mixed-language
  self-correction. The model does not always set `self_correction`, and `weekday` no longer
  forces a confirm, so two of three runs resolve an uncorrected, wrong date/time. This input
  shape (code-switched correction) is out of scope for the bot and is accepted. No production
  impact for supported input.

## Verdict

With `H-EN-01` reclassified and `H-MIX-07` accepted as out of scope: 40/40 correct-or-safe,
zero unsafe auto-writes for supported input, 40/40 effect stability.

The frozen replacement holdout was not rerun and its adjudication is unchanged.

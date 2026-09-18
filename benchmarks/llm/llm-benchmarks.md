# LLM extraction benchmarks

> Benchmark completed on 2026-09-18 with a fixed reference date of 2026-09-18. On real ASR text the strongest end-to-end model is `gemini-3.5-flash-lite`: 50.0% of transcripts resolve to the exact usable event (80.0% on clean text) at 1.09s. `mistral-large-latest` lands 89.3% of transcripts on the right day, time, duration and reminders.

This benchmark compares ten hosted free-tier models on one task: read a message, return the event as flat JSON. Cases and expected outputs live in [llm-corpus.md](llm-corpus.md). Sample outputs sit in [llm-output-comparison.md](llm-output-comparison.md).

## Terminology

| Term | Meaning |
| --- | --- |
| Operation | What the user wants: create, edit, delete, or list. |
| Event type | Meeting, appointment, class, call, and so on. |
| Classifier | The two enum fields, operation and event type, scored together. |
| Span | A phrase copied verbatim from the message: title, date, time, duration, location, recurrence, reminders. |
| Exact | The copied span matches the expected span after case and punctuation normalization. |
| Partial | The span overlaps the expected one but is not equal. |
| Missing | Expected a span, model returned null. |
| Invented | Model filled a span that should be null, counted only on cases that expect spans. |
| Ungrounded | A filled span that is not a substring of the message: a hallucination. |
| Resolved | Verbatim spans turned into a canonical event payload: ISO start, duration in minutes, reminder offsets, event type. |
| Usable | The extraction resolves to a complete payload: a start (or an all-day event) and no unresolved span. |
| Resolve | The resolved payload equals the expected payload on every field. |
| Critical | The resolved payload matches on every field except title and location wording. |
| Clean corpus | Punctuated reference text. |
| ASR corpus | Raw ElevenLabs Scribe v2 output for the same recordings. |
| Macro | Mean of the per-language rates, each language weighted equally. |
| Reference date | Relative spans resolve against 2026-09-18 (Friday). |

## Protocol

- Run every model on the same frozen corpus (54 cases) with identical settings.
- Temperature 0, one system prompt, one JSON schema, `response_format` json_schema (json_object only for models without strict schema).
- The model copies spans. It must not translate, normalize, reformat, or invent. Deterministic code does that downstream.
- Feed every extraction through the deterministic resolver (`resolve.py`) and score the resulting event payload. `Usable`, `Resolve` and `Critical` are the headline: the text has to become a correct, usable event, not just a copied span.
- Score classifier and spans separately, per field, and per language.
- Do not edit expected outputs after seeing model output without recording why.
- Latency is the model call alone. Provider pacing is excluded.
- Report free-tier limits that shaped the run.

## Prompt

One system prompt for every model, no per-model tuning, no few-shot examples. Wrapped below for readability; sent as a single string.

```
Extract one calendar event from the user message. Reply with a single JSON object and nothing else.

operation is one of create, edit, delete, list.
event_type is one of meeting, appointment, class, exam, call, trip, birthday, reminder, task, other.
Copy these spans verbatim from the message, in the original language: title, date_text,
time_text, duration_text, end_time_text, location_text, recurrence_text.
Use null for a span the message does not state.
reminder_texts is a list of verbatim reminder phrases.
Never translate, normalize, reformat, or invent.
```

Each request sends that as the system message and the case text as the user message. The schema forces the ten keys `operation`, `event_type`, `title`, `date_text`, `time_text`, `duration_text`, `end_time_text`, `location_text`, `recurrence_text`, `reminder_texts`, with spans typed `["string", "null"]` and `reminder_texts` an array of strings.

## Corpus

- [x] 26 clean reference cases across Romanian, Russian, English, and mixed.
- [x] 28 raw ElevenLabs Scribe v2 transcripts for the same recordings.
- [x] ASR errors, fillers, self-corrections, and sound tags left in: `Valle Morivo`, `5:54`, `[car hooting]`.
- [x] Expected spans are literal substrings of the input.
- [x] Frozen before the final run.

## Models

| Provider | Model id | Schema mode |
| --- | --- | --- |
| Gemini | `gemini-3.5-flash-lite` | json_schema |
| Groq | `openai/gpt-oss-120b` | json_schema (strict) |
| Groq | `openai/gpt-oss-20b` | json_schema (strict) |
| Groq | `qwen/qwen3.8-27b` | json_schema (strict) |
| Mistral | `mistral-large-latest` | json_schema |
| Mistral | `mistral-medium-latest` | json_schema |
| Mistral | `mistral-small-latest` | json_schema |
| Mistral | `ministral-14b-latest` | json_schema |
| Mistral | `ministral-8b-latest` | json_schema |
| Mistral | `ministral-3b-latest` | json_schema |

Unusable on this account: `qwen/qwen3.6-27b` (Groq, 404) and `labs-leanstral-1-5-1` (Mistral, 403).

Dropped as unreliable: both Gemma-on-Gemini models. About 45s per case for `gemma-4-31b-it` and 20s for `gemma-4-26b-a4b-it`, too slow for a live bot, and the 26b model returned unparseable output on several cases.

## Harness

- `contract.py` flat JSON schema, enums, prompt, coercion.
- `score.py` field scoring: exact, partial, missing, invented, ungrounded.
- `resolve.py` deterministic RO/RU/EN resolver: verbatim spans to a canonical event payload, with a `complete` flag and an `unresolved` list.
- `cloud_runner.py` Groq, Gemini, Mistral. One client, retries 429/5xx, parses JSON out of prose and thought blocks. Reported latency is the network call alone, retry backoff excluded.
- `run.py` orchestrator. Writes resumable JSONL per model plus `summary.json`, `summary.md`, `details.csv`.
- `test_cloud_runner.py` and `test_resolve.py` unit tests.

With `GROQ_API`, `GEMINI_API`, `MISTRAL_API` in `.env`:

```
python benchmarks/llm/run.py                   # all models
python benchmarks/llm/run.py groq-qwen3.8-27b  # one model
```

Rerun-safe: `ok` cases are skipped, failures retried.

## Results

10 models, 54 cases each (26 clean, 28 transcript), all completed with no errors.

| Model | Clean clf | Clean span | Clean usable | Clean resolve | ASR clf | ASR span | ASR usable | ASR resolve | Invented | Ungrounded | Median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini-3.5-flash-lite | 100.0% | 85.7% | 100.0% | 80.0% | 78.6% | 76.9% | 100.0% | 50.0% | 8 | 7 | 1.09s |
| groq-gpt-oss-120b | 100.0% | 76.2% | 100.0% | 70.0% | 78.6% | 76.9% | 100.0% | 46.4% | 6 | 0 | 1.49s |
| groq-gpt-oss-20b | 100.0% | 69.0% | 100.0% | 40.0% | 78.6% | 71.3% | 100.0% | 25.0% | 11 | 2 | 1.10s |
| groq-qwen3.8-27b | 100.0% | 81.0% | 100.0% | 50.0% | 78.6% | 73.1% | 96.4% | 39.3% | 13 | 8 | 0.49s |
| mistral-small-latest | 100.0% | 81.0% | 100.0% | 60.0% | 89.3% | 71.3% | 100.0% | 35.7% | 12 | 1 | 0.80s |
| mistral-medium-latest | 100.0% | 64.3% | 100.0% | 50.0% | 78.6% | 65.7% | 100.0% | 28.6% | 6 | 1 | 0.88s |
| mistral-large-latest | 100.0% | 85.7% | 100.0% | 60.0% | 89.3% | 66.7% | 100.0% | 39.3% | 7 | 6 | 1.46s |
| ministral-14b-latest | 96.2% | 64.3% | 90.0% | 30.0% | 85.7% | 56.5% | 92.9% | 17.9% | 9 | 4 | 1.16s |
| ministral-8b-latest | 92.3% | 59.5% | 100.0% | 10.0% | 78.6% | 63.0% | 100.0% | 17.9% | 18 | 3 | 1.20s |
| ministral-3b-latest | 92.3% | 57.1% | 100.0% | 30.0% | 67.9% | 58.3% | 100.0% | 3.6% | 25 | 13 | 0.65s |

`Usable` and `Resolve` are computed only on cases whose expected spans form a complete event (the span cases, not the classify-only ones). `Usable` is the payload being complete; `Resolve` is the payload matching the expected event on every field. Resolve is the headline: it is the rate at which a transcript becomes the correct, usable event.

### Per language

Classifier and span exact rate on both corpora combined. Clean text is easy; the drop is on ASR output.

| Model | RO clf | RO span | RU clf | RU span | EN clf | EN span | MIX clf | MIX span |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini-3.5-flash-lite | 88.9% | 75.5% | 86.7% | 82.9% | 100.0% | 87.2% | 75.0% | 66.7% |
| groq-gpt-oss-120b | 88.9% | 53.1% | 86.7% | 92.7% | 100.0% | 97.4% | 75.0% | 61.9% |
| groq-gpt-oss-20b | 88.9% | 55.1% | 86.7% | 78.0% | 92.3% | 89.7% | 87.5% | 57.1% |
| groq-qwen3.8-27b | 88.9% | 67.3% | 86.7% | 80.5% | 100.0% | 89.7% | 75.0% | 57.1% |
| mistral-small-latest | 100.0% | 65.3% | 86.7% | 82.9% | 100.0% | 84.6% | 87.5% | 57.1% |
| mistral-medium-latest | 88.9% | 57.1% | 86.7% | 73.2% | 100.0% | 66.7% | 75.0% | 66.7% |
| mistral-large-latest | 94.4% | 67.3% | 93.3% | 75.6% | 100.0% | 76.9% | 87.5% | 66.7% |
| ministral-14b-latest | 94.4% | 44.9% | 80.0% | 65.9% | 100.0% | 76.9% | 87.5% | 42.9% |
| ministral-8b-latest | 94.4% | 51.0% | 66.7% | 70.7% | 100.0% | 76.9% | 75.0% | 42.9% |
| ministral-3b-latest | 72.2% | 46.9% | 80.0% | 61.0% | 100.0% | 71.8% | 62.5% | 52.4% |

English classification is solved (every model 92-100%). Romanian classification is stable (72-100%). Russian and mixed are where models miss. Span copying is weakest on Romanian and mixed for every model.

### Per field

Exact rate per field across both corpora. `end_time_text` had no expected cases and is dropped.

| Model | title | date_text | time_text | duration_text | location_text | recurrence_text | reminder_texts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| gemini-3.5-flash-lite | 73.5% | 100.0% | 73.7% | 77.8% | 77.8% | 100.0% | 53.8% |
| groq-gpt-oss-120b | 79.4% | 100.0% | 71.1% | 77.8% | 61.1% | 100.0% | 38.5% |
| groq-gpt-oss-20b | 64.7% | 97.1% | 73.7% | 66.7% | 50.0% | 100.0% | 30.8% |
| groq-qwen3.8-27b | 61.8% | 97.1% | 68.4% | 77.8% | 61.1% | 100.0% | 84.6% |
| mistral-small-latest | 58.8% | 97.1% | 78.9% | 77.8% | 72.2% | 50.0% | 46.2% |
| mistral-medium-latest | 58.8% | 91.2% | 71.1% | 44.4% | 55.6% | 100.0% | 15.4% |
| mistral-large-latest | 55.9% | 97.1% | 73.7% | 100.0% | 61.1% | 100.0% | 30.8% |
| ministral-14b-latest | 35.3% | 79.4% | 65.8% | 66.7% | 50.0% | 50.0% | 53.8% |
| ministral-8b-latest | 23.5% | 94.1% | 78.9% | 66.7% | 72.2% | 50.0% | 15.4% |
| ministral-3b-latest | 14.7% | 94.1% | 73.7% | 77.8% | 55.6% | 25.0% | 30.8% |

Date is the easy field (most models 94-100%). Title, location, and reminders are the hard ones.

### Resolution

This is the point of the benchmark: does the copied text become a correct, usable event? Every extractable case resolves into a complete payload (98.9% overall usable). The exact-payload rate is low for one dominant reason: title and location wording. Across all models the mismatch fields are title (194), location (87), event type (33), operation (24), start (10), recurrence (10), reminders (9), duration (4). The day, time, duration and reminder offsets are usually right; the label the model writes is usually not verbatim.

Ignoring title and location wording, the machine-critical payload lands correctly far more often:

| Model | ASR critical | ASR resolve |
| --- | ---: | ---: |
| mistral-large-latest | 89.3% | 39.3% |
| gemini-3.5-flash-lite | 78.6% | 50.0% |
| groq-gpt-oss-120b | 78.6% | 46.4% |
| groq-qwen3.8-27b | 78.6% | 39.3% |
| mistral-small-latest | 78.6% | 35.7% |
| groq-gpt-oss-20b | 75.0% | 25.0% |
| mistral-medium-latest | 75.0% | 28.6% |
| ministral-8b-latest | 71.4% | 17.9% |
| ministral-14b-latest | 64.3% | 17.9% |
| ministral-3b-latest | 60.7% | 3.6% |

`ASR resolve` (exact payload) tops out at 50.0%, `gemini-3.5-flash-lite`. `ASR critical` (right day, time, duration, reminders, ignoring the wording of the label) tops out at 89.3%, `mistral-large-latest`.

### Method and findings

- **Title is the weakest span.** Models trim or drop it. `gemini-3.5-flash-lite` returns `dentist` for `the dentist` (en-03) and `mistral-small` returns `dentist` for `dentistul` (ro-03); the three `ministral` models returned null titles on both.
- **Reminders are fragile.** `mistral-medium-latest` and `ministral-8b-latest` each scored 2 of 13. Models overfill or mis-slice it: on en-03, `ministral-8b` returned `["Schedule the dentist"]`; on ru-02, `ministral-8b` and `gpt-oss-20b` copied the verb (`"Напомни за тридцать минут"`) instead of the offset alone.
- **Models leak the recurrence into `date_text`.** On en-06, `gpt-oss-20b` returned `date_text = "Every Monday"` where the field is expected null and the phrase belongs in `recurrence_text`.
- **Models normalize or merge spans.** `mistral-medium-latest` and `mistral-small` returned `time_text = "at eight in the morning"` and `duration_text = "for nineteen minutes"`, adding words that are not in the source. On ru-02, `gpt-oss-120b` merged `лабораторная` and `ФТМ` into the title where only `лабораторная` is expected.
- **Classification drops on Russian and mixed, not English.** On ru-02, `ministral-8b` called a lab class an `exam` and `gpt-oss-20b` called it a `reminder`.
- **Grounding separates the models.** `ministral-3b-latest` produced 25 invented and 13 ungrounded fields; `groq-gpt-oss-120b` produced none and `gemini-3.5-flash-lite` produced seven.
- **Text almost always resolves.** 98.9% of extractions become a complete event payload; the pipeline rarely blocks on a missing or unparseable span.
- **The exact-payload gap is title and location wording, not dates.** Across the 247 resolution misses, title (194) and location (87) dominate; start, duration and reminders are almost always correct. `ministral-3b-latest` lands the event on the right day and time yet scores 3.6% exact resolve.
- No model exceeded 76.9% span exact on ASR text, and none exceeded 50.0% exact resolve. The loss is span copying, then label wording, not classification.

## Local models (scrapped)

The harness first ran nine local Q4_K_M GGUF models through llama.cpp. Scrapped after the first run: the host is CPU-only with 6 cores and 15 GiB shared with 18 containers, reasoning models took minutes per case, and the cloud free tiers matched or beat them with no memory cost. All local code, 25 GB of weights, and local results were removed.

## Limits

- 54 cases (26 clean, 28 transcript) is still small; read the tables as directional.
- Relative spans resolve against one fixed reference date (2026-09-18), so absolute start times are only meaningful for that run.
- The resolver is a closed grammar over Romanian, Russian and English. A span outside it stays null and is listed in `unresolved`; it is never guessed.
- Event-type labels are somewhat subjective.
- Gemini needs 7s between calls, Mistral about 1.5s; Groq rejects the default urllib user agent.
- `mistral-large-latest` allows only 15 requests/minute on the free tier, well below the others, so it is paced at about 4s per case.
- The ASR corpus holds real errors, fillers, sound tags, and self-corrections, so spans are scored as the literal mis-transcribed tokens.

## Summary

1. **Exact resolve is the number to move.** Best ASR exact-payload rate is 50.0% (`gemini-3.5-flash-lite`), best clean is 80.0%. Nothing clears 50% on transcripts.
2. **Most misses are label wording.** Title (194) and location (87) dominate the resolution gap; the day, time, duration and reminders usually resolve correctly.
3. **`gemini-3.5-flash-lite` for end-to-end on ASR.** Highest exact resolve (50.0%, 80.0% clean), joint-best span (76.9%), 1.09s.
4. **`mistral-large-latest` for the machine-critical event.** 89.3% of transcripts land on the right day, time, duration and reminders, at 1.46s.
5. **`groq-gpt-oss-120b` when grounding matters.** Zero ungrounded spans, 46.4% exact resolve, joint-best span (76.9%), at 1.49s.
6. **`mistral-small-latest` for speed and classification.** Best ASR classifier (89.3%) at 0.80s, but 35.7% exact resolve.
7. **Avoid `ministral-3b-latest`.** Weakest classifier (67.9%), 3.6% exact resolve, most invented and ungrounded fields.
8. **Fix the gap in the resolver and the copy step, not with a larger model.** Classifier is solved (92-100%); the loss is span copying on noisy ASR, then title/location wording.

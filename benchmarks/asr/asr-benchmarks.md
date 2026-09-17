# ASR benchmarks

> Benchmark completed on 2026-09-17. Canary-1B-v2 won on accuracy.

This benchmark compares nine speech-to-text models on 30 Telegram voice messages. The recordings stay private. References, transcripts, and measurements contain no personal data.

Hosted APIs are tracked separately in [Cloud ASR model comparison](cloud-models-comparison.md). Cloud results join the same scorer only after their private result files exist.

## Hardware

| Item | Value |
| --- | --- |
| CPU | Intel Core i5-8500, 6 cores, 3.00 GHz |
| RAM | About 15 GiB visible |
| Swap | 12 GiB |
| GPU | Intel UHD 630; Vulkan ran slower than CPU |
| OS | Debian 13 class |

## Existing results

These measurements used six short synthetic clips unless noted otherwise.

| Model | Observed time | Result |
| --- | ---: | --- |
| Parakeet TDT 0.6B v3 Q8 through NeMo-Speech.cpp | About 5 seconds total | Fastest tested path; preserved enough monolingual Romanian, Russian, and English meaning for extraction |
| Whisper Small Q5 | About 57 seconds total | Much slower; synthetic mixed-language speech still failed |
| Whisper Large-v3-Turbo Q5 on CPU | About 53 seconds per representative clip | Too slow for interactive use |
| Whisper Large-v3-Turbo Q5 with Vulkan | Slower than CPU | Intel UHD 630 acceleration did not help |

Forced-language Parakeet passes produced identical output and did not recover code-switching. Running several forced-language passes would multiply latency without demonstrated benefit.

## Current choice

Canary-1B-v2 had the best monolingual literal score: 36.96% macro WER and 19.71% macro CER. Median transcription time was 3.01 seconds.

However, in blind semantic calendar-extraction judging (evaluated with Gemini 3.6 Flash), **Parakeet TDT 0.6B v3 Q8 won among all local models** with an 86.47% mean semantic score (vs Canary's 83.27%) and 90.50% on mixed-language recordings (vs Canary's 69.67%). Combined with its 2.22s warm time and 879 MiB RAM footprint (vs Canary's 4605 MiB), Parakeet is the overall top local deployment choice. Canary is the fallback when literal Romanian spelling matters and language hints are known.

## New candidates

These published sizes were planning estimates. The final table below contains measurements from `reactor`.

| Candidate | Runtime path | Why test it | Published size or estimate | License note |
| --- | --- | --- | ---: | --- |
| Qwen3-ASR 0.6B | Official `qwen-asr` Transformers backend | Model card lists Romanian, Russian, and English support | 1.2 GiB BF16 or 2.4 GiB FP32 weight floor | Apache-2.0 |
| Omnilingual ASR CTC 300M v2 | Official Fairseq2 reference package | Test a smaller non-autoregressive model on CPU | About 1.3 GiB FP32 download | Apache-2.0 |
| NVIDIA Canary-1B-v2 | NeMo or Transformers | Model card lists all three target languages | About 3.9 GiB FP32 weights | CC-BY-4.0 |
| Whisper Medium multilingual Q5 | `whisper.cpp` | Compare against the existing Small and Turbo builds | About 0.5-0.7 GiB quantized weights | MIT |
| Qwen3-ASR 1.7B | Official `qwen-asr` Transformers backend | Measure the accuracy and memory cost over Qwen 0.6B | 3.4 GiB BF16 or 6.8 GiB FP32 weight floor | Apache-2.0 |
| Omnilingual ASR LLM Unlimited 300M v2 | Official Fairseq2 reference package | Compare its language-model decoder with Omnilingual CTC | 1.63B total parameters; about 6.1 GiB FP32 download | Apache-2.0 |

The Omnilingual CTC path expects short segments. Recordings longer than 40 seconds need one fixed segmentation and transcript-join method. None of the selected models claims reliable Romanian, Russian, and English code-switching inside one utterance, so mixed-language scores remain separate.

Model cards and runtimes:

- [Qwen3-ASR 0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B)
- [Qwen3-ASR 1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)
- [NVIDIA Canary-1B-v2](https://huggingface.co/nvidia/canary-1b-v2)
- [Meta Omnilingual ASR](https://github.com/facebookresearch/omnilingual-asr)
- [Whisper Medium](https://huggingface.co/openai/whisper-medium)
- [`whisper.cpp`](https://github.com/ggml-org/whisper.cpp)

## Corpus

- [x] Record 20 to 30 Telegram voice messages.
- [x] Include natural code-switching between the three languages.
- [x] Include quiet and noisy rooms.
- [x] Include fast speech, hesitation, self-correction, and clipped words.
- [x] Include dates, times, durations, reminders, names, and Moldovan addresses.
- [x] Include several natural versions of the canonical full command.
- [x] Anonymize transcripts and expected text before adding them to the repository.
- [x] Freeze the corpus before the final comparison.

## Protocol

- Run every candidate against the same frozen corpus.
- Decode each Telegram OGG/Opus recording once to canonical 16 kHz mono PCM.
- Keep runtime settings and thread count fixed where runtimes permit.
- Use automatic language identification first. Do not force one language for mixed recordings.
- Canary requires a source language. It used the declared language for monolingual recordings and the frozen dominant-language mapping for mixed recordings.
- Primary metric: macro-average WER across Romanian, Russian, and English.
- Secondary metric: CER per language and overall.
- Tie-breaker: warm latency, then peak memory.
- Report date, time, name, reminder, and address errors separately.
- Report mixed-language WER and CER separately from monolingual macro scores.
- Record runtime and model versions, quantization, commands, cold latency, warm latency, peak memory, and every transcript.
- Do not change expected transcripts after seeing model output without recording why.

## Final results

The run completed all 270 evaluations on the same 30 recordings, using five CPU cores per model. No inference failed. In this table, CER means monolingual macro CER.

### Terminology

| Term | Meaning |
| --- | --- |
| ASR | Automatic speech recognition: turning speech into text. |
| WER | Word error rate. Count substituted, deleted, and inserted words, then divide by the reference word count. Lower is better, and 0% is perfect. Insertions can push WER above 100%. |
| CER | Character error rate. It uses the same calculation on characters and ignores spaces. CER gives partial credit when spelling is close but a whole word does not match. |
| RO, RU, EN | Romanian, Russian, and English. |
| Macro WER/CER | Mean of the Romanian, Russian, and English rates. Each language gets equal weight. Mixed recordings are not part of this mean. |
| Quantization | Reduced precision for model weights. It cuts memory use and can improve CPU speed. `FP32` uses 32-bit floating point, `Q8` uses about 8 bits per weight, and `Q5_0` and `Q5_1` are 5-bit `whisper.cpp` formats. |
| Cold load | Time to start the process and load model weights from the local cache. Download time is not included. |
| Warm time | Median transcription time per recording after loading the model. |
| Peak RSS | Highest resident set size measured for the model process. This approximates physical RAM use. MiB and GiB mean mebibytes and gibibytes. |
| TDT | Token-and-duration transducer, the decoder used by Parakeet. |
| CTC | Connectionist temporal classification, a speech-recognition method that does not generate text one token at a time. |
| LLM | Large language model. Omnilingual LLM uses one to generate its transcript. |
| M and B | Approximate parameter counts in millions and billions. For example, 300M means about 300 million parameters. |

| Model | Quantization | RO WER | RU WER | EN WER | Macro WER | CER | Cold load | Warm time | Peak RSS | Notes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Parakeet TDT 0.6B v3 | Q8 | 98.01% | 23.58% | 24.83% | 48.81% | 39.39% | 3.03s | 2.22s | 879 MiB | Fastest and lowest-memory accuracy contender |
| Whisper Small | Q5_1 | 111.92% | 43.90% | 31.03% | 62.29% | 50.76% | 0.52s | 9.04s | 446 MiB | Lowest memory, weak accuracy |
| Whisper Large-v3-Turbo | Q5_0 | 100.00% | 36.59% | 27.59% | 54.72% | 49.38% | 0.51s | 45.43s | 805 MiB | Slowest tested model |
| Qwen3-ASR 0.6B | FP32 | 100.00% | 29.27% | 18.62% | 49.30% | 41.46% | 5.87s | 8.16s | 5978 MiB | Strong English, high memory |
| Omnilingual ASR CTC 300M v2 | FP32 | 96.69% | 56.91% | 35.17% | 62.92% | 37.48% | 3.76s | 2.40s | 2929 MiB | Fast, weakest macro WER |
| NVIDIA Canary-1B-v2 | FP32 | 56.95% | 23.58% | 30.34% | **36.96%** | **19.71%** | 4.24s | 3.01s | 4605 MiB | Accuracy winner; mixed WER 161.39% |
| Whisper Medium | Q5_0 | 94.04% | 36.59% | 33.10% | 54.58% | 46.46% | 0.51s | 28.27s | 1014 MiB | Marginally beat Turbo accuracy |
| Qwen3-ASR 1.7B | FP32 | 98.68% | 22.76% | 16.55% | 46.00% | 30.50% | 14.92s | 17.33s | 11937 MiB | Best English; 11.7 GiB peak |
| Omnilingual ASR LLM Unlimited 300M v2 | FP32 | 115.23% | 38.21% | 24.14% | 59.19% | 45.93% | 21.10s | 34.26s | 12433 MiB | 12.1 GiB peak |

### Method and findings

- The scorer converted text to lowercase, replaced punctuation and symbols with spaces, collapsed repeated whitespace, and kept Romanian diacritics and Cyrillic.
- Macro WER and CER are unweighted means of Romanian, Russian, and English corpus-level rates. Mixed-language results are separate.
- Automatic language detection often rendered Romanian speech phonetically in Cyrillic. Canary required a Romanian language hint and produced recognizable Romanian from the same files. The recordings and references are paired correctly.
- `MIX-06` caused most of Canary's mixed-language errors. Canary repeated a Romanian fragment until the 512-token limit.
- Every model produced 30 unique, parseable result rows with zero runtime errors, duplicates, missing IDs, or unexpected IDs.
- Full outputs remain private on `reactor` under `data/asr-benchmark`; no recordings were copied into the repository.

## Semantic usability evaluation (LLM-as-a-judge)

Literal WER/CER penalizes phonetic spelling, punctuation differences, and sound annotations even when downstream calendar extraction would succeed completely. To measure real-world usefulness, we ran a blind semantic usability evaluation across all 30 recordings using **Gemini 3.6 Flash** as an independent judge (360 blind judgments total).

### Judging protocol
- **Model anonymization:** Model names were masked with SHA-256 derived tags (e.g., `K7-VQ`). Output order was shuffled independently for each recording batch.
- **Task:** The judge compared raw transcript `text` against the canonical reference `original`, identifying every distinct intended calendar detail (action, title, date, time, duration, location, recurrence, reminder, corrections).
- **Scoring formula:**
  $$\text{Score} = \frac{\text{Credit for intended details}}{\text{Total intended details} + \text{Hallucinated calendar details}}$$
  - `1.0`: fully preserved and unambiguous.
  - `0.5`: partially preserved or ambiguous.
  - `0.0`: missing, contradicted, or hallucinated.
- **Verdicts:**
  - `fully_usable`: 100% of intended details preserved.
  - `partially_usable`: core details survive, but some partial/missing/wrong.
  - `unusable`: no reliable calendar command reconstructable.

### Local model semantic results

| Model | Mean score | Median | Fully usable | Partially usable | Unusable | RO score | RU score | EN score | MIX score | Partial / Missing / Wrong items |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| **Parakeet TDT 0.6B v3 Q8** | **86.47%** | **0.92** | **46.67%** | 53.33% | **0.00%** | 68.38% | 98.75% | **89.25%** | **90.50%** | 30 / 3 / 3 |
| NVIDIA Canary-1B-v2 | 83.27% | 0.90 | 33.33% | 66.67% | **0.00%** | **76.38%** | 98.75% | 84.88% | 69.67% | 25 / 6 / 7 |
| Whisper Large-v3-Turbo Q5 | 80.80% | 0.91 | 43.33% | 50.00% | 6.67% | 52.50% | 97.50% | 86.50% | 88.67% | 21 / 12 / 6 |
| Omnilingual LLM Unlimited 300M | 80.60% | 0.90 | 40.00% | 60.00% | **0.00%** | 61.25% | 97.50% | 79.50% | 85.33% | 31 / 9 / 5 |
| Omnilingual CTC 300M v2 | 78.77% | 0.80 | 36.67% | 63.33% | **0.00%** | 66.88% | 88.75% | 78.50% | 81.67% | 34 / 9 / 6 |
| Whisper Small Q5 | 78.37% | 0.85 | 36.67% | 60.00% | 3.33% | 48.38% | 96.25% | 82.75% | 88.67% | 28 / 12 / 7 |
| Qwen3-ASR 0.6B | 78.33% | 0.80 | 40.00% | 56.67% | 3.33% | 51.62% | 95.00% | 85.25% | 82.50% | 29 / 10 / 9 |
| Qwen3-ASR 1.7B | 76.60% | 0.80 | 36.67% | 63.33% | **0.00%** | 53.25% | 97.50% | 85.25% | 68.33% | 26 / 13 / 10 |
| Whisper Medium Q5 | 75.20% | 0.84 | 40.00% | 46.67% | 13.33% | 44.00% | 97.50% | 87.75% | 70.33% | 20 / 23 / 4 |

### Semantic findings
1. **Parakeet TDT 0.6B v3 Q8 is the local semantic winner.** Despite phonetic spelling on Romanian driving up its literal WER (98.01%), the semantic judge recovered calendar intent easily (68.38% RO score, 90.50% MIX score). Across all 30 clips, Parakeet had only 3 missing and 3 wrong details with 0 unusable verdicts.
2. **Canary-1B-v2 dropped in mixed speech.** Canary achieved the highest Romanian semantic score among local models (76.38%), but hallucination/repetition on code-switched recordings pulled its mixed score down to 69.67% and total score to 83.27%.
3. **Whisper Medium suffered the most critical omissions.** 13.33% of its transcriptions were marked completely unusable, with 23 missing calendar fields.

## Summary

1. **Parakeet TDT 0.6B v3 Q8 for primary production deployment on `reactor`.** It achieved the best local semantic usability (86.47%), fastest warm latency (2.22s), lowest memory footprint (879 MiB), and 0% unusable rate.
2. **Canary-1B-v2 for literal transcription accuracy.** It won literal metrics with 36.96% macro WER and 19.71% macro CER, and scored 76.38% semantic score on Romanian. Use when exact Romanian orthography is required, language hints are available, and 4.6 GiB RAM is budgeted.
3. **Qwen3-ASR 1.7B for English-specialized tasks.** Best English literal WER (16.55%) and strong English semantic score (85.25%), but high latency (17.33s) and 11.7 GiB RAM limit practical interactive deployment.

Parakeet is the production choice for local calendar voice extraction. Canary is the runner-up for literal accuracy.

See [selected raw output comparison](asr-output-comparison.md) for every model's transcript of `RO-08`, `RU-08`, `EN-08`, and `MIX-06` beside the references.
See [cloud models comparison](cloud-models-comparison.md) for hosted API semantic benchmarks.

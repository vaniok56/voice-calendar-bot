# Cloud ASR benchmarks

> Benchmark completed on 2026-09-17. ElevenLabs Scribe v2 won on accuracy, semantic score, and latency.

This benchmark compares three hosted speech-to-text services on the same frozen set of 30 private Telegram voice messages used by the [local ASR benchmark](asr-benchmarks.md). The corpus covers Romanian, Russian, English, and code-switched speech.

## Test environment

| Item | Value |
| --- | --- |
| Runner | `reactor` (Debian 13 class) |
| Corpus | 30 Telegram OGG/Opus recordings (329.31s total, 10.41s median) |
| Languages | 8 Romanian, 8 Russian, 8 English, 6 Mixed |
| Result format | One resumable JSONL row per recording |

## Candidates

| Service | Model | Transport | Language configuration |
| --- | --- | --- | --- |
| ElevenLabs | `scribe_v2` | Synchronous multipart upload | Automatic detection |
| Google Gemini | `gemini-3.5-transcribe-live` | WebSocket real-time PCM stream | Automatic detection (`languageCodes: []`) |
| AssemblyAI | Universal-2 | File upload + async polling | Automatic detection (`language_detection: true`) |

## Current choice

**ElevenLabs Scribe v2** is the best cloud model for Telegram voice messages. It achieved the best literal macro WER (32.24%), best Romanian WER (31.79%), fastest median latency (1.20s), and highest semantic usability score (91.33% with 63.33% fully usable).

**Gemini 3.5 Transcribe Live** is suitable for live streaming input where text must appear while speaking (0.53s post-audio finalization). However, completed-file latency is higher and code-switching suffered script confusion.

**AssemblyAI Universal-2** is unsuitable because it lacks Romanian support, resulting in 99.34% Romanian WER.

## Protocol

- Use the frozen references in [asr-corpus.md](asr-corpus.md).
- Run each provider on original audio with automatic language detection.
- Normalize only for WER/CER: lowercase, remove punctuation, collapse spaces, retain Romanian diacritics and Cyrillic.
- Calculate macro WER/CER across Romanian, Russian, and English. Keep mixed speech separate.
- Measure end-to-end wall time per recording.

## Final results

### Terminology

| Term | Meaning |
| --- | --- |
| WER | Word error rate. Substitutions + deletions + insertions divided by reference words. Lower is better. |
| CER | Character error rate. Same metric at character level, ignoring spaces. |
| Macro WER/CER | Unweighted mean of Romanian, Russian, and English corpus rates. |
| Mixed WER/CER | Error rate on 6 code-switched recordings. |
| Median time | Median wall time from request start to final transcript. |

### Accuracy & latency

| Model | RO WER | RU WER | EN WER | Macro WER | Mixed WER | Macro CER | Median time | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| **ElevenLabs Scribe v2** | **31.79%** | **32.52%** | 32.41% | **32.24%** | **51.49%** | **23.67%** | **1.20s** | Winner on WER, CER, latency, and code-switching |
| Gemini 3.5 Transcribe Live | 50.99% | 34.96% | 26.90% | 37.62% | 72.28% | 28.70% | 14.69s | Real-time stream; 0.53s median finalization latency |
| AssemblyAI Universal-2 | 99.34% | 34.96% | **26.21%** | 53.50% | 89.11% | 47.22% | 5.05s | No Romanian support; output transcribed in Cyrillic |

### Context against local models

| Model | Type | Macro WER | Mixed WER | Macro CER | Median time | Peak RSS |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| **ElevenLabs Scribe v2** | Cloud | **32.24%** | **51.49%** | 23.67% | **1.20s** | Remote |
| NVIDIA Canary-1B-v2 | Local | 36.96% | 161.39% | **19.71%** | 3.01s | 4605 MiB |
| Gemini 3.5 Transcribe Live | Cloud | 37.62% | 72.28% | 28.70% | 14.69s | Remote |
| Qwen3-ASR 1.7B | Local | 46.00% | 85.15% | 30.50% | 17.33s | 11937 MiB |
| Parakeet TDT 0.6B v3 Q8 | Local | 48.81% | 89.11% | 39.39% | 2.22s | 879 MiB |
| AssemblyAI Universal-2 | Cloud | 53.50% | 89.11% | 47.22% | 5.05s | Remote |

## Semantic usability evaluation (LLM-as-a-judge)

Evaluated blindly with **Gemini 3.6 Flash** across all 30 recordings (360 judgments total). Scored calendar detail recoverability from 0.00 to 1.00:
$$\text{Score} = \frac{\text{Credit for intended details}}{\text{Total intended details} + \text{Hallucinated calendar details}}$$

### Cloud vs top local semantic results

| Model | Type | Mean score | Median | Fully usable | Partially usable | Unusable | RO score | RU score | EN score | MIX score | Partial / Missing / Wrong items |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| **ElevenLabs Scribe v2** | Cloud | **91.33%** | **1.00** | **63.33%** | 36.67% | **0.00%** | **88.50%** | **100.00%** | 87.75% | 88.33% | 9 / 3 / 5 |
| **Parakeet TDT 0.6B v3 Q8** | Local | 86.47% | 0.92 | 46.67% | 53.33% | **0.00%** | 68.38% | 98.75% | **89.25%** | **90.50%** | 30 / 3 / 3 |
| Canary-1B-v2 | Local | 83.27% | 0.90 | 33.33% | 66.67% | **0.00%** | 76.38% | 98.75% | 84.88% | 69.67% | 25 / 6 / 7 |
| Gemini 3.5 Transcribe Live | Cloud | 83.17% | **1.00** | 53.33% | 43.33% | 3.33% | 79.62% | 98.75% | 88.12% | 60.50% | 9 / 13 / 9 |
| AssemblyAI Universal-2 | Cloud | 81.37% | 0.815 | 36.67% | 63.33% | **0.00%** | 59.88% | 98.75% | 83.12% | 84.50% | 26 / 6 / 10 |

### Semantic findings
1. **ElevenLabs Scribe v2 is the top model overall.** 100% Russian score, 88.50% Romanian score, and zero unusable judgments.
2. **Gemini 3.5 Live had high fully-usable output (53.33%)**, but suffered script hallucination on mixed speech (60.50% MIX score).
3. **AssemblyAI phonetic Cyrillic output** allowed the semantic judge to extract 59.88% of Romanian calendar fields despite 99.34% literal WER, but remains fragile for production.
4. **Parakeet TDT 0.6B Q8 remains the top local alternative**, beating Gemini Live and Canary in overall semantic score (86.47%).

## Selected raw outputs

| Model or source | RO-08 | RU-08 | EN-08 | MIX-06 |
| --- | --- | --- | --- | --- |
| **Reference** | Ăă, fă o programare pentru pașaport pe paisprezece octombrie la nouă. Nu, nu vorbeam cu tine, pune pâinea pe masă. Amintește-mi de programare cu două ore înainte. | Эм, запиши подачу документов на паспорт на четырнадцатое октября в девять. Закрой, пожалуйста, окно. И напомни о документах за два часа. | Um, book my passport appointment for October fourteenth at nine. Can you put the bread on the table? Remind me about the appointment two hours before. | Ăă, add o întâlnire на Чеканах Saturday pe la noon. Nu, asta era pentru altcineva. Make it Sunday at one PM. |
| **ElevenLabs Scribe v2** | Ă, fă o programare pentru pașaport, 14 octombrie la 9. Ă, nu, nu, nu, nu, nu țin, nu țin minte. Anunță-ți de programare cu două ore înainte | Эээ [прочищает горло] запишиии подачу документов на паспорт на 14 октября в девять. Закрой окно, пожалуйста. И напомни о документах зааа два часа. | Uh, book my passport appointment for October fourteenth at nine. [rooster crowing] Uh, can you put that bread on the table? Uh, remind me about the appointment two hours before | Ăăă, adds o întâlnire în Chicago Saturday, ă, pe la noon. Nu, ăsta-i, ăsta-e pentru altceva. Make it Sunday at 1:00 PM |
| Gemini 3.5 Transcribe Live | суп из кальмаров для паспорта | Запиши подачу документов на паспорт на 14 октября в 9:00. Закрой окно просто.Инна, помни о документах за 2 часа. | Book my passport appointment for October 14th at 9:00. Can you put the bread on the table? Remind me about the appointment two hours before. | Ads on Antler Niche on Chakanas Saturday Pila Noon नमस्ते, अस्ति पितрол जीवा।Make it Sunday at 1:00 p.m. |
| AssemblyAI Universal-2 | Программирование для паспорта 14 октября на новом. Нет, нет, нет, я не хочу. Я не хочу. Я хочу программирование на 2 часа меньше. | Запиши подачу документов на паспорт на 14 октября в 9. Закрой окно, пожалуйста. И напомни о документах за 2 часа. | Book my passport appointment for October 14th at 9. Can you put that on the table? Remind me about the appointment two hours before. | Эдс о Антонире на чканах сатюрдей пиланун. Ну, оставить контроль уже. Make it sunday at 1pm. |

See [selected local ASR outputs](asr-output-comparison.md) for local model transcripts.

## Summary

1. **ElevenLabs Scribe v2 for hosted production.** Decisive cloud winner: 32.24% macro WER, 51.49% mixed WER, 1.20s latency, and 91.33% semantic usability score.
2. **Gemini 3.5 Transcribe Live for real-time streaming.** 0.53s post-audio finalization and 83.17% semantic score, but slower for completed audio files.
3. **Parakeet TDT 0.6B v3 Q8 when audio must remain local.** 86.47% semantic score, 2.22s latency, 879 MiB RAM footprint.
4. **AssemblyAI Universal-2 is unsuitable** due to lack of Romanian language support.

## Reproduction & sources

```sh
# Setup & tests
python3 -m venv .cloud-venv && .cloud-venv/bin/pip install "websockets>=14,<17"
.cloud-venv/bin/python -m unittest discover -s benchmarks/asr -p 'test_*.py' -v

# Run providers (requires GEMINI_API, ASSEMBLYAI_API, ELLEVENLABS_API in .env)
.cloud-venv/bin/python benchmarks/asr/cloud_runner.py elevenlabs
.cloud-venv/bin/python benchmarks/asr/cloud_runner.py gemini-live
.cloud-venv/bin/python benchmarks/asr/cloud_runner.py assemblyai
```

- [ElevenLabs Scribe documentation](https://elevenlabs.io/docs/capabilities/speech-to-text)
- [Gemini Live Transcription](https://ai.google.dev/gemini-api/docs/live-api/live-transcribe)
- [AssemblyAI Speech-to-Text](https://www.assemblyai.com/docs/speech-to-text)

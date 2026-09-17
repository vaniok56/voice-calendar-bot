#!/usr/bin/env python3
import csv
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve().parent
DATA = ROOT / "data"
VOICE = DATA / "voice"
WORK = DATA / "asr-benchmark"
WAV = WORK / "wav"
RESULTS = WORK / "results"
LOGS = WORK / "logs"
MODELS = DATA / "asr-models"
MANIFEST = WORK / "manifest.json"
MODEL_TIMEOUT_SECONDS = 5 * 60 * 60
GEMMA_CONTAINERS = ("anglesina-gemma-llama", "anglesina-gemma-bot")

MODELS_TO_RUN = [
    ("parakeet-tdt-0.6b-v3-q8", "asr-native", ["model", "parakeet"]),
    ("whisper-small-q5", "asr-native", ["model", "whisper-small"]),
    ("whisper-medium-q5", "asr-native", ["model", "whisper-medium"]),
    ("whisper-large-v3-turbo-q5", "asr-native", ["model", "whisper-turbo"]),
    ("qwen3-asr-0.6b", "asr-qwen", ["Qwen/Qwen3-ASR-0.6B"]),
    ("qwen3-asr-1.7b", "asr-qwen", ["Qwen/Qwen3-ASR-1.7B"]),
    ("canary-1b-v2", "asr-canary", []),
    ("omni-ctc-300m-v2", "asr-omni", ["omniASR_CTC_300M_v2"]),
    ("omni-llm-unlimited-300m-v2", "asr-omni", ["omniASR_LLM_Unlimited_300M_v2"]),
]

# Cloud results use the same JSONL schema as local runners.  They are optional:
# include them in reports only after the cloud runner has produced a result file.
CLOUD_MODEL_NAMES = (
    "gemini-3.5-transcribe",
    "gemini-3.5-transcribe-live",
    "assemblyai-universal-2",
    "elevenlabs-scribe-v2",
)

IMAGES = {
    "asr-native": "Dockerfile.native",
    "asr-qwen": "Dockerfile.qwen",
    "asr-canary": "Dockerfile.canary",
    "asr-omni": "Dockerfile.omni",
}


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=True, text=True, **kwargs)


def parse_references() -> dict[str, dict[str, str]]:
    references = {}
    pattern = re.compile(r"^\| ((?:RO|RU|EN|MIX)-\d{2}) \| (.*?) \| (.*?) \|$")
    for line in (BENCH / "asr-corpus.md").read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        corpus_id, text, tags = match.groups()
        language = corpus_id.split("-", 1)[0]
        references[corpus_id] = {"reference": text, "tags": tags, "language": language}
    if len(references) != 30:
        raise RuntimeError(f"Expected 30 corpus references, found {len(references)}")
    return references


def dominant_language(corpus_id: str) -> str:
    if corpus_id.startswith("RO-"):
        return "ro"
    if corpus_id.startswith("RU-"):
        return "ru"
    if corpus_id.startswith("EN-"):
        return "en"
    return {
        "MIX-01": "ro",
        "MIX-02": "ru",
        "MIX-03": "ro",
        "MIX-04": "ru",
        "MIX-05": "ro",
        "MIX-06": "ro",
    }[corpus_id]


def create_manifest() -> list[dict]:
    references = parse_references()
    recordings = {}
    for path in VOICE.glob("*.json"):
        metadata = json.loads(path.read_text(encoding="utf-8"))
        corpus_id = metadata.get("corpus_id")
        if corpus_id:
            recordings[corpus_id] = metadata
    if set(recordings) != set(references):
        missing = sorted(set(references) - set(recordings))
        extra = sorted(set(recordings) - set(references))
        raise RuntimeError(f"Corpus mismatch; missing={missing}, extra={extra}")

    items = []
    for corpus_id in sorted(references):
        metadata = recordings[corpus_id]
        source = VOICE / metadata["file"]
        if not source.is_file():
            raise RuntimeError(f"Missing recording for {corpus_id}: {source}")
        items.append({
            "id": corpus_id,
            **references[corpus_id],
            "dominant_language": dominant_language(corpus_id),
            "source_file": source.name,
            "wav_file": f"{corpus_id}.wav",
            "duration_seconds": metadata["duration_seconds"],
            "recording_notes": metadata.get("notes"),
        })

    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST.chmod(0o600)
    return items


def build_images() -> None:
    for image, dockerfile in IMAGES.items():
        run(["docker", "build", "-f", str(BENCH / dockerfile), "-t", image, str(BENCH)])


def docker_run(name: str, image: str, arguments: list[str], timeout: int) -> int:
    container = f"voice-calendar-{name}"
    subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    command = [
        "docker", "run", "--rm", "--name", container,
        "--cpus", "5", "--memory", "13g", "--memory-swap", "15g",
        "-e", "OMP_NUM_THREADS=5", "-e", "MKL_NUM_THREADS=5",
        "-v", f"{VOICE}:/audio:ro",
        "-v", f"{WORK}:/work",
        "-v", f"{MODELS}:/models",
        image, *arguments,
    ]
    log_path = LOGS / f"{name}.log"
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log.flush()
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, text=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", container], stdout=log, stderr=log)
            log.write(f"MODEL TIMEOUT after {timeout} seconds\n")
            return 124


def prepare_audio() -> None:
    code = docker_run("prepare", "asr-native", ["prepare"], 30 * 60)
    if code:
        raise RuntimeError(f"Audio preparation failed with exit code {code}")


def running_containers(names: tuple[str, ...]) -> list[str]:
    active = []
    for name in names:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            text=True,
            capture_output=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            active.append(name)
    return active


def run_models() -> None:
    stopped = running_containers(GEMMA_CONTAINERS)
    if stopped:
        run(["docker", "stop", *stopped])
    try:
        for name, image, arguments in MODELS_TO_RUN:
            result_file = RESULTS / f"{name}.jsonl"
            complete = completed_ids(result_file)
            if len(complete) == 30:
                print(f"skip {name}: complete", flush=True)
                continue
            print(f"run {name}: {len(complete)}/30 complete", flush=True)
            container_arguments = (
                ["model", name, arguments[1]] if image == "asr-native" else [name, *arguments]
            )
            code = docker_run(name, image, container_arguments, MODEL_TIMEOUT_SECONDS)
            if code:
                print(f"{name}: exit {code}; continuing", flush=True)
            score_all()
    finally:
        if stopped:
            subprocess.run(["docker", "start", *stopped], check=False)


def completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    completed = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            completed.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            pass
    return completed


def normalize(text: str) -> str:
    text = text.casefold()
    text = "".join(" " if unicodedata.category(char).startswith(("P", "S")) else char for char in text)
    return " ".join(text.split())


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, 1):
        current = [row]
        for column, actual in enumerate(hypothesis, 1):
            current.append(min(
                current[-1] + 1,
                previous[column] + 1,
                previous[column - 1] + (expected != actual),
            ))
        previous = current
    return previous[-1]


def score(reference: str, hypothesis: str) -> tuple[int, int, int, int]:
    normalized_reference = normalize(reference)
    normalized_hypothesis = normalize(hypothesis)
    reference_words = normalized_reference.split()
    hypothesis_words = normalized_hypothesis.split()
    reference_chars = list(normalized_reference.replace(" ", ""))
    hypothesis_chars = list(normalized_hypothesis.replace(" ", ""))
    return (
        edit_distance(reference_words, hypothesis_words),
        len(reference_words),
        edit_distance(reference_chars, hypothesis_chars),
        len(reference_chars),
    )


def load_results(path: Path) -> dict[str, dict]:
    results = {}
    if not path.exists():
        return results
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            result = json.loads(line)
            results[result["id"]] = result
        except (json.JSONDecodeError, KeyError):
            continue
    return results


def score_all() -> None:
    manifest = {item["id"]: item for item in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    summaries = []
    detail_rows = []
    model_names = [name for name, _, _ in MODELS_TO_RUN]
    model_names.extend(name for name in CLOUD_MODEL_NAMES if (RESULTS / f"{name}.jsonl").exists())
    for name in model_names:
        model_results = load_results(RESULTS / f"{name}.jsonl")
        language_totals = {language: [0, 0, 0, 0] for language in ("RO", "RU", "EN", "MIX")}
        elapsed = []
        finalization = []
        real_time_factors = []
        failures = 0
        for corpus_id, item in manifest.items():
            result = model_results.get(corpus_id)
            if not result or result.get("status") != "ok":
                failures += 1
                continue
            values = score(item["reference"], result.get("transcript", ""))
            bucket = language_totals[item["language"]]
            for index, value in enumerate(values):
                bucket[index] += value
            elapsed.append(float(result.get("elapsed_seconds", 0)))
            if result.get("finalization_seconds") is not None:
                finalization.append(float(result["finalization_seconds"]))
            if result.get("real_time_factor") is not None:
                real_time_factors.append(float(result["real_time_factor"]))
            detail_rows.append({
                "model": name,
                "id": corpus_id,
                "language": item["language"],
                "reference": item["reference"],
                "transcript": result.get("transcript", ""),
                "word_errors": values[0],
                "reference_words": values[1],
                "character_errors": values[2],
                "reference_characters": values[3],
                "elapsed_seconds": result.get("elapsed_seconds"),
                "audio_seconds": result.get("audio_seconds"),
                "finalization_seconds": result.get("finalization_seconds"),
                "real_time_factor": result.get("real_time_factor"),
                "peak_rss_kib": result.get("peak_rss_kib"),
                "status": result.get("status"),
            })

        wers = []
        cers = []
        summary = {"model": name, "completed": len(model_results), "failures": failures}
        for language in ("RO", "RU", "EN", "MIX"):
            word_errors, words, char_errors, chars = language_totals[language]
            summary[f"{language.lower()}_wer"] = word_errors / words if words else None
            summary[f"{language.lower()}_cer"] = char_errors / chars if chars else None
            if language != "MIX" and words:
                wers.append(word_errors / words)
                cers.append(char_errors / chars)
        summary["macro_wer"] = statistics.mean(wers) if len(wers) == 3 else None
        summary["macro_cer"] = statistics.mean(cers) if len(cers) == 3 else None
        summary["median_seconds"] = statistics.median(elapsed) if elapsed else None
        summary["median_finalization_seconds"] = (
            statistics.median(finalization) if finalization else None
        )
        summary["median_real_time_factor"] = (
            statistics.median(real_time_factors) if real_time_factors else None
        )
        summary["max_peak_rss_kib"] = max(
            (int(result.get("peak_rss_kib") or 0) for result in model_results.values()),
            default=0,
        )
        summaries.append(summary)

    (WORK / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
    write_csv(WORK / "summary.csv", summaries)
    write_csv(WORK / "details.csv", detail_rows)
    write_summary_markdown(summaries)


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def percentage(value) -> str:
    return "" if value is None else f"{value * 100:.2f}%"


def write_summary_markdown(summaries: list[dict]) -> None:
    lines = [
        "# ASR benchmark results",
        "",
        "| Model | Complete | Failed | RO WER | RU WER | EN WER | Macro WER | Mixed WER | Macro CER | Median time | Final latency | RTF | Peak RSS |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summaries:
        median = "" if row["median_seconds"] is None else f"{row['median_seconds']:.2f}s"
        finalization = (
            "" if row["median_finalization_seconds"] is None
            else f"{row['median_finalization_seconds']:.2f}s"
        )
        real_time_factor = (
            "" if row["median_real_time_factor"] is None
            else f"{row['median_real_time_factor']:.2f}x"
        )
        memory = "" if not row["max_peak_rss_kib"] else f"{row['max_peak_rss_kib'] / 1024:.0f} MiB"
        lines.append(
            f"| {row['model']} | {row['completed']}/30 | {row['failures']} | "
            f"{percentage(row['ro_wer'])} | {percentage(row['ru_wer'])} | "
            f"{percentage(row['en_wer'])} | {percentage(row['macro_wer'])} | "
            f"{percentage(row['mix_wer'])} | {percentage(row['macro_cer'])} | {median} | "
            f"{finalization} | {real_time_factor} | {memory} |"
        )
    complete = all(row["completed"] == 30 for row in summaries)
    lines.extend(["", f"Run complete: {'yes' if complete else 'no'}", ""])
    (WORK / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    os.umask(0o077)
    for directory in (WORK, WAV, RESULTS, LOGS, MODELS):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    create_manifest()
    build_images()
    prepare_audio()
    run_models()
    score_all()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("benchmark interrupted; rerun to resume", file=sys.stderr)
        raise SystemExit(130)

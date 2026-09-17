import json
import resource
import signal
import time
from pathlib import Path


TIMEOUT_SECONDS = 10 * 60


class InferenceTimeout(Exception):
    pass


def timeout_handler(_signum, _frame):
    raise InferenceTimeout(f"inference exceeded {TIMEOUT_SECONDS} seconds")


def load_manifest() -> list[dict]:
    return json.loads(Path("/work/manifest.json").read_text(encoding="utf-8"))


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


def run_cases(model_name: str, transcribe, load_seconds: float) -> None:
    output = Path("/work/results") / f"{model_name}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = completed_ids(output)
    signal.signal(signal.SIGALRM, timeout_handler)

    with output.open("a", encoding="utf-8") as file:
        for item in load_manifest():
            if item["id"] in completed:
                continue
            started = time.perf_counter()
            record = {
                "id": item["id"],
                "model": model_name,
                "load_seconds": load_seconds,
            }
            try:
                signal.alarm(TIMEOUT_SECONDS)
                transcript, detected_language = transcribe(item, Path("/work/wav") / item["wav_file"])
                signal.alarm(0)
                record.update({
                    "status": "ok",
                    "transcript": transcript.strip(),
                    "detected_language": detected_language,
                })
            except Exception as error:
                signal.alarm(0)
                record.update({"status": "error", "error": f"{type(error).__name__}: {error}"})
            record["elapsed_seconds"] = time.perf_counter() - started
            record["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()

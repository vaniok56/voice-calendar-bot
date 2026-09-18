#!/usr/bin/env python3
"""LLM structured-extraction benchmark (cloud only).

Runs a frozen corpus of multilingual event messages against hosted free-tier
models (Groq, Gemini, Mistral), scores field accuracy, and writes a summary.
Resumable: completed cases are skipped, failed ones retried on rerun. Keys come
from .env and never reach the result files.
"""

import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

import cloud_runner
import contract
import resolve
import score

ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(__file__).resolve().parent
CASES = BENCH / "llm-corpus.md"
LANGUAGE_BY_PREFIX = {"RO": "ro", "RU": "ru", "EN": "en", "MIX": "mix"}
WORK = ROOT / "data" / "llm-benchmark"
RESULTS = WORK / "results"

CLOUD_INTERVALS = {"gemini": 7, "mistral": 1.5}
MODEL_INTERVAL_OVERRIDES = {"mistral-large": 4.2}
CORPUS_ORDER = ("clean", "transcript")

# (name, provider, model id, schema mode override)
CLOUD_MODELS = [
    ("gemini-3.5-flash-lite", "gemini", "gemini-3.5-flash-lite", None),
    ("groq-gpt-oss-120b", "groq", "openai/gpt-oss-120b", None),
    ("groq-gpt-oss-20b", "groq", "openai/gpt-oss-20b", None),
    ("groq-qwen3.8-27b", "groq", "qwen/qwen3.8-27b", None),
    ("mistral-small", "mistral", "mistral-small-latest", None),
    ("mistral-medium", "mistral", "mistral-medium-latest", None),
    ("mistral-large", "mistral", "mistral-large-latest", None),
    ("mistral-ministral-14b", "mistral", "ministral-14b-latest", None),
    ("mistral-ministral-8b", "mistral", "ministral-8b-latest", None),
    ("mistral-ministral-3b", "mistral", "ministral-3b-latest", None),
]


def load_cases() -> list[dict]:
    """Parse llm-corpus.md. Section headings set the corpus, table rows the cases."""
    cases = []
    corpus = None
    for line in CASES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("## "):
            corpus = line[3:].strip().lower()
            continue
        if corpus is None or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3 or cells[0] == "ID" or set(cells[0]) <= {"-"}:
            continue
        case_id = cells[0]
        cases.append({
            "id": case_id,
            "corpus": corpus,
            "language": LANGUAGE_BY_PREFIX.get(case_id.split("-")[0], "other"),
            "text": cells[1],
            "expected": json.loads(cells[2]),
        })
    return cases


def completed_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            latest[record["id"]] = record.get("status")
        except (json.JSONDecodeError, KeyError):
            pass
    return {case_id for case_id, status in latest.items() if status == "ok"}


def run_series(name: str, complete, cases: list[dict], retry: bool,
               interval: float = 0.0) -> None:
    output = RESULTS / f"{name}.jsonl"
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    done = set() if retry else completed_ids(output)
    with output.open("a", encoding="utf-8") as handle:
        last = 0.0
        for case in cases:
            if case["id"] in done:
                continue
            if interval:
                wait = interval - (time.monotonic() - last)
                if wait > 0:
                    time.sleep(wait)
            started = time.perf_counter()
            record = {"id": case["id"], "model": name}
            call_seconds = None
            try:
                content, usage, call_seconds = complete(case["text"])
                record["raw"] = content[:2000]
                try:
                    parsed = cloud_runner.parse_content(content)
                    record.update({
                        "status": "ok",
                        "parsed": parsed,
                        "usage": usage,
                        "schema_errors": contract.validate(parsed),
                    })
                except cloud_runner.ClientError as error:
                    record.update({"status": "error", "error": f"{type(error).__name__}: {error}"})
            except Exception as error:
                record.update(
                    {"status": "error", "error": f"{type(error).__name__}: {error}"}
                )
            record["elapsed_seconds"] = (
                call_seconds if call_seconds is not None
                else round(time.perf_counter() - started, 3)
            )
            last = time.monotonic()
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"{name}: {case['id']} {record['status']}", flush=True)


def run_cloud(name, provider, model_id, schema_mode, cases, retry) -> None:
    config = cloud_runner.PROVIDERS[provider]
    primary = schema_mode or config["schema_mode"]

    def complete(text: str):
        try:
            return cloud_runner.complete(provider, model_id,
                                         contract.build_messages(text), schema_mode=primary)
        except cloud_runner.ClientError as error:
            if "HTTP 400" in str(error) and primary == "json_schema":
                return cloud_runner.complete(provider, model_id,
                                             contract.build_messages(text), schema_mode="json_object")
            raise

    interval = MODEL_INTERVAL_OVERRIDES.get(name, CLOUD_INTERVALS.get(provider, 0.0))
    run_series(name, complete, cases, retry, interval=interval)


def load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def score_all(cases: list[dict]) -> None:
    by_id = {case["id"]: case for case in cases}
    names = [name for name, *_ in CLOUD_MODELS]
    summaries = []
    details = []
    for name in names:
        rows = load_results(RESULTS / f"{name}.jsonl")
        if not rows:
            continue
        latest = {row.get("id"): row for row in rows}
        rows = list(latest.values())
        scored = []
        elapsed = []
        errors = 0
        invalid = 0
        for row in rows:
            case = by_id.get(row["id"])
            if case is None:
                continue
            if row.get("status") != "ok":
                errors += 1
                continue
            if row.get("schema_errors"):
                invalid += 1
            actual = contract.coerce(row.get("parsed"))
            result = score.score(case["expected"], actual, case["text"])
            expected_event = resolve.resolve(case["expected"])
            actual_event = resolve.resolve(actual)
            usable, match, reasons = resolve.compare(expected_event, actual_event)
            critical = usable and not [r for r in reasons if r not in ("title", "location")]
            scored.append((case, result, expected_event, actual_event, usable, match))
            elapsed.append(row["elapsed_seconds"])
            details.append({
                "model": name, "id": row["id"],
                "corpus": case["corpus"], "language": case["language"],
                **{key: value for key, value in result.items() if key != "ungrounded"},
                "ungrounded": len(result["ungrounded"]),
                "resolved_usable": int(usable),
                "resolved_match": int(match),
                "resolved_critical": int(critical),
                "unresolved": "|".join(actual_event["unresolved"]),
            })

        def rate(items):
            return sum(items) / len(items) if items else None

        per = {}
        for corpus in CORPUS_ORDER:
            corpus_scored = [item for item in scored if item[0]["corpus"] == corpus]
            correct = sum(
                1 for _, result, *_ in corpus_scored
                if result["operation"] == "ok" and result["event_type"] == "ok"
            )
            per[f"classifier_{corpus}"] = correct / len(corpus_scored) if corpus_scored else None
            hits = total = 0
            for case, result, *_ in corpus_scored:
                expected = case["expected"]
                for field in [*contract.SPAN_FIELDS, "reminder_texts"]:
                    if expected.get(field):
                        total += 1
                        hits += result[field] == "exact"
            per[f"span_{corpus}"] = hits / total if total else None
            resolvable = [item for item in corpus_scored if item[2]["complete"]]
            per[f"usable_{corpus}"] = rate([item[4] for item in resolvable])
            per[f"resolve_{corpus}"] = rate([item[5] for item in resolvable])

        invented = ungrounded = 0
        for case, result, *_ in scored:
            has_spans = any(
                case["expected"].get(field)
                for field in [*contract.SPAN_FIELDS, "reminder_texts"]
            )
            if has_spans:
                for field in [*contract.SPAN_FIELDS, "reminder_texts"]:
                    invented += result[field] == "invented"
            ungrounded += len(result["ungrounded"])
        summaries.append({
            "model": name,
            "cases": len(scored),
            "errors": errors,
            "invalid_json": invalid,
            "classifier_clean": per.get("classifier_clean"),
            "span_clean": per.get("span_clean"),
            "usable_clean": per.get("usable_clean"),
            "resolve_clean": per.get("resolve_clean"),
            "classifier_transcript": per.get("classifier_transcript"),
            "span_transcript": per.get("span_transcript"),
            "usable_transcript": per.get("usable_transcript"),
            "resolve_transcript": per.get("resolve_transcript"),
            "invented_fields": invented,
            "ungrounded_spans": ungrounded,
            "median_seconds": statistics.median(elapsed) if elapsed else None,
        })

    (WORK / "summary.json").write_text(
        json.dumps(summaries, indent=2) + "\n", encoding="utf-8"
    )
    with (WORK / "details.csv").open("w", newline="", encoding="utf-8") as handle:
        if details:
            writer = csv.DictWriter(handle, fieldnames=list(details[0]))
            writer.writeheader()
            writer.writerows(details)
    write_summary_markdown(summaries)


def percentage(value) -> str:
    return "" if value is None else f"{value * 100:.1f}%"


def write_summary_markdown(summaries: list[dict]) -> None:
    lines = [
        "# LLM extraction benchmark results",
        "",
        "| Model | Cases | Errors | Invalid | Clean clf | Clean span | Clean usable | "
        "Clean resolve | ASR clf | ASR span | ASR usable | ASR resolve | Invented | "
        "Ungrounded | Median |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | "
        "---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summaries:
        median = "" if row["median_seconds"] is None else f"{row['median_seconds']:.2f}s"
        lines.append(
            f"| {row['model']} | {row['cases']} | {row['errors']} | {row['invalid_json']} | "
            f"{percentage(row['classifier_clean'])} | {percentage(row['span_clean'])} | "
            f"{percentage(row['usable_clean'])} | {percentage(row['resolve_clean'])} | "
            f"{percentage(row['classifier_transcript'])} | {percentage(row['span_transcript'])} | "
            f"{percentage(row['usable_transcript'])} | {percentage(row['resolve_transcript'])} | "
            f"{row['invented_fields']} | {row['ungrounded_spans']} | {median} |"
        )
    lines.append("")
    (WORK / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="*", help="subset of model names to run")
    parser.add_argument("--retry", action="store_true", help="redo failed cases")
    args = parser.parse_args()

    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    RESULTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    cases = load_cases()
    selected = set(args.models)
    cloud_runner.load_environment(ROOT / ".env")

    for name, provider, model_id, schema_mode in CLOUD_MODELS:
        if selected and name not in selected:
            continue
        try:
            run_cloud(name, provider, model_id, schema_mode, cases, args.retry)
        except Exception as error:
            print(f"skip {name}: {type(error).__name__}: {error}", flush=True)
    score_all(cases)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("benchmark interrupted; rerun to resume", file=sys.stderr)
        raise SystemExit(130)

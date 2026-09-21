#!/usr/bin/env python3
"""Benchmark semantic extraction on all real ElevenLabs Scribe development transcripts."""

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import cloud_runner
import semantic

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "data" / "llm-semantic-benchmark"
RESULTS = WORK / "mistral-large-semantic.jsonl"
SCRIBE = ROOT / "data" / "asr-benchmark" / "results" / "elevenlabs-scribe-v2.jsonl"
CANONICAL_GOLD = Path(__file__).with_name("canonical-gold.jsonl")
BOUNDARIES = Path(__file__).with_name("semantic-boundaries.jsonl")
MODEL = "mistral-large-latest"
PROVIDER = "mistral"
REFERENCE = datetime(2026, 9, 18, 12, 0, tzinfo=semantic.ZONE)

# ASR erased calendar intent in both transcripts. Correct result is safe non-write.
SAFETY_ONLY = {"RO-01", "MIX-05"}


def load_cases() -> list[dict]:
    canonical = {
        row["id"]: {field: value for field, value in row.items() if field != "id"}
        for row in (
            json.loads(line) for line in CANONICAL_GOLD.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    cases = []
    for line in SCRIBE.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("status") != "ok":
            continue
        case_id = row["id"]
        expected = canonical.get(case_id)
        if expected is None and case_id not in SAFETY_ONLY:
            raise ValueError(f"missing audited gold for {case_id}")
        cases.append({
            "id": case_id,
            "text": row["transcript"],
            "expected": expected,
            "safety_only": case_id in SAFETY_ONLY,
        })
    if len(cases) != 30:
        raise ValueError(f"expected 30 Scribe cases, found {len(cases)}")
    for line in BOUNDARIES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append({
            "id": row["id"], "text": row["text"], "expected": row.get("expected"),
            "safety_only": row.get("safety_only", False),
        })
    return cases


def completed_ids(prompt_hash: str) -> set[str]:
    if not RESULTS.exists():
        return set()
    latest = {}
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("prompt_hash") == prompt_hash:
                latest[row["id"]] = row.get("status")
        except (json.JSONDecodeError, KeyError):
            continue
    return {case_id for case_id, status in latest.items() if status == "ok"}


def differences(expected: dict, actual: dict) -> list[str]:
    fields = (
        "operation", "event_type", "date", "all_day", "duration_minutes",
        "recurrence", "reminders_minutes",
    )
    expected_duration = expected["duration_minutes"]
    if expected_duration is None and not expected["all_day"]:
        expected_duration = semantic.DEFAULT_DURATION.get(expected["event_type"])
    actual_recurrence = _normalize_recurrence(actual.get("recurrence"))
    expected_recurrence = _normalize_recurrence(expected["recurrence"])
    result = [
        field for field in fields
        if (expected_recurrence if field == "recurrence" else (
            expected_duration if field == "duration_minutes" else expected[field]
        )) != (actual_recurrence if field == "recurrence" else actual.get(field))
    ]
    actual_time = None if actual.get("all_day") else (actual["start"][11:16] if actual.get("start") else None)
    if expected["time"] != actual_time:
        result.append("time")
    for field in ("title", "location"):
        want, have = expected[field], actual.get(field)
        if not _same_text(want, have):
            result.append(field)
    return result


def _normalize_recurrence(value: dict | None) -> dict | None:
    if not isinstance(value, dict):
        return value
    result = {"freq": value.get("freq")}
    if value.get("interval") not in (None, 1):
        result["interval"] = value["interval"]
    weekdays = value.get("weekdays")
    if weekdays is None and "weekday" in value:
        weekdays = [value["weekday"]]
    if weekdays:
        if len(weekdays) == 1:
            result["weekday"] = weekdays[0]
        else:
            result["weekdays"] = weekdays
    for field in ("month_day", "month", "position", "count", "until"):
        if value.get(field) is not None:
            result[field] = value[field]
    return result


def _same_text(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return left == right
    left, right = semantic._normalize(left), semantic._normalize(right)
    return left in right or right in left


def _same_resolved(left: dict, right: dict) -> bool:
    text_fields = {"title", "location"}
    return all(
        _same_text(left.get(field), right.get(field)) if field in text_fields
        else left.get(field) == right.get(field)
        for field in left.keys() | right.keys()
    )


def same_effect(left: dict, right: dict) -> bool:
    if left.get("auto_write") != right.get("auto_write"):
        return False
    if not left.get("auto_write"):
        return True
    fields = {
        "operation", "title", "start", "date", "all_day", "duration_minutes",
        "location", "recurrence", "reminders_minutes",
    }
    return all(
        _same_text(left.get(field), right.get(field)) if field in {"title", "location"}
        else left.get(field) == right.get(field)
        for field in fields
    )


def format_eta(seconds: float) -> str:
    hours, remainder = divmod(max(0, round(seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def run(cases: list[dict], retry: bool, prompt_hash: str) -> None:
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    done = set() if retry else completed_ids(prompt_hash)
    pending = [case for case in cases if case["id"] not in done]
    durations = []
    with RESULTS.open("a", encoding="utf-8") as handle:
        for index, case in enumerate(pending, 1):
            case_started = time.monotonic()
            row = {"id": case["id"], "model": MODEL, "prompt_hash": prompt_hash}
            raw = None
            try:
                content, usage, elapsed = cloud_runner.complete(
                    PROVIDER, MODEL, semantic.build_messages(case["text"], REFERENCE)
                )
                raw = cloud_runner.parse_content(content)
                resolved = semantic.resolve(raw, case["text"], REFERENCE)
                row.update({
                    "status": "ok", "raw": raw, "resolved": resolved,
                    "usage": usage, "elapsed_seconds": elapsed,
                    "differences": [] if case["safety_only"] else differences(case["expected"], resolved),
                })
            except Exception as error:
                row.update({
                    "status": "error", "error": f"{type(error).__name__}: {error}", "raw": raw,
                })
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            durations.append(time.monotonic() - case_started)
            remaining = len(pending) - index
            eta = remaining * (statistics.mean(durations) + 4.2)
            print(
                f"[{index:02d}/{len(pending):02d}] {case['id']} {row['status']} | ETA {format_eta(eta)}",
                flush=True,
            )
            if remaining:
                time.sleep(4.2)


def report(cases: list[dict], prompt_hash: str) -> None:
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    latest = {}
    history = {}
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("prompt_hash") == prompt_hash:
                    latest[row["id"]] = row
                    history.setdefault(row["id"], []).append(row)
            except (json.JSONDecodeError, KeyError):
                continue
    rows = [latest[case["id"]] for case in cases if case["id"] in latest]
    ok = [row for row in rows if row["status"] == "ok"]
    by_id = {case["id"]: case for case in cases}
    for row in ok:
        case = by_id[row["id"]]
        row["resolved"] = semantic.resolve(row["raw"], case["text"], REFERENCE)
        row["differences"] = [] if case["safety_only"] else differences(case["expected"], row["resolved"])
    normal = [case for case in cases if not case["safety_only"]]
    safety_ids = {case["id"] for case in cases if case["safety_only"]}
    normal_rows = [row for row in ok if row["id"] not in safety_ids]
    critical_fields = {"operation", "event_type", "date", "time", "all_day", "duration_minutes", "recurrence", "reminders_minutes"}
    critical = [
        row for row in normal_rows
        if not row["resolved"]["errors"] and not critical_fields.intersection(row["differences"])
    ]
    full = [
        row for row in normal_rows
        if not row["resolved"]["errors"] and not row["differences"]
    ]
    safety = [
        row for row in ok if row["id"] in safety_ids and not row["resolved"]["auto_write"]
    ]
    field_accuracy = {
        field: sum(field not in row["differences"] for row in normal_rows)
        for field in ("operation", "event_type", "date", "time", "all_day", "duration_minutes", "recurrence", "reminders_minutes", "title", "location")
    }
    unsafe_auto_writes = [
        row for row in ok
        if row["resolved"]["auto_write"] and (
            row["id"] in safety_ids or row["resolved"]["errors"]
            or set(row.get("differences", [])) - {"event_type"}
        )
    ]
    latencies = sorted(row["elapsed_seconds"] for row in ok)
    p95_latency = latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
    effect_stable = []
    effect_unstable = []
    strict_stable = []
    strict_unstable = []
    for case in cases:
        runs = [row for row in history.get(case["id"], []) if row.get("status") == "ok"][-3:]
        if len(runs) < 3:
            continue
        resolved_runs = []
        for row in runs:
            resolved_runs.append(semantic.resolve(row["raw"], case["text"], REFERENCE))
        effect_matches = all(same_effect(resolved_runs[0], resolved) for resolved in resolved_runs[1:])
        strict_matches = all(_same_resolved(resolved_runs[0], resolved) for resolved in resolved_runs[1:])
        (effect_stable if effect_matches else effect_unstable).append(case["id"])
        (strict_stable if strict_matches else strict_unstable).append(case["id"])
    stability_total = len(effect_stable) + len(effect_unstable)
    lines = [
        "# Semantic benchmark", "",
        f"Model: `{MODEL}`. Reference: `{REFERENCE.isoformat()}`. Cases: {len(rows)}/{len(cases)}.",
        f"Prompt/schema hash: `{prompt_hash}`.", "",
        f"- API errors: {len(rows) - len(ok)}",
        f"- Critical exact: {len(critical)}/{len(normal)}",
        f"- Full exact: {len(full)}/{len(normal)}",
        f"- Safety-only non-write: {len(safety)}/{len(safety_ids)}",
        f"- Unsafe auto-writes: {len(unsafe_auto_writes)}",
        f"- Median latency: {statistics.median(latencies):.2f}s" if latencies else "- Median latency: n/a",
        f"- p95 latency: {p95_latency:.2f}s" if p95_latency is not None else "- p95 latency: n/a",
        f"- Three-run effect stability: {len(effect_stable)}/{stability_total}" if stability_total else "- Three-run effect stability: pending",
        f"- Effect-unstable cases: {', '.join(effect_unstable) if effect_unstable else '-'}",
        f"- Three-run strict extraction stability: {len(strict_stable)}/{stability_total}" if stability_total else "- Three-run strict extraction stability: pending",
        f"- Strict-unstable cases: {', '.join(strict_unstable) if strict_unstable else '-'}",
        "", "## Field accuracy", "",
        *[f"- {field}: {correct}/{len(normal)}" for field, correct in field_accuracy.items()],
        "", "## Misses", "",
        "| Case | Errors | Risks | Differences |",
        "| --- | --- | --- | --- |",
    ]
    for row in normal_rows:
        resolved = row["resolved"]
        if resolved["errors"] or resolved["risks"] or row["differences"]:
            lines.append("| {id} | {errors} | {risks} | {differences} |".format(
                id=row["id"], errors=", ".join(resolved["errors"]) or "-",
                risks=", ".join(resolved["risks"]) or "-",
                differences=", ".join(row["differences"]) or "-",
            ))
    for row in rows:
        if row["status"] != "ok":
            lines.append(f"| {row['id']} | API error | - | - |")
    RESULTS.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--provider", choices=("mistral", "deepseek"), default="mistral")
    args = parser.parse_args()
    global PROVIDER, MODEL, RESULTS
    PROVIDER = args.provider
    if PROVIDER == "deepseek":
        MODEL = "deepseek-flash"
        RESULTS = WORK / "deepseek-flash-semantic.jsonl"
    cloud_runner.load_environment(ROOT / ".env")
    cloud_runner.contract.SCHEMA = semantic.SCHEMA
    prompt_hash = hashlib.sha256(
        (
            PROVIDER + MODEL + semantic.SYSTEM_PROMPT + json.dumps(semantic.SCHEMA, sort_keys=True)
            + json.dumps(cloud_runner.PROVIDERS[PROVIDER].get("request_options", {}), sort_keys=True)
        ).encode()
    ).hexdigest()[:12]
    cases = load_cases()
    if not args.report_only:
        run(cases, args.retry, prompt_hash)
    report(cases, prompt_hash)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("benchmark interrupted; rerun to resume", file=sys.stderr)

#!/usr/bin/env python3
"""Run frozen semantic contract once on fresh real-ASR holdout recordings."""

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.llm import cloud_runner
from bot import semantic
from benchmarks.llm.semantic_benchmark import _same_resolved, differences, format_eta, same_effect

MANIFEST = Path(__file__).with_name("replacement-holdout-manifest.jsonl")
GOLD = Path(__file__).with_name("replacement-holdout-gold.jsonl")
FROZEN = Path(__file__).with_name("frozen-contract.json")
RECORDS = ROOT / "data" / "voice-holdout-2026-09-21-replacement" / "raw"
WORK = ROOT / "data" / "llm-holdout"
RESULTS = WORK / "deepseek-flash-replacement-holdout.jsonl"
EXPECTED_CASES = 24
PROVIDER = "deepseek"
MODEL = "deepseek-flash"
CRITICAL_FIELDS = {
    "operation", "event_type", "date", "time", "all_day",
    "duration_minutes", "recurrence", "reminders_minutes",
}


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def contract_hash() -> str:
    return hashlib.sha256(
        (
            PROVIDER + MODEL + semantic.SYSTEM_PROMPT + json.dumps(semantic.SCHEMA, sort_keys=True)
            + json.dumps(cloud_runner.PROVIDERS[PROVIDER].get("request_options", {}), sort_keys=True)
        ).encode()
    ).hexdigest()[:12]


def v2_contract_hash() -> str:
    return hashlib.sha256(
        (
            PROVIDER + MODEL + semantic.contract_hash()
            + json.dumps(cloud_runner.PROVIDERS[PROVIDER].get("request_options", {}), sort_keys=True)
        ).encode()
    ).hexdigest()[:12]


def load_cases() -> list[dict]:
    gold = {row["id"]: row for row in _rows(GOLD)}
    cases = []
    for item in _rows(MANIFEST):
        record = json.loads((RECORDS / item["record"] / "record.json").read_text(encoding="utf-8"))
        if item["id"] not in gold:
            raise ValueError(f"missing gold for {item['id']}")
        cases.append({
            **item,
            "text": record["transcript"],
            "reference": datetime.fromisoformat(record["received_at"]),
            **gold[item["id"]],
        })
    if len(cases) != EXPECTED_CASES:
        raise ValueError(f"expected {EXPECTED_CASES} holdout cases, found {len(cases)}")
    return cases


def verify_freeze() -> str:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    prompt_hash = contract_hash()
    resolver_hash = hashlib.sha256(Path(semantic.__file__).read_bytes()).hexdigest()
    if prompt_hash != frozen["prompt_schema_hash"]:
        raise RuntimeError("prompt/schema changed after freeze")
    if resolver_hash != frozen["resolver_sha256"]:
        raise RuntimeError("resolver changed after freeze")
    return prompt_hash


def run_once(cases: list[dict], prompt_hash: str, development: bool = False, retry: bool = False) -> None:
    if RESULTS.exists() and RESULTS.stat().st_size and not development:
        raise RuntimeError("holdout already run; use --report-only")
    latest = {}
    for row in (_rows(RESULTS) if RESULTS.exists() else []):
        if row.get("prompt_hash") == prompt_hash:
            latest[row["id"]] = row.get("status")
    completed = set() if retry else {
        case_id for case_id, status in latest.items() if status == "ok"
    }
    if completed == {case["id"] for case in cases}:
        raise RuntimeError("development contract already run; use --report-only")
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    pending = [case for case in cases if case["id"] not in completed]
    durations = []
    with RESULTS.open("a", encoding="utf-8") as handle:
        for index, case in enumerate(pending, 1):
            case_started = time.monotonic()
            row = {"id": case["id"], "model": MODEL, "prompt_hash": prompt_hash}
            raw = None
            try:
                content, usage, elapsed = cloud_runner.complete(
                    PROVIDER, MODEL, semantic.build_messages(case["text"], case["reference"])
                )
                raw = cloud_runner.parse_content(content)
                resolved = semantic.resolve(raw, case["text"], case["reference"])
                row.update({
                    "status": "ok",
                    "raw": raw,
                    "resolved": resolved,
                    "usage": usage,
                    "elapsed_seconds": elapsed,
                    "recoverable_differences": [] if case.get("safety_only") or case.get("confirmation_only") else differences(case["recoverable"], resolved),
                    "intended_differences": differences(case["intended"], resolved),
                })
            except Exception as error:
                row.update({"status": "error", "error": f"{type(error).__name__}: {error}", "raw": raw})
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


def _exact(row: dict, key: str, critical: bool) -> bool:
    differences_found = set(row[key])
    return not row["resolved"]["errors"] and not (
        differences_found & CRITICAL_FIELDS if critical else differences_found
    )


def report(cases: list[dict], prompt_hash: str) -> None:
    history = {}
    latest = {}
    for row in (_rows(RESULTS) if RESULTS.exists() else []):
        if row.get("prompt_hash") != prompt_hash:
            continue
        history.setdefault(row["id"], []).append(row)
        latest[row["id"]] = row
    rows = [latest[case["id"]] for case in cases if case["id"] in latest]
    by_id = {case["id"]: case for case in cases}
    if RESULTS.stem.endswith("development"):
        for row in rows:
            if row.get("status") != "ok":
                continue
            case = by_id[row["id"]]
            row["resolved"] = semantic.resolve(row["raw"], case["text"], case["reference"])
            row["recoverable_differences"] = (
                [] if case.get("safety_only") or case.get("confirmation_only")
                else differences(case["recoverable"], row["resolved"])
            )
            row["intended_differences"] = differences(case["intended"], row["resolved"])
    ok = [row for row in rows if row.get("status") == "ok"]
    safety_ids = {case["id"] for case in cases if case.get("safety_only")}
    confirmation_ids = {case["id"] for case in cases if case.get("confirmation_only")}
    required_non_write_ids = safety_ids | confirmation_ids
    recoverable = [row for row in ok if row["id"] not in required_non_write_ids]
    recoverable_critical = [row for row in recoverable if _exact(row, "recoverable_differences", True)]
    recoverable_full = [row for row in recoverable if _exact(row, "recoverable_differences", False)]
    intended_critical = [row for row in ok if _exact(row, "intended_differences", True)]
    intended_full = [row for row in ok if _exact(row, "intended_differences", False)]
    safety = [row for row in ok if row["id"] in safety_ids and not row["resolved"]["auto_write"]]
    confirmations = [
        row for row in ok if row["id"] in confirmation_ids and not row["resolved"]["auto_write"]
    ]
    unsafe = [
        row for row in ok
        if row["resolved"]["auto_write"] and (
            row["id"] in required_non_write_ids or row["resolved"]["errors"]
            or set(row["recoverable_differences"]) - {"event_type"}
        )
    ]
    correct_or_safe = [
        row for row in ok
        if row["id"] in required_non_write_ids and not row["resolved"]["auto_write"]
        or row["id"] not in required_non_write_ids and (
            _exact(row, "recoverable_differences", False) or not row["resolved"]["auto_write"]
        )
    ]
    latencies = sorted(row["elapsed_seconds"] for row in ok)
    p95 = latencies[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
    effect_stable = []
    effect_unstable = []
    strict_stable = []
    strict_unstable = []
    for case in cases:
        runs = [row for row in history.get(case["id"], []) if row.get("status") == "ok"][-3:]
        if len(runs) < 3:
            continue
        resolved_runs = [semantic.resolve(row["raw"], case["text"], case["reference"]) for row in runs]
        effect_matches = all(same_effect(resolved_runs[0], resolved) for resolved in resolved_runs[1:])
        strict_matches = all(_same_resolved(resolved_runs[0], resolved) for resolved in resolved_runs[1:])
        (effect_stable if effect_matches else effect_unstable).append(case["id"])
        (strict_stable if strict_matches else strict_unstable).append(case["id"])
    stability_total = len(effect_stable) + len(effect_unstable)
    if "replay" in RESULTS.stem:
        title = "Archived holdout replay"
    elif "development" in RESULTS.stem:
        title = "Former holdout development"
    else:
        title = "Frozen holdout"
    lines = [
        f"# {title} benchmark", "",
        f"Model: `{MODEL}`. Cases: {len(rows)}/{len(cases)}. Contract hash: `{prompt_hash}`.", "",
        f"- API errors: {len(rows) - len(ok)}",
        f"- Recoverable critical exact: {len(recoverable_critical)}/{len(recoverable)}",
        f"- Recoverable full exact: {len(recoverable_full)}/{len(recoverable)}",
        f"- Intended critical exact: {len(intended_critical)}/{len(cases)}",
        f"- Intended full exact: {len(intended_full)}/{len(cases)}",
        f"- Safety-only non-write: {len(safety)}/{len(safety_ids)}",
        f"- Required confirmations: {len(confirmations)}/{len(confirmation_ids)}",
        f"- Correct or safe confirmation: {len(correct_or_safe)}/{len(cases)}",
        f"- Unsafe auto-writes: {len(unsafe)}",
        f"- Median latency: {statistics.median(latencies):.2f}s" if latencies else "- Median latency: n/a",
        f"- p95 latency: {p95:.2f}s" if p95 is not None else "- p95 latency: n/a",
        f"- Three-run effect stability: {len(effect_stable)}/{stability_total}" if stability_total else "- Three-run effect stability: pending",
        f"- Effect-unstable cases: {', '.join(effect_unstable) if effect_unstable else '-'}",
        f"- Three-run strict extraction stability: {len(strict_stable)}/{stability_total}" if stability_total else "- Three-run strict extraction stability: pending",
        f"- Strict-unstable cases: {', '.join(strict_unstable) if strict_unstable else '-'}",
        "", "## Misses", "",
        "| Case | Auto | Errors | Risks | Recoverable differences | Intended differences |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in ok:
        if row["recoverable_differences"] or row["intended_differences"] or row["resolved"]["errors"]:
            lines.append("| {id} | {auto} | {errors} | {risks} | {recoverable} | {intended} |".format(
                id=row["id"], auto=row["resolved"]["auto_write"],
                errors=", ".join(row["resolved"]["errors"]) or "-",
                risks=", ".join(row["resolved"]["risks"]) or "-",
                recoverable=", ".join(row["recoverable_differences"]) or "-",
                intended=", ".join(row["intended_differences"]) or "-",
            ))
    for row in rows:
        if row.get("status") != "ok":
            lines.append(f"| {row['id']} | - | API error | - | - | - |")
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    RESULTS.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def replay_v2(cases: list[dict], prompt_hash: str, source: Path) -> None:
    by_id = {case["id"]: case for case in cases}
    rows = []
    for row in (_rows(source) if source.exists() else []):
        if row.get("status") != "ok" or row["id"] not in by_id:
            continue
        case = by_id[row["id"]]
        resolved = semantic.resolve(row["raw"], case["text"], case["reference"])
        rows.append({
            **row,
            "prompt_hash": prompt_hash,
            "resolved": resolved,
            "recoverable_differences": (
                [] if case.get("safety_only") or case.get("confirmation_only")
                else differences(case["recoverable"], resolved)
            ),
            "intended_differences": differences(case["intended"], resolved),
        })
    WORK.mkdir(mode=0o700, parents=True, exist_ok=True)
    RESULTS.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--development", action="store_true", help="rerun exposed holdout as development data")
    parser.add_argument("--retry", action="store_true", help="rerun all development cases for stability")
    parser.add_argument("--v2-replay", action="store_true", help="re-resolve archived raw outputs without API calls")
    args = parser.parse_args()
    if args.retry and not args.development:
        parser.error("--retry requires --development")
    if args.v2_replay and (args.retry or args.report_only):
        parser.error("--v2-replay cannot be combined with --retry or --report-only")
    global EXPECTED_CASES, GOLD, MANIFEST, RECORDS, RESULTS
    if args.development:
        MANIFEST = Path(__file__).with_name("holdout-manifest.jsonl")
        GOLD = Path(__file__).with_name("holdout-gold.jsonl")
        RECORDS = ROOT / "data" / "voice-holdout-2026-09-21" / "raw"
        RESULTS = WORK / "deepseek-flash-former-holdout-development-v2.jsonl"
        EXPECTED_CASES = 40
    cloud_runner.load_environment(ROOT / ".env")
    cloud_runner.contract.SCHEMA = semantic.SCHEMA
    cases = load_cases()
    if args.v2_replay:
        source = RESULTS
        if args.development:
            RESULTS = WORK / "deepseek-flash-former-holdout-development-v3-replay.jsonl"
        else:
            RESULTS = WORK / "deepseek-flash-replacement-holdout-v3-replay.jsonl"
        prompt_hash = v2_contract_hash()
        replay_v2(cases, prompt_hash, source)
        report(cases, prompt_hash)
        return
    prompt_hash = v2_contract_hash() if args.development else verify_freeze()
    if not args.report_only:
        run_once(cases, prompt_hash, args.development, args.retry)
    report(cases, prompt_hash)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("holdout interrupted; do not rerun before auditing partial results", file=sys.stderr)

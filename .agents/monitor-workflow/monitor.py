#!/usr/bin/env python3
"""Monitor all GitHub Actions runs for one commit SHA until they finish.

Usage: monitor.py <full-sha>

The caller pushes; this script only reads from GitHub (repo inferred by ``gh``
from this checkout). Several instances (different commits) can run in parallel;
polling is intentionally conservative (interval + jitter) to stay under GitHub
API limits.

No file writes. Results go to stdout for the calling agent:

    MONITOR_JSON: {...}
    MONITOR_RESULT: PASS|FAIL

Exit codes: 0 = all runs for the SHA succeeded, 1 = failure / error.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER_PASS = "MONITOR_RESULT: PASS"
MARKER_FAIL = "MONITOR_RESULT: FAIL"
MARKER_JSON = "MONITOR_JSON:"

# Sized for three concurrent monitors (a soft target). Auth REST budget is
# 5000 req/h; three clients at ~15s ≈ 720 list calls/h total.
INTERVAL = 15.0
JITTER = 5.0
SETTLE = 20.0
APPEAR_TIMEOUT = 180.0
COMPLETE_TIMEOUT = 1800.0


class MonitorError(Exception):
    """Expected operational failure (auth, missing runs, timeouts, etc.)."""


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def gh_json(args: list[str]) -> object:
    proc = run(["gh", *args])
    text = proc.stdout.strip()
    if not text:
        return None
    return json.loads(text)


def require_gh() -> None:
    try:
        run(["gh", "auth", "status"])
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise MonitorError(
            "`gh` is missing or not authenticated. Run: gh auth login"
        ) from exc


def sleep_poll() -> None:
    time.sleep(INTERVAL + random.uniform(0.0, JITTER))


def list_runs(sha: str) -> list[dict]:
    data = gh_json(
        [
            "run",
            "list",
            "--commit",
            sha,
            "--limit",
            "100",
            "--json",
            "databaseId,name,workflowName,status,conclusion,url,headSha,event",
        ]
    )
    if data is None:
        return []
    if not isinstance(data, list):
        raise MonitorError(f"Unexpected gh run list payload: {data!r}")
    # Dedupe by databaseId
    return list({r["databaseId"]: r for r in data}.values())


def run_label(run: dict) -> str:
    return str(run.get("workflowName") or run.get("name") or run["databaseId"])


def wait_for_all_runs(sha: str) -> list[dict]:
    """Wait until every Actions run for ``sha`` has shown up.

    GitHub does not announce "all workflows created" up front, so we:
    1. wait until ≥1 run appears
    2. keep polling until the set of run IDs is unchanged for ``SETTLE`` seconds
       (late workflows still get included)
    """
    print(
        f"Waiting for workflow runs on {sha[:12]} "
        f"(settle={SETTLE:.0f}s once the set is stable) ...",
        flush=True,
    )
    deadline = time.monotonic() + APPEAR_TIMEOUT
    seen_ids: set[int] = set()
    stable_since: float | None = None
    runs: list[dict] = []

    while time.monotonic() < deadline:
        runs = list_runs(sha)
        ids = {int(r["databaseId"]) for r in runs}

        if ids != seen_ids:
            names = ", ".join(sorted(run_label(r) for r in runs)) or "(none yet)"
            print(f"  runs={len(runs)}: {names}", flush=True)
            seen_ids = ids
            stable_since = time.monotonic() if ids else None
        elif ids and stable_since is not None:
            if time.monotonic() - stable_since >= SETTLE:
                print(
                    f"  run set stable for {SETTLE:.0f}s — "
                    f"monitoring {len(runs)} run(s)",
                    flush=True,
                )
                return runs

        sleep_poll()

    if not runs:
        raise MonitorError(
            f"Timed out after {APPEAR_TIMEOUT:.0f}s with no workflow runs for "
            f"{sha}. Check the commit was pushed to a branch whose workflows "
            "trigger (main/master, or one with an open PR)."
        )
    # Appeared but never stayed stable long enough — still proceed with what we have.
    print(
        "  appear-timeout reached with runs present; proceeding with current set",
        flush=True,
    )
    return runs


def wait_until_complete(sha: str, known: list[dict]) -> list[dict]:
    """Wait until all runs for ``sha`` complete; pick up late-arriving runs too."""
    print("Waiting for all runs on this commit to complete ...", flush=True)
    deadline = time.monotonic() + COMPLETE_TIMEOUT
    known_ids = {int(r["databaseId"]) for r in known}

    while time.monotonic() < deadline:
        runs = list_runs(sha)
        ids = {int(r["databaseId"]) for r in runs}
        if ids - known_ids:
            new = ids - known_ids
            print(f"  discovered {len(new)} additional run(s)", flush=True)
            known_ids = ids

        if not runs:
            sleep_poll()
            continue

        pending = [r for r in runs if r.get("status") != "completed"]
        for r in sorted(runs, key=run_label):
            print(
                f"  {run_label(r)}: status={r.get('status')} "
                f"conclusion={r.get('conclusion')}",
                flush=True,
            )
        if not pending:
            return runs
        sleep_poll()

    raise MonitorError(
        f"Timed out after {COMPLETE_TIMEOUT:.0f}s waiting for runs to finish on {sha}."
    )


def failed_logs(run_id: int) -> str:
    proc = run(["gh", "run", "view", str(run_id), "--log-failed"], check=False)
    return ((proc.stdout or "") + (proc.stderr or "")).strip()


def emit_result(payload: dict) -> None:
    # Compact one-line JSON for agents / notify_on_output consumers.
    print(f"{MARKER_JSON} {json.dumps(payload, separators=(',', ':'))}", flush=True)


def full_sha(value: str) -> str:
    # gh run list --commit matches the exact head SHA; a short SHA finds nothing.
    if not re.fullmatch(r"[0-9a-f]{40}", value.lower()):
        raise argparse.ArgumentTypeError("expected a full 40-character hex SHA")
    return value.lower()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Monitor ALL GitHub Actions runs for an already-pushed commit. "
            "Safe for a few parallel instances; results on stdout."
        ),
    )
    p.add_argument(
        "sha",
        type=full_sha,
        help="Full 40-character commit SHA to monitor (must already be pushed).",
    )
    return p.parse_args(argv)


class Terminated(Exception):
    """Raised from the SIGTERM handler so the marker is still printed."""


def on_sigterm(signum: int, frame: object) -> None:
    raise Terminated("received SIGTERM")


def main(argv: list[str] | None = None) -> int:
    result: dict = {
        "ok": False,
        "summary": "FAIL",
        "sha": None,
        "runs": [],
        "failed": [],
        "error": None,
    }
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            return 0
        return report_failure(result, "invalid arguments (see usage above)")
    signal.signal(signal.SIGTERM, on_sigterm)

    try:
        require_gh()
        sha = args.sha
        result["sha"] = sha
        print(f"Monitoring commit {sha}", flush=True)

        runs = wait_until_complete(sha, wait_for_all_runs(sha))

        result["runs"] = [
            {
                "databaseId": r.get("databaseId"),
                "workflowName": r.get("workflowName") or r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "url": r.get("url"),
            }
            for r in runs
        ]
        failed = [
            r
            for r in result["runs"]
            if (r.get("conclusion") or "").lower() != "success"
        ]
        result["failed"] = failed

        if failed:
            print("One or more workflow runs did not succeed:", flush=True)
            for r in failed:
                name, conclusion = r.get("workflowName"), r.get("conclusion")
                print(f"  - {name}: {conclusion} ({r.get('url')})", flush=True)
                if r.get("databaseId") is not None:
                    logs = failed_logs(int(r["databaseId"]))
                    if logs:
                        print("--- failed logs ---", flush=True)
                        print(logs, flush=True)
                        print("--- end logs ---", flush=True)
            emit_result(result)
            print(MARKER_FAIL, flush=True)
            return 1

        result["ok"] = True
        result["summary"] = "PASS"
        print(f"All {len(runs)} workflow run(s) succeeded.", flush=True)
        for r in result["runs"]:
            print(f"  - {r.get('workflowName')}: success ({r.get('url')})", flush=True)
        emit_result(result)
        print(MARKER_PASS, flush=True)
        return 0

    except MonitorError as exc:
        return report_failure(result, str(exc))
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return report_failure(result, f"command failed: {detail}")
    except (Exception, KeyboardInterrupt) as exc:
        # Callers wait on the marker line, so it must be printed on every exit path.
        return report_failure(result, f"unexpected {type(exc).__name__}: {exc}")


def report_failure(result: dict, message: str) -> int:
    result["error"] = message
    result["summary"] = "FAIL"
    emit_result(result)
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    print(MARKER_FAIL, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

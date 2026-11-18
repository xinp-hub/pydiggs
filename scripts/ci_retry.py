#!/usr/bin/env python3
"""Re-run a CI run whose only failures are jobs GitHub never gave a runner.

GitHub-hosted macOS arm64 runners are sometimes short of capacity. A job that waits too
long is cancelled with the annotation "The job was not acquired by Runner of type hosted
even after multiple attempts": it never started, so it says nothing about the code, yet
`all green` fails on it and the pull request needs a person to press "Re-run failed jobs".

``.github/workflows/ci-retry.yml`` runs this after every failed CI run. It re-runs the
failed jobs only when every failure is of that kind and the run has had fewer than
``MAX_ATTEMPTS`` attempts; any other failure is left for a person to read.

    python3 scripts/ci_retry.py decide --run RUN_ID --repo OWNER/NAME

prints ``rerun=true`` or ``rerun=false`` (for ``$GITHUB_OUTPUT``) and the reason on
stderr. It reads the GitHub REST API with ``GITHUB_TOKEN`` and never writes; the workflow
does the re-run. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from typing import Any

#: The run attempts after which a person decides: the original and two automatic re-runs.
MAX_ATTEMPTS = 3
#: GitHub's annotation on a job it could not place on a hosted runner.
NOT_ACQUIRED = "was not acquired by Runner"
#: Jobs that only report the others' results: they fail because a job they need failed.
SUMMARY_JOBS = frozenset({"all green"})
API = "https://api.github.com"

Fetch = Callable[[str], Any]


def never_started(job: Mapping[str, Any], annotations: Iterable[Mapping[str, Any]]) -> bool:
    """True when ``job`` failed only because no hosted runner ever took it."""
    if job.get("conclusion") not in ("cancelled", "failure"):
        return False
    if job.get("runner_name") or job.get("steps"):
        return False  # it ran (or began to): its failure may be real
    return any(NOT_ACQUIRED in str(a.get("message", "")) for a in annotations)


def decide(
    run: Mapping[str, Any],
    jobs: Iterable[Mapping[str, Any]],
    annotations_of: Callable[[Mapping[str, Any]], Iterable[Mapping[str, Any]]],
) -> tuple[bool, str]:
    """Whether to re-run the failed jobs of ``run``, and why."""
    if run.get("conclusion") != "failure":
        return False, f"run concluded {run.get('conclusion')!r}, not failure"
    attempt = int(run.get("run_attempt") or 1)
    if attempt >= MAX_ATTEMPTS:
        return False, f"attempt {attempt} of {MAX_ATTEMPTS}: a person decides now"
    failed = [
        j
        for j in jobs
        if j.get("conclusion") in ("cancelled", "failure", "timed_out")
        and j.get("name") not in SUMMARY_JOBS
    ]
    if not failed:
        return False, "no failed job besides the summary"
    real = [j["name"] for j in failed if not never_started(j, annotations_of(j))]
    if real:
        return False, "failed after starting: " + ", ".join(sorted(real))
    names = ", ".join(sorted(j["name"] for j in failed))
    return True, f"never given a runner: {names} (attempt {attempt})"


def _fetch(token: str) -> Fetch:
    def get(path: str) -> Any:
        request = urllib.request.Request(  # noqa: S310 - a fixed https API host
            API + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.load(response)

    return get


def _jobs(get: Fetch, repo: str, run_id: int, attempt: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    page = 1
    while True:
        data = get(
            f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page={page}"
        )
        jobs.extend(data.get("jobs", []))
        if len(jobs) >= int(data.get("total_count", 0)) or not data.get("jobs"):
            return jobs
        page += 1


def main(argv: list[str] | None = None) -> int:
    """Run the ``decide`` subcommand; see the module docstring."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("decide", help="print rerun=true|false")
    cmd.add_argument("--run", type=int, required=True)
    cmd.add_argument("--repo", required=True, help="OWNER/NAME")
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("error: GITHUB_TOKEN is not set", file=sys.stderr)
        return 2
    get = _fetch(token)
    run = get(f"/repos/{args.repo}/actions/runs/{args.run}")
    attempt = int(run.get("run_attempt") or 1)
    jobs = _jobs(get, args.repo, args.run, attempt)

    def annotations_of(job: Mapping[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = get(f"/repos/{args.repo}/check-runs/{job['id']}/annotations")
        return result

    rerun, reason = decide(run, jobs, annotations_of)
    print(reason, file=sys.stderr)
    print(f"rerun={'true' if rerun else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

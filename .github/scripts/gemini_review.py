#!/usr/bin/env python3
"""A tool-less reviewer for agent_review.py: one Gemini API call, prompt in, verdict out.

This is a model call, not an agent: it has no tools, reads no files and runs no loop, so
nothing in the pull request can make it act. agent_review.py hands it the prompt on stdin
and the verdict's JSON Schema in $AGENT_REVIEW_SCHEMA; Gemini is asked to answer in that
schema, and agent_review.py validates the answer again regardless.

    python3 gemini_review.py --model gemini-3.6-flash

Reads the key from $GEMINI_API_KEY and sends it in the x-goog-api-key header, never in
the URL, so it cannot end up in a log line. Exits non-zero on any failure (quota exhausted,
network, a blocked or empty answer); agent_review.py then tries its fallback reviewer.

Stdlib only.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.parse
from collections.abc import Callable

HOST = "generativelanguage.googleapis.com"
PATH = "/v1beta/models/{model}:generateContent"
TIMEOUT_S = 600
# A distinct status for "unavailable" (quota, rate limit, overload), so the log says why.
EXIT_UNAVAILABLE = 75
EXIT_FAILED = 1
HTTP_TOO_MANY_REQUESTS = 429
# Transient answers Google asks clients to retry: rate limit, internal error, overload.
RETRYABLE = frozenset({HTTP_TOO_MANY_REQUESTS, 500, 503})
BACKOFF_S = (5, 20, 45)  # waits before attempts 2-4: about a minute and a quarter at most
MAX_WAIT_S = 60  # never honour a Retry-After longer than this; hand over to the fallback


def request_body(prompt: str, schema: dict) -> dict:
    """The generateContent request: the prompt, JSON output in the verdict schema, no tools."""
    return {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "temperature": 0,
        },
    }


def answer_text(response: dict) -> str | None:
    """The model's text from a generateContent response, or None if it gave no answer."""
    candidates = response.get("candidates") or []
    if not candidates:
        return None
    first = candidates[0]
    if first.get("finishReason") not in (None, "STOP"):
        return None
    parts = (first.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return text or None


class HTTPError(Exception):
    """A non-2xx answer from the API."""

    def __init__(self, status: int, body: str, retry_after: str | None = None) -> None:
        """Keep the status, the response body and any Retry-After for the caller."""
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.retry_after = retry_after


def call(model: str, key: str, body: dict) -> dict:
    """POST the request over HTTPS to the fixed Gemini host; return the decoded JSON."""
    conn = http.client.HTTPSConnection(HOST, timeout=TIMEOUT_S)
    try:
        conn.request(
            "POST",
            PATH.format(model=urllib.parse.quote(model, safe="")),
            body=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        retry_after = resp.getheader("Retry-After")
    finally:
        conn.close()
    if resp.status not in range(200, 300):
        raise HTTPError(resp.status, data, retry_after)
    return json.loads(data)


def call_with_retry(
    model: str, key: str, body: dict, sleep: Callable[[float], None] = time.sleep
) -> dict:
    """Call, retrying the transient answers Google asks clients to retry, with backoff.

    A Retry-After header is honoured up to MAX_WAIT_S; past that, or after the last
    attempt, the error goes to the caller and agent_review.py tries its fallback reviewer.
    """
    for wait in (*BACKOFF_S, None):
        try:
            return call(model, key, body)
        except HTTPError as err:
            if err.status not in RETRYABLE or wait is None:
                raise
            delay = wait
            if err.retry_after and err.retry_after.isdigit():
                delay = int(err.retry_after)
            if delay > MAX_WAIT_S:
                raise
            sys.stderr.write(f"gemini_review: HTTP {err.status}, retrying in {delay} s\n")
            sleep(delay)
    msg = "unreachable: the loop returns or raises"
    raise AssertionError(msg)


def fail(message: str, code: int) -> int:
    """Report a failure on stderr, never echoing the key, and return the exit status."""
    sys.stderr.write(f"gemini_review: {message}\n")
    return code


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--model", required=True, help="a specific stable model ID")
    args = parser.parse_args(argv)

    key = os.environ.get("GEMINI_API_KEY", "")
    schema_text = os.environ.get("AGENT_REVIEW_SCHEMA", "")
    if not key:
        return fail("GEMINI_API_KEY is not set", EXIT_FAILED)
    if not schema_text:
        return fail("AGENT_REVIEW_SCHEMA is not set", EXIT_FAILED)
    try:
        body = request_body(sys.stdin.read(), json.loads(schema_text))
        response = call_with_retry(args.model, key, body)
    except HTTPError as err:
        limited = err.status in RETRYABLE
        status = "unavailable" if limited else "failed"
        code = EXIT_UNAVAILABLE if limited else EXIT_FAILED
        return fail(f"HTTP {err.status} ({status}): {err.body[:500]}", code)
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as err:
        return fail(f"request failed: {err}", EXIT_FAILED)
    text = answer_text(response)
    if text is None:
        reason = (response.get("candidates") or [{}])[0].get("finishReason", "no candidates")
        return fail(f"no answer ({reason})", EXIT_FAILED)
    sys.stdout.write(text.strip() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

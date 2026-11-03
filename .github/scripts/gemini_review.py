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
import urllib.parse

HOST = "generativelanguage.googleapis.com"
PATH = "/v1beta/models/{model}:generateContent"
TIMEOUT_S = 600
# A distinct status for "unavailable" (quota, rate limit), so the log says why.
EXIT_UNAVAILABLE = 75
EXIT_FAILED = 1
HTTP_TOO_MANY_REQUESTS = 429


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

    def __init__(self, status: int, body: str) -> None:
        """Keep the status and the response body for the error message."""
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


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
    finally:
        conn.close()
    if resp.status not in range(200, 300):
        raise HTTPError(resp.status, data)
    return json.loads(data)


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
        response = call(args.model, key, request_body(sys.stdin.read(), json.loads(schema_text)))
    except HTTPError as err:
        limited = err.status == HTTP_TOO_MANY_REQUESTS
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

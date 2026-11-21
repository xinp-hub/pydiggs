"""Reports a person reads: a grading result as text or HTML, and the scorecard."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, Final

from pydiggs.conformance._catalogue import DIRECTIONS, families
from pydiggs.conformance._challenge import SUITE_VERSION

if TYPE_CHECKING:
    from pydiggs.conformance._grade import Result

__all__ = ["Scorecard", "ScorecardRow", "html", "scorecard", "text"]

_SHOWN_ITEMS: Final = 20
_MARK: Final = {"pass": "PASS", "fail": "FAIL", "skipped": "SKIPPED"}
#: Scorecard cell -> how it is written.
_CELL: Final = {
    "pass": "pass",
    "fail": "FAIL",
    "not-run": "not run",
    "not-testable": "not yet testable",
}


def _stage_mark(status: str, required: bool) -> str:
    mark = _MARK[status]
    return mark if required or status != "fail" else "NOT MET (advisory)"


def text(result: Result) -> str:
    """The result as plain text. File text is untrusted: control characters are spelled out."""
    from pydiggs.findings._cli import _safe  # noqa: PLC0415  # the CLI's own escaping

    verdict = "PASS" if result.passed else "FAIL"
    lines = [
        f"{result.info.id}: {verdict}",
        f"  product     {_safe(result.product_name)} {_safe(result.product_version)} (as declared)",
        f"  suite       {SUITE_VERSION}, DIGGS {result.info.diggs_version}, self-tested",
        f"  challenge   seed {result.seed}, token {result.token}",
        f"  submission  {_safe(result.submission_name)}, sha256 {result.submission_sha256[:16]}…",
        "",
    ]
    for stage in result.stages:
        count = f"{stage.passed}/{stage.total}" if stage.total else "-"
        lines.append(f"  {_stage_mark(stage.status, stage.required):<18} {stage.title} ({count})")
        for item in stage.items[:_SHOWN_ITEMS]:
            detail = item.message
            if item.expected is not None or item.got is not None:
                detail += f" (expected {item.expected}, got {item.got})"
            lines.append(f"      {_safe(item.check)}: {_safe(detail)}")
        if stage.failed > min(len(stage.items), _SHOWN_ITEMS):
            lines.append(f"      … {stage.failed} in all; the JSON evidence lists up to 200")
    lines += ["", "  Self-tested: this does not show which software produced the file."]
    return "\n".join(lines) + "\n"


_STYLE: Final = (
    "body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:24px 16px;color:#1c1c1c;"
    "background:#fff}main{max-width:760px;margin:auto}h1{font-size:1.5rem;margin:0 0 4px}"
    ".v{display:inline-block;padding:2px 10px;border-radius:4px;font-weight:600;color:#fff}"
    ".pass{background:#1b6e3c}.fail{background:#a32020}.skipped,.adv{background:#6b6b6b}"
    "table{border-collapse:collapse;width:100%;margin:8px 0 20px}"
    "td,th{text-align:left;padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}"
    "code{font-size:.9em;overflow-wrap:anywhere}.note{color:#555;font-size:.9rem}"
    "@media(prefers-color-scheme:dark){body{background:#161616;color:#eee}"
    "td,th{border-color:#444}.note{color:#bbb}}"
)


def html(result: Result) -> str:
    """The result as one self-contained HTML page. Every value is escaped."""
    verdict = "pass" if result.passed else "fail"
    rows = []
    for stage in result.stages:
        css = stage.status if stage.required or stage.status != "fail" else "adv"
        count = f"{stage.passed}/{stage.total}" if stage.total else ""
        rows.append(
            f'<tr><td><span class="v {css}">{escape(_stage_mark(stage.status, stage.required))}'
            f"</span></td><td>{escape(stage.title)}</td><td>{count}</td></tr>"
        )
    items = []
    for stage in result.stages:
        for item in stage.items:
            wanted = "" if item.expected is None else escape(item.expected)
            got = "" if item.got is None else escape(item.got)
            items.append(
                f"<tr><td><code>{escape(item.check)}</code></td><td>{escape(item.message)}</td>"
                f"<td>{wanted}</td><td>{got}</td></tr>"
            )
    failures = (
        "<h2>What failed</h2><table><tr><th>Check</th><th>What</th><th>Expected</th>"
        f"<th>Got</th></tr>{''.join(items)}</table>"
        if items
        else ""
    )
    product = escape(f"{result.product_name} {result.product_version}")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(result.info.id)} conformance result</title><style>{_STYLE}</style></head>"
        f"<body><main><h1>{escape(result.info.title)}</h1>"
        f'<p><span class="v {verdict}">{verdict.upper()}</span> {product} (as declared)</p>'
        f'<p class="note">pyDIGGS conformance suite {escape(SUITE_VERSION)}, DIGGS '
        f"{escape(result.info.diggs_version)}, class <code>{escape(result.info.id)}</code>."
        f" Challenge seed <code>{escape(result.seed)}</code>, token <code>{escape(result.token)}"
        f"</code>. Submission <code>{escape(result.submission_name)}</code>, sha256 <code>"
        f"{escape(result.submission_sha256)}</code>.</p>"
        f"<table><tr><th>Result</th><th>Stage</th><th>Passed</th></tr>{''.join(rows)}</table>"
        f"{failures}"
        '<p class="note">Self-tested. This records that the submitted file passed or failed'
        " the named class. It does not show which software produced the file, and it is not"
        " a certification by the DIGGS committee or the ASCE Geo-Institute. Anyone can"
        " re-run it: <code>pydiggs conformance verify</code>.</p></main></body></html>\n"
    )


def _markdown(text: str) -> str:
    """``text`` as inline Markdown text: one line, with nothing that starts markup."""
    one_line = " ".join(text.split())
    return "".join("\\" + c if c in "\\`*_[]<>|#" else c for c in one_line)


@dataclass(frozen=True, slots=True)
class ScorecardRow:
    """One family on a scorecard: its status for export and for import.

    A status is ``pass``, ``fail``, ``not-run`` (the suite has a challenge and no evidence
    was given) or ``not-testable`` (the suite has no challenge for this family yet).
    """

    family: str
    group: str
    title: str
    export: str
    import_: str


@dataclass(frozen=True, slots=True)
class Scorecard:
    """What one product has shown, across the whole DIGGS catalogue."""

    product_name: str
    product_version: str
    rows: tuple[ScorecardRow, ...]

    def _class_rows(self) -> tuple[ScorecardRow, ...]:
        # Core is a stage of every challenge, not a class of its own.
        return tuple(row for row in self.rows if row.family != "core")

    @property
    def passed(self) -> int:
        """How many classes passed."""
        rows = self._class_rows()
        return sum((row.export == "pass") + (row.import_ == "pass") for row in rows)

    @property
    def testable(self) -> int:
        """How many classes the suite can test."""
        return 2 * sum(row.export != "not-testable" for row in self._class_rows())

    @property
    def untestable(self) -> int:
        """How many classes of the catalogue the suite cannot test yet."""
        return 2 * sum(row.export == "not-testable" for row in self._class_rows())

    def to_dict(self) -> dict[str, Any]:
        """The scorecard as plain data."""
        return {
            "format": "diggs-conformance-scorecard",
            "suite_version": SUITE_VERSION,
            "tier": "self-tested",
            "product": {"name": self.product_name, "version": self.product_version},
            "classes_passed": self.passed,
            "classes_testable": self.testable,
            "classes_not_yet_testable": self.untestable,
            "families": [
                {
                    "id": row.family,
                    "group": row.group,
                    "title": row.title,
                    "export": row.export,
                    "import": row.import_,
                }
                for row in self.rows
            ],
        }

    def to_text(self) -> str:
        """The scorecard as a plain-text table."""
        width = max(len(row.title) for row in self.rows)
        lines = [
            f"{self.product_name} {self.product_version} (as declared), suite {SUITE_VERSION},"
            " self-tested",
            f"{self.passed} of {self.testable} testable classes passed;"
            f" {self.untestable} classes of DIGGS are not yet testable",
            "",
            f"{'':<{width}}  {'export':<16}  import",
        ]
        group = None
        for row in self.rows:
            if row.group != group:
                group = row.group
                lines.append(f"{group}:")
            lines.append(f"{row.title:<{width}}  {_CELL[row.export]:<16}  {_CELL[row.import_]}")
        return "\n".join(lines) + "\n"

    def to_markdown(self) -> str:
        """The scorecard as a Markdown table, for a README or a product page."""
        product = _markdown(f"{self.product_name} {self.product_version}")
        lines = [
            f"**{product}**: {self.passed} of {self.testable}"
            f" testable classes passed (pyDIGGS conformance suite {SUITE_VERSION}, self-tested).",
            "",
            "| DIGGS part | Export | Import |",
            "| --- | --- | --- |",
        ]
        lines += [
            f"| {row.title} | {_CELL[row.export]} | {_CELL[row.import_]} |" for row in self.rows
        ]
        return "\n".join(lines) + "\n"


def scorecard(documents: Iterable[Mapping[str, Any]]) -> tuple[Scorecard, ...]:
    """One scorecard per product from evidence documents.

    Every family of the catalogue is a row, so what was not tested, and what cannot be
    tested yet, is as visible as what passed. When two documents are for the same
    product, version and class, the later one in ``documents`` counts. Documents of
    another suite version are refused.

    Raises:
        ValueError: A document is not diggs-conformance evidence of this suite version,
            or is malformed.

    Examples:
        >>> from pydiggs import conformance
        >>> challenge = conformance.new("spt-export", product="Demo", version="1", seed="demo")
        >>> evidence = conformance.grade(challenge, challenge.reference()).to_dict()
        >>> (card,) = conformance.scorecard([evidence])
        >>> card.passed, card.testable
        (1, 4)
        >>> next(r for r in card.rows if r.family == "deep-foundations").export
        'not-testable'
    """
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for document in documents:
        if not isinstance(document, Mapping) or document.get("format") != "diggs-conformance":
            raise ValueError("not a diggs-conformance document")
        if document.get("suite_version") != SUITE_VERSION:
            raise ValueError(
                f"evidence of suite {document.get('suite_version')!r}; this is {SUITE_VERSION!r}"
            )
        product, tested = document.get("product"), document.get("class")
        stages, verdict = document.get("stages"), document.get("verdict")
        if (
            not isinstance(product, Mapping)
            or not isinstance(tested, Mapping)
            or not isinstance(stages, list)
            or verdict not in ("pass", "fail")
            or tested.get("direction") not in DIRECTIONS
            or not all(isinstance(product.get(k), str) for k in ("name", "version"))
            or not isinstance(tested.get("id"), str)
        ):
            raise ValueError("a malformed diggs-conformance document")
        verdicts = seen.setdefault((product["name"], product["version"]), {})
        verdicts[tested["id"]] = verdict
        # The core family has no class: it is the core stage of every challenge, and one
        # failing core stage in a direction is a failing core cell.
        core = next(
            (s.get("status") for s in stages if isinstance(s, Mapping) and s.get("id") == "core"),
            "skipped",
        )
        cell = f"core-{tested['direction']}"
        if verdicts.get(cell) != "fail":
            verdicts[cell] = "pass" if core == "pass" else "fail"
    cards = []
    for (name, version), verdicts in seen.items():
        rows = []
        for family in families():
            cells = [
                verdicts.get(f"{family.id}-{direction}", "not-run")
                if family.testable or family.embedded
                else "not-testable"
                for direction in DIRECTIONS
            ]
            rows.append(ScorecardRow(family.id, family.group, family.title, cells[0], cells[1]))
        cards.append(Scorecard(name, version, tuple(rows)))
    return tuple(cards)

"""``validate()``: run every native check on one file and return a :class:`Report`.

This is the lxml adapter between the legacy checks (``dictionary.py``,
``semantic.py``, ``pydiggs.py``) and the pure findings model. It prints nothing and
writes no files.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import tomllib
from collections.abc import Iterable, Iterator
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Final, Literal

from lxml import etree, isoschematron

import pydiggs
from pydiggs import _xml, deprecations
from pydiggs.detect import SCHEMA_PATHS, detect_diggs_version, schema_path_for
from pydiggs.dictionary import (
    MAX_LOCAL_DICTIONARIES,
    DictionarySemanticValidator,
    ValidationMessage,
)
from pydiggs.errors import InstanceReadError, ResourceError
from pydiggs.findings import rules
from pydiggs.findings.locate import Locator
from pydiggs.findings.model import Finding, Report, Severity, Step, StepStatus
from pydiggs.pydiggs import _DEFAULT_DICTIONARY, _DEFAULT_SCHEMATRON, _casing_diameter_problems
from pydiggs.semantic import validate_context_tree

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_FINDINGS",
    "MAX_MESSAGE_CHARS",
    "MAX_REPORT_BYTES",
    "MAX_XPATH_CHARS",
    "NATIVE_STEPS",
    "display_paths",
    "reference_data",
    "validate",
]

#: Steps the native layer implements, in pipeline order. ``checks`` selects from these.
NATIVE_STEPS: Final = (
    Step.STRUCTURE,
    Step.SCHEMA,
    Step.DICTIONARY,
    Step.SCHEMATRON,
    Step.GEOMETRY,
    Step.DEPRECATION,
    Step.CONTEXT,
)
#: Longest ``Finding.message`` kept, in characters; longer ones end in an ellipsis.
MAX_MESSAGE_CHARS: Final = 4096
#: Longest ``xpath`` (and ``gml_id``) kept; a longer one is reported as None.
MAX_XPATH_CHARS: Final = 4096
#: Budget for one report's findings, roughly as serialised to JSON.
MAX_REPORT_BYTES: Final = 16 * 2**20
_FINDING_OVERHEAD: Final = 200  # JSON keys, punctuation and the fixed fields
#: Longest SVRL location lxml's Schematron skeleton may build before the step is skipped.
MAX_SCHEMATRON_LOCATION: Final = 16 * 1024
#: Longest namespace URI the Schematron step accepts.
MAX_SCHEMATRON_NAMESPACE: Final = 1024
_NS_STEP_CHARS: Final = len("/*[local-name()='' and namespace-uri()='']")
_INDEX_CHARS: Final = 12  # "[n]" for any sibling count
#: Where validate() may read dictionaries named by relative codeSpaces.
LocalDictionaries = Literal["instance-dir", "off"]
#: Default ``max_findings`` of :func:`validate`.
DEFAULT_MAX_FINDINGS: Final = 10_000
#: Default ``max_bytes`` of :func:`validate`: 100 MiB.
DEFAULT_MAX_BYTES: Final = 100 * 1024 * 1024
_CONTEXT_STEPS: Final = frozenset({Step.STRUCTURE, Step.GEOMETRY, Step.CONTEXT})
_SEVERITY: Final = {"ERROR": Severity.ERROR, "WARNING": Severity.WARNING, "INFO": Severity.INFO}
#: libxml2 appends the position to syntax errors; it is in line/column already.
_POSITION_SUFFIX: Final = re.compile(r", line \d+, column \d+$")
_CHECK_PREFIX: Final = re.compile(r"^Check \d+:\n")
_SVRL: Final = "{http://purl.oclc.org/dsdl/svrl}"
_NO_ERRORS: Final = etree.XPath("/..")
_LOG_LEVELS: Final = {
    "WARNING": Severity.WARNING,
    "ERROR": Severity.ERROR,
    "FATAL": Severity.ERROR,
}


class _Cache(threading.local):
    """Compiled bundled validators, one set per thread (lxml validators are not shared)."""

    def __init__(self) -> None:
        self.schemas: dict[str, etree.XMLSchema] = {}
        self.schematrons: dict[str, isoschematron.Schematron] = {}
        self.dictionaries: dict[
            tuple[str, bool, str], tuple[tuple[int, int], DictionarySemanticValidator]
        ] = {}


_CACHE: Final = _Cache()


@cache
def reference_data() -> dict[str, str]:
    """Upstream commit of each vendored reference-data source, from PROVENANCE.toml."""
    try:
        text = resources.files("pydiggs").joinpath("PROVENANCE.toml").read_text("utf-8")
        sources = tomllib.loads(text)["sources"]
        return {name: str(info["commit"]) for name, info in sorted(sources.items())}
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ResourceError(f"bundled PROVENANCE.toml is missing or broken: {exc}") from exc


def display_paths(
    path: str | os.PathLike[str], base_dir: str | os.PathLike[str] | None = None
) -> tuple[str, str]:
    """Return (reported path, absolute path), both with forward slashes.

    The reported path is relative to ``base_dir`` when given and the file is under it.
    Without ``base_dir`` it is relative to the working directory, unless that is the
    file-system root or an ancestor of the home directory (where a relative path would
    still spell out the user's directory layout). Otherwise it is the bare file name.
    """
    absolute = Path(path).resolve()
    if base_dir is not None:
        base: Path | None = Path(base_dir).resolve()
    else:
        cwd = Path.cwd().resolve()
        try:
            home = Path.home().resolve()
        except (KeyError, RuntimeError):  # no home directory (some service accounts)
            home = None
        too_broad = cwd == Path(cwd.anchor) or (home is not None and cwd in home.parents)
        base = None if too_broad else cwd
    shown = ""
    if base is not None:
        try:
            shown = absolute.relative_to(base).as_posix()
        except ValueError:
            shown = ""
    if shown in ("", "."):
        shown = absolute.name
    return _printable(shown), _printable(absolute.as_posix())


def _printable(name: str) -> str:
    """Make a name printable: bytes that are not valid UTF-8 become backslash escapes."""
    return os.fsencode(name).decode("utf-8", "backslashreplace")


def _reason(exc: OSError) -> str:
    """Why an instance could not be read: the OS error text only, never a path.

    ``strerror`` carries no file name (unlike ``str(exc)``), so nothing has to be
    redacted, and nothing in the reason can be garbled by a replacement.
    """
    return exc.strerror or "could not be read"


def _limits(max_findings: int | None, max_bytes: int) -> int:
    """Check the two limits; return the size limit to apply."""
    if max_findings is not None and (type(max_findings) is not int or max_findings < 1):
        raise ValueError(f"max_findings must be a positive int or None, got {max_findings!r}")
    limit = max_bytes
    if type(limit) is not int or limit < 1:
        raise ValueError(f"max_bytes must be a positive int, got {max_bytes!r}")
    return limit


def _checks(checks: Iterable[str | Step] | None) -> frozenset[Step]:
    if checks is None:
        return frozenset(NATIVE_STEPS)
    if isinstance(checks, str):
        raise ValueError(f"checks must be an iterable of step names, not the string {checks!r}")
    allowed = {s.value: s for s in NATIVE_STEPS}
    chosen: set[Step] = set()
    for name in checks:
        if str(name) not in allowed:
            raise ValueError(f"unknown check {str(name)!r}; expected some of {', '.join(allowed)}")
        chosen.add(allowed[str(name)])
    if not chosen:
        raise ValueError("checks must name at least one step")
    return frozenset(chosen)


class _Builder:
    """Collects findings for one file, locating each from its element.

    Once full (``max_findings`` findings, or :data:`MAX_REPORT_BYTES` of them), further
    findings are only counted per step, and callers skip locating them.
    """

    def __init__(
        self,
        locator: Locator | None,
        *,
        snippets: bool,
        scrub: tuple[str, str],
        max_findings: int | None = None,
    ) -> None:
        self.locator = locator
        self.snippets = snippets
        self.scrub = scrub
        self.max_findings = max_findings
        self.findings: list[Finding] = []
        self.size = 0
        #: Findings not kept: count and worst severity per step.
        self.dropped: dict[Step, tuple[int, Severity]] = {}

    @property
    def full(self) -> bool:
        """No more findings are kept (the cap or the size budget was reached)."""
        at_cap = self.max_findings is not None and len(self.findings) >= self.max_findings
        return at_cap or self.size >= MAX_REPORT_BYTES

    @property
    def remaining(self) -> int | None:
        """How many more findings may be kept (None: no cap)."""
        if self.max_findings is None:
            return None if self.size < MAX_REPORT_BYTES else 0
        return 0 if self.full else self.max_findings - len(self.findings)

    def drop(self, step: Step, severity: Severity, count: int = 1) -> None:
        """Count findings that are not kept."""
        seen, worst = self.dropped.get(step, (0, severity))
        self.dropped[step] = (seen + count, max(worst, severity, key=lambda s: s.rank))

    def add(
        self,
        step: Step,
        rule_id: str,
        severity: Severity,
        message: str,
        *,
        node: etree._Element | None = None,
        line: int | None = None,
        column: int | None = None,
        attribute: str | None = None,
    ) -> None:
        if self.full:
            self.drop(step, severity)
            return
        absolute, shown = self.scrub
        text = message.replace(absolute, shown) if absolute else message
        if len(text) > MAX_MESSAGE_CHARS:
            text = text[: MAX_MESSAGE_CHARS - 1] + "\u2026"
        xpath = gml_id = snippet = None
        if node is not None and self.locator is not None:
            line = line if line is not None else self.locator.line(node)
            suffix = f"/@{attribute}" if attribute else ""
            # A path over the cap is None (a cut path could name another element); the
            # line remains. The walk itself stops at the cap.
            element_path = self.locator.xpath(node, MAX_XPATH_CHARS - len(suffix))
            xpath = None if element_path is None else element_path + suffix
            gml_id = self.locator.gml_id(node)
            if gml_id is not None and len(gml_id) > MAX_XPATH_CHARS:
                gml_id = None
            snippet = self.locator.snippet(node) if self.snippets else None
        finding = Finding(
            step=step,
            rule_id=rule_id,
            severity=severity,
            message=text,
            line=line if line is not None and line >= 1 else None,
            column=column if column is not None and column >= 1 else None,
            xpath=xpath,
            gml_id=gml_id,
            source_snippet=snippet,
        )
        self.size += _FINDING_OVERHEAD + sum(
            len(v.encode("utf-8", "surrogatepass"))
            for v in (text, rule_id, xpath or "", gml_id or "", snippet or "")
        )
        self.findings.append(finding)

    def add_legacy(self, step: Step, rule_id: str, message: ValidationMessage) -> None:
        self.add(
            step,
            rule_id,
            _SEVERITY.get(message.severity, Severity.ERROR),
            _CHECK_PREFIX.sub("", message.text),
            node=message.node,
        )

    def status(self, step: Step) -> StepStatus:
        if step in self.dropped:
            return "incomplete"
        failed = any(f.step is step and f.severity is Severity.ERROR for f in self.findings)
        return "failed" if failed else "passed"

    def truncation(self) -> Finding | None:
        """One finding saying how many were left out, as severe as the worst of them."""
        if not self.dropped:
            return None
        total = sum(count for count, _ in self.dropped.values())
        worst = max((sev for _, sev in self.dropped.values()), key=lambda s: s.rank)
        names = ", ".join(step.value for step in sorted(self.dropped, key=lambda s: s.order))
        kept = len(self.findings)
        noun = "finding" if kept == 1 else "findings"
        more = "1 more was" if total == 1 else f"{total} more were"
        if self.max_findings is not None and kept >= self.max_findings:
            why = "Raise max_findings to see them."
        else:
            why = f"The report reached its {MAX_REPORT_BYTES // 2**20} MiB size budget."
        return Finding(
            step=Step.STRUCTURE,
            rule_id=rules.FINDINGS_TRUNCATED,
            severity=worst,
            message=(f"Stopped after {kept} {noun}; {more} not reported (steps: {names}). {why}"),
        )


def _load_trusted(path: str | os.PathLike[str], what: str) -> etree._ElementTree:
    try:
        return _xml.parse(path)
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ResourceError(f"cannot read {what} {os.fspath(path)!r}: {exc}") from exc


def _schema(profile: str, schema_path: str | os.PathLike[str] | None) -> etree.XMLSchema:
    path = Path(schema_path) if schema_path is not None else schema_path_for(profile)

    def build() -> etree.XMLSchema:
        try:
            return etree.XMLSchema(_load_trusted(path, "schema"))
        except etree.XMLSchemaParseError as exc:
            raise ResourceError(f"cannot use schema {str(path)!r}: {exc}") from exc

    if schema_path is not None:
        return build()
    if str(path) not in _CACHE.schemas:
        _CACHE.schemas[str(path)] = build()
    return _CACHE.schemas[str(path)]


def _run_schema(
    doc: etree._ElementTree,
    builder: _Builder,
    profile: str,
    schema_path: str | os.PathLike[str] | None,
) -> StepStatus:
    schema = _schema(profile, schema_path)
    locator = builder.locator
    try:
        schema.validate(doc)
    except etree.XMLSchemaValidateError as exc:
        builder.add(
            Step.SCHEMA,
            rules.SCHEMA_INCOMPLETE,
            Severity.ERROR,
            f"Schema validation could not complete: {exc}",
        )
        return "incomplete"
    for entry in schema.error_log:
        severity = _LOG_LEVELS.get(entry.level_name)
        if severity is None:
            continue
        if builder.full:  # count only; do not locate
            builder.drop(Step.SCHEMA, severity)
            continue
        node = locator.element_at(entry.path) if locator is not None else None
        builder.add(
            Step.SCHEMA,
            rules.schema_rule(entry.type_name),
            severity,
            entry.message,
            node=node,
            line=entry.line if node is None else None,
        )
    return builder.status(Step.SCHEMA)


def _schematron(schematron_path: str | os.PathLike[str] | None) -> isoschematron.Schematron:
    path = Path(schematron_path) if schematron_path is not None else _DEFAULT_SCHEMATRON

    def build() -> isoschematron.Schematron:
        try:
            return isoschematron.Schematron(
                _load_trusted(path, "Schematron rules"),
                store_report=True,
                # validate() reads the SVRL report itself; an error finder that selects
                # nothing stops lxml serialising every failed assert into its error log.
                error_finder=_NO_ERRORS,
            )
        except (etree.SchematronParseError, etree.XSLTError) as exc:
            raise ResourceError(f"cannot use Schematron rules {str(path)!r}: {exc}") from exc

    if schematron_path is not None:
        return build()
    if str(path) not in _CACHE.schematrons:
        _CACHE.schematrons[str(path)] = build()
    return _CACHE.schematrons[str(path)]


def _svrl_items(report: etree._ElementTree) -> Iterator[tuple[str, etree._Element]]:
    """(rule context, failed-assert or successful-report) in report order."""
    context = ""
    for el in report.getroot():
        if el.tag == f"{_SVRL}fired-rule":
            context = el.get("context") or ""
        elif el.tag in (f"{_SVRL}failed-assert", f"{_SVRL}successful-report"):
            yield context, el


def _schematron_budget(doc: etree._ElementTree) -> str | None:
    """Why lxml's Schematron skeleton must not run on ``doc``, or None.

    The skeleton writes, for every failed assert, a location with one step per
    ancestor, each carrying that element's full namespace URI, and keeps them all in
    memory. One linear pass predicts the longest such location (namespace URIs are
    read once, from their declarations).
    """
    root = doc.getroot()
    for _, (_, uri) in etree.iterwalk(root, events=("start-ns",)):
        if len(uri) > MAX_SCHEMATRON_NAMESPACE:
            return f"a namespace URI is longer than {MAX_SCHEMATRON_NAMESPACE} characters"
    lengths = [0]
    for event, el in etree.iterwalk(root, events=("start", "end")):
        if event == "end":
            lengths.pop()
            continue
        tag = str(el.tag)
        if tag.startswith("{"):
            uri, _, local = tag[1:].partition("}")
            step = _NS_STEP_CHARS + len(local) + len(uri) + _INDEX_CHARS
        else:
            step = 1 + len(tag) + _INDEX_CHARS
        total = lengths[-1] + step
        if total > MAX_SCHEMATRON_LOCATION:
            return (
                f"an element's Schematron location would exceed {MAX_SCHEMATRON_LOCATION // 1024}"
                " KiB (deep nesting with long names or namespace URIs)"
            )
        lengths.append(total)
    return None


def _run_schematron(
    doc: etree._ElementTree,
    builder: _Builder,
    schematron_path: str | os.PathLike[str] | None,
) -> StepStatus:
    schematron = _schematron(schematron_path)
    reason = _schematron_budget(doc)
    if reason is not None:
        builder.add(
            Step.SCHEMATRON,
            rules.SCHEMATRON_SKIPPED,
            Severity.WARNING,
            f"The Schematron rules were not run: {reason}.",
        )
        _run_casing(doc, builder)
        return "incomplete"
    try:
        schematron.validate(doc)
    except etree.XSLTError as exc:  # e.g. an unknown function in a user rule's test
        where = str(schematron_path) if schematron_path is not None else "bundled"
        raise ResourceError(f"cannot apply Schematron rules {where!r}: {exc}") from exc
    for context, item in _svrl_items(schematron.validation_report):
        test = item.get("test") or ""
        if schematron_path is None:
            rule_id = rules.SCHEMATRON_BUNDLED.get(
                (context, " ".join(test.split())),
                rules.schematron_user_rule(item.get("id"), context, test),
            )
        else:
            rule_id = rules.schematron_user_rule(item.get("id"), context, test)
        if builder.full:  # count only; do not locate
            builder.drop(Step.SCHEMATRON, Severity.ERROR)
            continue
        location = item.get("location")
        node = builder.locator.svrl_element(location) if builder.locator else None
        text = " ".join(item.findtext(f"{_SVRL}text", default="").split())
        builder.add(Step.SCHEMATRON, rule_id, Severity.ERROR, text, node=node)
    _run_casing(doc, builder)
    return builder.status(Step.SCHEMATRON)


def _run_casing(doc: etree._ElementTree, builder: _Builder) -> None:
    """Pydiggs's own unit-converting casing rule (part of the Schematron step)."""
    for casing, message in _casing_diameter_problems(doc):
        rule_id = (
            rules.SCHEMATRON_CASING_NUMERIC
            if message == "Casing diameter values must be numeric."
            else rules.SCHEMATRON_CASING_UNITS
        )
        builder.add(Step.SCHEMATRON, rule_id, Severity.ERROR, message, node=casing)


def _dictionary_validator(
    dictionary_path: str | os.PathLike[str] | None,
    *,
    allow_network: bool,
    local_dictionaries: LocalDictionaries = "instance-dir",
) -> DictionarySemanticValidator:
    """A validator for this dictionary, reused within the thread while the file is unchanged.

    Building one parses every bundled dictionary (most of the time per file), so it is
    cached per (dictionary, allow_network, local_dictionaries), one entry each, replaced
    when the dictionary file changes; per-instance state is reset by ``validate_tree``.
    """
    path = Path(dictionary_path) if dictionary_path is not None else _DEFAULT_DICTIONARY
    try:
        resolved = path.resolve()
        stat = resolved.stat()
    except OSError as exc:
        raise ResourceError(f"dictionary {str(path)!r} is not a readable file") from exc
    if not path.is_file():
        raise ResourceError(f"dictionary {str(path)!r} is not a readable file")
    key = (str(resolved), allow_network, local_dictionaries)
    version = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.dictionaries.get(key)
    if cached is None or cached[0] != version:
        try:
            validator = DictionarySemanticValidator(
                dictionary_path=path,
                allow_network=allow_network,
                check_value_code_spaces=True,
                local_dictionaries=local_dictionaries,
                legacy_paths=False,
            )
        except (OSError, etree.XMLSyntaxError) as exc:
            raise ResourceError(f"cannot read dictionary data: {exc}") from exc
        cached = (version, validator)
        _CACHE.dictionaries[key] = cached
    return cached[1]


def _run_dictionary(
    doc: etree._ElementTree,
    builder: _Builder,
    instance: Path,
    dictionary_path: str | os.PathLike[str] | None,
    *,
    allow_network: bool,
    local_dictionaries: LocalDictionaries,
    max_bytes: int,
) -> StepStatus:
    semantic = _dictionary_validator(
        dictionary_path, allow_network=allow_network, local_dictionaries=local_dictionaries
    )
    result = semantic.validate_tree(
        doc, instance, max_messages=builder.remaining, max_bytes=max_bytes
    )
    for count, severity in result.dropped.values():
        builder.drop(Step.DICTIONARY, _SEVERITY.get(severity, Severity.ERROR), count)
    for message in result.messages:
        if not allow_network and _is_offline(message):
            builder.add(
                Step.DICTIONARY,
                rules.DICTIONARY_OFFLINE,
                Severity.INFO,
                _CHECK_PREFIX.sub("", message.text)
                + " It is not bundled with pydiggs and network access is off"
                " (allow_network=False), so it was not fetched.",
                node=message.node,
            )
            continue
        if _is_local_inaccessible(message):
            url = _QUOTED.search(message.text)
            capped = url is not None and url[1] in semantic.local_capped
            note = _CAPPED_NOTE if capped else _LOCAL_NOTE[local_dictionaries]
            builder.add(
                Step.DICTIONARY,
                rules.dictionary_rule(message.check),
                Severity.WARNING,
                _CHECK_PREFIX.sub("", message.text) + note,
                node=message.node,
            )
            continue
        builder.add_legacy(Step.DICTIONARY, rules.dictionary_rule(message.check), message)
    return builder.status(Step.DICTIONARY)


_REMOTE: Final = re.compile(r'"https?://[^"]+"')


_LOCAL_NOTE: Final = {
    "instance-dir": (
        " Local dictionaries are read only from inside the instance's folder"
        " (local_dictionaries='instance-dir'); this one is outside it, missing or unreadable."
    ),
    "off": " Local dictionaries are not read (local_dictionaries='off').",
}


_QUOTED: Final = re.compile(r'"([^"]*)"')
_CAPPED_NOTE: Final = (
    f" The instance names more than {MAX_LOCAL_DICTIONARIES} different local dictionaries;"
    " the others were not read."
)


def _is_local_inaccessible(message: ValidationMessage) -> bool:
    """A Check 3 or Check 13 'could not be accessed' about a local (non-http) resource."""
    return (
        message.check in (3, 13)
        and message.severity == "WARNING"
        and "could not be accessed" in message.text
        and _REMOTE.search(message.text) is None
    )


def _is_offline(message: ValidationMessage) -> bool:
    """A Check 3 or Check 13 'could not be accessed' about a remote (http/https) resource.

    The URL was already whitelisted (Check 2 / 13 runs first), so with network access
    off the only reason is that pydiggs does not bundle it.
    """
    return (
        message.check in (3, 13)
        and message.severity == "WARNING"
        and "could not be accessed" in message.text
        and _REMOTE.search(message.text) is not None
    )


def _run_deprecation(doc: etree._ElementTree, builder: _Builder, profile: str) -> StepStatus:
    for match in deprecations.matches(doc, profile):
        removed = match.entry.status == "removed"
        builder.add(
            Step.DEPRECATION,
            rules.REMOVED if removed else rules.DEPRECATED,
            Severity.ERROR if removed else Severity.WARNING,
            match.entry.text(),
            node=match.element,
            attribute=match.attribute,
        )
    return builder.status(Step.DEPRECATION)


def _run_context(root: etree._Element, builder: _Builder, wanted: frozenset[Step]) -> None:
    checks = frozenset(c for c, (step, _) in rules.CONTEXT_RULES.items() if step in wanted)
    result = validate_context_tree(
        root, max_messages=builder.remaining, legacy_paths=False, checks=checks
    )
    for check, (count, severity) in result.dropped.items():
        step, _ = rules.CONTEXT_RULES[check]
        builder.drop(step, _SEVERITY.get(severity, Severity.ERROR), count)
    for message in result.messages:
        step, rule_id = rules.CONTEXT_RULES[message.check]
        if step in wanted:
            builder.add_legacy(step, rule_id, message)


def validate(
    path: str | os.PathLike[str],
    *,
    diggs_version: str | None = None,
    checks: Iterable[str | Step] | None = None,
    schema_path: str | os.PathLike[str] | None = None,
    dictionary_path: str | os.PathLike[str] | None = None,
    schematron_path: str | os.PathLike[str] | None = None,
    allow_network: bool = False,
    include_snippets: bool = False,
    max_findings: int | None = DEFAULT_MAX_FINDINGS,
    max_bytes: int = DEFAULT_MAX_BYTES,
    base_dir: str | os.PathLike[str] | None = None,
    local_dictionaries: LocalDictionaries = "instance-dir",
) -> Report:
    """Validate one DIGGS instance with every native check and return the findings.

    Nothing is printed and no file is written. Problems in the instance, including XML
    that is not well-formed, are findings in the report; only an unreadable instance,
    a broken trusted input, or a bad argument raises.

    Args:
        path: The instance file.
        diggs_version: ``"2.5.a"``, ``"2.6"`` or ``"3.0.0"``; detected from the root
            namespace when omitted (``3.0.0`` when unknown).
        checks: Step names to run, from ``structure``, ``schema``, ``dictionary``,
            ``schematron``, ``geometry``, ``deprecation``, ``context``. Default: all
            of them. Every selected step runs even when an earlier one fails.
        schema_path: XSD to use instead of the bundled one (trusted input).
        dictionary_path: Primary property dictionary instead of the bundled one.
        schematron_path: Schematron rules instead of the bundled ones (trusted input:
            Schematron compiles to XSLT, which can read local files).
        allow_network: Fetch whitelisted remote code lists that are not bundled.
        include_snippets: Attach each finding's element XML (up to 1 KiB). Off by
            default because snippets carry the file's data.
        max_findings: Keep at most this many findings (default 10,000; None for no
            limit). When it is reached, one ``structure.findings-truncated`` finding,
            as severe as the worst one left out, says how many were dropped, and the
            steps that lost findings are ``incomplete``.
        max_bytes: Largest instance accepted, in bytes (default
            :data:`DEFAULT_MAX_BYTES`, 100 MiB). The legacy ``validator`` keeps 1 GiB.
        local_dictionaries: ``"instance-dir"`` (default): a relative or ``file:``
            codeSpace is read only if it names a file inside the instance's folder
            (no working-directory fallback, no escape through ``..`` or symlinks);
            anything else is reported as not accessible (``dictionary.3``). ``"off"``:
            no local dictionary is read. The legacy ``dictionary_check()`` keeps its
            1.1 behaviour.
        base_dir: Report the path relative to this folder (the bare file name when
            the file is outside it). Default: the working directory, unless that is
            ``/`` or an ancestor of the home directory, when the bare name is used.

    Returns:
        A :class:`~pydiggs.Report`; ``report.ok`` is True when no finding is an error.

    Raises:
        InstanceReadError: The instance cannot be read (also an ``OSError``).
        ResourceError: A schema, dictionary or Schematron file, or bundled data, is
            missing or broken.
        ValueError: ``diggs_version`` or ``checks`` names something unknown, or a
            limit is not positive.

    Example:
        >>> from pydiggs import validate
        >>> report = validate("tests/test_files/2.6/Schema_Error_1-2_6.xml")
        >>> report.ok, report.diggs_version, report.steps["schema"]
        (False, '2.6', 'failed')
        >>> first = report.findings[0]
        >>> first.rule_id, first.line
        ('schema.cvc-complex-type.2.4', 12)
        >>> first.xpath.rsplit("/", 1)[-1]
        'businessAssociate[1]'
    """
    if diggs_version is not None and diggs_version not in SCHEMA_PATHS:
        raise ValueError(
            f"unknown DIGGS version {diggs_version!r}; expected one of {sorted(SCHEMA_PATHS)}"
        )
    limit = _limits(max_findings, max_bytes)
    if local_dictionaries not in ("instance-dir", "off"):
        raise ValueError(
            f"local_dictionaries must be 'instance-dir' or 'off', got {local_dictionaries!r}"
        )
    wanted = _checks(checks)
    shown, absolute = display_paths(path, base_dir)
    instance = Path(path)
    try:
        data, _ = _xml.read_limited(instance, limit, regular_only=True)
    except OSError as exc:
        raise InstanceReadError(exc.errno or 0, _reason(exc), shown) from exc
    sha256 = hashlib.sha256(data).hexdigest()
    steps: dict[Step, StepStatus] = dict.fromkeys(NATIVE_STEPS, "skipped")
    scrub = (str(instance.resolve()), shown)

    def report(builder: _Builder, version: str | None) -> Report:
        return Report(
            path=shown,
            absolute_path=absolute,
            sha256=sha256,
            diggs_version=version,
            steps=steps,
            findings=(*builder.findings, *filter(None, [builder.truncation()])),
            tool_version=pydiggs.__version__,
            reference_data=reference_data(),
        )

    try:
        doc = _xml.parse_bytes(data, str(instance.resolve()))
    except etree.XMLSyntaxError as exc:
        builder = _Builder(None, snippets=False, scrub=scrub)
        line, column = exc.position
        builder.add(
            Step.STRUCTURE,
            rules.WELL_FORMED,
            Severity.ERROR,
            _POSITION_SUFFIX.sub("", exc.msg or "") or "The file is not well-formed XML.",
            line=line,
            column=column,
        )
        steps[Step.STRUCTURE] = "failed"
        return report(builder, diggs_version)

    profile = diggs_version or detect_diggs_version(doc)
    builder = _Builder(
        Locator(doc), snippets=include_snippets, scrub=scrub, max_findings=max_findings
    )
    if Step.SCHEMA in wanted:
        steps[Step.SCHEMA] = _run_schema(doc, builder, profile, schema_path)
    if Step.DICTIONARY in wanted:
        steps[Step.DICTIONARY] = _run_dictionary(
            doc,
            builder,
            instance,
            dictionary_path,
            allow_network=allow_network,
            local_dictionaries=local_dictionaries,
            max_bytes=limit,
        )
    if Step.SCHEMATRON in wanted:
        steps[Step.SCHEMATRON] = _run_schematron(doc, builder, schematron_path)
    if Step.DEPRECATION in wanted:
        steps[Step.DEPRECATION] = _run_deprecation(doc, builder, profile)
    if wanted & _CONTEXT_STEPS:
        _run_context(doc.getroot(), builder, wanted)
        for step in _CONTEXT_STEPS & wanted:
            steps[step] = builder.status(step)
    # Well-formedness is always checked (nothing else can run without it), but an
    # unrequested structure step stays "skipped" unless the file is not well-formed.
    for step in builder.dropped:
        steps[step] = "incomplete"
    return report(builder, profile)

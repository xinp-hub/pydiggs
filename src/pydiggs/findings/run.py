"""``validate()``: run every native check on one file and return a :class:`Report`.

This is the lxml adapter between the legacy checks (``dictionary.py``,
``semantic.py``, ``pydiggs.py``) and the pure findings model. It prints nothing and
writes no files.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import threading
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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
from pydiggs.findings._schematron import (
    SVRL,
    FiredPattern,
    RuleContexts,
    fired_patterns,
    svrl_items,
)
from pydiggs.findings.locate import Locator
from pydiggs.findings.model import Finding, Report, Severity, Step, StepStatus
from pydiggs.pydiggs import _DEFAULT_DICTIONARY, _DEFAULT_SCHEMATRON, _casing_diameter_problems
from pydiggs.semantic import validate_context_tree

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_FINDINGS",
    "MAX_ELEMENTS",
    "MAX_ENTITY_EXPANSION",
    "MAX_LOCAL_DICTIONARY_BYTES",
    "MAX_MESSAGE_CHARS",
    "MAX_REPORT_BYTES",
    "MAX_SCHEMATRON_FIRED",
    "MAX_SCHEMATRON_SIBLINGS",
    "MAX_XPATH_CHARS",
    "NATIVE_STEPS",
    "display_paths",
    "reference_data",
    "selected_steps",
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
#: Most elements the XPath-based steps (dictionary, Schematron, deprecation, geometry,
#: context, structure root checks) accept; libxml2 caps XPath node sets at 10,000,000.
MAX_ELEMENTS: Final = 9_900_000
_XPATH_STEPS: Final = frozenset(
    {
        Step.DICTIONARY,
        Step.SCHEMATRON,
        Step.DEPRECATION,
        Step.STRUCTURE,
        Step.GEOMETRY,
        Step.CONTEXT,
    }
)
#: Most child elements one element may have for the Schematron step to run; above it the
#: step is skipped with a ``schematron.incomplete`` error (libxslt is quadratic there).
MAX_SCHEMATRON_SIBLINGS: Final = 50_000
#: Most elements the Schematron rules may fire on (counted before running them); above
#: it the step is skipped with a ``schematron.incomplete`` error.
MAX_SCHEMATRON_FIRED: Final = 100_000
#: Most bytes of local dictionaries (relative codeSpaces) read for one instance, in
#: total; a parsed dictionary takes about 35 times its size in memory.
MAX_LOCAL_DICTIONARY_BYTES: Final = 20 * 2**20
#: Dictionary validators kept per thread (least recently used dropped first).
MAX_CACHED_VALIDATORS: Final = 8
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
_NO_ERRORS: Final = etree.XPath("/..")

_NOT_FIRED: Final = FiredPattern(None, [])
_LOG_LEVELS: Final = {
    "WARNING": Severity.WARNING,
    "ERROR": Severity.ERROR,
    "FATAL": Severity.ERROR,
}


class _Cache(threading.local):
    """Compiled bundled validators, one set per thread (lxml validators are not shared)."""

    def __init__(self) -> None:
        self.schemas: dict[str, etree.XMLSchema] = {}
        self.schematrons: dict[str, tuple[isoschematron.Schematron, RuleContexts]] = {}
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


def selected_steps(checks: Iterable[str | Step] | None) -> frozenset[Step]:
    """The steps ``checks`` selects (all of them for None); ValueError on unknown names."""
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
        #: Steps that could not finish (each has an ERROR finding).
        self.incomplete: set[Step] = set()

    @property
    def full(self) -> bool:
        """No more findings are kept (the cap or the size budget was reached)."""
        at_cap = self.max_findings is not None and len(self.findings) >= self.max_findings
        return at_cap or self.size >= MAX_REPORT_BYTES

    @property
    def remaining(self) -> int:
        """How many more findings may be kept: the cap, or what the size budget allows.

        Without ``max_findings`` the legacy layers are still bounded, by the most
        findings the 16 MiB budget could hold, so they never build unbounded lists.
        """
        if self.full:
            return 0
        by_size = (MAX_REPORT_BYTES - self.size) // _FINDING_OVERHEAD + 1
        if self.max_findings is None:
            return by_size
        return min(self.max_findings - len(self.findings), by_size)

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
            gml_id = self.locator.gml_id(node, MAX_XPATH_CHARS)
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
        if step in self.dropped or step in self.incomplete:
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


def _schematron(
    schematron_path: str | os.PathLike[str] | None,
) -> tuple[isoschematron.Schematron, RuleContexts]:
    path = Path(schematron_path) if schematron_path is not None else _DEFAULT_SCHEMATRON

    def build() -> tuple[isoschematron.Schematron, RuleContexts]:
        schema = _load_trusted(path, "Schematron rules")
        try:
            compiled = isoschematron.Schematron(
                schema,
                store_report=True,
                # validate() reads the SVRL report itself; an error finder that selects
                # nothing stops lxml serialising every failed assert into its error log.
                error_finder=_NO_ERRORS,
                # No @location strings: they cost memory per failure and time per
                # sibling; findings are located by RuleContexts instead.
                compile_params={"generate-paths": "false"},
                # Locate failures from the same included and expanded schema.
                store_schematron=True,
            )
        except (etree.SchematronParseError, etree.XSLTError) as exc:
            raise ResourceError(f"cannot use Schematron rules {str(path)!r}: {exc}") from exc
        return compiled, RuleContexts(compiled.schematron)

    if schematron_path is not None:
        return build()
    if str(path) not in _CACHE.schematrons:
        _CACHE.schematrons[str(path)] = build()
    return _CACHE.schematrons[str(path)]


def _run_schematron(
    doc: etree._ElementTree,
    builder: _Builder,
    schematron_path: str | os.PathLike[str] | None,
) -> StepStatus:
    schematron, contexts = _schematron(schematron_path)
    widest = _widest_parent(doc)
    if widest > MAX_SCHEMATRON_SIBLINGS:
        # libxslt matches rule contexts in time quadratic in a parent's matched
        # children; refuse rather than run for minutes (human decision, round 5).
        _incomplete(
            builder,
            Step.SCHEMATRON,
            f"an element has {widest:,} child elements, more than the"
            f" {MAX_SCHEMATRON_SIBLINGS:,} the Schematron rules are run on",
        )
        _run_casing(doc, builder)
        return "incomplete"
    fired_total = contexts.fired_count(doc, MAX_SCHEMATRON_FIRED)
    if fired_total > MAX_SCHEMATRON_FIRED:
        # libxslt's time grows with the square of the elements a rule fires on.
        _incomplete(
            builder,
            Step.SCHEMATRON,
            f"the rules would fire on more than {MAX_SCHEMATRON_FIRED:,} elements,"
            " the most the Schematron rules are run on",
        )
        _run_casing(doc, builder)
        return "incomplete"
    try:
        schematron.validate(doc)
    except etree.XSLTError as exc:
        if schematron_path is not None:  # e.g. an unknown function in a user rule's test
            raise ResourceError(
                f"cannot apply Schematron rules {str(schematron_path)!r}: {exc}"
            ) from exc
        # The bundled rules are known to work: this is a libxml2/libxslt limit.
        _incomplete(builder, Step.SCHEMATRON, exc)
        _run_casing(doc, builder)
        return "incomplete"
    report = schematron.validation_report
    fired = fired_patterns(report)
    matched_by_pattern: dict[int, list[etree._Element] | None] = {}
    for context, item, pattern, index in svrl_items(report):
        test = item.get("test") or ""
        if schematron_path is None:
            bundled = rules.schematron_bundled_rule(context, test)
            rule_id = bundled or rules.schematron_user_rule(item.get("id"), context, test)
        else:
            rule_id = rules.schematron_user_rule(item.get("id"), context, test)
        if builder.full:  # count only; do not locate
            builder.drop(Step.SCHEMATRON, Severity.ERROR)
            continue
        if pattern not in matched_by_pattern:
            matched_by_pattern[pattern] = contexts.matched(
                doc, pattern, fired.get(pattern, _NOT_FIRED)
            )
        matched = matched_by_pattern[pattern]
        node = matched[index] if matched is not None and 0 <= index < len(matched) else None
        text = " ".join(item.findtext(f"{SVRL}text", default="").split())
        builder.add(Step.SCHEMATRON, rule_id, Severity.ERROR, text, node=node)
    _run_casing(doc, builder)
    return builder.status(Step.SCHEMATRON)


def _widest_parent(doc: etree._ElementTree) -> int:
    """The most element children any one element has (one linear pass)."""
    widest = 0
    for el in doc.getroot().iter(etree.Element):
        if len(el) > widest:  # len() counts comments too; count elements only if needed
            widest = max(widest, sum(1 for _ in el.iterchildren(etree.Element)))
    return widest


def _element_count(doc: etree._ElementTree) -> int:
    """Nodes in the parsed tree, counted exactly, stopping just past :data:`MAX_ELEMENTS`.

    Counted on the tree, never estimated from the bytes: an entity can expand to many
    elements and a UTF-7 file spells ``<`` as ``+ADw-``. About 0.1 s per million.
    """
    return sum(1 for _ in itertools.islice(doc.getroot().iter(), MAX_ELEMENTS + 1))


_dtd_markup_entity = _xml.dtd_markup_entity
#: validate() refuses a file whose internal-DTD entities make its text more than this
#: many times its raw size (human decision, 2026-10-21).
MAX_ENTITY_EXPANSION: Final = 2


def _expanded_size(doc: etree._ElementTree, limit: int) -> int:
    """Characters of text, tails and attribute values, if the DTD declares entities.

    Only measured when the internal DTD declares an entity (without one, nothing can
    expand); one pass over the tree, stopping as soon as ``limit`` is passed.
    Returns 0 when there is nothing to measure.
    """
    dtd = doc.docinfo.internalDTD
    if dtd is None or next(iter(dtd.iterentities()), None) is None:
        return 0
    total = 0
    for node in doc.getroot().iter():
        total += len(node.text or "") + len(node.tail or "")
        if isinstance(node.tag, str):
            total += sum(len(v) for v in node.attrib.values())
        if total > limit:
            break
    return total


def _base_url(instance: Path) -> str | None:
    """The instance's path for libxml2, or its file: URI if the path is not UTF-8."""
    resolved = instance.resolve()
    try:
        str(resolved).encode("utf-8")
    except UnicodeEncodeError:  # a surrogate-escaped (non-UTF-8) file name
        try:
            return resolved.as_uri()
        except ValueError:
            return None
    return str(resolved)


_markup_entity = _xml.markup_entity


def _incomplete(builder: _Builder, step: Step, exc: Exception | str) -> None:
    """Fail closed: a step that could not finish is an error, never a pass."""
    builder.incomplete.add(step)
    if isinstance(exc, str):
        why = exc
    else:  # the builder scrubs the instance's path; keep an exception's text short
        text = " ".join(str(exc).split())
        why = f"{type(exc).__name__}: {text[:300]}" if text else type(exc).__name__
    builder.add(
        step,
        rules.incomplete(step),
        Severity.ERROR,
        f"The {step.value} check could not finish ({why}); its results are partial,"
        " so the file is not reported as passing.",
    )


def _run_casing(doc: etree._ElementTree, builder: _Builder) -> None:
    """Pydiggs's own unit-converting casing rule (part of the Schematron step)."""
    try:
        problems = _casing_diameter_problems(doc)
    except Exception as exc:  # an XPath limit or a bug: fail closed
        _incomplete(builder, Step.SCHEMATRON, exc)
        return
    for casing, message in problems:
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
                bounded=True,
                dictionary_path_wins=True,
            )
        except (OSError, etree.XMLSyntaxError) as exc:
            raise ResourceError(f"cannot read dictionary data: {exc}") from exc
        cached = (version, validator)
    _CACHE.dictionaries.pop(key, None)
    _CACHE.dictionaries[key] = cached  # most recently used last
    while len(_CACHE.dictionaries) > MAX_CACHED_VALIDATORS:
        del _CACHE.dictionaries[next(iter(_CACHE.dictionaries))]
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

    try:

        def classify(severity: str, text: str, check: int) -> str:
            # Count a dropped message at the severity validate() would have reported it.
            probe = ValidationMessage(severity=severity, element_path="", text=text, check=check)
            return "INFO" if not allow_network and _is_offline(probe) else severity

        try:
            result = semantic.validate_tree(
                doc,
                instance,
                max_messages=builder.remaining,
                max_bytes=max_bytes,
                classify=classify,
            )
        except etree.XPathError as exc:  # a libxml2 limit
            _incomplete(builder, Step.DICTIONARY, exc)
            return builder.status(Step.DICTIONARY)
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
                name = url[1] if url is not None else None
                note = (
                    _REFUSED_NOTE
                    if name in semantic.local_refused
                    else _CAPPED_NOTE
                    if name in semantic.local_capped
                    else _BUDGET_NOTE
                    if name in semantic.local_over_budget
                    else _LOCAL_NOTE[local_dictionaries]
                )
                builder.add(
                    Step.DICTIONARY,
                    rules.dictionary_rule(message.check),
                    Severity.WARNING,
                    _CHECK_PREFIX.sub("", message.text) + note,
                    node=message.node,
                )
                continue
            builder.add_legacy(Step.DICTIONARY, rules.dictionary_rule(message.check), message)
        _local_skips_incomplete(builder, semantic)
        if semantic.limits_hit:
            why = semantic.limits_hit[0]
            more = len(semantic.limits_hit) - 1
            _incomplete(builder, Step.DICTIONARY, why + (f" ({more:,} more)" if more else ""))
        return builder.status(Step.DICTIONARY)
    finally:
        semantic.release()  # the validator is cached; keep no instance tree alive


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


_REFUSED_NOTE: Final = (
    " It was not read for safety (a markup entity in its DTD, or a libxml2 limit)."
)
_BUDGET_NOTE: Final = (
    " The instance's local dictionaries together exceed the local-dictionary byte budget;"
    " this one was not read."
)


def _local_skips_incomplete(builder: _Builder, semantic: DictionarySemanticValidator) -> None:
    """Fail closed when a safety limit kept a local dictionary from being read."""
    if semantic.local_capped:
        _incomplete(
            builder,
            Step.DICTIONARY,
            f"the instance names more than {MAX_LOCAL_DICTIONARIES} different local"
            f" dictionaries; {len(semantic.local_capped)} codeSpace(s) were not read",
        )
    if semantic.local_refused:
        url, reason = next(iter(semantic.local_refused.items()))
        more = len(semantic.local_refused) - 1
        _incomplete(
            builder,
            Step.DICTIONARY,
            f"the local dictionary {url[:200]!r} was not read: {reason}"
            + (f" ({more:,} more)" if more else ""),
        )
    if semantic.local_over_budget:
        _incomplete(
            builder,
            Step.DICTIONARY,
            "local dictionaries exceed their byte budget"
            f" ({MAX_LOCAL_DICTIONARY_BYTES // 2**20} MiB, or what max_bytes leaves);"
            f" {len(semantic.local_over_budget)} codeSpace(s) were not read",
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
    try:
        found, more = deprecations.matches(doc, profile, limit=builder.remaining)
    except etree.XPathError as exc:
        _incomplete(builder, Step.DEPRECATION, exc)
        return builder.status(Step.DEPRECATION)
    for status, count in more.items():  # past the cap: counted, never located
        builder.drop(
            Step.DEPRECATION, Severity.ERROR if status == "removed" else Severity.WARNING, count
        )
    for match in found:
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
    try:
        result = validate_context_tree(
            root, max_messages=builder.remaining, legacy_paths=False, checks=checks
        )
    except etree.XPathError as exc:  # a libxml2 limit
        for step in sorted(_CONTEXT_STEPS & wanted, key=lambda s: s.order):
            _incomplete(builder, step, exc)
        return
    if result.limits_hit:
        _incomplete(builder, Step.CONTEXT, result.limits_hit[0])
    for check, (count, severity) in result.dropped.items():
        step, _ = rules.CONTEXT_RULES[check]
        builder.drop(step, _SEVERITY.get(severity, Severity.ERROR), count)
    for message in result.messages:
        step, rule_id = rules.CONTEXT_RULES[message.check]
        if step in wanted:
            builder.add_legacy(step, rule_id, message)


@dataclass(frozen=True, slots=True)
class _StepOptions:
    profile: str
    instance: Path
    schema_path: str | os.PathLike[str] | None
    dictionary_path: str | os.PathLike[str] | None
    schematron_path: str | os.PathLike[str] | None
    allow_network: bool
    local_dictionaries: LocalDictionaries
    max_bytes: int
    instance_bytes: int


def _gate_element_count(
    doc: etree._ElementTree,
    builder: _Builder,
    wanted: frozenset[Step],
    steps: dict[Step, StepStatus],
) -> frozenset[Step]:
    """Remove the XPath-based steps (each an error) when the file has too many elements.

    libxml2 refuses XPath node sets over 10,000,000 nodes, so those steps cannot be
    trusted to finish.
    """
    if _element_count(doc) <= MAX_ELEMENTS:
        return wanted
    why = f"the file has more than {MAX_ELEMENTS:,} elements"
    for step in sorted(_XPATH_STEPS & wanted, key=lambda s: s.order):
        _incomplete(builder, step, why)
        steps[step] = "incomplete"
    return wanted - _XPATH_STEPS


def _run_steps(
    doc: etree._ElementTree,
    builder: _Builder,
    steps: dict[Step, StepStatus],
    wanted: frozenset[Step],
    o: _StepOptions,
) -> None:
    if Step.SCHEMA in wanted:
        steps[Step.SCHEMA] = _guarded(
            builder, [Step.SCHEMA], lambda: _run_schema(doc, builder, o.profile, o.schema_path)
        )
    if Step.DICTIONARY in wanted:
        steps[Step.DICTIONARY] = _guarded(
            builder,
            [Step.DICTIONARY],
            lambda: _run_dictionary(
                doc,
                builder,
                o.instance,
                o.dictionary_path,
                allow_network=o.allow_network,
                local_dictionaries=o.local_dictionaries,
                max_bytes=min(MAX_LOCAL_DICTIONARY_BYTES, o.max_bytes - o.instance_bytes),
            ),
        )
    if Step.SCHEMATRON in wanted:
        steps[Step.SCHEMATRON] = _guarded(
            builder, [Step.SCHEMATRON], lambda: _run_schematron(doc, builder, o.schematron_path)
        )
    if Step.DEPRECATION in wanted:
        steps[Step.DEPRECATION] = _guarded(
            builder, [Step.DEPRECATION], lambda: _run_deprecation(doc, builder, o.profile)
        )
    if wanted & _CONTEXT_STEPS:
        context_steps = sorted(_CONTEXT_STEPS & wanted, key=lambda s: s.order)
        _guarded(builder, context_steps, lambda: _run_context(doc.getroot(), builder, wanted))
        for step in _CONTEXT_STEPS & wanted:
            steps[step] = builder.status(step)


def _guarded(
    builder: _Builder, steps: list[Step], run: Callable[[], StepStatus | None]
) -> StepStatus:
    """Run one step; an unexpected error makes it incomplete (fail closed), never raises.

    Only the errors validate() documents (a broken trusted resource) propagate. Any
    other exception, from a pydiggs bug or an lxml surprise, becomes a
    ``<step>.incomplete`` error, so it can neither escape validate() nor leave the
    step "passed".
    """
    try:
        status = run()
    except (ResourceError, InstanceReadError):
        raise
    except Exception as exc:  # fail closed on anything unexpected
        for step in steps:
            _incomplete(builder, step, exc)
        return "incomplete"
    return status if status is not None else builder.status(steps[0])


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
    wanted = selected_steps(checks)
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

    def refuse(entity: str, rule_id: str = rules.MARKUP_ENTITY, message: str = "") -> Report:
        builder = _Builder(None, snippets=False, scrub=scrub)
        builder.add(
            Step.STRUCTURE,
            rule_id,
            Severity.ERROR,
            message
            or f"The internal DTD declares the entity {entity[:200]!r}, whose"
            " value contains markup ('<'). validate() does not expand markup entities,"
            " so no other check ran.",
        )
        steps[Step.STRUCTURE] = "failed"
        return report(builder, diggs_version)

    entity = _markup_entity(data)
    if entity is not None:
        return refuse(entity)
    try:
        doc = _xml.parse_bytes(data, _base_url(instance))
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

    entity = _dtd_markup_entity(doc)  # what the main parser itself saw
    if entity is not None:
        return refuse(entity)
    expanded = _expanded_size(doc, MAX_ENTITY_EXPANSION * len(data))
    if expanded > MAX_ENTITY_EXPANSION * len(data):
        return refuse(
            "",
            rules.ENTITY_EXPANSION,
            f"The internal DTD's entities expand this {len(data):,}-byte file to more than"
            f" {MAX_ENTITY_EXPANSION} times its size in text; validate() does not run"
            " checks on files that grow when parsed, so no other check ran.",
        )
    profile = diggs_version or detect_diggs_version(doc)
    builder = _Builder(
        Locator(doc), snippets=include_snippets, scrub=scrub, max_findings=max_findings
    )
    _run_steps(
        doc,
        builder,
        steps,
        _gate_element_count(doc, builder, wanted, steps),
        _StepOptions(
            profile=profile,
            instance=instance,
            schema_path=schema_path,
            dictionary_path=dictionary_path,
            schematron_path=schematron_path,
            allow_network=allow_network,
            local_dictionaries=local_dictionaries,
            max_bytes=limit,
            instance_bytes=len(data),
        ),
    )
    # Well-formedness is always checked (nothing else can run without it), but an
    # unrequested structure step stays "skipped" unless the file is not well-formed.
    for step in builder.dropped:
        steps[step] = "incomplete"
    return report(builder, profile)

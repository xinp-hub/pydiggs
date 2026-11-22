"""The vocabulary check: profile tables against the DIGGS dictionaries, without writing DIGGS.

:func:`check` is what ``pydiggs map check`` runs. It reports what
:func:`~pydiggs.tables.to_model` reports (keys, references, required columns) and adds
what only the bundled DIGGS dictionaries can say: a result column that is not a property
of the property dictionary, a unit DIGGS does not have for that property, a code that is
not in the code list its code space names.

The dictionaries are the ones :func:`pydiggs.validate` uses, read through the same
lookups, so this check and the validation of the file written agree. Nothing is fetched:
a code list that is not bundled with pyDIGGS is not checked, and nothing is said about it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cache
from types import MappingProxyType
from typing import Any, Final

from pydiggs import _xml
from pydiggs._bundled import _DEFAULT_DICTIONARY
from pydiggs.convert._mapping import measure_facts, property_facts
from pydiggs.dictionary import _UOM_ALIASES, DictionarySemanticValidator
from pydiggs.findings.model import Finding
from pydiggs.tables import _report as rep
from pydiggs.tables._arrow import Rows, rows_of
from pydiggs.tables._profile import RESULT_KEYS, RESULT_PLACES, RESULTS_PREFIX
from pydiggs.tables._to_model import _text, to_model

__all__ = [
    "CODE_COLUMNS",
    "check",
    "code_known",
    "code_problem",
    "property_problem",
    "unit_known",
    "unit_problem",
]

Row = Mapping[str, Any]
_GML_ID: Final = "{http://www.opengis.net/gml/3.2}id"
#: Quantity classes the property dictionary names that stand for two classes of units.
_COMBINED: Final = MappingProxyType(
    {"force or mass per volume": ("force per volume", "mass per volume")}
)
#: Columns of a result table that are not results.
_NOT_RESULTS: Final = frozenset({*RESULT_KEYS, *RESULT_PLACES, "depth_uom"})
#: Columns that hold a length, with the column that holds its unit.
_LENGTHS: Final = (
    ("features", "total_depth_uom"),
    ("sampling_activities", "recovery_uom"),
)
#: Columns that hold a code: table, column, the column of its code space, and the DIGGS
#: name the code is written under (None: the row's own ``name``).
CODE_COLUMNS: Final = (
    ("params", "value", "code_space", None),
    ("samples", "type", "type_code_space", "type"),
    ("layers", "classification", "classification_code_space", "classificationSymbol"),
    ("sampling_activities", "method", "method_code_space", "samplingMethod"),
)


@cache
def _dictionaries() -> DictionarySemanticValidator:
    """The bundled dictionaries, as validation reads them; no local file, no network."""
    return DictionarySemanticValidator(local_dictionaries="off", allow_network=False)


@cache
def _quantity_classes() -> Mapping[str, str]:
    """Property key -> its quantity class, for the properties that have one."""
    found: dict[str, str] = {}
    for definition in _xml.parse(_DEFAULT_DICTIONARY).getroot().iter("{*}Definition"):
        key = definition.get(_GML_ID)
        quantity = next(definition.iter("{*}quantityClass"), None)
        text = "" if quantity is None else (quantity.text or "").strip()
        if key and text:
            found.setdefault(key, text)
    return MappingProxyType(found)


@cache
def _all_units() -> frozenset[str]:
    """Every unit of the DIGGS units dictionary and of the DIGGS 3.0.0 length measure."""
    by_class = _dictionaries()._uom_units_by_class
    units: set[str] = set(measure_facts("Length").uoms)
    for members in by_class.values():
        units |= members
    return frozenset(units)


def unit_known(uom: str) -> bool:
    """True when ``uom`` is a unit of the DIGGS units dictionary, for any quantity.

    Examples:
        >>> unit_known("kPa"), unit_known("ft[US]"), unit_known("kilopascals")
        (True, True, False)
    """
    return _UOM_ALIASES.get(uom, uom) in _all_units()


def unit_problem(key: str, uom: str) -> str | None:
    """What is wrong with ``uom`` as the unit of property ``key``, or None.

    None also when the property is unknown, has no quantity class, or has a class the
    units dictionary lists no unit for: there is then nothing to check the unit against.

    Examples:
        >>> unit_problem("tip_resistance", "MPa") is None
        True
        >>> unit_problem("tip_resistance", "m")
        "'m' is not a unit of pressure"
    """
    quantity = _quantity_classes().get(key)
    if quantity is None:
        return None
    by_class = _dictionaries()._uom_units_by_class
    allowed: set[str] = set()
    for name in _COMBINED.get(quantity, (quantity,)):
        allowed |= by_class.get(name, set())
    if not allowed or uom in allowed or _UOM_ALIASES.get(uom, uom) in allowed:
        return None
    return f"{rep.shown(uom)!r} is not a unit of {quantity}"


def property_rule(key: str) -> str:
    """The rule a :func:`property_problem` of ``key`` is reported under.

    ``tables.unknown-property`` when ``key`` is not in the property dictionary, else
    ``tables.unknown-unit``: the property is known and its unit is missing or not wanted.

    Examples:
        >>> property_rule("my_column"), property_rule("tip_resistance")
        ('tables.unknown-property', 'tables.unknown-unit')
    """
    return rep.UNKNOWN_PROPERTY if property_facts().get(key) is None else rep.UNKNOWN_UNIT


def property_problem(key: str, uom: str | None) -> str | None:
    """What is wrong with ``key`` as a property with unit ``uom`` (None: no unit), or None.

    A property must be a key of the bundled DIGGS property dictionary; one that has a
    quantity class needs a unit, and one that has none may not have one. Whether the unit
    is one of that quantity is :func:`unit_problem`.

    Examples:
        >>> property_problem("n_value", None) is None
        True
        >>> property_problem("my_column", None)
        "'my_column' is not a property of the DIGGS property dictionary"
        >>> property_problem("tip_resistance", None)
        "'tip_resistance' is a quantity (pressure) and needs a unit"
        >>> property_problem("n_value", "m")
        "'n_value' has no unit in the DIGGS property dictionary, and one is given ('m')"
    """
    facts = property_facts().get(key)
    if facts is None:
        return f"{rep.shown(key)!r} is not a property of the DIGGS property dictionary"
    if facts.has_unit and uom is None:
        quantity = _quantity_classes().get(key, "a quantity")
        return f"{key!r} is a quantity ({quantity}) and needs a unit"
    if not facts.has_unit and uom is not None:
        return (
            f"{key!r} has no unit in the DIGGS property dictionary, and one is given"
            f" ({rep.shown(uom)!r})"
        )
    return None


def _definition(value: str, code_space: str) -> tuple[bool, Any]:
    """Whether the code list of ``code_space`` is bundled, and the definition of the code."""
    if not code_space.startswith(("http://", "https://")):
        return False, None
    base, _, fragment = code_space.partition("#")
    dictionaries = _dictionaries()
    # The properties dictionary stands in for any URL that ends in properties.xml; only
    # a code list that is registered under its own URL is "bundled" here.
    listed = dictionaries._dict_cache.get(base)
    if listed is None:
        return False, None
    if fragment:
        return True, dictionaries._find_definition(listed, fragment)
    return True, dictionaries._find_definition_by_value(listed, value)


def code_known(value: str, code_space: str) -> bool | None:
    """Whether ``value`` is a code of the code list ``code_space`` names.

    None when the code list is not bundled with pyDIGGS (or ``code_space`` is not a URL):
    nothing is fetched, so nothing can be said. The lookup is the one validation does: a
    code space with a ``#fragment`` names the code itself; one without is searched for
    ``value`` by code and by name.

    Examples:
        >>> sample_types = "https://diggsml.org/def/codes/DIGGS/0.1/sampleType.xml"
        >>> code_known("open_tube", sample_types), code_known("tube", sample_types)
        (True, False)
        >>> code_known("anything", "https://example.com/our-codes.xml") is None
        True
    """
    bundled, definition = _definition(value, code_space)
    return (definition is not None) if bundled else None


def code_problem(value: str, code_space: str, element: str | None = None) -> str | None:
    """What is wrong with ``value`` as a code of ``code_space`` in ``element``, or None.

    Beyond :func:`code_known`: a DIGGS code list says which DIGGS values its codes are
    for, and validation refuses a code used anywhere else. ``element`` is the DIGGS
    name of the value the code is written as (``classificationSymbol`` for
    ``layers.classification``); with None only the code itself is checked.

    Examples:
        >>> uscs = "https://diggsml.org/def/codes/DIGGS/0.1/astmD2487.xml"
        >>> code_problem("CL", uscs) is None
        True
        >>> code_problem("CL", uscs, "classificationSymbol")
        "'CL' is a code for classificationCode, not for classificationSymbol"
        >>> code_problem("clay", uscs)[:27]
        "'clay' is not a code of the"
    """
    bundled, definition = _definition(value, code_space)
    if not bundled:
        return None
    if definition is None:
        listed = rep.shown(_base(code_space), 120)
        return f"{rep.shown(value)!r} is not a code of the code list {listed}"
    if element is None:
        return None
    sources = _dictionaries()._paths(definition).sources
    if not sources.paths or sources.candidates(element):
        return None
    places = ", ".join(sorted(sources.by_terminal)) or "other values"
    return f"{rep.shown(value)!r} is a code for {places}, not for {rep.shown(element)}"


def _base(code_space: str) -> str:
    return code_space.partition("#")[0]


class _Vocabulary:
    """One check: the tables, and the notes about their properties, units and codes."""

    def __init__(self, tables: Mapping[str, Sequence[Row]]) -> None:
        self.tables = tables
        self.notes = rep.Notes()

    def result_columns(self) -> set[tuple[str, str]]:
        """Check every ``result_columns`` row; return the (procedure, column) pairs described.

        A row for one test only is filed under the procedure ``""``: it describes the
        column whatever the procedure of its table.
        """
        described: set[tuple[str, str]] = set()
        for index, row in enumerate(self.tables.get("result_columns", ())):
            column = _text(row.get("column"))
            if column is None:
                continue  # to_model names the row
            procedure = "" if _text(row.get("measurement")) else _text(row.get("procedure")) or ""
            described.add((procedure, column))
            key = _text(row.get("property")) or column
            self.property("result_columns", index, "property", key, _text(row.get("uom")))
        return described

    def property(self, table: str, index: int, column: str, key: str, uom: str | None) -> None:
        problem = property_problem(key, uom)
        if problem is not None:
            self.notes.add(
                property_rule(key),
                table,
                f"Result column: {problem}.",
                row=index,
                column=column,
            )
        wrong = None if uom is None else unit_problem(key, uom)
        if wrong is not None:
            self.notes.add(
                rep.UNKNOWN_UNIT,
                table,
                f"Result column {rep.shown(key)!r}: {wrong}; DIGGS validation will refuse it.",
                row=index,
                column="uom",
            )

    def results(self, described: set[tuple[str, str]]) -> None:
        """Check the columns of the result tables that ``result_columns`` does not describe."""
        for name, rows in self.tables.items():
            if not name.startswith(RESULTS_PREFIX):
                continue
            procedure = name[len(RESULTS_PREFIX) :]
            first: dict[str, int] = {}
            for index, row in enumerate(rows):
                for column, value in row.items():
                    if column not in _NOT_RESULTS and _text(value) is not None:
                        first.setdefault(str(column), index)
            for column, index in first.items():
                if (procedure, column) not in described and ("", column) not in described:
                    self.property(name, index, column, column, None)

    def units(self) -> None:
        """Check the units the profile gives a column for, beyond the depth units."""
        lengths = measure_facts("Length").uoms
        for table, column in _LENGTHS:
            for index, row in enumerate(self.tables.get(table, ())):
                uom = _text(row.get(column))
                if uom is not None and uom not in lengths:
                    self.notes.add(
                        rep.UNKNOWN_UNIT,
                        table,
                        f"Values of {column} in {table} are not units of length DIGGS 3.0.0"
                        f" has, such as {rep.shown(uom)!r} ({{times}}).",
                        row=index,
                        column=column,
                    )
        for index, row in enumerate(self.tables.get("params", ())):
            uom = _text(row.get("uom"))
            if uom is not None and not unit_known(uom):
                self.notes.add(
                    rep.UNKNOWN_UNIT,
                    "params",
                    f"The unit {rep.shown(uom)!r} is not in the DIGGS units dictionary"
                    " ({times}).",
                    row=index,
                    column="uom",
                )

    def codes(self) -> None:
        """Check every code whose code list is bundled with pyDIGGS."""
        seen: dict[tuple[str, str, str | None], str | None] = {}
        for table, column, space_column, written in CODE_COLUMNS:
            for index, row in enumerate(self.tables.get(table, ())):
                value, space = _text(row.get(column)), _text(row.get(space_column))
                if value is None or space is None:
                    continue
                element = written or _text(row.get("name"))
                asked = (value, space, element)
                if asked not in seen:
                    seen[asked] = code_problem(value, space, element)
                problem = seen[asked]
                if problem is not None:
                    self.notes.add(
                        rep.UNKNOWN_CODE,
                        table,
                        f"Codes of {column} in {table}: {problem}; DIGGS validation will"
                        " refuse it ({times}).",
                        row=index,
                        column=column,
                    )


def check(tables: Mapping[str, Rows]) -> tuple[Finding, ...]:
    """Check profile tables against the DIGGS dictionaries, without writing a file.

    The findings are those of :func:`~pydiggs.tables.to_model` (keys used twice,
    references to no row, values DIGGS requires) followed by what the bundled DIGGS
    dictionaries say:

    - ``tables.unknown-property``: a result column is not a property of the DIGGS
      property dictionary (its ``property`` in ``result_columns``, else the column
      name), or has a unit when the property has none, or none when it has one;
    - ``tables.unknown-unit``: the unit of a result column is not a unit of the
      property's quantity, a ``total_depth_uom`` or ``recovery_uom`` is not a unit of
      length, or the ``uom`` of a ``params`` row is not a DIGGS unit at all;
    - ``tables.unknown-code``: a value with a code space (in ``params``, ``samples.type``,
      ``layers.classification``, ``sampling_activities.method``) is not in that code
      list. A code list that is not bundled with pyDIGGS is not checked.

    Nothing is fetched, and no value is changed.

    Args:
        tables: The tables, as for :func:`~pydiggs.tables.to_model`.

    Returns:
        The findings, each with the table, row and column as its ``path``.

    Raises:
        TablesError: See :func:`~pydiggs.tables.to_model`.

    Examples:
        >>> found = check(
        ...     {
        ...         "document": [{"creation_date": "2026-11-21"}],
        ...         "projects": [{"key": "P1"}],
        ...         "features": [
        ...             {
        ...                 "key": "BH-1",
        ...                 "project": "P1",
        ...                 "x": 1,
        ...                 "y": 2,
        ...                 "crs": "http://www.opengis.net/def/crs/EPSG/0/27700",
        ...                 "depth_uom": "m",
        ...             }
        ...         ],
        ...         "measurements": [
        ...             {
        ...                 "key": "SPT-1",
        ...                 "feature": "BH-1",
        ...                 "procedure": "DrivenPenetrationTest",
        ...                 "depth": 3,
        ...             }
        ...         ],
        ...         "params": [
        ...             {
        ...                 "node": "SPT-1",
        ...                 "scope": "procedure",
        ...                 "name": "penetrationTestType",
        ...                 "value": "SPT",
        ...             }
        ...         ],
        ...         "results_DrivenPenetrationTest": [{"measurement": "SPT-1", "blows": 21}],
        ...     }
        ... )
        >>> [(finding.rule_id, finding.path) for finding in found]
        [('tables.unknown-property', 'results_DrivenPenetrationTest/1/blows')]
    """
    sent = {name: [dict(row) for row in rows_of(rows, name)] for name, rows in tables.items()}
    structure = to_model(sent).findings
    kept = {
        name: [row for row in rows if any(_text(v) for v in row.values())]
        for name, rows in sent.items()
    }
    vocabulary = _Vocabulary(kept)
    vocabulary.results(vocabulary.result_columns())
    vocabulary.units()
    vocabulary.codes()
    return (*structure, *vocabulary.notes.findings())

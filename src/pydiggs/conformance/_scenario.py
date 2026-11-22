"""Scenario challenges: seeded, realistic source data for one conformance class.

A scenario is a :class:`Dataset`: plain tables (project, locations, tests) with a unit
for every column, as a field or laboratory system would hold them. The tables are what a
vendor loads for an export challenge. The same tables, written as DIGGS 3.0.0 by
pyDIGGS's own converter, are the file a vendor reads for an import challenge.

Every value is kept as the text the table shows, so the precision of the source is known
when an export is graded. The data is synthetic and is not from any project.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydiggs.conformance._rng import Rng
from pydiggs.errors import ResourceError

__all__ = ["SCENARIOS", "Dataset", "Field", "Table", "build", "reference_xml"]

_PLACES = ("Harbour", "Mill Creek", "Northgate", "Riverside", "Summit", "Westfield")
_WORKS = ("Bridge", "Culvert", "Embankment", "Pump Station", "Retaining Wall")


@dataclass(frozen=True, slots=True)
class Field:
    """One column of a table: its name, its unit ("" for none) and what it holds."""

    name: str
    unit: str
    meaning: str


@dataclass(frozen=True, slots=True)
class Table:
    """One source table: its fields and its rows, every value as text."""

    name: str
    fields: tuple[Field, ...]
    rows: tuple[tuple[str, ...], ...]

    def column(self, name: str) -> tuple[str, ...]:
        """The values of the column ``name``."""
        index = [f.name for f in self.fields].index(name)
        return tuple(row[index] for row in self.rows)


@dataclass(frozen=True, slots=True)
class Dataset:
    """The source data of one challenge."""

    scenario: str
    token: str
    tables: tuple[Table, ...]

    def table(self, name: str) -> Table:
        """The table ``name``.

        Raises:
            KeyError: The dataset has no such table.
        """
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(name)


def _fixed(value: Decimal, places: int) -> str:
    return f"{value:.{places}f}"


def _project(rng: Rng, token: str) -> Table:
    # The name carries an ampersand (it must be escaped in XML) and the challenge token.
    name = f"{rng.choice(_PLACES)} {rng.choice(_WORKS)} & Approach {token}"
    return Table(
        "project",
        (
            Field("project_id", "", "The project's identifier"),
            Field("project_name", "", "The project's name; carry it exactly as written"),
        ),
        ((f"P-{token}", name),),
    )


def _locations(rng: Rng, names: tuple[str, ...], kind: str, unit: str) -> Table:
    east = Decimal(rng.integer(400_000, 480_000))
    north = Decimal(rng.integer(200_000, 280_000))
    rows = []
    for i, name in enumerate(names):
        rows.append(
            (
                name,
                kind,
                _fixed(east + Decimal(rng.integer(5, 60)) * (i + 1), 2),
                _fixed(north + Decimal(rng.integer(5, 60)) * (i + 1), 2),
                _fixed(Decimal(rng.integer(800, 4000)) / 100, 2),
            )
        )
    return Table(
        "locations",
        (
            Field("location_id", "", "The name of the boring or sounding; carry it exactly"),
            Field("kind", "", "borehole or sounding"),
            Field("easting", unit, "Easting on the site grid"),
            Field("northing", unit, "Northing on the site grid"),
            Field("ground_level", unit, "Ground level at the location"),
        ),
        tuple(rows),
    )


def _with_depths(locations: Table, depths: dict[str, str], unit: str) -> Table:
    fields = (*locations.fields, Field("total_depth", unit, "Final depth of the hole"))
    rows = tuple((*row, depths[row[0]]) for row in locations.rows)
    return Table(locations.name, fields, rows)


def _spt(rng: Rng, token: str) -> Dataset:
    """Standard penetration tests in three borings, in metres or in feet.

    Traps a correct exporter passes without noticing: a boring name with a space and one
    with leading zeros, a refusal (50 blows over a short drive), a weight-of-hammer test
    (N of 0), and depths in feet for about half the seeds.
    """
    feet = rng.integer(0, 1) == 1
    unit, drive_unit = ("ft", "in") if feet else ("m", "mm")
    step = Decimal("5.0") if feet else Decimal("1.5")
    full_drive, short_drives = ("18", ("3", "5", "9")) if feet else ("450", ("75", "125", "225"))
    number = rng.integer(2, 89)
    names = (f"B-{number:03d}", f"B-{number + 1}A", f"BH {number + 2}")
    locations = _locations(rng.fork("loc"), names, "borehole", unit)
    rows: list[tuple[str, ...]] = []
    depths: dict[str, str] = {}
    for name in names:
        hole = rng.fork(name)
        count = hole.integer(4, 7)
        efficiency = str(hole.integer(60, 85))
        special = {hole.integer(0, count - 2): "woh"}
        special[count - 1] = "refusal"
        top = step
        for i in range(count):
            kind = special.get(i)
            if kind == "refusal":
                blows, drive, remark = "50", hole.choice(short_drives), "Refusal"
            elif kind == "woh":
                blows, drive, remark = "0", full_drive, "Weight of hammer"
            else:
                blows, drive, remark = str(hole.integer(3, 48)), full_drive, ""
            rows.append((name, _fixed(top, 2), blows, drive, efficiency, remark))
            top += step
        depths[name] = _fixed(top, 2)
    tests = Table(
        "spt",
        (
            Field("location_id", "", "The boring the test is in"),
            Field("depth_top", unit, "Depth to the top of the test drive"),
            Field("n_value", "", "SPT N value (blows for the test drive)"),
            Field("penetration", drive_unit, "Total penetration of the test drive"),
            Field("hammer_efficiency", "%", "Hammer energy ratio"),
            Field("remark", "", "Remark; may be empty"),
        ),
        tuple(rows),
    )
    return Dataset(
        "spt",
        token,
        (_project(rng.fork("proj"), token), _with_depths(locations, depths, unit), tests),
    )


def _cpt(rng: Rng, token: str) -> Dataset:
    """Piezocone soundings with qc, fs and u2, in metres or in feet.

    Traps: cone resistance in MPa beside sleeve friction and pore pressure in kPa,
    negative pore pressures, and a gap in the depths where a rod was added.
    """
    feet = rng.integer(0, 1) == 1
    unit = "ft" if feet else "m"
    step = Decimal("0.20") if feet else Decimal("0.05")
    number = rng.integer(1, 60)
    names = (f"CPT-{number:02d}", f"CPT {number + 1}B")
    locations = _locations(rng.fork("loc"), names, "sounding", unit)
    tests: list[tuple[str, ...]] = []
    rows: list[tuple[str, ...]] = []
    depths: dict[str, str] = {}
    for name in names:
        hole = rng.fork(name)
        count = hole.integer(24, 40)
        gap_at = hole.integer(8, count - 8)
        tests.append((name, "1"))
        depth = step
        qc = Decimal(hole.integer(40, 300)) / 100
        for i in range(count):
            if i == gap_at:
                depth += step * 4
            qc = max(Decimal("0.20"), qc + Decimal(hole.integer(-60, 80)) / 100)
            fs = (qc * Decimal(hole.integer(5, 40))).quantize(Decimal("0.1"))
            u2 = Decimal(hole.integer(-250, 4000)) / 10
            rows.append((name, "1", _fixed(depth, 2), _fixed(qc, 2), _fixed(fs, 1), _fixed(u2, 1)))
            depth += step
        depths[name] = _fixed(depth, 2)
    general = Table(
        "cpt_tests",
        (
            Field("location_id", "", "The sounding the test is in"),
            Field("test_id", "", "The test's reference within the sounding"),
        ),
        tuple(tests),
    )
    data = Table(
        "cpt_data",
        (
            Field("location_id", "", "The sounding"),
            Field("test_id", "", "The test"),
            Field("depth", unit, "Depth of the reading"),
            Field("tip_resistance", "MPa", "Cone resistance qc"),
            Field("sleeve_friction", "kPa", "Sleeve friction fs"),
            Field("pore_pressure_u2", "kPa", "Pore pressure u2 (behind the cone)"),
        ),
        tuple(rows),
    )
    return Dataset(
        "cpt",
        token,
        (_project(rng.fork("proj"), token), _with_depths(locations, depths, unit), general, data),
    )


#: Scenario name -> its builder.
SCENARIOS = {"spt": _spt, "cpt": _cpt}


def build(scenario: str, seed: str, token: str) -> Dataset:
    """The dataset of ``scenario`` for ``seed``; the same arguments give the same data."""
    return SCENARIOS[scenario](Rng(seed), token)


def _group(
    name: str, headings: tuple[tuple[str, str, str], ...], rows: list[tuple[str, ...]]
) -> str:
    def line(kind: str, values: tuple[str, ...]) -> str:
        return ",".join(f'"{v}"' for v in (kind, *values))

    out = [
        line("GROUP", (name,)),
        line("HEADING", tuple(h[0] for h in headings)),
        line("UNIT", tuple(h[1] for h in headings)),
        line("TYPE", tuple(h[2] for h in headings)),
    ]
    out.extend(line("DATA", tuple(v.replace('"', '""') for v in row)) for row in rows)
    return "\r\n".join(out) + "\r\n"


def _interchange(dataset: Dataset) -> bytes:
    """The dataset as the table format pyDIGGS's converter reads (internal)."""
    project = dataset.table("project").rows[0]
    locations = dataset.table("locations")
    unit = locations.fields[2].unit
    kind = {"borehole": "CP", "sounding": "CPT"}
    groups = [
        _group("PROJ", (("PROJ_ID", "", "ID"), ("PROJ_NAME", "", "X")), [project]),
        _group(
            "TRAN",
            (
                ("TRAN_ISNO", "", "X"),
                ("TRAN_AGS", "", "X"),
                ("TRAN_DLIM", "", "X"),
                ("TRAN_RCON", "", "X"),
            ),
            [("1", "4.1.1", "|", "+")],
        ),
        _group(
            "LOCA",
            (
                ("LOCA_ID", "", "ID"),
                ("LOCA_TYPE", "", "PA"),
                ("LOCA_NATE", unit, "2DP"),
                ("LOCA_NATN", unit, "2DP"),
                ("LOCA_GL", unit, "2DP"),
                ("LOCA_FDEP", unit, "2DP"),
            ),
            [(r[0], kind[r[1]], r[2], r[3], r[4], r[5]) for r in locations.rows],
        ),
    ]
    if dataset.scenario == "lab":
        from pydiggs.conformance import _lab  # noqa: PLC0415  # _lab imports this module

        groups.extend(_group(*group) for group in _lab.groups(dataset, unit))
    elif dataset.scenario == "spt":
        spt = dataset.table("spt")
        drive_unit = spt.fields[3].unit
        groups.append(
            _group(
                "ISPT",
                (
                    ("LOCA_ID", "", "ID"),
                    ("ISPT_TOP", unit, "2DP"),
                    ("ISPT_NVAL", "", "0DP"),
                    ("ISPT_NPEN", drive_unit, "0DP"),
                    ("ISPT_ERAT", "%", "0DP"),
                    ("ISPT_REM", "", "X"),
                ),
                list(spt.rows),
            )
        )
    else:  # cpt
        groups.append(
            _group(
                "SCPG",
                (("LOCA_ID", "", "ID"), ("SCPG_TESN", "", "X"), ("SCPG_TYPE", "", "PA")),
                [(*row, "CPTU") for row in dataset.table("cpt_tests").rows],
            )
        )
        groups.append(
            _group(
                "SCPT",
                (
                    ("LOCA_ID", "", "ID"),
                    ("SCPG_TESN", "", "X"),
                    ("SCPT_DPTH", unit, "2DP"),
                    ("SCPT_RES", "MPa", "2DP"),
                    ("SCPT_FRES", "kPa", "1DP"),
                    ("SCPT_PWP2", "kPa", "1DP"),
                ),
                list(dataset.table("cpt_data").rows),
            )
        )
    return "\r\n".join(groups).encode("utf-8")


def reference_xml(dataset: Dataset) -> bytes:
    """The dataset as a DIGGS 3.0.0 document, written by pyDIGGS's converter.

    Raises:
        ResourceError: The converter did not carry the whole dataset, or the result is
            not valid DIGGS. Either is a defect in the suite, never in a submission.
    """
    from pydiggs import convert  # noqa: PLC0415  # the converter loads on demand

    result = convert.ags4_to_diggs(_interchange(dataset), name=f"{dataset.scenario}.source")
    valid = result.validation is not None and result.validation.ok
    if result.xml is None or not result.complete or not valid:
        raise ResourceError(f"the {dataset.scenario} reference file could not be written")
    return result.xml

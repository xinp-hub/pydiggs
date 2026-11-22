"""The laboratory index tests scenario: water content, Atterberg limits, density, gradation.

A seeded, synthetic laboratory programme on samples from two or three borings: natural
water content, liquid and plastic limits (one specimen non-plastic), bulk and dry
density on the undisturbed samples, and one particle-size curve per boring. The data is
written for the suite and is not from any project.

Traps a correct exporter passes without noticing: a non-plastic specimen (no limits, a
flag), density given only for some samples (an empty cell is not a zero), percentages that
DIGGS writes as plain numbers (liquid limit 48) beside ones it writes in percent (water
content 31.5 %), sieve sizes in millimetres beside depths in feet, and a sample name with
a space.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Final

from pydiggs.conformance import _facts
from pydiggs.conformance._facts import Fact, Question
from pydiggs.conformance._rng import Rng
from pydiggs.conformance._scenario import Dataset, Field, Table, _fixed, _locations, _project

__all__ = ["build", "facts", "groups", "questions"]

#: Standard sieves, in millimetres, coarse to fine (ASTM D6913 sizes).
_SIEVES: Final = (
    "37.5",
    "19.0",
    "9.5",
    "4.75",
    "2.00",
    "0.850",
    "0.425",
    "0.250",
    "0.150",
    "0.075",
)
_PROCEDURES: Final = {
    "water_content": ("WaterContentTest|LabDensityTest", "water_content_natural"),
    "liquid_limit": ("AtterbergLimitsTest", "liquid_limit"),
    "plastic_limit": ("AtterbergLimitsTest", "plastic_limit"),
    "plasticity_index": ("AtterbergLimitsTest", "plasticity_index"),
    "bulk_density": ("LabDensityTest", "bulk_density"),
    "dry_density": ("LabDensityTest", "dry_density"),
}


def _samples(rng: Rng, names: tuple[str, ...], unit: str) -> tuple[Table, dict[str, str]]:
    """Two to four samples per boring, alternating undisturbed and disturbed."""
    step = Decimal("5.0") if unit == "ft" else Decimal("1.5")
    length = Decimal("1.5") if unit == "ft" else Decimal("0.45")
    rows: list[tuple[str, ...]] = []
    totals: dict[str, str] = {}
    for hole in names:
        hole_rng = rng.fork(hole)
        top = step
        for i in range(hole_rng.integer(2, 4)):
            kind = "U" if i % 2 == 0 else "D"
            sample_id = f"{hole} {kind}{i + 1}" if " " in hole else f"{hole}-{kind}{i + 1}"
            rows.append((hole, sample_id, kind, _fixed(top, 2), _fixed(top + length, 2)))
            top += step + Decimal(hole_rng.integer(0, 4)) * step / 2
        totals[hole] = _fixed(top + step, 2)
    table = Table(
        "samples",
        (
            Field("location_id", "", "The boring the sample is from"),
            Field("sample_id", "", "The sample's name; carry it exactly"),
            Field("sample_type", "", "U undisturbed, D disturbed"),
            Field("depth_top", unit, "Depth to the top of the sample"),
            Field("depth_base", unit, "Depth to the base of the sample"),
        ),
        tuple(rows),
    )
    return table, totals


def _tests(rng: Rng, samples: Table, unit: str) -> Table:
    """One specimen per sample: water content, limits (or non-plastic), density on U."""
    non_plastic = rng.integer(0, len(samples.rows) - 1)
    rows: list[tuple[str, ...]] = []
    for i, (hole, sample_id, kind, top, _base) in enumerate(samples.rows):
        here = rng.fork(sample_id)
        specimen = _fixed(Decimal(top) + Decimal(here.integer(1, 20)) / 100, 2)
        water = _fixed(Decimal(here.integer(80, 450)) / 10, 1)
        if i == non_plastic:
            limits = ("NP", "NP", "NP")
        else:
            plastic = here.integer(14, 32)
            liquid = plastic + here.integer(4, 45)
            limits = (str(liquid), str(plastic), str(liquid - plastic))
        bulk = dry = ""
        if kind == "U":
            bulk_value = Decimal(here.integer(175, 215)) / 100
            dry_value = bulk_value / (1 + Decimal(water) / 100)
            bulk, dry = _fixed(bulk_value, 2), _fixed(dry_value, 2)
        rows.append((hole, sample_id, specimen, water, *limits, bulk, dry))
    return Table(
        "lab_tests",
        (
            Field("location_id", "", "The boring"),
            Field("sample_id", "", "The sample the specimen is from"),
            Field("specimen_depth", unit, "Depth of the specimen"),
            Field("water_content", "%", "Natural water content"),
            Field("liquid_limit", "%", "Liquid limit; NP for a non-plastic specimen"),
            Field("plastic_limit", "%", "Plastic limit; NP for a non-plastic specimen"),
            Field("plasticity_index", "%", "Plasticity index; NP for a non-plastic specimen"),
            Field("bulk_density", "Mg/m3", "Bulk density; empty when not measured"),
            Field("dry_density", "Mg/m3", "Dry density; empty when not measured"),
        ),
        tuple(rows),
    )


def _gradation(rng: Rng, tests: Table) -> tuple[Table, Table]:
    """One particle-size test per boring, on its first disturbed (or first) specimen.

    Returns the test (with its reported clay fraction, from the hydrometer) and the
    points of its curve.
    """
    chosen: dict[str, tuple[str, ...]] = {}
    for row in tests.rows:
        disturbed = row[1].rsplit(" ", 1)[-1].rsplit("-", 1)[-1].startswith("D")
        if row[0] not in chosen or (disturbed and not chosen[row[0]][-1]):
            chosen[row[0]] = (*row, "D" if disturbed else "")
    summary: list[tuple[str, ...]] = []
    points: list[tuple[str, ...]] = []
    for hole, row in chosen.items():
        here = rng.fork("grading/" + hole)
        passing = Decimal(100)
        for sieve in _SIEVES:
            passing = max(Decimal(1), passing - Decimal(here.integer(0, 14)))
            points.append((hole, row[1], row[2], sieve, _fixed(passing, 0)))
        clay = Decimal(here.integer(0, int(passing) * 10)) / 10
        summary.append((hole, row[1], row[2], _fixed(clay, 1)))
    depth_unit = tests.fields[2].unit
    test_table = Table(
        "particle_size_tests",
        (
            Field("location_id", "", "The boring"),
            Field("sample_id", "", "The sample"),
            Field("specimen_depth", depth_unit, "Depth of the specimen"),
            Field("clay_percent", "%", "Percent finer than 2 micrometres (hydrometer)"),
        ),
        tuple(summary),
    )
    curve = Table(
        "gradation",
        (
            Field("location_id", "", "The boring"),
            Field("sample_id", "", "The sample"),
            Field("specimen_depth", depth_unit, "Depth of the specimen"),
            Field("sieve_size", "mm", "Sieve opening"),
            Field("percent_passing", "%", "Percent of the specimen passing the sieve"),
        ),
        tuple(points),
    )
    return test_table, curve


def build(rng: Rng, token: str) -> Dataset:
    """A laboratory programme in two or three borings, in metres or in feet."""
    unit = "ft" if rng.integer(0, 1) == 1 else "m"
    number = rng.integer(2, 89)
    names = (f"B-{number:03d}", f"BH {number + 1}") + (
        (f"B-{number + 2}",) if rng.integer(0, 1) else ()
    )
    samples, totals = _samples(rng.fork("samples"), names, unit)
    locations = _locations(rng.fork("loc"), names, "borehole", unit)
    fields = (*locations.fields, Field("total_depth", unit, "Final depth of the hole"))
    locations = Table(locations.name, fields, tuple((*r, totals[r[0]]) for r in locations.rows))
    tests = _tests(rng.fork("tests"), samples, unit)
    particle_size, curve = _gradation(rng.fork("grading"), tests)
    return Dataset(
        "lab",
        token,
        (
            _project(rng.fork("proj"), token),
            locations,
            samples,
            tests,
            particle_size,
            curve,
        ),
    )


def facts(dataset: Dataset) -> tuple[Fact, ...]:
    """The answer key: the core facts, each reported value, each point of each curve."""
    out = _facts.core_facts(dataset)
    tests = dataset.table("lab_tests")
    units = {f.name: f.unit for f in tests.fields}
    depth_unit = units["specimen_depth"]
    names = [f.name for f in tests.fields]
    for row in tests.rows:
        record = dict(zip(names, row, strict=True))
        where = (record["location_id"], record["specimen_depth"], depth_unit)
        for column, (procedure, key) in _PROCEDURES.items():
            text = record[column]
            if not text or text == "NP":
                fact = _facts.value_fact(
                    "lab", where, column, "0", units[column], procedure=procedure, key=key
                )
                out.append(
                    replace(
                        fact,
                        kind="absent",
                        shown=f"no {column} at {where[1]} {depth_unit} in {where[0]!r}"
                        f" (the source {'says non-plastic' if text else 'leaves it empty'})",
                    )
                )
                continue
            out.append(
                _facts.value_fact(
                    "lab", where, column, text, units[column], procedure=procedure, key=key
                )
            )
        flag = _facts.value_fact(
            "lab", where, "non_plastic", "1", "", procedure="AtterbergLimitsTest", key="non_plastic"
        )
        if record["liquid_limit"] == "NP":
            out.append(flag)
        else:
            out.append(
                replace(
                    flag,
                    kind="not-flagged",
                    shown=f"sample {record['sample_id']!r} not flagged non-plastic",
                )
            )
    for hole, _sample, depth, clay in dataset.table("particle_size_tests").rows:
        out.append(
            _facts.value_fact(
                "lab",
                (hole, depth, depth_unit),
                "clay_percent",
                clay,
                "%",
                procedure="ParticleSizeTest",
                key="clay_percent_2_micron",
            )
        )
    grading = dataset.table("gradation")
    for hole, _sample, _depth, sieve, passing in grading.rows:
        # The curve's axis is the sieve size, not the depth: one curve per boring.
        fact = _facts.value_fact(
            "lab",
            (hole, sieve, "mm"),
            "percent_passing",
            passing,
            "%",
            procedure="ParticleSizeTest",
            key=_facts.GRADING_KEY,
        )
        out.append(
            replace(
                fact,
                id=f"lab/{hole}/sieve {sieve} mm/percent_passing",
                shown=f"{passing} % passing the {sieve} mm sieve in {hole!r}",
            )
        )
    return tuple(out)


def questions(dataset: Dataset, stream: str) -> tuple[Question, ...]:
    """The import questions: core, then values read from the file, then the defect."""
    rng = Rng(stream + "/questions")
    out = _facts.core_questions(dataset, rng)
    tests = dataset.table("lab_tests")
    names = [f.name for f in tests.fields]
    rows = [dict(zip(names, row, strict=True)) for row in tests.rows]
    out.append(
        Question(
            "values.samples",
            "values",
            "count",
            "How many lab specimens does the file hold?",
            "",
            str(len(rows)),
        )
    )
    plastic = rng.choice([r for r in rows if r["liquid_limit"] != "NP"])
    out.append(
        Question(
            "values.liquid-limit",
            "values",
            "count",
            f"What is the liquid limit of sample {plastic['sample_id']}, in percent?",
            "%",
            plastic["liquid_limit"],
        )
    )
    expected, tol = _facts._convert(plastic["water_content"], "%", "%")
    out.append(
        Question(
            "values.water-content",
            "values",
            "number",
            f"What is the natural water content of sample {plastic['sample_id']}?",
            "%",
            expected,
            tol,
        )
    )
    non_plastic = next(r for r in rows if r["liquid_limit"] == "NP")
    out.append(
        Question(
            "values.non-plastic",
            "values",
            "text",
            "Which sample is non-plastic? Give its name.",
            "",
            non_plastic["sample_id"],
        )
    )
    dense = rng.choice([r for r in rows if r["dry_density"]])
    expected, tol = _facts._convert(dense["dry_density"], "Mg/m3", "kg/m3")
    out.append(
        Question(
            "values.dry-density",
            "values",
            "number",
            f"What is the dry density of sample {dense['sample_id']}?",
            "kg/m3",
            expected,
            tol,
        )
    )
    curve = rng.choice([r for r in dataset.table("gradation").rows if r[3] == "0.075"])
    expected, tol = _facts._convert(curve[4], "%", "%")
    out.append(
        Question(
            "values.fines",
            "values",
            "number",
            f"What percent of sample {curve[1]} passes the 0.075 mm sieve?",
            "%",
            expected,
            tol,
        )
    )
    out.append(_facts.defect_question(rng))
    return tuple(out)


def groups(
    dataset: Dataset, unit: str
) -> list[tuple[str, tuple[tuple[str, str, str], ...], list[tuple[str, ...]]]]:
    """The laboratory groups of the interchange table format the converter reads.

    Returns (group, headings, rows) for SAMP, LNMC, LLPL, LDEN, GRAG and GRAT.
    """
    samples = dataset.table("samples")
    index = {row[1]: (row[0], row[3], str(i + 1), row[2]) for i, row in enumerate(samples.rows)}
    keys = (
        ("LOCA_ID", "", "ID"),
        ("SAMP_TOP", unit, "2DP"),
        ("SAMP_REF", "", "X"),
        ("SAMP_TYPE", "", "PA"),
        ("SAMP_ID", "", "ID"),
        ("SPEC_REF", "", "X"),
        ("SPEC_DPTH", unit, "2DP"),
    )

    def key(sample_id: str, depth: str) -> tuple[str, ...]:
        hole, top, ref, kind = index[sample_id]
        return (hole, top, ref, kind, sample_id, "A", depth)

    tests = dataset.table("lab_tests").rows
    grading = dataset.table("gradation").rows
    return [
        (
            "SAMP",
            (*keys[:5], ("SAMP_BASE", unit, "2DP")),
            [(r[0], r[3], index[r[1]][2], r[2], r[1], r[4]) for r in samples.rows],
        ),
        ("LNMC", (*keys, ("LNMC_MC", "%", "1DP")), [(*key(r[1], r[2]), r[3]) for r in tests]),
        (
            "LLPL",
            (*keys, ("LLPL_LL", "%", "0DP"), ("LLPL_PL", "%", "0DP"), ("LLPL_PI", "%", "0DP")),
            [(*key(r[1], r[2]), r[4], r[5], r[6]) for r in tests],
        ),
        (
            "LDEN",
            (*keys, ("LDEN_BDEN", "Mg/m3", "2DP"), ("LDEN_DDEN", "Mg/m3", "2DP")),
            [(*key(r[1], r[2]), r[7], r[8]) for r in tests if r[7]],
        ),
        (
            "GRAG",
            (*keys, ("GRAG_CLAY", "%", "1DP")),
            [(*key(r[1], r[2]), r[3]) for r in dataset.table("particle_size_tests").rows],
        ),
        (
            "GRAT",
            (*keys, ("GRAT_SIZE", "mm", "3SF"), ("GRAT_PERP", "%", "0DP")),
            [(*key(r[1], r[2]), r[3], r[4]) for r in grading],
        ),
    ]

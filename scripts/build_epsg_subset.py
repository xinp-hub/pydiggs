r"""Build ``src/pydiggs/crs/epsg-subset.json``: facts about EPSG coordinate systems.

pyDIGGS reads coordinates without pyproj, but it still needs to know what an EPSG code
is: a geographic system (and so which axis is latitude), a projected one and its unit,
a vertical one. This script extracts those facts, and nothing else (no WKT, no
transformation parameters), from the EPSG dataset that PROJ bundles, so the file is
reproducible from a pinned PROJ version. It is a maintainer tool, never a dependency::

    uv run --no-project --python 3.12 --with pyproj==3.8.0 --with lxml \
        python scripts/build_epsg_subset.py

The subset is every non-deprecated EPSG CRS that is

* geographic 2D or 3D, or vertical, anywhere (axis order and datum decide how pyDIGGS
  reads a position);
* projected, geocentric or compound with an area of use in the United States or its
  territories, where almost every DIGGS file comes from;
* a WGS 84 or ETRS89 UTM zone;
* used by an ``srsName`` in the conformance corpus, plus the components of every
  compound CRS above.

The EPSG Dataset is owned by the International Association of Oil & Gas Producers
(IOGP) and used under its terms of use (https://epsg.org/terms-of-use.html); the file
records the dataset version and carries that acknowledgement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pyproj import CRS, __version__, proj_version_str
from pyproj.aoi import AreaOfInterest
from pyproj.database import get_database_metadata, query_crs_info
from pyproj.enums import PJType

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "pydiggs" / "crs" / "epsg-subset.json"
sys.path.insert(0, str(ROOT / "src"))

#: The United States and its territories (west, south, east, north in degrees).
US_AREAS = {
    "conterminous": (-125.0, 24.0, -66.9, 49.5),
    "alaska": (-179.9, 51.0, -129.9, 71.5),
    "hawaii": (-160.5, 18.8, -154.7, 22.3),
    "puerto-rico-virgin-islands": (-67.95, 17.6, -64.5, 18.6),
    "guam-northern-marianas": (144.5, 13.2, 146.1, 20.6),
    "american-samoa": (-171.1, -14.6, -168.1, -11.0),
}
EVERYWHERE = [PJType.GEOGRAPHIC_2D_CRS, PJType.GEOGRAPHIC_3D_CRS, PJType.VERTICAL_CRS]
US_TYPES = [
    PJType.PROJECTED_CRS,
    PJType.GEOCENTRIC_CRS,
    PJType.COMPOUND_CRS,
    PJType.GEOGRAPHIC_2D_CRS,
    PJType.GEOGRAPHIC_3D_CRS,
    PJType.VERTICAL_CRS,
]
#: PROJ's CRS type name -> the type pyDIGGS records.
TYPES = {
    "Geographic 2D CRS": "geographic_2d",
    "Geographic 3D CRS": "geographic_3d",
    "Geocentric CRS": "geocentric",
    "Projected CRS": "projected",
    "Vertical CRS": "vertical",
    "Compound CRS": "compound",
}
#: One subset entry: name, type, axis order, unit, datum, component codes.
Entry = tuple[str, str, str, str | None, str | None, list[int]]
#: Axis direction -> how pyDIGGS names it; geographic axes are latitude and longitude.
GEOGRAPHIC_AXES = {"north": "lat", "south": "lat", "east": "lon", "west": "lon", "up": "h"}


def corpus_codes() -> set[int]:
    """Every EPSG code an ``srsName`` in the conformance corpus uses."""
    from lxml import etree  # noqa: PLC0415 - only this script needs it

    from pydiggs._geo_checks import parse_srs_name  # noqa: PLC0415

    files = [
        *(ROOT / "tests" / "fixtures" / "official").rglob("*.xml"),
        *(ROOT / ".cache" / "conformance").rglob("*.xml"),
    ]
    codes: set[int] = set()
    for path in files:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
        for el in etree.parse(str(path), parser).iter(etree.Element):
            srs = el.get("srsName")
            if not srs:
                continue
            ref = parse_srs_name(srs)
            codes |= {c for c in (ref.horizontal, ref.vertical) if c is not None}
    return codes


def selected() -> set[int]:
    """The EPSG codes the subset holds (see the module docstring)."""
    codes: set[int] = set()
    for info in query_crs_info(auth_name="EPSG", pj_types=EVERYWHERE, allow_deprecated=False):
        codes.add(int(info.code))
    for box in US_AREAS.values():
        area = AreaOfInterest(*box)
        for info in query_crs_info(
            auth_name="EPSG", pj_types=US_TYPES, area_of_interest=area, allow_deprecated=False
        ):
            codes.add(int(info.code))
    for info in query_crs_info(
        auth_name="EPSG", pj_types=[PJType.PROJECTED_CRS], allow_deprecated=False
    ):
        if info.name.startswith(("WGS 84 / UTM zone", "ETRS89 / UTM zone")):
            codes.add(int(info.code))
    return codes | corpus_codes()


def facts(crs: CRS) -> Entry:
    """The facts pyDIGGS keeps about one CRS."""
    kind = TYPES.get(crs.type_name, crs.type_name.lower())
    # a compound CRS on a geographic one names its axes like that one: lat,lon,h
    horizontal = crs.sub_crs_list[0] if kind == "compound" and crs.sub_crs_list else crs
    geographic = TYPES.get(horizontal.type_name, "").startswith("geographic")
    axes = [
        GEOGRAPHIC_AXES.get(a.direction, a.direction) if geographic else a.direction
        for a in crs.axis_info
    ]
    units = list(dict.fromkeys(a.unit_name for a in crs.axis_info))
    datum = None if crs.datum is None else crs.datum.name
    components = [
        int(code)
        for part in (crs.sub_crs_list if kind == "compound" else [])
        if (code := part.to_epsg()) is not None
    ]
    return (
        crs.name,
        kind,
        ",".join(axes),
        units[0] if len(units) == 1 else None,
        datum,
        components,
    )


def main() -> None:
    """Write the subset, one CRS per line."""
    codes = selected()
    table: dict[str, Entry] = {}
    for code in sorted(codes):
        try:
            crs = CRS.from_epsg(code)
        except Exception as exc:
            print(f"skipped EPSG:{code}: {exc}", file=sys.stderr)
            continue
        table[str(code)] = facts(crs)
        for part in table[str(code)][5]:
            if str(part) not in table and part not in codes:
                table[str(part)] = facts(CRS.from_epsg(part))
    source = {
        "dataset": "EPSG Geodetic Parameter Dataset",
        "version": get_database_metadata("EPSG.VERSION"),
        "date": get_database_metadata("EPSG.DATE"),
        "extracted_with": f"PROJ {proj_version_str} (pyproj {__version__})",
        "owner": "International Association of Oil & Gas Producers (IOGP)",
        "terms": "https://epsg.org/terms-of-use.html",
        "notice": (
            "Facts (name, type, axis order, unit, datum, components) extracted"
            " unmodified from the EPSG Dataset, owned by IOGP and used under its terms"
            " of use. The full dataset is at https://epsg.org."
        ),
    }
    document: dict[str, object] = {
        "source": source,
        "fields": ["name", "type", "axis_order", "unit", "datum", "components"],
        "crs": dict(sorted(table.items(), key=lambda kv: int(kv[0]))),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"), indent=None)
    # one CRS per line: small diffs when the EPSG version moves
    text = text.replace('],"', '],\n"')
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"{len(table)} CRSs from EPSG {source['version']} -> {OUT}")


if __name__ == "__main__":
    main()

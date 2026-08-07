"""Additional DIGGSml/validation semantic checks (structure, geometry, href, dataBlock)."""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from pydiggs.dictionary import ValidationMessage


@dataclass
class ContextValidationResult:
    """Outcome of non-dictionary semantic checks."""

    messages: list[ValidationMessage] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(m.severity == "ERROR" for m in self.messages)

    def add(self, severity: str, path: str, text: str, check: int) -> None:
        self.messages.append(
            ValidationMessage(severity=severity, element_path=path, text=text, check=check)
        )


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname


def _path(el: etree._Element) -> str:
    parts: list[str] = []
    node: etree._Element | None = el
    while node is not None and isinstance(node.tag, str):
        name = _local(node)
        prev = [
            s
            for s in node.itersiblings(preceding=True)
            if isinstance(s.tag, str) and _local(s) == name
        ]
        parts.append(f"{name}[{len(prev) + 1}]")
        node = node.getparent()
    return "/" + "/".join(reversed(parts))


def validate_context(instance_path: str) -> ContextValidationResult:
    """Run DIGGS structure, geometry SRS, xlink href, and dataBlock checks."""
    result = ContextValidationResult()
    doc = etree.parse(instance_path)
    root = doc.getroot()

    _check_structure(root, result)
    _check_geometry_srs(root, result)
    _check_hrefs(root, result)
    _check_datablocks(root, result)
    return result


def _check_structure(root: etree._Element, result: ContextValidationResult) -> None:
    if _local(root) != "Diggs":
        result.add(
            "ERROR",
            f"/{_local(root)}[1]",
            "Structure: root element must be Diggs.",
            100,
        )
        return
    # documentInformation property expected as early child (DIGGSml diggs-check)
    doc_info = [c for c in root if isinstance(c.tag, str) and _local(c) == "documentInformation"]
    if not doc_info:
        result.add(
            "ERROR",
            "/Diggs[1]",
            "Structure: Diggs root must contain a documentInformation element.",
            100,
        )
        return
    objects = [
        c for c in doc_info[0] if isinstance(c.tag, str) and _local(c) == "DocumentInformation"
    ]
    if len(objects) != 1:
        result.add(
            "ERROR",
            _path(doc_info[0]),
            "Structure: documentInformation must contain exactly one DocumentInformation object.",
            100,
        )


def _check_geometry_srs(root: etree._Element, result: ContextValidationResult) -> None:
    # Top-level geometry objects that DIGGS requires srsName / srsDimension on.
    geom_names = {
        "Point",
        "LineString",
        "LinearExtent",
        "Polygon",
        "Curve",
        "Surface",
        "MultiPoint",
        "MultiCurve",
        "MultiSurface",
        "PointLocation",
        "MultiPointLocation",
    }
    for el in root.iter():
        if not isinstance(el.tag, str) or _local(el) not in geom_names:
            continue
        # Only require on elements that look like geometry containers with pos/posList
        has_coords = any(
            isinstance(c.tag, str) and _local(c) in {"pos", "posList", "coordinates"}
            for c in el.iter()
            if c is not el
        )
        if not has_coords and el.get("srsName") is None:
            continue
        if not el.get("srsName"):
            result.add(
                "WARNING",
                _path(el),
                f"Geometry: <{_local(el)}> should declare srsName.",
                101,
            )
        if not el.get("srsDimension"):
            result.add(
                "WARNING",
                _path(el),
                f"Geometry: <{_local(el)}> should declare srsDimension.",
                101,
            )


def _check_hrefs(root: etree._Element, result: ContextValidationResult) -> None:
    gml_ids = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        gid = el.get("{http://www.opengis.net/gml/3.2}id") or el.get("id")
        if gid:
            gml_ids.add(gid)

    xlink = "{http://www.w3.org/1999/xlink}href"
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        href = el.get(xlink) or el.get("href")
        if not href or not href.startswith("#"):
            continue
        target = href[1:]
        if target not in gml_ids:
            result.add(
                "WARNING",
                _path(el),
                f'xlink:href "{href}" does not resolve to a gml:id in this instance.',
                102,
            )


def _check_datablocks(root: etree._Element, result: ContextValidationResult) -> None:
    """Ensure dataBlock tuple arity matches Property count when both are present."""
    for measurement in root.xpath("//*[local-name()='measurement']"):
        prop_nodes = measurement.xpath(
            ".//*[local-name()='result']//*[local-name()='Property']"
            " | .//*[local-name()='Result']//*[local-name()='Property']"
        )
        blocks = measurement.xpath(".//*[local-name()='dataBlock']")
        if not prop_nodes or not blocks:
            continue
        n_props = len(prop_nodes)
        for block in blocks:
            text = (block.text or "").strip()
            if not text:
                continue
            tuples = text.split()
            for i, tup in enumerate(tuples, start=1):
                values = [v for v in tup.split(",") if v != ""]
                if len(values) != n_props:
                    result.add(
                        "ERROR",
                        _path(block),
                        (
                            f"dataBlock tuple {i} has {len(values)} value(s) but "
                            f"{n_props} Property declaration(s) were found."
                        ),
                        103,
                    )
                    break

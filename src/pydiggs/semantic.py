"""Additional DIGGSml/validation semantic checks (structure, geometry, href, dataBlock)."""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from pydiggs import _xml
from pydiggs.dictionary import ValidationMessage, _element_path, _sibling_positions, _worse


@dataclass
class ContextValidationResult:
    """Outcome of non-dictionary semantic checks."""

    messages: list[ValidationMessage] = field(default_factory=list)
    #: Keep at most this many messages (None: all). The rest are only counted in
    #: ``dropped`` as {check: (count, worst severity)}; validate() sets it.
    limit: int | None = None
    dropped: dict[int, tuple[int, str]] = field(default_factory=dict)
    #: False in validate(): the Locator gives locations, so no legacy path is built.
    legacy_paths: bool = True
    #: Context check numbers to record (None: all); validate() leaves out unrequested steps.
    checks: frozenset[int] | None = None
    _positions: dict[etree._Element, int] | None = field(
        default=None, init=False, repr=False, compare=False
    )
    #: validate(): why a check stopped early (its results are partial).
    limits_hit: list[str] = field(default_factory=list, compare=False)

    def path(self, el: etree._Element) -> str:
        """The legacy ``/name[n]/...`` path of ``el`` ("" when not needed).

        Sibling positions are computed once per document (they were recounted for
        every message, which was quadratic in the number of siblings).
        """
        if not self.legacy_paths or (self.limit is not None and len(self.messages) >= self.limit):
            return ""
        if self._positions is None:
            self._positions = _sibling_positions(el.getroottree().getroot())
        return _element_path(el, self._positions)

    @property
    def ok(self) -> bool:
        return not any(m.severity == "ERROR" for m in self.messages) and not any(
            severity == "ERROR" for _, severity in self.dropped.values()
        )

    def add(
        self,
        severity: str,
        path: str,
        text: str,
        check: int,
        node: etree._Element | None = None,
    ) -> None:
        if self.checks is not None and check not in self.checks:
            return
        if self.limit is not None and len(self.messages) >= self.limit:
            count, worst = self.dropped.get(check, (0, severity))
            self.dropped[check] = (count + 1, _worse(worst, severity))
            return
        self.messages.append(
            ValidationMessage(
                severity=severity, element_path=path, text=text, check=check, node=node
            )
        )


def _local(el: etree._Element) -> str:
    return str(etree.QName(el).localname)


def validate_context(instance_path: str) -> ContextValidationResult:
    """Run DIGGS structure, geometry SRS, xlink href, and dataBlock checks."""
    return validate_context_tree(_xml.parse(instance_path).getroot())


def validate_context_tree(
    root: etree._Element,
    *,
    max_messages: int | None = None,
    legacy_paths: bool = True,
    checks: frozenset[int] | None = None,
) -> ContextValidationResult:
    """Run the context checks on an already-parsed document root.

    With ``max_messages``, messages past that number are only counted. With
    ``legacy_paths=False`` (validate()), no legacy element path is built. ``checks``
    limits the messages recorded (and counted) to those check numbers.
    """
    result = ContextValidationResult(limit=max_messages, legacy_paths=legacy_paths, checks=checks)
    _check_structure(root, result)
    _check_geometry_srs(root, result)
    _check_hrefs(root, result)
    if legacy_paths:
        _check_datablocks(root, result)
    else:
        _check_datablocks_linear(root, result)
    result._positions = None  # do not keep a map of the whole tree in the result
    return result


def _check_structure(root: etree._Element, result: ContextValidationResult) -> None:
    if _local(root) != "Diggs":
        result.add(
            "ERROR",
            f"/{_local(root)}[1]",
            "Structure: root element must be Diggs.",
            100,
            node=root,
        )
        return
    # documentInformation property expected as early child (DIGGSml diggs-check)
    # Tag filters ("{*}name") match in C: no element's {namespace}name string is built
    # (a long namespace URI made each of those strings huge).
    doc_info = list(root.iterchildren("{*}documentInformation"))
    if not doc_info:
        result.add(
            "ERROR",
            "/Diggs[1]",
            "Structure: Diggs root must contain a documentInformation element.",
            100,
            node=root,
        )
        return
    objects = list(doc_info[0].iterchildren("{*}DocumentInformation"))
    if len(objects) != 1:
        result.add(
            "ERROR",
            result.path(doc_info[0]),
            "Structure: documentInformation must contain exactly one DocumentInformation object.",
            100,
            node=doc_info[0],
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
    for el in root.iter(*(f"{{*}}{name}" for name in sorted(geom_names))):
        # Only require on elements that look like geometry containers with pos/posList
        has_coords = any(c is not el for c in el.iter("{*}pos", "{*}posList", "{*}coordinates"))
        if not has_coords and el.get("srsName") is None:
            continue
        if not el.get("srsName"):
            result.add(
                "WARNING",
                result.path(el),
                f"Geometry: <{_local(el)}> should declare srsName.",
                101,
                node=el,
            )
        if not el.get("srsDimension"):
            result.add(
                "WARNING",
                result.path(el),
                f"Geometry: <{_local(el)}> should declare srsDimension.",
                101,
                node=el,
            )


def _check_hrefs(root: etree._Element, result: ContextValidationResult) -> None:
    gml_ids = set()
    for el in root.iter(etree.Element):  # elements only, without building their tags
        gid = el.get("{http://www.opengis.net/gml/3.2}id") or el.get("id")
        if gid:
            gml_ids.add(gid)

    xlink = "{http://www.w3.org/1999/xlink}href"
    for el in root.iter(etree.Element):
        href = el.get(xlink) or el.get("href")
        if not href or not href.startswith("#"):
            continue
        target = href[1:]
        if target not in gml_ids:
            result.add(
                "WARNING",
                result.path(el),
                f'xlink:href "{href}" does not resolve to a gml:id in this instance.',
                102,
                node=el,
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
                        result.path(block),
                        (
                            f"dataBlock tuple {i} has {len(values)} value(s) but "
                            f"{n_props} Property declaration(s) were found."
                        ),
                        103,
                        node=block,
                    )
                    break


#: validate(): most (dataBlock, enclosing measurement) pairs examined per document.
MAX_DATABLOCK_PAIRS = 5_000_000


def _tuple_lengths(block: etree._Element) -> list[int]:
    text = (block.text or "").strip()
    return [len([v for v in tup.split(",") if v != ""]) for tup in text.split()] if text else []


def _check_datablocks_linear(root: etree._Element, result: ContextValidationResult) -> None:
    """:func:`_check_datablocks` in linear time (validate(); same messages, same order).

    One pre-order walk finds, for every ``Property``, its deepest ``result``/``Result``
    ancestor, and lists every ``dataBlock`` under each enclosing ``measurement``; one
    post-order sum then gives each measurement the number of Properties below a result
    inside it (what the legacy XPath counted, which rescanned every nested
    measurement's subtree). The pairs (block, enclosing measurement) are bounded by
    MAX_DATABLOCK_PAIRS; past it the check stops and says so.
    """
    walk = _DataBlockWalk(root)
    if walk.over_limit:
        result.limits_hit.append(
            "dataBlocks inside nested measurements needed more than "
            f"{MAX_DATABLOCK_PAIRS:,} comparisons"
        )
        return
    lengths: dict[etree._Element, list[int]] = {}
    for measurement in walk.measurements:  # document order, as //measurement
        n_props = walk.properties_below(measurement)
        if not n_props or not walk.blocks[measurement]:
            continue
        for block in walk.blocks[measurement]:
            counts = lengths.get(block)
            if counts is None:
                counts = lengths[block] = _tuple_lengths(block)
            bad = next(((i, c) for i, c in enumerate(counts, start=1) if c != n_props), None)
            if bad is not None:
                result.add(
                    "ERROR",
                    result.path(block),
                    (
                        f"dataBlock tuple {bad[0]} has {bad[1]} value(s) but "
                        f"{n_props} Property declaration(s) were found."
                    ),
                    103,
                    node=block,
                )


class _DataBlockWalk:
    """The measurements, their dataBlocks and Property counts of one document.

    Memory grows with the number of measurements and (dataBlock, measurement) pairs,
    not with the size of the document.
    """

    def __init__(self, root: etree._Element) -> None:
        self.measurements: list[etree._Element] = []
        self.blocks: dict[etree._Element, list[etree._Element]] = {}
        self.over_limit = False
        own: dict[etree._Element, int] = {}  # Properties whose deepest result it encloses
        parent: dict[etree._Element, etree._Element | None] = {}
        pairs = 0
        # (element, enclosing measurements, innermost measurement around its deepest
        # result ancestor: the measurement a Property there is counted for)
        stack: list[tuple[etree._Element, tuple[etree._Element, ...], etree._Element | None]]
        stack = [(root, (), None)]
        while stack:
            el, inside, counted = stack.pop()
            local = _local(el)
            if local == "Property" and counted is not None:
                own[counted] += 1
            if local == "dataBlock" and inside:
                pairs += len(inside)
                if pairs > MAX_DATABLOCK_PAIRS:
                    self.over_limit = True
                    return
                for measurement in inside:
                    self.blocks[measurement].append(el)
            if local == "measurement":
                self.measurements.append(el)
                self.blocks[el] = []
                own[el] = 0
                parent[el] = inside[-1] if inside else None
                inside = (*inside, el)
            elif local in ("result", "Result"):
                # A Property below this result counts for every measurement around it.
                counted = inside[-1] if inside else None
            children = list(el.iterchildren(etree.Element))
            stack.extend((child, inside, counted) for child in reversed(children))
        self._totals: dict[etree._Element, int] = dict(own)
        for measurement in reversed(self.measurements):  # inner measurements first
            up = parent[measurement]
            if up is not None:
                self._totals[up] += self._totals[measurement]

    def properties_below(self, measurement: etree._Element) -> int:
        """Properties below a result strictly inside ``measurement``."""
        return self._totals[measurement]

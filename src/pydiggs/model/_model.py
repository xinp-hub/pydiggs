"""The :class:`Model` root: navigation, integrity checks and the JSON codec.

JSON is the dataclasses' fields in declaration order, written with ``ensure_ascii=False``
and ``allow_nan=False``, so one model always serialises to the same bytes.
:meth:`Model.from_dict` decodes by the classes' own type hints: there is one codec, and
the reader builds every model through it.
"""

from __future__ import annotations

import json
import types
from collections.abc import Callable, Iterator, Mapping
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from functools import cache
from importlib import resources
from typing import IO, Any, Final, Literal, Union, get_args, get_origin, get_type_hints

from pydiggs.findings._json import finding_entry
from pydiggs.findings.model import Finding
from pydiggs.model._nodes import (
    Associate,
    DocumentInfo,
    Feature,
    LithologyLayer,
    Measurement,
    Node,
    Observation,
    Other,
    Project,
    Sample,
    SamplingActivity,
)
from pydiggs.model._values import CrsRef, Link, SourceInfo

__all__ = ["MODEL_SCHEMA_URL", "MODEL_SCHEMA_VERSION", "Model", "model_schema"]

#: Version of the diggs-model schema this release writes (a beta until 1.7.0 freezes 1.0).
MODEL_SCHEMA_VERSION: Final = "1.0-beta.1"
#: ``$id`` of that schema, published on the documentation site.
MODEL_SCHEMA_URL: Final = (
    "https://xinp-hub.github.io/pydiggs/schemas/preview/diggs-model-1.0-beta.1.schema.json"
)
_SCHEMA_RESOURCE: Final = "diggs-model-1.0-beta.1.schema.json"

Direction = Literal["out", "in", "both"]

#: The node collections of a model, in JSON order.
_COLLECTIONS: Final = (
    "projects",
    "associates",
    "features",
    "sampling_activities",
    "samples",
    "observations",
    "measurements",
    "others",
)


def model_schema() -> dict[str, Any]:
    """The packaged diggs-model JSON Schema, as a fresh dict."""
    text = resources.files("pydiggs.model").joinpath(_SCHEMA_RESOURCE).read_text("utf-8")
    return dict(json.loads(text))


@dataclass(frozen=True, slots=True, kw_only=True)
class Model:
    """Everything one DIGGS file describes, as a graph of nodes and links.

    Read one with :func:`pydiggs.preview.read`. Nodes are grouped by what they are
    (``features``, ``samples``, ``measurements``, …), each tuple in document order;
    ``links`` holds every reference between them and ``notes`` what reading had to work
    around (findings, not a validity verdict).
    """

    schema_version: str
    tool: Mapping[str, str]
    source: SourceInfo
    complete: bool
    limits_hit: tuple[str, ...] = ()
    document: DocumentInfo | None = None
    projects: tuple[Project, ...] = ()
    associates: tuple[Associate, ...] = ()
    features: tuple[Feature, ...] = ()
    sampling_activities: tuple[SamplingActivity, ...] = ()
    samples: tuple[Sample, ...] = ()
    observations: tuple[Observation, ...] = ()
    measurements: tuple[Measurement, ...] = ()
    others: tuple[Other, ...] = ()
    crs: Mapping[str, CrsRef] = field(default_factory=lambda: types.MappingProxyType({}))
    links: tuple[Link, ...] = ()
    notes: tuple[Finding, ...] = ()
    _index: Mapping[str, Node] = field(
        init=False, repr=False, compare=False, default_factory=lambda: types.MappingProxyType({})
    )
    _by_xpath: Mapping[str, Node] = field(
        init=False, repr=False, compare=False, default_factory=lambda: types.MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool", types.MappingProxyType(dict(self.tool)))
        object.__setattr__(self, "crs", types.MappingProxyType(dict(self.crs)))
        index: dict[str, Node] = {}
        by_xpath: dict[str, Node] = {}
        for node in self.nodes():
            if node.key in index:
                raise ValueError(f"duplicate node key {node.key!r}")
            index[node.key] = node
            by_xpath.setdefault(node.provenance.xpath, node)
        for link in self.links:
            if link.source not in index:
                raise ValueError(f"link source {link.source!r} is not a node")
            if link.target is not None and link.target not in index:
                raise ValueError(f"link target {link.target!r} is not a node")
        object.__setattr__(self, "_index", types.MappingProxyType(index))
        object.__setattr__(self, "_by_xpath", types.MappingProxyType(by_xpath))

    # --- navigation ---------------------------------------------------------------

    def nodes(self) -> Iterator[Node]:
        """Every node, the document first, then each collection in order (layers last)."""
        if self.document is not None:
            yield self.document
        for name in _COLLECTIONS:
            yield from getattr(self, name)
        for observation in self.observations:
            yield from observation.layers

    def get(self, key: str) -> Node | None:
        """The node with this key, or None."""
        return self._index.get(key)

    def related(
        self,
        node: Node | str,
        *,
        relation: str | None = None,
        direction: Direction = "out",
    ) -> tuple[Node, ...]:
        """Nodes linked to ``node``, in link order, each once.

        ``direction`` picks those it refers to (``out``), those that refer to it
        (``in``), or both; ``relation`` keeps only links of that relation.
        """
        key = node if isinstance(node, str) else node.key
        found: list[Node] = []
        seen: set[str] = set()
        for link in self.links:
            if relation is not None and link.relation != relation:
                continue
            other = None
            if direction in ("out", "both") and link.source == key:
                other = link.target
            if other is None and direction in ("in", "both") and link.target == key:
                other = link.source
            if other is not None and other not in seen:
                seen.add(other)
                found.append(self._index[other])
        return tuple(found)

    def owner_of(self, finding: Finding) -> Node | None:
        """The deepest node that contains the element a finding points at, or None."""
        xpath = finding.xpath
        while xpath:
            node = self._by_xpath.get(xpath)
            if node is not None:
                return node
            xpath = xpath.rpartition("/")[0]
        return None

    # --- JSON ---------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The model as a diggs-model document (plain dicts and lists)."""
        body = _encode(self)
        return {"$schema": MODEL_SCHEMA_URL, **body}

    def to_json(self, indent: int | None = None) -> str:
        """The model as diggs-model JSON; the same model always gives the same text."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, allow_nan=False)

    def write_json(self, fp: IO[str], indent: int | None = None) -> None:
        """Write :meth:`to_json` to a text stream."""
        fp.write(self.to_json(indent=indent))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Model:
        """Build a model from a diggs-model document; unknown fields are ignored.

        Raises:
            ValueError: the document does not have the model's shape, a node key
                repeats, a link names a node that is not there, or its
                ``schema_version`` has another major version.
        """
        if not isinstance(data, Mapping):
            raise ValueError("a diggs-model document is a JSON object")
        version = data.get("schema_version")
        if not isinstance(version, str) or not version.startswith("1."):
            raise ValueError(f"unsupported diggs-model schema_version {version!r}")
        model = _decode(cls, data, "$")
        if not isinstance(model, Model):  # pragma: no cover - _decode returns cls
            raise TypeError("decoder returned the wrong type")
        return model

    @classmethod
    def from_json(cls, text: str | bytes) -> Model:
        """Build a model from diggs-model JSON (see :meth:`from_dict`)."""
        return cls.from_dict(json.loads(text))


# --- codec --------------------------------------------------------------------------


def _encode(value: Any) -> Any:
    if isinstance(value, Finding):
        return finding_entry(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _encode(getattr(value, f.name))
            for f in fields(value)
            if not f.name.startswith("_")
        }
    if isinstance(value, tuple | list):
        return [_encode(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    return value


def _finding(data: Any, where: str) -> Finding:
    if not isinstance(data, Mapping):
        raise ValueError(f"{where}: a finding is an object")
    location = data.get("location") or {}
    return Finding(
        layer=data.get("layer", "native"),
        step=data["step"],
        rule_id=data["rule_id"],
        severity=data["severity"],
        message=data["message"],
        line=location.get("line"),
        column=location.get("column"),
        xpath=location.get("xpath"),
        gml_id=location.get("gml_id"),
    )


Decoder = Callable[[Any, str], Any]
#: A ``Mapping[K, V]`` has two type arguments.
_KEY_AND_VALUE: Final = 2


def _decode(tp: Any, value: Any, where: str) -> Any:
    """Decode ``value`` as type ``tp`` (``where`` names the place in error messages)."""
    return _compile(tp)(value, where)


def _check(ok: bool, where: str, expected: str) -> None:
    if not ok:
        raise ValueError(f"{where}: expected {expected}")


def _primitive(tp: Any) -> Decoder | None:  # noqa: PLR0911 - one return per primitive
    if tp is Any or tp is object:
        return lambda value, _where: value
    if tp is bool:

        def boolean(value: Any, where: str) -> Any:
            _check(isinstance(value, bool), where, "a boolean")
            return value

        return boolean
    if tp is int:

        def integer(value: Any, where: str) -> Any:
            _check(type(value) is int, where, "an integer")
            return value

        return integer
    if tp is float:

        def number(value: Any, where: str) -> Any:
            _check(type(value) is float or type(value) is int, where, "a number")
            return float(value)

        return number
    if tp is str:

        def string(value: Any, where: str) -> Any:
            _check(isinstance(value, str), where, "a string")
            return value

        return string
    if tp is Finding:
        return _finding
    return None


def _union(tp: Any) -> Decoder:
    options = [o for o in get_args(tp) if o is not type(None)]
    nullable = len(options) < len(get_args(tp))
    decoders = [_compile(o) for o in options]
    single = decoders[0] if len(decoders) == 1 else None

    def union(value: Any, where: str) -> Any:
        if value is None:
            _check(nullable, where, "a value, not null")
            return None
        if single is not None:
            return single(value, where)
        for decoder in decoders:
            try:
                return decoder(value, where)
            except (TypeError, ValueError, KeyError):
                continue
        raise ValueError(f"{where}: {value!r} is not a {tp}")

    return union


def _sequence(tp: Any) -> Decoder:
    item = _compile(get_args(tp)[0])

    def sequence(value: Any, where: str) -> Any:
        _check(isinstance(value, list | tuple), where, "an array")
        return tuple([item(v, where) for v in value])

    return sequence


def _mapping(tp: Any) -> Decoder:
    args = get_args(tp)
    item = _compile(args[1] if len(args) == _KEY_AND_VALUE else Any)

    def mapping(value: Any, where: str) -> Any:
        _check(isinstance(value, Mapping), where, "an object")
        return types.MappingProxyType({str(k): item(v, where) for k, v in value.items()})

    return mapping


def _record(tp: type) -> Decoder:
    """A dataclass decoder; field decoders are compiled on first use (types recurse)."""
    plan: list[tuple[str, Any, bool]] = []
    compiled: dict[str, Decoder] = {}

    def record(value: Any, where: str) -> Any:
        if not plan:
            hints = get_type_hints(tp)
            plan.extend(
                (f.name, hints[f.name], f.default is MISSING and f.default_factory is MISSING)
                for f in fields(tp)
                if f.init
            )
        _check(isinstance(value, Mapping), where, f"an object for {tp.__name__}")
        kwargs: dict[str, Any] = {}
        for name, hint, required in plan:
            if name in value:
                decoder = compiled.get(name)
                if decoder is None:
                    decoder = compiled[name] = _compile(hint)
                kwargs[name] = decoder(value[name], f"{where}.{name}")
            elif required:
                raise ValueError(f"{where}: missing {name!r}")
        return tp(**kwargs)

    return record


@cache
def _compile(tp: Any) -> Decoder:
    """One decoder per type, built once from the type's hints."""
    primitive = _primitive(tp)
    if primitive is not None:
        return primitive
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        return _union(tp)
    if origin is tuple:
        return _sequence(tp)
    if origin is Mapping or tp is Mapping or origin is dict:
        return _mapping(tp)
    if isinstance(tp, type) and is_dataclass(tp):
        return _record(tp)
    raise TypeError(f"no decoder for {tp!r}")  # pragma: no cover - a model bug


#: Every node class, by collection, for the reader and the schema reflection test.
NODE_CLASSES: Final[Mapping[str, type[Node]]] = types.MappingProxyType(
    {
        "document": DocumentInfo,
        "projects": Project,
        "associates": Associate,
        "features": Feature,
        "sampling_activities": SamplingActivity,
        "samples": Sample,
        "observations": Observation,
        "measurements": Measurement,
        "others": Other,
        "layers": LithologyLayer,
    }
)

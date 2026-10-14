"""Hardened XML parsing: entity, DTD and network attacks must not reach libxml2."""

import io
from pathlib import Path

import pytest
from lxml import etree

import pydiggs
from pydiggs import _xml
from pydiggs.dictionary import DictionarySemanticValidator
from pydiggs.semantic import validate_context

_SRC = Path(pydiggs.__file__).resolve().parent
_CANARY = "CANARY-7f3a"


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "secret.txt"
    path.write_text(_CANARY, encoding="utf-8")
    return path


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_external_general_entity_is_not_expanded(tmp_path: Path, secret_file: Path) -> None:
    doc = _write(
        tmp_path,
        "xxe.xml",
        f'<!DOCTYPE r [<!ENTITY x SYSTEM "{secret_file.as_uri()}">]><r>&x;</r>',
    )
    assert _CANARY not in etree.tostring(_xml.parse(doc), encoding="unicode")


def test_external_parameter_entity_is_not_loaded(tmp_path: Path, secret_file: Path) -> None:
    # lxml < 6.1.3 loaded external parameter entities by default (LP#2165901).
    evil = _write(
        tmp_path,
        "evil.dtd",
        f'<!ENTITY leak SYSTEM "{secret_file.as_uri()}">',
    )
    doc = _write(
        tmp_path,
        "pe.xml",
        f'<!DOCTYPE r [<!ENTITY % p SYSTEM "{evil.as_uri()}"> %p;]><r>&leak;</r>',
    )
    try:
        tree = _xml.parse(doc)
    except etree.XMLSyntaxError:
        return  # refusing the document outright is also safe
    assert _CANARY not in etree.tostring(tree, encoding="unicode")


def test_external_dtd_is_not_loaded(tmp_path: Path) -> None:
    dtd = _write(tmp_path, "ext.dtd", '<!ATTLIST r injected CDATA "yes">')
    doc = _write(tmp_path, "dtd.xml", f'<!DOCTYPE r SYSTEM "{dtd.as_uri()}"><r/>')
    assert _xml.parse(doc).getroot().get("injected") is None


def test_entity_expansion_bomb_is_not_expanded(tmp_path: Path) -> None:
    entities = '<!ENTITY a0 "xxxxxxxxxx">' + "".join(
        f'<!ENTITY a{i} "{f"&a{i - 1};" * 10}">' for i in range(1, 10)
    )
    doc = _write(tmp_path, "bomb.xml", f"<!DOCTYPE r [{entities}]><r>&a9;</r>")
    try:
        tree = _xml.parse(doc)
    except etree.XMLSyntaxError:
        return
    assert len(etree.tostring(tree)) < 10_000


def test_checks_do_not_leak_external_entities(tmp_path: Path, secret_file: Path) -> None:
    doc = _write(
        tmp_path,
        "xxe_diggs.xml",
        f'<!DOCTYPE Diggs [<!ENTITY x SYSTEM "{secret_file.as_uri()}">]>'
        '<Diggs xmlns="http://diggsml.org/schemas/2.6" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="d">'
        '<gml:name codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml">&x;</gml:name>'
        "</Diggs>",
    )
    dictionary = DictionarySemanticValidator().validate_instance(doc)
    context = validate_context(str(doc))
    messages = [m.text for m in dictionary.messages + context.messages]
    assert not any(_CANARY in text for text in messages)


def test_fetch_refuses_non_http_schemes(secret_file: Path) -> None:
    with pytest.raises(OSError, match="non-HTTP"):
        _xml.fetch_and_parse(secret_file.as_uri())


def test_fetch_caps_response_size(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b"<r>" + b"x" * (_xml.MAX_REMOTE_BYTES + 1) + b"</r>"
    monkeypatch.setattr(_xml.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    with pytest.raises(OSError, match="exceeds"):
        _xml.fetch_and_parse("https://diggsml.org/def/codes/huge.xml")


def test_fetch_parses_with_hardened_parser(
    monkeypatch: pytest.MonkeyPatch, secret_file: Path
) -> None:
    body = f'<!DOCTYPE r [<!ENTITY x SYSTEM "{secret_file.as_uri()}">]><r>&x;</r>'.encode()
    monkeypatch.setattr(_xml.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    tree = _xml.fetch_and_parse("https://diggsml.org/def/codes/x.xml")
    assert _CANARY not in etree.tostring(tree, encoding="unicode")


def test_no_module_bypasses_the_hardened_parser() -> None:
    offenders = [
        path.name
        for path in _SRC.glob("*.py")
        if path.name != "_xml.py"
        and any(
            call in path.read_text(encoding="utf-8")
            for call in ("etree.parse(", "etree.fromstring(", "etree.XML(", "etree.iterparse(")
        )
    ]
    assert offenders == []

"""Hardened XML parsing: entity, DTD and network attacks must not reach libxml2."""

import ast
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from lxml import etree

import pydiggs
from pydiggs import _xml, validator
from pydiggs.dictionary import DictionarySemanticValidator
from pydiggs.semantic import validate_context

_SRC = Path(pydiggs.__file__).resolve().parent
_CANARY = "CANARY-7f3a"
_VALID_2_6 = Path("tests/test_files/2.6/No_Error_2_6.xml")
_DIGGS_HEAD = (
    '<Diggs xmlns="http://diggsml.org/schemas/2.6" '
    'xmlns:gml="http://www.opengis.net/gml/3.2" gml:id="d">'
)


@pytest.fixture
def secret_file(tmp_path: Path) -> Path:
    path = tmp_path / "secret.txt"
    path.write_text(_CANARY, encoding="utf-8")
    return path


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _parse_or_refuse(path: Path) -> str:
    """Serialised tree, or "" when libxml2 refuses the document (also safe)."""
    try:
        return etree.tostring(_xml.parse(path), encoding="unicode")
    except etree.XMLSyntaxError:
        return ""


# --- parser hardening -------------------------------------------------------


def test_external_general_entity_is_not_expanded(tmp_path: Path, secret_file: Path) -> None:
    doc = _write(
        tmp_path,
        "xxe.xml",
        f'<!DOCTYPE r [<!ENTITY x SYSTEM "{secret_file.as_uri()}">]><r>&x;</r>',
    )
    assert _CANARY not in _parse_or_refuse(doc)


def test_external_parameter_entity_is_not_loaded(tmp_path: Path, secret_file: Path) -> None:
    # lxml < 6.1.3 loaded external parameter entities by default (LP#2165901).
    evil = _write(tmp_path, "evil.dtd", f'<!ENTITY leak SYSTEM "{secret_file.as_uri()}">')
    doc = _write(
        tmp_path,
        "pe.xml",
        f'<!DOCTYPE r [<!ENTITY % p SYSTEM "{evil.as_uri()}"> %p;]><r>&leak;</r>',
    )
    assert _CANARY not in _parse_or_refuse(doc)


def test_external_dtd_is_not_loaded(tmp_path: Path) -> None:
    dtd = _write(tmp_path, "ext.dtd", '<!ATTLIST r injected CDATA "yes">')
    doc = _write(tmp_path, "dtd.xml", f'<!DOCTYPE r SYSTEM "{dtd.as_uri()}"><r/>')
    assert _xml.parse(doc).getroot().get("injected") is None


def test_entity_expansion_bomb_is_refused(tmp_path: Path) -> None:
    entities = '<!ENTITY a0 "xxxxxxxxxx">' + "".join(
        f'<!ENTITY a{i} "{f"&a{i - 1};" * 10}">' for i in range(1, 10)
    )
    doc = _write(tmp_path, "bomb.xml", f"<!DOCTYPE r [{entities}]><r>&a9;</r>")
    assert len(_parse_or_refuse(doc)) < 10_000


def test_internal_entities_still_expand(tmp_path: Path) -> None:
    doc = _write(tmp_path, "internal.xml", '<!DOCTYPE r [<!ENTITY org "ACME">]><r>&org;</r>')
    assert _xml.parse(doc).getroot().text == "ACME"


def test_parse_accepts_binary_file_objects() -> None:
    with _VALID_2_6.open("rb") as fh:
        assert _xml.parse(fh).getroot() is not None


# --- the checks on hostile and entity-bearing files -------------------------


def test_schema_check_accepts_internal_entities(tmp_path: Path) -> None:
    text = _VALID_2_6.read_text(encoding="utf-8")
    start = text.index("<Diggs")
    doc = _write(
        tmp_path,
        "entity.xml",
        text[:start] + '<!DOCTYPE Diggs [<!ENTITY org "ACME">]>' + text[start:],
    )
    assert validator(str(doc), output_log=False).schema_check() is True


@pytest.mark.parametrize("check", ["schema_check", "schematron_check"])
def test_checks_return_false_on_xxe(tmp_path: Path, secret_file: Path, check: str) -> None:
    doc = _write(
        tmp_path,
        "xxe_diggs.xml",
        f'<!DOCTYPE Diggs [<!ENTITY x SYSTEM "{secret_file.as_uri()}">]>'
        f"{_DIGGS_HEAD}<gml:name>&x;</gml:name></Diggs>",
    )
    assert getattr(validator(str(doc), output_log=False), check)() is False


def test_validator_accepts_file_objects() -> None:
    with _VALID_2_6.open("rb") as fh:
        assert validator(fh, output_log=False).schema_check() is True


def test_dictionary_and_context_do_not_leak(tmp_path: Path, secret_file: Path) -> None:
    doc = _write(
        tmp_path,
        "leak.xml",
        f'<!DOCTYPE Diggs [<!ENTITY x SYSTEM "{secret_file.as_uri()}">]>{_DIGGS_HEAD}'
        '<gml:name codeSpace="https://diggsml.org/def/codes/DIGGS/0.1/properties.xml">'
        "&x;</gml:name></Diggs>",
    )
    try:
        dictionary = DictionarySemanticValidator().validate_instance(doc)
        context = validate_context(str(doc))
    except etree.XMLSyntaxError:
        return
    messages = [m.text for m in dictionary.messages + context.messages]
    assert not any(_CANARY in text for text in messages)


# --- whitelist matching ------------------------------------------------------

_PATTERNS = ("https://diggsml.org", "http://www.opengis.net/def", "./", "file:///")


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://diggsml.org/def/codes/DIGGS/0.1/properties.xml", True),
        ("https://diggsml.org", True),
        ("http://www.opengis.net/def/crs/EPSG/0/4326", True),
        ("https://diggsml.org.127.0.0.1.nip.io/x", False),
        ("https://diggsml.org@127.0.0.1/x", False),
        ("https://user:pw@diggsml.org/x", False),
        ("https://diggsml.org:8443/x", False),
        ("http://diggsml.org/x", False),
        ("http://www.opengis.net/definitely-not", False),
        ("http://diggsml.org:abc/x", False),
        ("file:///etc/passwd", False),
        ("./local.xml", False),
    ],
)
def test_url_allowed(url: str, ok: bool) -> None:
    assert _xml.url_allowed(url, _PATTERNS) is ok


# --- network fetches against a local server ---------------------------------


class _Handler(BaseHTTPRequestHandler):
    hits: ClassVar[list[str]] = []

    def log_message(self, *args: object) -> None:  # keep pytest output clean
        pass

    def do_GET(self) -> None:
        type(self).hits.append(self.path)
        port = self.server.server_address[1]
        if self.path == "/dict/ok.xml":
            self._send(b"<Dictionary><name>ok</name></Dictionary>")
        elif self.path == "/dict/big.xml":
            self._send(b"<r>" + b"x" * 4096 + b"</r>")
        elif self.path == "/dict/hop.xml":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/dict/ok.xml")
            self.end_headers()
        elif self.path == "/dict/out.xml":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/internal")
            self.end_headers()
        elif self.path == "/dict/garbage.xml":
            self.wfile.write(b"garbage\r\n")
        else:
            self._send(b"<internal/>")

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    _Handler.hits = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_fetch_parses_whitelisted_dictionary(server: str) -> None:
    tree = _xml.fetch_and_parse(f"{server}/dict/ok.xml", [f"{server}/dict"])
    assert tree.getroot().tag == "Dictionary"


def test_fetch_refuses_url_outside_whitelist(server: str) -> None:
    with pytest.raises(OSError, match="outside the whitelist"):
        _xml.fetch_and_parse(f"{server}/internal", [f"{server}/dict"])
    assert _Handler.hits == []


def test_fetch_follows_redirects_inside_whitelist(server: str) -> None:
    tree = _xml.fetch_and_parse(f"{server}/dict/hop.xml", [f"{server}/dict"])
    assert tree.getroot().tag == "Dictionary"


def test_fetch_refuses_redirect_outside_whitelist(server: str) -> None:
    with pytest.raises(OSError, match="redirect outside the whitelist"):
        _xml.fetch_and_parse(f"{server}/dict/out.xml", [f"{server}/dict"])
    assert "/internal" not in _Handler.hits


def test_fetch_maps_protocol_errors_to_oserror(server: str) -> None:
    with pytest.raises(OSError):
        _xml.fetch_and_parse(f"{server}/dict/garbage.xml", [f"{server}/dict"])


def test_fetch_caps_response_size(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_xml, "MAX_REMOTE_BYTES", 1024)
    with pytest.raises(OSError, match="exceeds"):
        _xml.fetch_and_parse(f"{server}/dict/big.xml", [f"{server}/dict"])


def test_failed_fetch_is_attempted_once(tmp_path: Path, server: str) -> None:
    whitelist = _write(
        tmp_path,
        "whiteList.xml",
        f"<whiteList><pattern>{server}/dict</pattern></whiteList>",
    )
    names = "".join(
        f'<gml:name codeSpace="{server}/dict/garbage.xml#X{i}">v</gml:name>' for i in range(5)
    )
    doc = _write(tmp_path, "many.xml", f"{_DIGGS_HEAD}{names}</Diggs>")
    semantic = DictionarySemanticValidator(whitelist_path=whitelist, allow_network=True)
    semantic.validate_instance(doc)
    assert _Handler.hits.count("/dict/garbage.xml") == 1


# --- no module may bypass the hardened parser -------------------------------

_PARSE_CALLS = {"parse", "fromstring", "XML", "iterparse", "XMLParser", "XMLID", "parseid"}


def _bypasses(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"lxml.etree", "lxml"}:
            found += [f"import {a.name}" for a in node.names if a.name in _PARSE_CALLS]
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        owner = func.value if isinstance(func, ast.Attribute) else None
        owner_name = getattr(owner, "id", getattr(owner, "attr", ""))
        if name in _PARSE_CALLS and owner_name == "etree":
            found.append(f"etree.{name}()")
        if name in {"XMLSchema", "Schematron", "RelaxNG", "XSLT"} and any(
            kw.arg == "file" for kw in node.keywords
        ):
            found.append(f"{name}(file=...)")
    return found


def test_no_module_bypasses_the_hardened_parser() -> None:
    offenders = {
        str(path.relative_to(_SRC)): hits
        for path in _SRC.rglob("*.py")
        if path.name != "_xml.py"
        and (hits := _bypasses(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert offenders == {}


def test_bypass_guard_catches_known_forms() -> None:
    sample = (
        "from lxml.etree import parse\n"
        "etree.parse('a')\n"
        "lxml.etree.fromstring(b'')\n"
        "etree.XMLSchema(file='s.xsd')\n"
        "isoschematron.Schematron(file='r.sch')\n"
    )
    assert len(_bypasses(ast.parse(sample))) == 5


# --- findings from the security audit ---------------------------------------


def test_gzip_compressed_file_is_refused(tmp_path: Path) -> None:
    import gzip

    bomb = tmp_path / "bomb.xml"
    bomb.write_bytes(gzip.compress(b"<r>" + b"<a/>" * 100_000 + b"</r>"))
    with pytest.raises(OSError, match="compressed"):
        _xml.parse(bomb)
    assert validator(str(bomb), output_log=False).schema_check() is False


def test_file_uri_is_parsed(tmp_path: Path) -> None:
    doc = _write(tmp_path, "u.xml", "<r/>")
    assert _xml.parse(doc.as_uri()).getroot().tag == "r"


def test_unc_dictionary_path_is_never_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    touched: list[str] = []
    monkeypatch.setattr(Path, "is_file", lambda self: touched.append(str(self)) or False)
    semantic = DictionarySemanticValidator()
    assert semantic._resolve_dictionary("file:////attacker/share/d.xml") is None
    assert semantic._resolve_dictionary("//attacker/share/d.xml") is None
    assert not any(t.startswith(("//", "\\\\")) for t in touched)


def test_non_xml_local_dictionary_is_check_3_not_a_crash(tmp_path: Path) -> None:
    junk = _write(tmp_path, "junk.txt", "not xml at all")
    doc = _write(
        tmp_path,
        "local.xml",
        f'{_DIGGS_HEAD}<gml:name codeSpace="{junk.as_uri()}#X">v</gml:name></Diggs>',
    )
    result = DictionarySemanticValidator().validate_instance(doc)
    assert any(m.check == 3 for m in result.messages)


def test_element_paths_match_the_sibling_scan() -> None:
    from pydiggs.dictionary import _element_path, _sibling_positions

    root = _xml.parse(_VALID_2_6).getroot()
    positions = _sibling_positions(root)
    for el in root.iter():
        if isinstance(el.tag, str):
            assert _element_path(el, positions) == _element_path(el)

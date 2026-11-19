"""MkDocs hooks: publish pydiggs's machine-readable documents with the docs site.

- the diggs-findings JSON Schema, at the path its ``$id`` names (the current version,
  and every earlier one, which stays published unchanged);
- the diggs-diff (model diff) JSON Schema, at the path its ``$id`` names;
- the HTTP API's OpenAPI 3.1 document (``pydiggs/server/openapi.json``);
- the stdio sidecar's OpenRPC 1.3 document (``pydiggs/sidecar/openrpc.json``);
- the diggs-model (read API) JSON Schema, at the path its ``$id`` names;
- the diggs-capabilities JSON Schema (``pydiggs.capabilities()``), at the path its
  ``$id`` names.

``docs/history.md`` is the root CHANGELOG.md, embedded here (one history, not two); its
links to ``docs/...`` are rewritten to work from inside ``docs/``.

It writes ``llms.txt`` (an index of every page) and ``llms-full.txt`` (their text) for
language-model readers.

It also renders the email sign-up form on the "Stay updated" page from
``extra.newsletter.buttondown`` in mkdocs.yml, and stamps the version of the checkout being
built into every page (``<meta name="package-version">``, written by
``overrides/main.html``), so that no committed page repeats it and a release bump touches
nothing in ``docs/``. The ``verify`` job of ``.github/workflows/docs.yml`` reads that tag
from the live site after a release.

Each is copied byte for byte from the package at build time, so the published file can
never drift from the one in the wheel (``tests/test_docs_schema.py``).
"""

from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "pydiggs"
SCHEMA = PACKAGE / "findings" / "diggs-findings-1.2.schema.json"
#: Path of the schema under the site root; matches the schema's ``$id``.
PUBLISHED = Path("schemas") / SCHEMA.name
#: Earlier diggs-findings schemas: documents that name them must keep resolving.
EARLIER_SCHEMAS = (
    PACKAGE / "findings" / "diggs-findings-1.0.schema.json",
    PACKAGE / "findings" / "diggs-findings-1.1.schema.json",
)
OPENAPI = PACKAGE / "server" / "openapi.json"
OPENRPC = PACKAGE / "sidecar" / "openrpc.json"
MODEL = PACKAGE / "model" / "diggs-model-1.0.schema.json"
DIFF = PACKAGE / "diff" / "diggs-diff-1.0.schema.json"
CAPABILITIES = PACKAGE / "_capabilities" / "diggs-capabilities-1.0.schema.json"
MAPPING = PACKAGE / "convert" / "convert-mapping-1.schema.json"
#: Every published document: packaged source -> path under the site root.
DOCUMENTS: dict[Path, Path] = {
    SCHEMA: PUBLISHED,
    OPENAPI: Path("schemas") / "openapi.json",
    OPENRPC: Path("schemas") / "openrpc.json",
    MODEL: Path("schemas") / MODEL.name,
    DIFF: Path("schemas") / DIFF.name,
    CAPABILITIES: Path("schemas") / CAPABILITIES.name,
    MAPPING: Path("schemas") / MAPPING.name,
    **{earlier: Path("schemas") / earlier.name for earlier in EARLIER_SCHEMAS},
}


INIT = PACKAGE / "__init__.py"
_VERSION = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)

#: Placeholder in a page that becomes the email sign-up form (docs/stay-updated.md).
NEWSLETTER_MARKER = "<!-- pydiggs:newsletter-form -->"
#: A Buttondown newsletter username: letters, digits, hyphen and underscore.
_NEWSLETTER_USERNAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: Rendered while ``extra.newsletter.buttondown`` is empty in mkdocs.yml.
NEWSLETTER_PENDING = """\
!!! note "Email sign-up is not open yet"
    Until it is, watch releases on GitHub or follow a feed (above): both include every
    feature release.
"""

#: A plain HTML form with no script, so the site still sets no cookies. Subscribing
#: opens Buttondown's page, which sends a confirmation email (double opt-in).
NEWSLETTER_FORM = """\
<form class="pd-subscribe" method="post"
      action="https://buttondown.com/api/emails/embed-subscribe/{username}">
  <label for="pd-subscribe-email">Email address</label>
  <div class="pd-subscribe-row">
    <input type="email" id="pd-subscribe-email" name="email" autocomplete="email"
           required>
    <input type="hidden" name="embed" value="1">
    <button type="submit" class="md-button md-button--primary">Subscribe</button>
  </div>
</form>

Your address is used only for these release emails and is never shared. You confirm the
subscription from the first email, and every email has an unsubscribe link. The list is
run on [Buttondown](https://buttondown.com/legal/privacy){{ rel="noopener" }}, which
stores the address.
"""


def newsletter_block(username: str) -> str:
    """Return the sign-up form for ``username``, or a note when it is empty.

    Raises:
        ValueError: ``username`` is not a valid Buttondown username.
    """
    if not username:
        return NEWSLETTER_PENDING
    if not _NEWSLETTER_USERNAME.fullmatch(username):
        msg = f"extra.newsletter.buttondown is not a Buttondown username: {username!r}"
        raise ValueError(msg)
    return NEWSLETTER_FORM.format(username=username)


def package_version() -> str:
    """Return ``__version__`` as written in this checkout's source, never an installed copy.

    Raises:
        RuntimeError: The source declares no ``__version__``; the build must not go on and
            publish pages that claim no version.
    """
    found = _VERSION.search(INIT.read_text(encoding="utf-8"))
    if not found:
        msg = f"no __version__ in {INIT}"
        raise RuntimeError(msg)
    return found.group(1)


def on_config(config: Any) -> Any:
    """Set ``extra.package_version`` before any page is rendered."""
    config["extra"]["package_version"] = package_version()
    _PAGES.clear()
    _ORDER.clear()
    return config


#: The line of docs/history.md that stands for the root CHANGELOG.md.
CHANGELOG_MARKER = '--8<-- "CHANGELOG.md"'


def changelog_links(markdown: str) -> str:
    """Make the root CHANGELOG.md's links to ``docs/`` pages work from inside ``docs/``."""
    return markdown.replace("](docs/", "](")


def embed_changelog(markdown: str) -> str:
    """Replace the marker line by the root CHANGELOG.md, with its links made relative.

    pymdownx.snippets would embed the file but only after MkDocs has checked the page's
    links, and the changelog's ``docs/...`` links (right on GitHub) would then fail
    ``--strict``. So the page is built here.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return markdown.replace(CHANGELOG_MARKER, changelog_links(changelog))


def on_page_markdown(markdown: str, *, config: Any, page: Any = None, **_: Any) -> str:
    """Fix the embedded changelog's links, and replace the newsletter placeholder."""
    if page is not None and page.file.src_uri == "history.md":
        markdown = embed_changelog(markdown)
    if page is not None:
        # Kept as written, before the sign-up form's HTML goes in: llms-full.txt is text.
        _PAGES[page.file.src_uri] = LlmsPage(str(page.title or ""), page.url, markdown)
    if NEWSLETTER_MARKER not in markdown:
        return markdown
    newsletter = (config.get("extra") or {}).get("newsletter") or {}
    username = str(newsletter.get("buttondown") or "").strip()
    return markdown.replace(NEWSLETTER_MARKER, newsletter_block(username))


# --- llms.txt and llms-full.txt --------------------------------------------------------
#
# Written here, with the standard library, and not by the mkdocs-llmstxt plugin: that
# plugin needs mdformat, which holds markdown-it-py below 4, and markdown-it-py is a
# runtime dependency of pyDIGGS (through rich). A docs plugin must not decide which
# version of a runtime dependency uv.lock, and so the tested constraints file, carries.


class LlmsPage:
    """One page as a language-model reader gets it.

    A plain class, not a dataclass: the tests load this file by path without registering
    it in ``sys.modules``, which ``dataclasses`` needs.
    """

    __slots__ = ("text", "title", "url")

    def __init__(self, title: str, url: str, text: str) -> None:
        self.title = title
        self.url = url
        self.text = text


#: Every page of the build in progress, by source path.
_PAGES: dict[str, LlmsPage] = {}
#: The navigation's page order, by source path.
_ORDER: list[str] = []
#: A page written as directives (``::: pydiggs``) has its content only once rendered.
_DIRECTIVE = re.compile(r"^::: ", re.MULTILINE)
_BLOCKS = frozenset(
    {"p", "div", "section", "pre", "li", "tr", "table", "dt", "dd", "br", "blockquote"}
    | {f"h{level}" for level in range(1, 7)}
)
_SILENT = frozenset({"script", "style", "svg"})


class _Text(HTMLParser):
    """Rendered HTML as plain text: headings marked, one block per line."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._silent = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],  # noqa: ARG002 - HTMLParser's signature
    ) -> None:
        if tag in _SILENT:
            self._silent += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")
            if tag[0] == "h" and tag[1:].isdigit():
                self.parts.append("#" * int(tag[1:]) + " ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SILENT:
            self._silent = max(0, self._silent - 1)
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._silent:
            self.parts.append(data)


def html_text(html: str) -> str:
    """Return the text of rendered HTML, without the permalink marks or blank runs."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = (
        line.rstrip().removesuffix("¶").rstrip() for line in "".join(parser.parts).splitlines()
    )
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def on_nav(nav: Any, **_: Any) -> Any:
    """Remember the order pages have in the navigation."""
    _ORDER[:] = [page.file.src_uri for page in nav.pages]
    return nav


def on_page_content(html: str, *, page: Any, **_: Any) -> str:
    """Take a generated page's text from what was rendered (the API reference)."""
    known = _PAGES.get(page.file.src_uri)
    if known is not None and _DIRECTIVE.search(known.text):
        known.text = html_text(html)
    return html


def llms_documents(config: Any) -> tuple[str, str]:
    """Return ``llms.txt`` (an index of every page) and ``llms-full.txt`` (their text)."""
    site = str(config.get("site_url") or "").rstrip("/")
    ordered = [name for name in _ORDER if name in _PAGES]
    ordered += sorted(set(_PAGES) - set(ordered))
    head = [f"# {config.get('site_name') or 'Documentation'}", ""]
    if config.get("site_description"):
        head += [f"> {config['site_description']}", ""]
    index = [*head, f"Version {package_version()}.", "", "## Documentation", ""]
    full = list(head)
    for name in ordered:
        page = _PAGES[name]
        address = f"{site}/{page.url}"
        index.append(f"- [{page.title}]({address})")
        full += [f"<!-- {address} -->", "", page.text.strip(), "", ""]
    return "\n".join(index) + "\n", "\n".join(full).rstrip() + "\n"


def on_post_build(config: Any) -> None:
    """Copy every document in ``DOCUMENTS`` into ``<site_dir>/schemas/``; write llms.txt."""
    for source, published in DOCUMENTS.items():
        target = Path(config["site_dir"]) / published
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    index, full = llms_documents(config)
    for name, text in (("llms.txt", index), ("llms-full.txt", full)):
        with (Path(config["site_dir"]) / name).open("w", encoding="utf-8", newline="\n") as out:
            out.write(text)

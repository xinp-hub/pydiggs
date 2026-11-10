"""MkDocs hooks: publish pydiggs's machine-readable documents with the docs site.

- the diggs-findings JSON Schema, at the path its ``$id`` names;
- the HTTP API's OpenAPI 3.1 document (``pydiggs/server/openapi.json``);
- the stdio sidecar's OpenRPC 1.3 document (``pydiggs/sidecar/openrpc.json``);
- the diggs-model (read API) JSON Schema, at the path its ``$id`` names.

It also renders the email sign-up form on the "Stay updated" page from
``extra.newsletter.buttondown`` in mkdocs.yml.

Each is copied byte for byte from the package at build time, so the published file can
never drift from the one in the wheel (``tests/test_docs_schema.py``).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "pydiggs"
SCHEMA = PACKAGE / "findings" / "diggs-findings-1.0.schema.json"
#: Path of the schema under the site root; matches the schema's ``$id``.
PUBLISHED = Path("schemas") / SCHEMA.name
OPENAPI = PACKAGE / "server" / "openapi.json"
OPENRPC = PACKAGE / "sidecar" / "openrpc.json"
MODEL = PACKAGE / "model" / "diggs-model-1.0.schema.json"
#: Every published document: packaged source -> path under the site root.
DOCUMENTS: dict[Path, Path] = {
    SCHEMA: PUBLISHED,
    OPENAPI: Path("schemas") / "openapi.json",
    OPENRPC: Path("schemas") / "openrpc.json",
    MODEL: Path("schemas") / MODEL.name,
}


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


def on_page_markdown(markdown: str, *, config: Any, **_: Any) -> str:
    """Replace the newsletter placeholder with the sign-up form (or a note)."""
    if NEWSLETTER_MARKER not in markdown:
        return markdown
    newsletter = (config.get("extra") or {}).get("newsletter") or {}
    username = str(newsletter.get("buttondown") or "").strip()
    return markdown.replace(NEWSLETTER_MARKER, newsletter_block(username))


def on_post_build(config: Any) -> None:
    """Copy every document in ``DOCUMENTS`` into ``<site_dir>/schemas/``."""
    for source, published in DOCUMENTS.items():
        target = Path(config["site_dir"]) / published
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

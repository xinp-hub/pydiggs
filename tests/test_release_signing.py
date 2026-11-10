"""Release tags must be signed: the release workflow verifies them against release-signers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SIGNERS = ROOT / ".github" / "release-signers"
PUBLISH = ROOT / ".github" / "workflows" / "publish.yml"

pytestmark = pytest.mark.skipif(not PUBLISH.is_file(), reason=".github/ is not in this tree")

# principal, namespaces="git", key type, base64 key (an optional comment is not allowed:
# keep the file minimal so a review sees exactly which keys can release).
_LINE = re.compile(
    r'(?P<principal>[^\s"]+) namespaces="git" '
    r"(?P<type>ssh-ed25519|sk-ssh-ed25519@openssh\.com|ecdsa-sha2-nistp256|"
    r"sk-ecdsa-sha2-nistp256@openssh\.com) (?P<key>[A-Za-z0-9+/]+={0,2})"
)


def _keys() -> list[str]:
    lines = SIGNERS.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.startswith("#")]


def test_signers_file_lists_at_least_one_git_key():
    keys = _keys()
    assert keys, ".github/release-signers lists no keys"
    for line in keys:
        assert _LINE.fullmatch(line), f"not an allowed-signers line for git: {line!r}"
    assert len({line.split()[-1] for line in keys}) == len(keys), "a key is listed twice"


def test_release_workflow_verifies_the_tag_before_anything_else():
    text = PUBLISH.read_text(encoding="utf-8")
    guard = text.split("\n  guard:\n", 1)[1].split("\n  already-published:", 1)[0]
    verify = guard.index("verify-tag")
    assert "gpg.ssh.allowedSignersFile=.github/release-signers" in guard
    assert 'git cat-file -t "refs/tags/${TAG}")" != "tag"' in guard
    assert verify < guard.index("id: check"), "verify the signature before the version checks"
    assert "TAG: ${{ github.ref_name }}" in guard, "pass the tag through env, not inline"


def test_release_workflow_never_edits_a_published_release():
    """Releases are immutable: a re-run fails instead of uploading to an existing one."""
    text = PUBLISH.read_text(encoding="utf-8")
    assert "gh release upload" not in text
    assert "--clobber" not in text
    create = text.index("gh release create")
    refuse = text.index('gh release view "$GITHUB_REF_NAME"')
    assert refuse < create
    assert "exit 1" in text[refuse:create], "an existing release must stop the run"

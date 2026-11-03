"""The sidecar path policy: roots, request-path forms and open-then-verify containment."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pydiggs.sidecar import _paths
from pydiggs.sidecar._paths import NotPermittedError, canonical_roots, open_verified

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file types")


@pytest.fixture
def tree(tmp_path):
    """``root/`` with a document, and ``outside/`` with a secret, both real paths."""
    base = Path(os.path.realpath(tmp_path))
    root = base / "root"
    outside = base / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "doc.xml").write_bytes(b"<Diggs/>")
    (outside / "secret.xml").write_bytes(b"<Secret/>")
    return root, outside


def _read(path, roots):
    fd = open_verified(str(path), roots)
    try:
        return os.read(fd, 100)
    finally:
        os.close(fd)


# --- roots -------------------------------------------------------------------------------------


def test_roots_are_canonical_and_deduplicated(tree, tmp_path, monkeypatch):
    root, outside = tree
    link = Path(os.path.realpath(tmp_path)) / "link"
    link.symlink_to(root, target_is_directory=True)
    monkeypatch.chdir(root.parent)
    roots = canonical_roots((str(link), "root", str(outside)), home=Path("/nonexistent-home"))
    assert roots == (root, outside)


@pytest.mark.parametrize(
    ("make", "text"),
    [
        (lambda t: "/", "filesystem root"),
        (lambda t: str(t / "missing"), "not an existing directory"),
        (lambda t: str(t / "root" / "doc.xml"), "not an existing directory"),
    ],
)
def test_bad_roots_are_refused(tree, make, text):
    root, _ = tree
    with pytest.raises(ValueError, match=text):
        canonical_roots((make(root.parent),), home=Path("/nonexistent-home"))


def test_the_home_folder_and_its_ancestors_are_refused(tree):
    root, _ = tree
    home = root / "home" / "me"
    home.mkdir(parents=True)
    for given in (root, root / "home", home):
        with pytest.raises(ValueError, match="home folder"):
            canonical_roots((str(given),), home=home)
    assert canonical_roots((str(home / ".."),), home=root.parent / "elsewhere") == (root / "home",)
    (home / "data").mkdir()
    assert canonical_roots((str(home / "data"),), home=home) == (home / "data",)


def test_the_real_home_is_used_by_default(monkeypatch, tree):
    root, _ = tree
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: root))
    with pytest.raises(ValueError, match="home folder"):
        canonical_roots((str(root),))

    def no_home(cls):
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", classmethod(no_home))
    assert canonical_roots((str(root),)) == (root,)


# --- request path forms ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "file:///etc/passwd",
        "FILE:/etc/passwd",
        "http://example.invalid/a.xml",
        "relative/a.xml",
        "a.xml",
        "./a.xml",
        "/data/../etc/passwd",
        "/data/sub/..",
        "\\\\server\\share\\a.xml",
        "//server/share/a.xml",
        "\\\\?\\C:\\a.xml",
        "\\\\.\\COM1",
        "/data/CON",
        "/data/nul.xml",
        "/data/Com1.txt",
        "/data/LPT9",
        "/data/aux /x",
        "/data/a\x00b.xml",
    ],
)
def test_refused_path_forms(path):
    with pytest.raises(NotPermittedError):
        _paths.vet_request_path(path)


@pytest.mark.parametrize("path", ["/data/a.xml", "/data/console.xml", "/data/com10.xml"])
def test_plain_absolute_paths_pass_the_form_check(path):
    assert _paths.vet_request_path(path) == path


# --- open then verify --------------------------------------------------------------------------


def test_a_file_inside_a_root_is_read(tree):
    root, _ = tree
    assert _read(root / "doc.xml", (root,)) == b"<Diggs/>"
    sub = root / "sub"
    sub.mkdir()
    (sub / "deep.xml").write_bytes(b"<D/>")
    assert _read(sub / "deep.xml", (Path("/nonexistent"), root)) == b"<D/>"


def test_no_roots_means_no_paths(tree):
    root, _ = tree
    with pytest.raises(NotPermittedError):
        open_verified(str(root / "doc.xml"), ())


def test_missing_and_outside_are_indistinguishable(tree):
    root, outside = tree
    errors = []
    for path in (root / "missing.xml", outside / "secret.xml", outside / "missing.xml"):
        with pytest.raises(NotPermittedError) as caught:
            open_verified(str(path), (root,))
        errors.append((type(caught.value), caught.value.args))
    assert len(set(errors)) == 1


@POSIX
def test_a_final_symlink_is_not_followed(tree):
    root, outside = tree
    (root / "escape.xml").symlink_to(outside / "secret.xml")
    (root / "inside.xml").symlink_to(root / "doc.xml")
    for name in ("escape.xml", "inside.xml"):
        with pytest.raises(NotPermittedError):
            open_verified(str(root / name), (root,))


@POSIX
def test_a_directory_symlink_cannot_escape(tree):
    root, outside = tree
    (root / "out").symlink_to(outside, target_is_directory=True)
    with pytest.raises(NotPermittedError):
        open_verified(str(root / "out" / "secret.xml"), (root,))


@POSIX
def test_a_swap_between_calls_is_caught(tree):
    root, outside = tree
    target = root / "swap.xml"
    target.write_bytes(b"<Ok/>")
    assert _read(target, (root,)) == b"<Ok/>"
    target.unlink()
    target.symlink_to(outside / "secret.xml")  # replaced between two requests
    with pytest.raises(NotPermittedError):
        open_verified(str(target), (root,))
    target.unlink()
    (root / "dir").mkdir()
    (root / "dir").rename(target)  # now a directory
    with pytest.raises(NotPermittedError):
        open_verified(str(target), (root,))


@POSIX
def test_a_fifo_neither_blocks_nor_passes(tree):
    root, _ = tree
    fifo = root / "pipe.xml"
    os.mkfifo(fifo)
    with pytest.raises(NotPermittedError):
        open_verified(str(fifo), (root,))


@POSIX
def test_a_directory_is_refused(tree):
    root, _ = tree
    with pytest.raises(NotPermittedError):
        open_verified(str(root), (root,))


@POSIX
def test_a_hard_to_resolve_descriptor_is_refused(tree, monkeypatch):
    root, _ = tree

    def broken(fd, given):
        raise NotPermittedError

    monkeypatch.setattr(_paths, "_fd_path", broken)
    with pytest.raises(NotPermittedError):
        open_verified(str(root / "doc.xml"), (root,))


def test_fd_path_errors_become_refusals(monkeypatch, tree):
    root, _ = tree
    monkeypatch.setattr(_paths, "_PLATFORM", "plan9")
    fd = os.open(root / "doc.xml", os.O_RDONLY)
    try:
        assert _paths._fd_path(fd, str(root / "doc.xml")) == str(root / "doc.xml")
        with pytest.raises(NotPermittedError):
            _paths._fd_path(fd, str(root / "missing.xml"))
        with pytest.raises(NotPermittedError):  # same name, different file
            _paths._fd_path(fd, str(root.parent / "outside" / "secret.xml"))
    finally:
        os.close(fd)


def test_the_generic_fallback_contains_too(monkeypatch, tree):
    root, outside = tree
    monkeypatch.setattr(_paths, "_PLATFORM", "plan9")
    assert _read(root / "doc.xml", (root,)) == b"<Diggs/>"
    with pytest.raises(NotPermittedError):
        open_verified(str(outside / "secret.xml"), (root,))

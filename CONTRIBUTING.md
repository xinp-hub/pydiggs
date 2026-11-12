# Contributing to pyDIGGS

Bug reports, fixes, tests and documentation are all welcome. This page is the short
version: the terms a contribution comes in under, and what a pull request needs to pass.
The longer guide (dev container, docs build, how pull requests are merged) is on the
[documentation site](https://xinp-hub.github.io/pydiggs/contributing/).

Security problems: please report them privately, see [SECURITY.md](SECURITY.md).

## Licence of contributions

pyDIGGS is licensed under the [Apache License 2.0](LICENSE). Contributions are accepted
under that same licence ("inbound = outbound"): by submitting a contribution you agree
that it is licensed under Apache-2.0, as section 5 of the licence says. There is no
contributor licence agreement to sign and no copyright assignment.

## Sign your work: the Developer Certificate of Origin

Every commit in a pull request must be signed off. The sign-off certifies the
[Developer Certificate of Origin 1.1](https://developercertificate.org/): in short, that
you wrote the change or otherwise have the right to submit it under the project's licence.
Read the full text at that link; it is a few lines long.

Sign off by adding `-s` when you commit:

```bash
git commit -s -m "Fix the unit lookup for kPa"
```

This adds a line to the commit message, taken from your `user.name` and `user.email`:

```text
Signed-off-by: Your Name <you@example.org>
```

Use your real name, and make sure the name and email match the commit's author (or
committer). The `DCO sign-off` check in CI fails a pull request that has a commit without
a matching line. To fix commits you have already made:

```bash
git commit --amend --signoff --no-edit    # the last commit
git rebase --signoff master               # every commit on your branch
```

then force-push your branch. Merge commits and Dependabot's commits are exempt.

A sign-off is not a cryptographic signature. `master` also requires commits that GitHub
shows as **Verified** (`git commit -S`, capital S); the
[longer guide](https://xinp-hub.github.io/pydiggs/contributing/#pull-request-guidelines)
has the setup. A maintainer can take care of that when merging a pull request from a fork.

## Set up

You need [uv](https://docs.astral.sh/uv/); it installs the right Python for you.

```bash
git clone https://github.com/<your-account>/pydiggs.git
cd pydiggs
uv sync --all-groups --all-extras
```

## Checks CI runs

Run these before you push; CI runs the same commands on Python 3.11 to 3.14.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

`uv run pytest -m "not slow and not scaling"` is the fast loop while you work. A fix
should come with a test that fails without it. A change users will notice gets a line in
[CHANGELOG.md](CHANGELOG.md) under "Unreleased".

## Releases

Merging a pull request never publishes anything. Only a maintainer releases: each release
is an annotated tag signed with a key listed in
[.github/release-signers](.github/release-signers), and the release workflow refuses to
publish a tag that is unsigned or signed by any other key. To verify a tag yourself,
point git at that file and run `git verify-tag`:

```bash
git -c gpg.ssh.allowedSignersFile=.github/release-signers verify-tag v1.8.0
```

[SECURITY.md](SECURITY.md) covers verifying the published files.

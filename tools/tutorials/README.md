# Video tutorials

pyDIGGS documents each user-facing feature with a short narrated video. A video is not
recorded by hand: it is rendered in CI from a YAML script in `docs/tutorials/<id>.yml`,
so it is rebuilt when the feature changes, and the build fails when the screen no longer
shows what the narration says.

| File | What it does |
| --- | --- |
| `docs/tutorials/<id>.yml` | One tutorial: what is said, and what is typed or clicked while it is said |
| `docs/tutorials/manifest.yml` | The voice, and which tutorial (or waiver) covers each public feature |
| `tools/tutorials/spec.py` | Loads and validates both formats; the reference for every rule below |
| `tools/tutorials/check.py` | The gate that `tests/test_tutorials.py` runs |
| `tools/tutorials/build.py` | The renderer: voice, screen recording, captions, chapters, poster |
| `tools/tutorials/mkdocs_tutorials.py` | Puts a rendered video into the documentation page that asks for it |

## A tutorial

```yaml
id: validate                      # must match the file name; lowercase, digits and -
title: Validate a DIGGS file from the command line
summary: Check a DIGGS 3.0 file, read the findings, and write a SARIF report for CI.
kind: terminal                    # terminal or browser
setup:                            # shell lines run in a fresh work directory, not shown
  - cp {repo}/tests/fixtures/ags4/convert_tier_a.ags site.ags
  - pydiggs convert site.ags -o site.xml > /dev/null
files: [site.xml]                 # work files a viewer can download to follow along
scenes:
  - title: Validate a file        # becomes a chapter
    say: >-
      We type pydiggs validate and the file name. The last line reads valid,
      with no errors and no warnings.
    run: pydiggs validate site.xml
    expect: 'site\.xml: valid: 0 error'
```

- **Terminal tutorials** type one command per scene (`run`) into a real shell. The work
  directory persists from scene to scene. `expect` is a regular expression that the
  command's output must match. It must not also match the typed command, or it proves
  nothing; anchor it to the output, for example `(?m)^results\.sarif\s*$`. `files` is
  required, and lists plain text files only (`.xml`, `.ags`, `.py`, `.json`, `.csv`,
  `.txt`).
- **Browser tutorials** start a server with `serve`, open `ready`, and give each scene
  `steps`, one action per step: `goto`, `click`, `fill`, `upload`, `press`, `wait_text`,
  `expect_text` or `wait` (milliseconds, at most 30 000). `fill`, `upload` and `press`
  take a mapping with a `selector`. An upload comes from `{repo}/` or `{work}/`.
  [`docs/tutorials/browser.yml`](../../docs/tutorials/browser.yml) is a complete example.
- **Placeholders.** `{repo}` is the repository root and `{work}` the work directory.
  `{port}` is the server's port, and `{glob:pattern}` / `{globname:pattern}` are the path
  or the name of the one file in the work directory that matches the pattern.
- **Nothing private.** A spec may not contain an absolute path outside the repository or
  anything that looks like a credential, and `spec.py` rejects both.

## Writing the narration

- Say what is on the screen while it is on the screen. Each scene's narration is 20 to
  1 200 characters, and each sentence becomes one voice clip and one caption.
- Say quantities in words ("two errors") and keep digits for names ("DIGGS 3.0",
  "rule 13"). A scene whose narration states a quantity must list it under `claims:`,
  as regular expressions the output must match. A video then cannot keep saying "two
  errors" over a screen that shows three.
- Spell out what a speech engine would misread ("site dot X M L"), or add the word to
  `pronounce:` in the manifest. Captions keep the written form.
- Avoid abbreviations that end in a full stop: they would split a sentence in two.
- Use example files written for the purpose. Never use project or client data.

## The manifest and the gate

`discover:` in the manifest lists where pyDIGGS's public surface is read from: the CLI
subcommands and their options, `pydiggs.__all__`, `pydiggs.capabilities()`, the HTTP,
sidecar and MCP interfaces, and the npm client's entry points. Each item it finds must be
in some feature's `covers`, and each feature names either a `tutorial` or a `waiver` that
says why a video would not help. `tests/test_tutorials.py` fails when an item is covered
by neither, and runs every terminal tutorial's commands to check they still print what
the narration says.

To show a video in the documentation, put `<!-- tutorial: <id> -->` where it belongs in a
page under `docs/`.

## Draft a missing tutorial

When the gate reports a new public item:

1. Decide whether it belongs with an existing feature. A new option of `pydiggs validate`
   usually does: add it to that feature's `covers` and, if the video should show it, add
   a scene.
2. Otherwise add a feature. If a video would show the item better than a page does, write
   `docs/tutorials/<id>.yml`, starting from the closest existing tutorial. Otherwise give
   the feature a `waiver` with a real reason.
3. Run the gate and the commands: `uv run pytest tests/test_tutorials.py`.
4. Render it locally if you can (below), or download the video from the `tutorials.yml`
   run on your pull request, and watch it with the sound on.

The maintainer reviews every new tutorial and every waiver.

## Rendering locally

You need ffmpeg and ffprobe, and [VHS](https://github.com/charmbracelet/vhs) for terminal
tutorials or Chromium (`playwright install chromium`) for browser ones. From the
repository root:

```bash
PATH="$PWD/.venv/bin:$PATH" uv run --locked --script tools/tutorials/build.py --only validate
```

The output goes to `build/tutorials/<id>/`. The voice model (Kokoro-82M) is downloaded
once, checked against pinned SHA-256 digests, into `$TUTORIALS_MODEL_DIR`, which
defaults to `~/.cache/pydiggs-tutorials`.

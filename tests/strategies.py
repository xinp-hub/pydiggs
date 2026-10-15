"""Hypothesis strategies for the findings model."""

from __future__ import annotations

from hypothesis import strategies as st

from pydiggs.findings.model import Finding, Report, Severity, Step, UnreadableFile

_NAME_START = st.sampled_from("abcXYZ0129")
_NAME_REST = st.text(alphabet="abcXYZ0129._-", max_size=12)
_TEXT = st.text(max_size=40)
_OPT_TEXT = st.none() | _TEXT
_POS = st.none() | st.integers(min_value=1, max_value=10**6)


@st.composite
def rule_ids(draw: st.DrawFn, step: Step | None = None) -> str:
    chosen = step if step is not None else draw(st.sampled_from(list(Step)))
    return f"{chosen.value}.{draw(_NAME_START)}{draw(_NAME_REST)}"


@st.composite
def findings(draw: st.DrawFn) -> Finding:
    step = draw(st.sampled_from(list(Step)))
    return Finding(
        layer=draw(st.sampled_from(["native", "official"])),
        step=step,
        rule_id=draw(rule_ids(step)),
        severity=draw(st.sampled_from(list(Severity))),
        message=draw(_TEXT),
        line=draw(_POS),
        column=draw(_POS),
        xpath=draw(st.none() | st.sampled_from(["/diggs:Diggs[1]", "/Diggs[1]/a[2]/@b", "x"])),
        gml_id=draw(_OPT_TEXT),
        source_snippet=draw(st.none() | st.text(max_size=1024)),
    )


_PATHS = st.text(alphabet="abc /._-é", min_size=1, max_size=20).filter(lambda s: s.strip())


@st.composite
def reports(draw: st.DrawFn) -> Report:
    steps = draw(
        st.dictionaries(
            st.sampled_from(list(Step)),
            st.sampled_from(["passed", "failed", "skipped", "incomplete"]),
            max_size=4,
        )
    )
    return Report(
        path=draw(_PATHS),
        absolute_path=draw(st.none() | _PATHS.map(lambda p: "/abs/" + p)),
        sha256=draw(st.text(alphabet="0123456789abcdef", min_size=64, max_size=64)),
        diggs_version=draw(st.none() | st.sampled_from(["2.5.a", "2.6", "3.0.0"])),
        steps=steps,
        findings=tuple(draw(st.lists(findings(), max_size=6))),
        tool_version="1.2.0",
        reference_data=draw(
            st.dictionaries(
                st.sampled_from(["schemas", "def", "diggs-examples"]),
                st.text(alphabet="0123456789abcdef", min_size=7, max_size=40),
            )
        ),
    )


@st.composite
def unreadable_files(draw: st.DrawFn) -> UnreadableFile:
    return UnreadableFile(
        path=draw(_PATHS),
        error=draw(_TEXT),
        absolute_path=draw(st.none() | _PATHS.map(lambda p: "/abs/" + p)),
    )

from __future__ import annotations

from pathlib import Path

import pytest

from specpilot.contracts.rfc import RfcLimits
from specpilot.corpus.clauses import ClauseLimits
from specpilot.corpus.qa import QaThresholds, run_parse_qa
from tests.helpers import rfc_factory


@pytest.fixture
def document(tmp_path: Path) -> Path:
    directory = tmp_path / "corpus"
    directory.mkdir(mode=0o700)
    return rfc_factory.write(directory, "qa.xml", rfc_factory.QA_RFC_XML)


def report(document: Path, **overrides: object):  # type: ignore[no-untyped-def]
    thresholds = QaThresholds(**overrides)  # type: ignore[arg-type]
    return run_parse_qa(document, RfcLimits(), ClauseLimits(), thresholds)


def test_a_clean_document_passes_every_line(document: Path) -> None:
    result = report(document)

    assert result.passed is True
    assert [line.name for line in result.lines if not line.passed] == []


def test_every_line_reports_its_measured_value_not_just_a_verdict(
    document: Path,
) -> None:
    """A gate that only says pass cannot show a regression coming."""
    result = report(document)

    for line in result.lines:
        assert line.measured is not None
        assert line.threshold is not None


def test_a_derived_section_number_must_match_the_one_the_source_publishes(
    document: Path,
) -> None:
    """RFC v3 writes its own numbering into every pn, so this is checkable
    over the whole corpus rather than the twenty-clause sample §4.1 settles
    for — 1909 of 1909 match on the real documents."""
    line = next(
        line for line in report(document).lines if line.name == "section_numbering"
    )

    assert line.measured == 1.0
    assert line.passed is True


def test_a_mismatched_section_number_fails_the_line(tmp_path: Path) -> None:
    directory = tmp_path / "broken"
    directory.mkdir(mode=0o700)
    broken = rfc_factory.write(
        directory,
        "broken.xml",
        rfc_factory.QA_RFC_XML.replace('pn="section-1-1"', 'pn="section-9-1"'),
    )

    result = run_parse_qa(broken, RfcLimits(), ClauseLimits(), QaThresholds())
    line = next(line for line in result.lines if line.name == "section_numbering")

    assert line.passed is False
    assert result.passed is False


def test_a_dangling_cross_reference_fails_the_line(tmp_path: Path) -> None:
    directory = tmp_path / "dangling"
    directory.mkdir(mode=0o700)
    broken = rfc_factory.write(
        directory,
        "dangling.xml",
        rfc_factory.QA_RFC_XML.replace('target="two"', 'target="nowhere"'),
    )

    result = run_parse_qa(broken, RfcLimits(), ClauseLimits(), QaThresholds())
    line = next(line for line in result.lines if line.name == "cross_references")

    assert line.passed is False


def test_text_that_reaches_no_unit_is_measured_against_the_two_percent_line(
    document: Path,
) -> None:
    """This is the line that would have caught the missing grammar blocks:
    they put 4.89% of RFC 9110 outside every unit."""
    line = next(line for line in report(document).lines if line.name == "coverage")

    assert line.threshold == pytest.approx(0.02)
    assert line.measured <= 0.02


def test_raising_the_coverage_ceiling_cannot_rescue_a_failing_document(
    tmp_path: Path,
) -> None:
    """A threshold is a decision to record, not a dial to turn until green."""
    directory = tmp_path / "loose"
    directory.mkdir(mode=0o700)
    broken = rfc_factory.write(
        directory,
        "loose.xml",
        rfc_factory.QA_RFC_XML.replace('pn="section-1-1"', 'pn="section-9-1"'),
    )

    result = run_parse_qa(
        broken, RfcLimits(), ClauseLimits(), QaThresholds(max_uncaptured=1.0)
    )

    assert result.passed is False


def test_a_normative_keyword_outside_every_clause_is_an_orphan(
    tmp_path: Path,
) -> None:
    """§4.1 caps orphan normative paragraphs at 1% of candidates.

    On both frozen documents this currently reads zero — every BCP 14 keyword
    belongs to a clause — and a figure that good needs to stay visible.
    """
    directory = tmp_path / "orphan"
    directory.mkdir(mode=0o700)
    broken = rfc_factory.write(
        directory,
        "orphan.xml",
        rfc_factory.QA_RFC_XML.replace(
            "<dt>term</dt>", "<dt>a <bcp14>MUST</bcp14> in a label</dt>"
        ),
    )

    result = run_parse_qa(broken, RfcLimits(), ClauseLimits(), QaThresholds())
    line = next(line for line in result.lines if line.name == "orphan_normatives")

    assert line.measured > 0.0
    assert line.passed is False


def test_the_report_carries_no_clause_text(document: Path) -> None:
    rendered = str(report(document))

    assert "Prose" not in rendered
    assert "token" not in rendered

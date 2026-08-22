import json

from app.modules.enrich import _extract_urls

SERP = json.dumps(
    {
        "organic": [
            {"link": "https://cassinfo.com/freight", "title": "Cass"},
            {"link": "https://loop.com/article", "title": "Loop"},
        ],
        "current_page": 1,
    }
)


def test_extracts_result_urls():
    assert _extract_urls(SERP) == {"https://cassinfo.com/freight", "https://loop.com/article"}


def test_tolerates_wrapper_text_around_json():
    """Bright Data wraps results in a security notice; the JSON still has to be found."""
    wrapped = f"SECURITY NOTICE: untrusted content follows\n{SERP}\n=====END====="
    assert len(_extract_urls(wrapped)) == 2


def test_returns_empty_on_unparseable_payload():
    assert _extract_urls("no json here at all") == set()
    assert _extract_urls("{ truncated json") == set()


def test_fabricated_citations_are_filtered_out():
    """A model-invented URL must not reach the PRD as a source."""
    from app.modules.enrich import Finding

    allowed = _extract_urls(SERP)
    findings = [
        Finding(finding="Cass is a vendor", source="cassinfo.com", url="https://cassinfo.com/freight"),
        Finding(finding="Made up", source="fake.com", url="https://fake.com/invented"),
    ]
    kept = [f for f in findings if f.url in allowed]
    assert [f.source for f in kept] == ["cassinfo.com"]

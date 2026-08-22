from app.modules.prd import Dropped, Feature, PRDDoc, render_markdown


def make_doc(**overrides) -> PRDDoc:
    base = dict(
        title="Freight Audit Platform",
        summary="Audit carrier invoices automatically.",
        problem=["Manual reconciliation is slow."],
        users=["Ops team"],
        features=[Feature(title="Invoice audit", detail="Flag variance.", priority="must-have")],
        requirements=["Write back to SAP"],
        constraints=["SOC 2 Type II"],
        out_of_scope=[],
        open_questions=["Pricing model?"],
        success_metrics=["Recovery rate"],
    )
    return PRDDoc(**{**base, **overrides})


def test_renders_core_sections():
    md = render_markdown(make_doc(), rev=3, enrichment=[])
    assert "# Freight Audit Platform" in md
    assert "*Revision 3" in md
    assert "**Invoice audit** _(must-have)_" in md
    assert "SOC 2 Type II" in md


def test_omits_empty_sections():
    md = render_markdown(make_doc(constraints=[], open_questions=[]), rev=1, enrichment=[])
    assert "## Constraints" not in md
    assert "## Open Questions" not in md


def test_rejected_work_renders_as_out_of_scope():
    """A reversed decision must be visible as dropped, not silently missing."""
    doc = make_doc(out_of_scope=[Dropped(item="Mobile app", reason="Ops works on desktop")])
    md = render_markdown(doc, rev=4, enrichment=[])
    assert "## Out of Scope" in md
    assert "**Mobile app** — Ops works on desktop" in md


def test_market_context_renders_citations():
    md = render_markdown(
        make_doc(),
        rev=2,
        enrichment=[{"finding": "AuditCo leads the space", "source": "auditco.com", "url": "https://auditco.com"}],
    )
    assert "## Market Context" in md
    assert "[auditco.com](https://auditco.com)" in md

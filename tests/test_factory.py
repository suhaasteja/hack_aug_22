from app.modules.factory import PROMPT_LIMIT, CrewMember, agent_prompt, slug


def member(**over) -> CrewMember:
    base = dict(
        role="backend-engineer",
        mission="Build the reconciliation engine.",
        justification=["Audit invoices against rate agreements"],
    )
    return CrewMember(**{**base, **over})


def test_slug_makes_port_safe_identifiers():
    assert slug("security-compliance-engineer") == "security_compliance_engineer"
    assert slug("Freight Audit & Pay!") == "freight_audit_pay"
    assert slug("--edge--") == "edge"


def test_prompt_stays_within_port_limit_for_a_large_prd():
    """Port rejects prompts over 5000 chars, and the PRD grows all meeting."""
    huge = "# PRD\n" + ("Requirement line that keeps growing.\n" * 2000)
    prompt = agent_prompt(member(), "Freight Audit Platform", huge)
    assert len(prompt) <= PROMPT_LIMIT


def test_prompt_keeps_role_and_justification_when_truncating():
    """Truncation must cut the document, never the agent's own identity."""
    huge = "x" * 50_000
    prompt = agent_prompt(member(), "Freight Audit Platform", huge)
    assert "backend-engineer" in prompt
    assert "Build the reconciliation engine." in prompt
    assert "Audit invoices against rate agreements" in prompt
    assert "investigate the Port" in prompt


def test_short_prd_is_not_truncated():
    prompt = agent_prompt(member(), "Freight Audit Platform", "# PRD\n\nSmall doc.")
    assert "Small doc." in prompt

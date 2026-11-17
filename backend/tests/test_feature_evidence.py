from datetime import date

from app.matching import recommend
from app.penny_grounding import filtered_context
from app.schemas import AutomaticReview, BusinessProfile
from conftest import cap, tool

ALL_PASS = {"official_page_fetched": True, "feature_supported": True, "business_fit_supported": True,
            "plan_inclusion_supported": True, "monthly_price_supported": True}


def partial(checked=date(2026, 11, 7), source="https://example.com/pricing", conflict=False, **failed):
    checks = {**ALL_PASS, **{key: False for key in failed}}
    evidence = {"conflicts": {"feature_negated": "true" if conflict else "false", "price_changed": "false"},
                "feature": {"url": source, "quote": "Answer every call"}}
    return AutomaticReview(checked=checked, capability="phone", checks=checks, notes=["Check vendor pricing."],
                           sources=[source], eligible=False, evidence=evidence)


def reviewed(tool_id, review, price=None, **extra):
    return tool(tool_id, price, [cap("phone")], status="pending_review", reviews={"phone": review}, **extra)


def run(catalog, budget=None, trade="hvac"):
    profile = BusinessProfile(trade=trade, budget_monthly_usd=budget)
    return recommend(catalog, profile, catalog.problem("calls"))


def test_fresh_feature_check_with_unknown_price_is_a_labelled_option_not_a_recommendation(catalog):
    catalog.tools = [reviewed("answer", partial(monthly_price_supported=True, plan_inclusion_supported=True))]
    result = run(catalog)
    assert result.options == [] and result.winner_tool_id is None
    option = result.check_first[0]
    assert option.tool_id == "answer" and option.status == "pending_review"
    assert option.feature_verified and option.unconfirmed == ["price", "plan"]


def test_unknown_price_never_counts_as_within_budget_even_when_the_catalog_lists_a_price(catalog):
    catalog.tools = [reviewed("answer", partial(monthly_price_supported=True), price=10)]
    result = run(catalog, budget=50)
    assert result.options == [] and result.winner_tool_id is None
    assert result.check_first[0].within_budget is None
    free = run(catalog, budget=0)
    assert free.winner_tool_id is None and all(o.within_budget is not True for o in free.check_first)


def test_missing_plan_inclusion_and_business_fit_are_disclosed(catalog):
    catalog.tools = [reviewed("answer", partial(plan_inclusion_supported=True, business_fit_supported=True))]
    assert run(catalog).check_first[0].unconfirmed == ["plan", "business_fit"]


def test_stale_conflicting_or_off_domain_evidence_is_not_feature_verified(catalog):
    cases = [partial(checked=date(2026, 10, 20), monthly_price_supported=True),
             partial(conflict=True, monthly_price_supported=True),
             partial(source="https://reviews.example.org/answer", monthly_price_supported=True),
             partial(source="http://example.com/pricing", monthly_price_supported=True),
             partial(feature_supported=True, monthly_price_supported=True)]
    for review in cases:
        catalog.tools = [reviewed("answer", review)]
        option = run(catalog).check_first[0]
        assert not option.feature_verified and option.unconfirmed == []


def test_a_review_for_another_task_or_an_expired_refresh_does_not_count(catalog):
    other = partial(monthly_price_supported=True).model_copy(update={"capability": "bookkeeping"})
    due = partial(monthly_price_supported=True)
    due = due.model_copy(update={"checks": {**due.checks, "fresh": False}})
    for review in (other, due):
        catalog.tools = [reviewed("answer", review)]
        assert not run(catalog).check_first[0].feature_verified


def test_feature_verified_tools_rank_above_unchecked_ones_but_never_above_fully_checked(catalog):
    catalog.tools = [tool("plain", 1, [cap("phone")], status="pending_review"),
                     reviewed("answer", partial(monthly_price_supported=True), price=90)]
    assert [o.tool_id for o in run(catalog).check_first] == ["answer", "plain"]


def test_known_mismatch_and_owner_exclusion_still_remove_a_feature_verified_tool(catalog):
    catalog.tools = [reviewed("answer", partial(monthly_price_supported=True), trades=("restaurant",))]
    result = run(catalog)
    assert result.check_first == [] and [s.tool_id for s in result.set_aside] == ["answer"]

    class Memory:
        ruled_out = ["answer"]
        requirements = None
    catalog.tools = [reviewed("answer", partial(monthly_price_supported=True))]
    clean, profile = filtered_context(catalog, BusinessProfile(trade="hvac"), Memory())
    result = recommend(clean, profile, clean.problem("calls"))
    assert result.check_first == [] and result.options == []


def test_chat_claim_states_the_evidence_tier_and_cites_the_vendor_page(catalog):
    from app.agent.evidence import build_evidence
    catalog.tools = [reviewed("answer", partial(monthly_price_supported=True, plan_inclusion_supported=True)),
                     tool("plain", 1, [cap("phone")], status="pending_review")]
    package = build_evidence(catalog, BusinessProfile(trade="hvac", budget_monthly_usd=50), ["calls"])
    claims = {c.tool_id: c for c in package.claims if c.tool_id}
    assert "Feature verified; price requires vendor confirmation. Confirm which plan includes this feature." in claims["answer"].text
    assert "within" not in claims["answer"].text.casefold() or "not counted" in claims["answer"].text.casefold()
    assert "answer:phone:feature_check" in claims["answer"].evidence_ids
    assert "Feature verified" not in claims["plain"].text
    assert package.winner_tool_ids == []


def test_directory_projects_feature_checks_without_marking_the_task_checked():
    from app.main import _directory
    fresh = reviewed("answer", partial(monthly_price_supported=True, plan_inclusion_supported=True))
    row = _directory(fresh)
    assert row.checked_capabilities == []
    assert row.feature_checked == {"phone": ["price", "plan"]}
    stale = reviewed("answer", partial(checked=date(2020, 1, 1), monthly_price_supported=True))
    assert _directory(stale).feature_checked == {}

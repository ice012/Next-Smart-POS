"""Regression tests for subscription plan module entitlements."""

from app.models.subscription import (
    ENTERPRISE_PLAN_MODULES,
    FREE_PLAN_MODULES,
    PROFESSIONAL_PLAN_MODULES,
    STARTER_PLAN_MODULES,
    get_default_plan_modules,
)

ALL_PLAN_MODULE_LISTS = {
    "free": FREE_PLAN_MODULES,
    "starter": STARTER_PLAN_MODULES,
    "professional": PROFESSIONAL_PLAN_MODULES,
    "enterprise": ENTERPRISE_PLAN_MODULES,
}


def test_quotations_available_on_every_plan():
    for tier, modules in ALL_PLAN_MODULE_LISTS.items():
        assert "quotations" in modules, f"{tier} plan is missing the quotations module"


def test_quotations_available_via_helper():
    for tier in ALL_PLAN_MODULE_LISTS:
        assert "quotations" in get_default_plan_modules(tier), tier


def test_purchases_remains_enterprise_only():
    for tier, modules in ALL_PLAN_MODULE_LISTS.items():
        if tier == "enterprise":
            assert "purchases" in modules
        else:
            assert "purchases" not in modules, f"{tier} should not include purchases"


def test_helper_falls_back_to_free_for_unknown_tier():
    assert get_default_plan_modules("nonsense") == FREE_PLAN_MODULES

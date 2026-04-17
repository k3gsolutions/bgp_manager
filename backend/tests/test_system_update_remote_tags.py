"""Semver tag selection for remote update checks."""

from app.services.system_update_remote_service import pick_latest_semver_tag


def test_pick_latest_semver_tag_orders_versions():
    assert pick_latest_semver_tag(["v0.1.0", "v1.0.0", "0.0.9"]) == "v1.0.0"
    assert pick_latest_semver_tag(["bad", "v2.0.0", "not-semver"]) == "v2.0.0"
    assert pick_latest_semver_tag([]) is None
    assert pick_latest_semver_tag(["only-bad"]) is None

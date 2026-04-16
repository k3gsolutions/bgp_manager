"""Community sets descobertos: serialização e comparação de membros."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models import CommunitySet
from app.routers.communities import _community_values_as_set, _set_to_out


def test_set_to_out_discovered_members_and_preview() -> None:
    now = datetime.now(timezone.utc)
    s = CommunitySet(
        id=1,
        device_id=1,
        company_id=1,
        name="MY-LIST",
        slug="d-my-list",
        vrp_object_name="MY-LIST",
        origin="discovered_running_config",
        discovered_members_json=["65001:1", "65001:2"],
        description="Descoberto",
        status="imported",
        is_active=True,
        created_by=None,
        updated_by=None,
        created_at=now,
        updated_at=now,
    )
    s.members = []
    out = _set_to_out(s)
    assert out.origin == "discovered_running_config"
    assert out.discovered_members == ["65001:1", "65001:2"]
    assert len(out.members) == 2
    assert out.members[0].community_value == "65001:1"
    assert out.members[0].missing_in_library is True
    assert out.members_missing == 2
    assert out.implied_config_preview and "ip community-list MY-LIST" in out.implied_config_preview


def test_community_values_as_set_manual_style() -> None:
    from unittest.mock import MagicMock

    li1 = MagicMock()
    li1.community_value = "1:1"
    li2 = MagicMock()
    li2.community_value = "1:2"
    m1 = MagicMock()
    m1.position = 0
    m1.community_value = "1:1"
    m1.linked_library_item = li1
    m2 = MagicMock()
    m2.position = 1
    m2.community_value = "1:2"
    m2.linked_library_item = li2
    now = datetime.now(timezone.utc)
    s = CommunitySet(
        id=2,
        device_id=1,
        company_id=1,
        name="M",
        slug="m",
        vrp_object_name="M",
        origin="app_created",
        discovered_members_json=None,
        status="draft",
        is_active=True,
        created_by=1,
        updated_by=1,
        created_at=now,
        updated_at=now,
    )
    s.members = [m1, m2]
    assert _community_values_as_set(s) == {"1:1", "1:2"}

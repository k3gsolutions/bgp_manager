"""Testes do agrupamento de ``ip community-list`` para snapshot."""

from app.services.community_catalog_service import (
    coalesce_groups_by_vrp_object_name,
    ordered_community_list_groups,
)
from app.services.huawei_community_parser import parse_running_config_communities


def test_ordered_community_list_groups_exporta_cdn_example():
    cfg = """
ip community-list EXPORTA-CDN-BVB
 community 64777:60021
 community 64777:52101
 community 64777:52102
"""
    parsed = parse_running_config_communities(cfg)
    groups = ordered_community_list_groups(parsed)
    assert len(groups) == 1
    name, comms = groups[0]
    assert name == "EXPORTA-CDN-BVB"
    assert [t[0] for t in comms] == ["64777:60021", "64777:52101", "64777:52102"]


def test_ordered_community_list_header_only_yields_empty_members():
    cfg = """
ip community-list EMPTY-LIST
"""
    parsed = parse_running_config_communities(cfg)
    groups = ordered_community_list_groups(parsed)
    assert len(groups) == 1
    assert groups[0][0] == "EMPTY-LIST"
    assert [t[0] for t in groups[0][1]] == []


def test_coalesce_groups_by_vrp_object_name_avoids_duplicate_insert_key():
    cfg = """
ip community-list A+B
 community 65001:1
 community 65001:2
ip community-list A_B
 community 65001:2
 community 65001:3
"""
    parsed = parse_running_config_communities(cfg)
    groups = ordered_community_list_groups(parsed)
    merged = coalesce_groups_by_vrp_object_name(groups)
    assert len(merged) == 1
    list_name, vrp_name, members = merged[0]
    assert list_name == "A+B"
    assert vrp_name == "A_B"
    assert [v for v, _ in members] == ["65001:1", "65001:2", "65001:3"]

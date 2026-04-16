"""Parser de communities no running-config Huawei VRP."""

from __future__ import annotations

from app.services.huawei_community_parser import (
    LIST_HEADER_EMPTY_VALUE,
    community_list_names_in_config,
    parse_running_config_communities,
)


def test_ip_community_list_members() -> None:
    cfg = """
#
ip community-list CL-TEST
 community 65001:100
 community 65001:200
"""
    out = parse_running_config_communities(cfg)
    assert len(out.community_lists) == 2
    names = {x.list_name for x in out.community_lists}
    assert names == {"CL-TEST"}
    vals = sorted(x.value for x in out.community_lists)
    assert vals == ["65001:100", "65001:200"]


def test_ip_community_list_header_only_gets_placeholder() -> None:
    cfg = """
ip community-list EMPTY-LIST
interface GigabitEthernet0/0/0
 description uplink
"""
    out = parse_running_config_communities(cfg)
    assert len(out.community_lists) == 1
    e = out.community_lists[0]
    assert e.list_name == "EMPTY-LIST"
    assert e.value == LIST_HEADER_EMPTY_VALUE


def test_community_line_without_leading_indent_before_community() -> None:
    cfg = """ip community-list LOOSE
community 1:1
 community 2:2
"""
    out = parse_running_config_communities(cfg)
    vals = sorted(x.value for x in out.community_lists)
    assert vals == ["1:1", "2:2"]


def test_community_list_names_includes_header_only() -> None:
    cfg = "ip community-list ONLY-HEAD\n#\n"
    names = community_list_names_in_config(cfg)
    assert "ONLY-HEAD" in names


def test_ip_community_filter_basic_and_advanced() -> None:
    cfg = """
ip community-filter basic C21-EXPORT-P1 index 10 permit 64777:52101
ip community-filter advanced FULL-ROUTE-ALL index 10 permit 64777:20000
"""
    out = parse_running_config_communities(cfg)
    assert len(out.community_filters) == 2
    b = [f for f in out.community_filters if f.match_type == "basic"][0]
    assert b.name == "C21-EXPORT-P1" and b.index == 10 and b.action == "permit" and b.value == "64777:52101"
    a = [f for f in out.community_filters if f.match_type == "advanced"][0]
    assert a.name == "FULL-ROUTE-ALL" and a.value == "64777:20000"


def test_community_list_line_trailing_description() -> None:
    cfg = """
ip community-list WITH-DESC
 community 64777:1 some remark here
"""
    out = parse_running_config_communities(cfg)
    assert len(out.community_lists) == 1
    e = out.community_lists[0]
    assert e.value == "64777:1"
    assert e.value_description == "some remark here"


def test_route_policy_if_match_community_filter_and_apply_community() -> None:
    cfg = """
route-policy RP-TEST permit node 10
 if-match community-filter C21-EXPORT-P1
 apply community 64777:1 64777:2 additive
"""
    out = parse_running_config_communities(cfg)
    assert len(out.route_policy_if_match) == 1
    ref = out.route_policy_if_match[0]
    assert ref.route_policy == "RP-TEST" and ref.node == "10" and ref.filter_name == "C21-EXPORT-P1"
    assert len(out.route_policy_apply_community) == 1
    ac = out.route_policy_apply_community[0]
    assert ac.route_policy == "RP-TEST" and set(ac.communities) == {"64777:1", "64777:2"}

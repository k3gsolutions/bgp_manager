"""Parser da secção Huawei 'Advertised to such N peers'."""

from __future__ import annotations

from app.services.bgp_export_lookup import (
    _parse_advertised_to_peers,
    _parse_advertised_to_peers_relaxed,
    _parse_detail_block,
    _sanitize_bgp_from_peer_ip,
    _ssh_text_for_advertised_peers,
    advertised_peer_ips_from_huawei_routing_outputs,
)


VISION_SAMPLE = """=== passo1 ===

 BGP local router ID : 10.200.1.255
 Local AS number : 269077
 Paths:   1 available, 1 best, 1 select, 0 best-external, 0 add-path
 BGP routing table entry information of 45.179.44.0/23:
 Network route.
 From: 0.0.0.0 (0.0.0.0)
 Route Duration: 6d10h11m19s
 Direct Out-interface: NULL0
 Original nexthop: 0.0.0.0
 Qos information : 0x0
 Community: <64777:10090>, <64777:50101>, <64777:50203>, <64777:50501>, <64777:50401>, <64777:51301>
 AS-path Nil, origin igp, MED 0, pref-val 0, valid, local, best, select, pre 60
 Advertised to such 14 peers:
    172.28.3.2
    172.28.1.30
    172.28.1.38
    10.20.255.2
    10.20.1.2
    172.28.1.34
    10.20.255.10
    186.239.158.129
    172.28.0.110
    10.20.1.17
    10.200.1.1
    172.16.92.182
    172.28.0.86
    10.20.1.9"""


def test_parse_advertised_peers_vision_sample_no_trailing_blank_line() -> None:
    ips = _parse_advertised_to_peers(VISION_SAMPLE)
    assert len(ips) == 14
    assert ips[0] == "172.28.3.2"
    assert ips[-1] == "10.20.1.9"
    assert "186.239.158.129" in ips


def test_parse_prefers_basic_when_detail_first_without_block() -> None:
    detail = "BGP routing table entry information of 45.179.44.0/23:\n From: 1.1.1.1\n"
    merged = _ssh_text_for_advertised_peers(detail, VISION_SAMPLE)
    ips = _parse_advertised_to_peers(merged)
    assert len(ips) == 14


def test_parse_ips_on_same_line_as_header() -> None:
    text = "Foo\n Advertised to such 2 peers: 10.0.0.1 10.0.0.2\nBar\n"
    ips = _parse_advertised_to_peers(text)
    assert ips == ["10.0.0.1", "10.0.0.2"]


def test_parse_total_N_peers_variant() -> None:
    text = (
        "BGP entry\n"
        " Advertised to total 3 peers:\n"
        "    192.0.2.1\n"
        "    192.0.2.2\n"
        "    2001:db8::1\n"
    )
    ips = _parse_advertised_to_peers(text)
    assert ips == ["192.0.2.1", "192.0.2.2", "2001:db8::1"]


def test_parse_N_bgp_peers_without_such() -> None:
    text = " Advertised to 2 BGP peers:\n 10.0.0.1\n 10.0.0.2\nNext line\n"
    ips = _parse_advertised_to_peers(text)
    assert ips == ["10.0.0.1", "10.0.0.2"]


def test_parse_the_following_peers_no_count() -> None:
    text = " Advertised to the following peers:\n 10.1.1.1\n 10.1.1.2\n"
    ips = _parse_advertised_to_peers(text)
    assert ips == ["10.1.1.1", "10.1.1.2"]


def test_parse_peers_only_header() -> None:
    text = " Advertised to peers:\n 10.2.2.2\n"
    ips = _parse_advertised_to_peers(text)
    assert ips == ["10.2.2.2"]


def test_parse_parentheses_around_ip() -> None:
    text = " Advertised to such 1 peers:\n    (203.0.113.5)\n"
    ips = _parse_advertised_to_peers(text)
    assert ips == ["203.0.113.5"]


def test_parse_these_peers_variant() -> None:
    text = " Advertised to these peers:\n 198.51.100.1\n"
    ips = _parse_advertised_to_peers(text)
    assert ips == ["198.51.100.1"]


def test_public_collect_matches_fixture_order() -> None:
    """Merge coloca basic (passo1) antes do detail — regressão para lista no fim do buffer."""
    detail = " Advertised to such 2 peers:\nNOT_IP\n"
    basic = " Advertised to such 2 peers:\n10.0.0.1\n10.0.0.2\n"
    ips = advertised_peer_ips_from_huawei_routing_outputs(detail, basic)
    assert ips == ["10.0.0.1", "10.0.0.2"]


def test_public_collect_empty() -> None:
    assert advertised_peer_ips_from_huawei_routing_outputs("", "") == []


def test_ssh_merge_basic_then_detail() -> None:
    merged = _ssh_text_for_advertised_peers("LINE_DETAIL\n", "LINE_BASIC\n")
    assert merged.split("\n")[0] == "LINE_BASIC"
    assert merged.split("\n")[1] == "LINE_DETAIL"


def test_sanitize_from_peer_rejects_null() -> None:
    assert _sanitize_bgp_from_peer_ip("0.0.0.0") is None
    assert _sanitize_bgp_from_peer_ip("0.0.0.0(0.0.0.0)") is None
    assert _sanitize_bgp_from_peer_ip("::") is None
    assert _sanitize_bgp_from_peer_ip("10.1.1.1") == "10.1.1.1"


def test_parse_detail_from_local_route() -> None:
    text = "From: 0.0.0.0 (0.0.0.0)\n AS-path Nil, origin igp\n"
    out = _parse_detail_block(text)
    assert out.get("from_peer_ip") is None


def test_relaxed_parser_variant_header() -> None:
    # Cabeçalho que não casa com os regex estritos (há texto entre "peer" e ":") — relaxed ainda apanha
    text = "Foo\n Advertised to 14 peer neighbors:\n10.0.0.1\n10.0.0.2\n"
    assert not _parse_advertised_to_peers(text)
    assert _parse_advertised_to_peers_relaxed(text) == ["10.0.0.1", "10.0.0.2"]

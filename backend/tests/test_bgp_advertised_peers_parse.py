"""Parser da secção Huawei 'Advertised to such N peers'."""

from __future__ import annotations

from app.services.bgp_export_lookup import _parse_advertised_to_peers, _ssh_text_for_advertised_peers


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

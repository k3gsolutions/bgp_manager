from app.services.route_policy_local_pref import parse_route_policy_local_preference


def test_parse_local_pref_uses_only_node_3010():
    cfg = """
route-policy C03-IMPORT-IPV4 permit node 2001
 apply local-preference 50
#
route-policy C03-IMPORT-IPV4 permit node 3010
 apply local-preference 501
#
route-policy C05-IMPORT-IPV4 permit node 3010
 apply local-preference 98
#
"""
    out = parse_route_policy_local_preference(cfg)
    assert out["C03-IMPORT-IPV4"] == 501
    assert out["C05-IMPORT-IPV4"] == 98


def test_parse_local_pref_ignores_policy_without_node_3010():
    cfg = """
route-policy C99-IMPORT-IPV4 permit node 2001
 apply local-preference 777
#
"""
    out = parse_route_policy_local_preference(cfg)
    assert "C99-IMPORT-IPV4" not in out


def test_parse_local_pref_accepts_trailing_tokens():
    cfg = """
route-policy C10-IMPORT-IPV4 permit node 3010 description PRIMARY
 apply local-preference 333 additive
#
"""
    out = parse_route_policy_local_preference(cfg)
    assert out["C10-IMPORT-IPV4"] == 333


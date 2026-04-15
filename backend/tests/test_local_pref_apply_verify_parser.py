from app.routers.devices import _extract_local_pref_for_node
from app.routers.devices import _extract_local_pref_for_policy_node_from_running_cfg


def test_extract_local_pref_for_node_3010():
    text = """
route-policy C03-IMPORT-IPV4 permit node 2001
 apply local-preference 50
#
route-policy C03-IMPORT-IPV4 permit node 3010
 apply local-preference 501
#
"""
    assert _extract_local_pref_for_node(text, node=3010) == 501


def test_extract_local_pref_for_node_missing_returns_none():
    text = """
route-policy C03-IMPORT-IPV4 permit node 2001
 apply local-preference 50
#
"""
    assert _extract_local_pref_for_node(text, node=3010) is None


def test_extract_local_pref_running_cfg_policy_specific():
    text = """
route-policy C03-IMPORT-IPV4 permit node 2001
 apply local-preference 50
#
route-policy C03-IMPORT-IPV4 permit node 3010 description PRIMARY
 apply local-preference 102 additive
#
route-policy C04-IMPORT-IPV4 permit node 3010
 apply local-preference 999
#
"""
    assert _extract_local_pref_for_policy_node_from_running_cfg(text, policy="C03-IMPORT-IPV4", node=3010) == 102


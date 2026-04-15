from app.services.interface_name import canonical_interface_name


def test_canonical_interface_name_removes_trailing_speed_suffix():
    assert canonical_interface_name("100GE0/3/0.25(40G)") == "100GE0/3/0.25"


def test_canonical_interface_name_keeps_plain_name():
    assert canonical_interface_name("100GE0/3/0.25") == "100GE0/3/0.25"


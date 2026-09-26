from lcr_studio.ut622e import SimulatedUT622E, parse_fetch


def test_simulator_answers_like_the_meter():
    m = SimulatedUT622E()
    assert m.idn().startswith("UNI_T,UT622E")
    st = m.read_settings(full=True)
    assert st["primary"] == "C" and st["frequency"] == "1kHz" and st["comp"] is False
    p, s, cmp = m.fetch()
    assert abs(p / 4.7e-6 - 1) < 0.01 and cmp is None


def test_setters_round_trip():
    m = SimulatedUT622E()
    m.set_speed("FAST")
    m.set_frequency("10kHz")
    m.set_primary("Z")
    m.set_secondary("DEG")
    m.set_range(3)
    st = m.read_settings(full=False)
    assert (st["frequency"], st["primary"], st["secondary"], st["range"], st["range_auto"]) == ("10kHz", "Z", "DEG", 3, False)
    assert m.raw("FREQ?") == "10kHz"


def test_comparator():
    m = SimulatedUT622E()
    m.set_speed("FAST")
    m.set_comp_nominal(4.7e-6)
    m.set_comp_tol(5)
    m.set_comp(True)
    assert m.fetch()[2] == "PASS"


def test_parse_fetch():
    assert parse_fetch("+4.95198e-14,+3.84492e-01,N") == (4.95198e-14, 0.384492, None)
    assert parse_fetch("+1.0e-06,+1.0e-02,0")[2] == "FAIL"

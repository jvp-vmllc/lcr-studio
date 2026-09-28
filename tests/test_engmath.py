import pytest

from lcr_studio.engmath import fmt, params_from_z, parse_eng, z_from_lc, z_from_measurement


@pytest.mark.parametrize("value,expected", [
    (4.7e-6, "4.7000 µF"), (0.000999996, "1.0000 mF"), (123456, "123.46 kF"),
    (-1.2e-14, "-12.000 fF"), (0, "0.0000 F"),
])
def test_fmt(value, expected):
    assert fmt(value, "F") == expected


@pytest.mark.parametrize("text,value", [
    ("4.7u", 4.7e-6), ("4.7 µF", 4.7e-6), ("10k", 1e4), ("1e-6", 1e-6), ("100m", 0.1), ("1M", 1e6), ("2.2nF", 2.2e-9),
])
def test_parse(text, value):
    assert parse_eng(text) == pytest.approx(value)


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_eng("abc")


def test_series_parallel_roundtrip():
    z = z_from_lc("C", 1e-6, 0.01, 1000, series=True)
    p = params_from_z(z, 1000)
    assert p["Cs"] == pytest.approx(1e-6)
    assert p["D"] == pytest.approx(0.01)
    assert z_from_lc("C", p["Cp"], p["D"], 1000, series=False) == pytest.approx(z)


def test_measurement_reconstruction():
    z = z_from_lc("L", 1e-3, 0.05, 1e4, series=False)
    p = params_from_z(z, 1e4)
    assert z_from_measurement("L", p["Lp"], "Q", p["Q"], 1e4, "PAR") == pytest.approx(z)
    assert z_from_measurement("Z", p["|Z|"], "DEG", p["θ"], 1e4, "SER") == pytest.approx(z)
    assert z_from_measurement("R", p["Rs"], "X", p["Xs"], 1e4, "SER") == pytest.approx(z)
    assert z_from_measurement("DCR", 10, None, None, None, None) is None


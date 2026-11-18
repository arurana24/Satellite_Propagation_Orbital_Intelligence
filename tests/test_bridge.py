"""The JSON bridge that the browser calls, tested natively. Every figure is validated with the REAL plotly
package: plotly.graph_objects.Figure() raises ValueError on any invalid property or value."""
import json

import pytest

from oi_bridge import api

go = pytest.importorskip("plotly.graph_objects")


def _valid(fig):
    go.Figure(fig)                       # raises on invalid properties (e.g. layout.geo.gridcolor, D1)


@pytest.fixture(scope="module")
def demo():
    r = json.loads(api.run_mission(json.dumps(api.default_config())))
    assert r["ok"], r.get("error")
    return r


def test_demo_mission_result_shape(demo):
    for k in ("summary", "invariants", "findings", "passes", "revisit", "coverage", "figures", "exports", "config", "track"):
        assert k in demo
    assert demo["summary"]["i_deg"] == pytest.approx(97.8)
    assert set(demo["passes"]) == {"Patiala", "Bengaluru"}
    assert demo["coverage"]["time_samples"] == demo["n_samples"]
    assert demo["invariants"]["energy_rel_drift"] < 1e-8


def test_every_mission_figure_is_valid_plotly(demo):
    assert {"ground_track", "earth_3d", "osculating", "pass_timeline", "coverage_heatmap", "coverage_time"} <= set(demo["figures"])
    for fig in demo["figures"].values():
        _valid(fig)


def test_ground_track_grid_colour_is_on_the_axes(demo):
    geo = demo["figures"]["ground_track"]["layout"]["geo"]
    assert "gridcolor" not in geo
    assert geo["lataxis"]["gridcolor"] == geo["lonaxis"]["gridcolor"] == "#22304A"


def test_negative_control_invalid_property_is_caught(demo):
    bad = json.loads(json.dumps(demo["figures"]["ground_track"]))
    bad["layout"]["geo"]["gridcolor"] = "#22304A"
    with pytest.raises(ValueError):
        _valid(bad)


def test_redraw_figures(demo):
    for name in ("earth_3d", "ground_track"):
        r = json.loads(api.figure(json.dumps({"name": name, "idx": 500, "frame": "ecef"})))
        assert r["ok"]; _valid(r["figure"])


@pytest.mark.parametrize("req", [{"action": "compare", "hours": 12, "step_s": 60}, {"action": "validate"},
                                 {"action": "sweep", "parameter": "inclination_deg", "start": 0, "stop": 120, "n": 7}])
def test_analysis_actions(demo, req):
    r = json.loads(api.analyse(json.dumps(req)))
    assert r["ok"], r.get("error")
    for fig in r["figures"].values():
        _valid(fig)
    if req["action"] == "validate":
        assert all(row["passed"] for row in r["rows"])
    if req["action"] == "compare":
        assert r["raan_rate_measured_deg_day"] == pytest.approx(r["raan_rate_analytical_deg_day"], rel=0.02)


@pytest.mark.slow
def test_launch_to_orbit_handoff():
    cfg = api.default_config(); cfg["launch"]["enabled"] = True; cfg["propagation"]["duration_h"] = 3
    r = json.loads(api.run_mission(json.dumps(cfg)))
    assert r["ok"], r.get("error")
    assert r["initial_state_source"] == "launch"
    assert abs(r["summary"]["i_deg"] - 97.8) < 0.3
    for fig in r["launch"]["figures"].values():
        _valid(fig)
    assert any(f["rule"] == "launch.range" for f in r["launch"]["findings"])


@pytest.mark.parametrize("patch,needle", [({"orbit": {"mode": "sma", "a_km": 7000, "e": 1.5}}, "Eccentricity"),
                                          ({"orbit": {"perigee_alt_km": 800, "apogee_alt_km": 500}}, "Apogee"),
                                          ({"propagation": {"dt_s": 0.1}}, "step"),
                                          ({"launch_site": "Atlantis"}, "Unknown launch site")])
def test_bad_input_returns_error_not_exception(patch, needle):
    r = json.loads(api.run_mission(json.dumps(patch)))
    assert r["ok"] is False and r["error"]["kind"] == "input" and needle in r["error"]["message"]


def test_mean_anomaly_input():
    cfg = api.default_config()
    cfg["orbit"].update(mode="sma", a_km=8000.0, e=0.1, anomaly_deg=30.0, anomaly_type="mean")
    cfg["propagation"]["duration_h"] = 1
    r = json.loads(api.run_mission(json.dumps(cfg)))
    assert r["ok"]
    assert r["summary"]["nu_deg"] > 30.0                 # true anomaly leads mean anomaly for e > 0 in (0, 180)


def test_result_is_strict_json(demo):
    json.loads(json.dumps(demo), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))   # no NaN/Infinity


def test_no_python_only_plotly_features(demo):
    """plotly-Python accepts conveniences that plotly.js does not (e.g. layout.template as a name string).
    The browser draws with plotly.js, so the JSON must not rely on them."""
    for fig in demo["figures"].values():
        assert "template" not in fig["layout"]
        for tr in fig["data"]:
            assert "type" in tr                     # plotly.js needs explicit trace types

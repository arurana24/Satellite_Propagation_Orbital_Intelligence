"""Regression tests for the defects found in the third-eye review (D2, D3, D4, D5)."""
import json
import math
from datetime import datetime

import numpy as np
import pytest

from orbital_intelligence.constants import RE, DEG
from orbital_intelligence.frames import geodetic_to_ecef
from orbital_intelligence.groundtrack import GroundTarget, compute_passes, region_coverage
from orbital_intelligence.launch import (SITES, DEFAULT_VEHICLE, inertial_azimuth, inertial_azimuths, choose_branch,
                                         corridor_check, tune_ascent)
from orbital_intelligence.simulation import MissionConfig, run_mission


# ----------------------------------------------------------------------------- D2 coverage
@pytest.fixture(scope="module")
def sso_day():
    res = run_mission(MissionConfig(region=None))
    return res.prop.t, res.track["lat"], res.track["lon"], res.track["alt"]


def _brute(t, la, lo, al, region, fov, grid):
    """Independent reference: full (time x cell) matrix, no chunking."""
    from orbital_intelligence.groundtrack import sensor_central_angle
    lats = np.arange(region[0] + grid / 2, region[1], grid); lons = np.arange(region[2] + grid / 2, region[3], grid)
    LA, LO = np.meshgrid(lats, lons, indexing="ij"); LA, LO = np.radians(LA.ravel()), np.radians(LO.ravel())
    s = np.stack((np.cos(np.radians(la)) * np.cos(np.radians(lo)), np.cos(np.radians(la)) * np.sin(np.radians(lo)), np.sin(np.radians(la))), 1)
    g = np.stack((np.cos(LA) * np.cos(LO), np.cos(LA) * np.sin(LO), np.sin(LA)), 1)
    cov = (s @ g.T) >= np.cos(sensor_central_angle(al, fov))[:, None]
    w = np.cos(LA)
    return float(np.sum(w * cov.any(0)) / w.sum())


def test_coverage_never_drops_time_samples(sso_day):
    t, la, lo, al = sso_day
    region = (5.0, 40.0, 65.0, 100.0)
    ref = _brute(t, la, lo, al, region, 30.0, 0.5)
    for budget in (4_000_000, 100_000, 5_000):            # chunk sizes from "all at once" to tiny
        c = region_coverage(t, la, lo, al, region, half_fov_deg=30.0, grid_deg=0.5, max_cells=budget)
        assert c.coverage_fraction == pytest.approx(ref, abs=1e-12)
        assert c.t.size == t.size                         # every time sample used
    assert ref > 0.5                                      # the old decimating code reported ~0.38 here


def test_coverage_chunking_is_invariant(sso_day):
    t, la, lo, al = sso_day
    a = region_coverage(t, la, lo, al, (5, 40, 65, 100), half_fov_deg=30.0, grid_deg=1.0)
    b = region_coverage(t, la, lo, al, (5, 40, 65, 100), half_fov_deg=30.0, grid_deg=1.0, max_cells=20_000)
    assert np.array_equal(a.n_events, b.n_events)
    assert np.allclose(a.max_gap_s, b.max_gap_s, equal_nan=True)
    assert np.allclose(a.cum_fraction, b.cum_fraction)
    assert a.mean_revisit_s > 0


def test_coverage_refuses_huge_jobs(sso_day):
    t, la, lo, al = sso_day
    with pytest.raises(ValueError, match="evaluations"):
        region_coverage(t, la, lo, al, (-70, 70, -180, 180), half_fov_deg=30.0, grid_deg=0.5, max_work=1_000_000)


# ----------------------------------------------------------------------------- D3 azimuth
def test_both_azimuth_branches_reach_same_inclination():
    for inc, lat in ((97.8, 13.72), (51.6, 28.56), (63.4, 5.23), (120.0, 34.74)):
        b = inertial_azimuths(inc, lat)
        for az in b.values():
            i = math.degrees(math.acos(math.sin(az * DEG) * math.cos(lat * DEG)))
            assert i == pytest.approx(inc, abs=1e-9)
        assert math.cos(b["north"] * DEG) >= 0 >= math.cos(b["south"] * DEG) - 1e-12
    assert inertial_azimuth(97.8, 13.72) == pytest.approx(inertial_azimuths(97.8, 13.72)["north"])   # back-compatible default


def test_sriharikota_sso_defaults_to_south_and_is_flagged():
    site = SITES["Satish Dhawan (Sriharikota)"]
    branch, _ = choose_branch(site, 97.8, 7.56)
    assert branch == "south"
    assert corridor_check(site, 188.0)["in_corridor"] is False          # needs a dog-leg in reality
    assert corridor_check(site, 120.0)["in_corridor"] is True
    assert corridor_check(SITES["Kourou"], 355.0)["in_corridor"] is True  # wrap-around corridor
    assert corridor_check(SITES["Baikonur"], 60.0)["in_corridor"] is None


@pytest.mark.slow
def test_sriharikota_sso_ascent_flies_south():
    _, lr, ok = tune_ascent(DEFAULT_VEHICLE, SITES["Satish Dhawan (Sriharikota)"], 600, 97.8, datetime(2026, 11, 17))
    assert ok
    lat = lr.samples["lat_deg"]
    assert lat[-1] < lat[0] - 5.0                         # heads south over the sea, not north over India
    assert lr.summary["heading"] == "south-going"
    assert abs(lr.summary["inclination_deg"] - 97.8) < 0.3


# ----------------------------------------------------------------------------- D4 peak elevation
def test_peak_elevation_refined_between_samples():
    lat0, lon0 = 10.0 * DEG, 20.0 * DEG
    site = geodetic_to_ecef(lat0, lon0, 0.0)
    up = site / np.linalg.norm(site)
    east = np.array([-math.sin(lon0), math.cos(lon0), 0.0])
    t = np.arange(0.0, 1200.0, 30.0)
    t0 = 597.0                                           # exactly overhead between two samples
    r = site + 500.0 * up + np.outer((t - t0) * 7.0, east)
    for dt_stride in (1, 4):                              # 30 s and 120 s sampling
        ps = compute_passes(t[::dt_stride], r[::dt_stride], GroundTarget("x", 10.0, 20.0, 0.0, 10.0))
        assert len(ps) == 1
        assert ps[0].max_elev_deg > 89.5                  # sampled value was ~78 deg (30 s) / ~58 deg (120 s)
        assert ps[0].t_max_s == pytest.approx(t0, abs=1.0)


# ----------------------------------------------------------------------------- D5 reproducibility
def test_exported_json_reproduces_the_run():
    from oi_bridge import api
    cfg = api.default_config(); cfg["propagation"]["duration_h"] = 3
    r1 = json.loads(api.run_mission(json.dumps(cfg)))
    exported = json.loads(r1["exports"]["json"])
    assert exported["config"]["propagation"]["duration_h"] == 3 and "engine_version" in exported
    r2 = json.loads(api.run_mission(json.dumps(exported["config"])))       # reload the saved mission
    assert r2["summary"] == r1["summary"]
    assert r2["passes"] == r1["passes"]
    assert r2["coverage"] == r1["coverage"]

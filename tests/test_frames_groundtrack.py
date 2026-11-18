import math, unittest
from datetime import datetime
import numpy as np
from orbital_intelligence.constants import RE, DEG, OMEGA_E, SIDEREAL_DAY_S
from orbital_intelligence.frames import *
from orbital_intelligence.groundtrack import *
from orbital_intelligence.elements import ClassicalElements
from orbital_intelligence.propagators import propagate


class TestFrames(unittest.TestCase):
    def test_julian_date(self):
        self.assertAlmostEqual(julian_date(datetime(2000, 1, 1, 12)), 2451545.0, places=9)
        self.assertAlmostEqual(julian_date(datetime(1987, 4, 10, 19, 21)), 2446896.30625, places=5)   # Meeus ex. 7.a

    def test_gmst_vallado_example(self):
        # Vallado Ex. 3-5: 1992-08-20 12:14 UT1 -> GMST 152.578787810 deg
        g = math.degrees(gmst(julian_date(datetime(1992, 8, 20, 12, 14))))
        self.assertAlmostEqual(g, 152.578787810, places=4)

    def test_gmst_rate_is_sidereal(self):
        ep = datetime(2026, 11, 17)
        d = (gmst_at(ep, SIDEREAL_DAY_S) - gmst_at(ep, 0.0) + math.pi) % (2 * math.pi) - math.pi
        self.assertLess(abs(float(d)), 1e-5)

    def test_eci_ecef_roundtrip(self):
        r = np.random.default_rng(0).normal(size=(50, 3)) * 7000; th = np.linspace(0, 6, 50)
        self.assertTrue(np.allclose(ecef_to_eci(eci_to_ecef(r, th), th), r))

    def test_ecef_velocity_of_corotating_point_is_zero(self):
        r = np.array([6378.0, 0, 0]); v = np.cross([0, 0, OMEGA_E], r)
        _, vr = state_eci_to_ecef(r, v, 0.0)
        self.assertLess(np.linalg.norm(vr), 1e-12)

    def test_geodetic_known_points(self):
        self.assertTrue(np.allclose(geodetic_to_ecef(0, 0, 0), [RE, 0, 0]))
        self.assertAlmostEqual(float(geodetic_to_ecef(math.pi / 2, 0, 0)[2]), 6356.752314, places=4)  # WGS-84 b
        # independent closed form at lat 40, lon -90, h=0: y = -N cos(lat), N = a / sqrt(1 - e^2 sin^2 lat)
        e2 = 0.0066943799901413165; N = 6378.137 / math.sqrt(1 - e2 * math.sin(40 * DEG) ** 2)
        r = geodetic_to_ecef(40 * DEG, -90 * DEG, 0.0)
        self.assertAlmostEqual(float(r[1]), -N * math.cos(40 * DEG), places=9); self.assertAlmostEqual(float(r[1]), -4892.7, delta=0.1)
        self.assertAlmostEqual(float(r[2]), N * (1 - e2) * math.sin(40 * DEG), places=9)

    def test_geodetic_roundtrip_including_poles(self):
        rng = np.random.default_rng(3)
        lat = np.append(rng.uniform(-math.pi / 2, math.pi / 2, 500), [math.pi / 2, -math.pi / 2, 0.0])
        lon = rng.uniform(-math.pi, math.pi, lat.size); h = np.append(rng.uniform(0, 36000, 500), [100, 100, 0])
        la, lo, hh = ecef_to_geodetic(geodetic_to_ecef(lat, lon, h))
        self.assertLess(np.max(np.abs(la - lat)), 1e-9); self.assertLess(np.max(np.abs(hh - h)), 1e-6)
        self.assertLess(np.max(np.abs(np.angle(np.exp(1j * (lo - lon)))[:500])), 1e-9)

    def test_look_angles_zenith(self):
        site = geodetic_to_ecef(30 * DEG, 70 * DEG, 0.0)
        up = site / np.linalg.norm(site)
        az, el, rng = look_angles(site + 500 * up, 30 * DEG, 70 * DEG, 0.0)
        self.assertAlmostEqual(math.degrees(float(el)), 90, delta=0.2); self.assertAlmostEqual(float(rng), 500, delta=0.5)


class TestGroundTrack(unittest.TestCase):
    ep = datetime(2026, 11, 17)

    def test_max_latitude_equals_inclination(self):
        el = ClassicalElements.from_altitudes(500, 500, 51.6); r, v = el.to_state()
        res = propagate(r, v, el.period * 2, 20); gt = ground_track(res.t, res.r, self.ep)
        self.assertAlmostEqual(gt["lat"].max(), 51.6, delta=0.35)           # geodetic vs geocentric lat
        self.assertAlmostEqual(gt["alt"].mean(), 500, delta=25)

    def test_geostationary_stays_overhead(self):
        a = 42164.17
        el = ClassicalElements.from_degrees(a, 0, 0, 0, 0, 0)
        # place sat over lon 0 at epoch
        th = float(gmst_at(self.ep, 0.0)); el = ClassicalElements(a, 0, 0, 0, 0, th)
        r, v = el.to_state(); res = propagate(r, v, 86164.0, 300, "twobody")
        gt = ground_track(res.t, res.r, self.ep)
        self.assertLess(np.ptp(gt["lon"]), 0.05); self.assertLess(np.max(np.abs(gt["lat"])), 0.01)
        ps = compute_passes(res.t, gt["r_ecef"], GroundTarget("sub", 0.0, 0.0, 0.0, 10.0))
        self.assertEqual(len(ps), 1); self.assertTrue(ps[0].clipped_start and ps[0].clipped_end)
        self.assertAlmostEqual(ps[0].max_elev_deg, 90.0, delta=0.5)

    def test_leo_pass_geometry(self):
        el = ClassicalElements.from_altitudes(500, 500, 97.4); r, v = el.to_state()
        res = propagate(r, v, 86400, 10); gt = ground_track(res.t, res.r, self.ep)
        ps = compute_passes(res.t, gt["r_ecef"], GroundTarget("T", 30.0, 76.0, 0.25, 5.0))
        self.assertTrue(len(ps) >= 2)
        for p in ps:
            self.assertGreater(p.duration_s, 30); self.assertLess(p.duration_s, 700)    # LEO pass <= ~11 min
            self.assertGreaterEqual(p.max_elev_deg, 5.0); self.assertLess(p.aos_s, p.los_s)
        self.assertTrue(all(ps[k].los_s < ps[k + 1].aos_s for k in range(len(ps) - 1)))
        rs = revisit_stats(ps, 86400.0); self.assertEqual(rs["n_passes"], len(ps)); self.assertGreater(rs["max_gap_s"], 0)

    def test_aos_los_refinement_hits_threshold(self):
        el = ClassicalElements.from_altitudes(500, 500, 97.4); r, v = el.to_state()
        tg = GroundTarget("T", 30.0, 76.0, 0.25, 10.0)
        res = propagate(r, v, 86400, 30); gt = ground_track(res.t, res.r, self.ep)
        p = compute_passes(res.t, gt["r_ecef"], tg)[0]
        fine = propagate(r, v, 86400, 1.0); gf = ground_track(fine.t, fine.r, self.ep)
        _, elf, _ = look_angles(gf["r_ecef"], tg.lat_deg * DEG, tg.lon_deg * DEG, tg.alt_km)
        mask = np.degrees(elf) >= 10
        first = fine.t[np.argmax(mask)]
        self.assertLess(abs(first - p.aos_s), 2.0)

    def test_footprint_formulas(self):
        # horizon angle at 500 km, 0 deg elev: acos(Re/(Re+h)) = 22.0 deg
        self.assertAlmostEqual(math.degrees(float(horizon_central_angle(500, 0))), math.degrees(math.acos(RE / (RE + 500))), places=9)
        # tiny FOV -> swath ~ 2*h*tan(eta)
        self.assertAlmostEqual(float(swath_width_km(500, 1.0)), 2 * 500 * math.tan(1 * DEG), delta=0.7)
        # FOV beyond the limb is clamped to the horizon
        self.assertAlmostEqual(float(sensor_central_angle(500, 80.0)), float(horizon_central_angle(500, 0)), places=9)

    def test_footprint_circle_geometry(self):
        la, lo = footprint_circle(20.0, 30.0, 0.2)
        d = np.arccos(np.clip(np.sin(np.radians(20)) * np.sin(np.radians(la)) + np.cos(np.radians(20)) * np.cos(np.radians(la)) * np.cos(np.radians(lo - 30)), -1, 1))
        self.assertTrue(np.allclose(d, 0.2, atol=1e-9))

    def test_dateline_split(self):
        lon, lat = split_dateline([170, 179, -179, -170], [0, 1, 2, 3])
        self.assertTrue(np.isnan(lon[2])); self.assertEqual(len(lon), 5)

    def test_region_coverage_bounds(self):
        el = ClassicalElements.from_altitudes(600, 600, 97.8); r, v = el.to_state()
        res = propagate(r, v, 86400 * 2, 30); gt = ground_track(res.t, res.r, self.ep)
        c = region_coverage(res.t, gt["lat"], gt["lon"], gt["alt"], (10, 40, 60, 100), half_fov_deg=30, grid_deg=3)
        self.assertTrue(0 < c.coverage_fraction <= 1); self.assertTrue(np.all(np.diff(c.cum_fraction) >= -1e-12))
        c2 = region_coverage(res.t, gt["lat"], gt["lon"], gt["alt"], (10, 40, 60, 100), half_fov_deg=50, grid_deg=3)
        self.assertGreaterEqual(c2.coverage_fraction, c.coverage_fraction)            # wider sensor never covers less
        with self.assertRaises(ValueError): region_coverage(res.t, gt["lat"], gt["lon"], gt["alt"], (10, 5, 60, 100), half_fov_deg=30)

    def test_geo_covers_most_of_disk(self):
        a = 42164.17; th = float(gmst_at(self.ep, 0.0)); r, v = ClassicalElements(a, 0, 0, 0, 0, th).to_state()
        res = propagate(r, v, 3600, 60, "twobody"); gt = ground_track(res.t, res.r, self.ep)
        c = region_coverage(res.t, gt["lat"], gt["lon"], gt["alt"], (-60, 60, -60, 60), min_elev_deg=0.0, grid_deg=5)
        self.assertGreater(c.coverage_fraction, 0.55)        # horizon disc ~81 deg radius covers most of the box

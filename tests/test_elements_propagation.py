import math, unittest
import numpy as np
from orbital_intelligence.constants import MU, RE, J2
from orbital_intelligence.elements import *
from orbital_intelligence.propagators import *


class TestElements(unittest.TestCase):
    def test_roundtrip_random(self):
        rng = np.random.default_rng(1)
        for _ in range(200):
            el = ClassicalElements.from_degrees(rng.uniform(6700, 45000), rng.uniform(0, 0.9), rng.uniform(1, 179),
                                                rng.uniform(0, 360), rng.uniform(0, 360), rng.uniform(0, 360))
            if el.rp < RE: continue
            e2 = rv_to_coe(*el.to_state())
            for k in ("a", "e", "i"):
                self.assertAlmostEqual(getattr(el, k), getattr(e2, k), delta=1e-6 * max(1, el.a if k == "a" else 1))
            for k in ("raan", "argp", "nu"):
                d = (getattr(el, k) - getattr(e2, k) + math.pi) % (2 * math.pi) - math.pi
                self.assertLess(abs(d), 1e-7)

    def test_kepler_equation_residual(self):
        for e in (0.0, 0.1, 0.5, 0.9, 0.99):
            M = np.linspace(-10, 10, 101)
            E = kepler_solve(M, e)
            self.assertLess(np.max(np.abs(E - e * np.sin(E) - M)), 1e-11)

    def test_kepler_rejects_hyperbolic(self):
        with self.assertRaises(OrbitError): kepler_solve(1.0, 1.2)

    def test_period_and_speed(self):
        self.assertAlmostEqual(period(RE + 400) / 60, 92.56, delta=0.05)       # ISS-like
        self.assertAlmostEqual(circular_velocity(RE + 400), 7.6686, delta=1e-3)
        self.assertAlmostEqual(period(42164.0) / 3600, 23.934, delta=0.01)    # GEO = sidereal day

    def test_vis_viva_and_energy(self):
        el = ClassicalElements.from_degrees(10000, 0.3, 30, 10, 20, 0)
        r, v = el.to_state()
        self.assertAlmostEqual(np.linalg.norm(v), vis_viva(np.linalg.norm(r), el.a), places=9)
        self.assertAlmostEqual(0.5 * v @ v - MU / np.linalg.norm(r), el.energy, places=9)
        self.assertAlmostEqual(np.linalg.norm(r), el.rp, places=6)  # nu=0 is perigee

    def test_circular_and_equatorial_singularities(self):
        e = rv_to_coe(*ClassicalElements.from_degrees(7000, 0.0, 0.0, 0, 0, 40).to_state())
        self.assertAlmostEqual(math.degrees(e.nu), 40, places=5)
        e = rv_to_coe(*ClassicalElements.from_degrees(7000, 0.0, 60.0, 30, 0, 40).to_state())
        self.assertAlmostEqual(math.degrees(e.nu), 40, places=5); self.assertAlmostEqual(math.degrees(e.raan), 30, places=5)
        e = rv_to_coe(*ClassicalElements.from_degrees(9000, 0.2, 0.0, 0, 70, 10).to_state())
        self.assertAlmostEqual(math.degrees(e.argp), 70, places=4)

    def test_invalid_inputs(self):
        with self.assertRaises(OrbitError): ClassicalElements.from_degrees(7000, 1.1, 10, 0, 0, 0)
        with self.assertRaises(OrbitError): ClassicalElements.from_altitudes(800, 400, 10)
        with self.assertRaises(OrbitError): rv_to_coe([7000, 0, 0], [0, 12, 0])   # hyperbolic

    def test_hohmann_leo_to_geo(self):
        h = hohmann_delta_v(RE + 200, 42164.0)
        self.assertAlmostEqual(h["total"], 3.9, delta=0.1)

    def test_sso_inclination(self):
        i = math.degrees(sun_sync_inclination(RE + 700))
        self.assertAlmostEqual(i, 98.2, delta=0.1)
        self.assertIsNone(sun_sync_inclination(42164.0))


class TestPropagation(unittest.TestCase):
    def setUp(self):
        self.el = ClassicalElements.from_degrees(7200, 0.05, 55, 20, 40, 10)
        self.r0, self.v0 = self.el.to_state()

    def test_dop853_matches_kepler(self):
        T = self.el.period * 3
        res = propagate(self.r0, self.v0, T, T / 6, "twobody", "dop853")
        ref = kepler_propagate(self.r0, self.v0, res.t)
        self.assertLess(np.max(np.linalg.norm(res.y[:, :3] - ref[:, :3], axis=1)), 1e-4)

    def test_rk4_matches_kepler_and_order(self):
        cs = convergence_study(self.r0, self.v0, self.el.period)
        self.assertGreater(cs["rk4_observed_order"], 3.6); self.assertLess(cs["rk4_observed_order"], 4.6)
        self.assertLess(cs["rk4_pos_err_km"][-1], 1e-4)
        errs = [d["pos_err_km"] for d in cs["dop853"]]
        self.assertTrue(all(errs[k + 1] < errs[k] for k in range(3)))

    def test_invariants(self):
        for model in ("twobody", "j2"):
            d = invariants_drift(propagate(self.r0, self.v0, 86400, 30, model, "dop853"))
            self.assertLess(d["energy_rel_drift"], 1e-8); self.assertLess(d["angmom_rel_drift"], 1e-8)

    def test_j2_acceleration_is_gradient_of_potential(self):
        r = np.array([5000.0, -3000.0, 4000.0]); U = lambda x: -MU / np.linalg.norm(x) + MU * J2 * RE**2 * (3 * x[2]**2 / (x @ x) - 1) / (2 * np.linalg.norm(x)**3)
        g = np.array([(U(r + d) - U(r - d)) / 2e-4 for d in np.eye(3) * 1e-4])
        self.assertTrue(np.allclose(-g, accel_j2(r), rtol=1e-6, atol=1e-12))

    def test_j2_zero_effect_when_equatorial_plane_radial(self):
        self.assertTrue(np.allclose(accel_j2(np.array([7000.0, 0, 0]))[1:], 0))

    def test_j2_nodal_regression_matches_theory(self):
        T = 3 * 86400.0
        res = propagate(self.r0, self.v0, T, 120, "j2", "dop853")
        raan = np.array([rv_to_coe(y[:3], y[3:]).raan for y in res.y])
        slope = np.polyfit(res.t, np.unwrap(raan), 1)[0]
        self.assertAlmostEqual(slope / j2_secular_rates(self.el.a, self.el.e, self.el.i)["raan_dot"], 1.0, delta=0.01)

    def test_rk4_and_dop853_agree_with_small_step(self):
        a = propagate(self.r0, self.v0, 6000, 60, "j2", "rk4", rk4_step_s=2.0)
        b = propagate(self.r0, self.v0, 6000, 60, "j2", "dop853")
        self.assertLess(np.linalg.norm(a.r[-1] - b.r[-1]), 1e-3)

    def test_bad_arguments(self):
        with self.assertRaises(ValueError): propagate(self.r0, self.v0, 100, 10, "sgp4")
        with self.assertRaises(ValueError): propagate(self.r0, self.v0, -1, 10)

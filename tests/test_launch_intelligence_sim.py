import math, unittest
from dataclasses import replace
from datetime import datetime
import numpy as np
from orbital_intelligence.constants import RE, MU, G0
from orbital_intelligence.atmosphere import atmosphere
from orbital_intelligence.launch import *
from orbital_intelligence.elements import ClassicalElements, rv_to_coe
from orbital_intelligence.groundtrack import GroundTarget
from orbital_intelligence.intelligence import *
from orbital_intelligence.simulation import *
from orbital_intelligence.analysis import *
from orbital_intelligence import report

EP = datetime(2026, 11, 17)


class TestAtmosphere(unittest.TestCase):
    def test_us76_reference_values(self):
        rho, P, T, a = atmosphere(0.0)
        self.assertAlmostEqual(rho, 1.225, delta=1e-3); self.assertAlmostEqual(P, 101325, delta=1); self.assertAlmostEqual(a, 340.3, delta=0.1)
        self.assertAlmostEqual(atmosphere(5.0)[0], 0.7364, delta=1e-3); self.assertAlmostEqual(atmosphere(11.0)[2], 216.77, delta=0.05)
        self.assertAlmostEqual(atmosphere(20.0)[0], 0.08891, delta=1e-4); self.assertAlmostEqual(atmosphere(50.0)[0], 1.027e-3, delta=3e-5)

    def test_monotone_density(self):
        hs = np.linspace(0, 900, 400); rho = [atmosphere(h)[0] for h in hs]
        self.assertTrue(all(rho[k + 1] < rho[k] for k in range(len(rho) - 1)))


class TestLaunchAnalytical(unittest.TestCase):
    def test_azimuth_and_min_inclination(self):
        self.assertAlmostEqual(inertial_azimuth(28.5, 28.5), 90.0, places=6)           # due east gives i = lat
        self.assertIsNone(inertial_azimuth(20.0, 28.5))
        self.assertAlmostEqual(inertial_azimuth(90.0, 28.5), 0.0, places=6)

    def test_equator_east_budget(self):
        r = analytical_launch(0.0, 400, 0.0)
        self.assertAlmostEqual(r["rotation_benefit_kms"], 0.4651, delta=0.005)
        self.assertLess(r["dv_ideal_kms"], r["v_circ_kms"])

    def test_retrograde_costs_more(self):
        self.assertGreater(analytical_launch(28.5, 600, 98.0)["dv_ideal_kms"], analytical_launch(28.5, 600, 28.5)["dv_ideal_kms"])

    def test_infeasible_reports_message(self):
        r = analytical_launch(28.5, 400, 10.0); self.assertFalse(r["feasible"]); self.assertIn("impossible", r["message"])

    def test_rocket_equation(self):
        v = Vehicle("t", (Stage("S", 1000, 9000, 100, 100, 300),), 0.0, 1.0)
        s1 = v.stages[0]; self.assertAlmostEqual(v.stage_delta_vs()[0], s1.isp_vac_s * G0 * math.log(10) / 1000, places=6)

    def test_payload_capacity_monotone(self):
        a = analytical_launch(28.5, 400, 51.6, DEFAULT_VEHICLE)["max_payload_kg"]
        b = analytical_launch(28.5, 1200, 51.6, DEFAULT_VEHICLE)["max_payload_kg"]
        self.assertGreater(a, b); self.assertGreater(a, 0)


class TestAscent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = SITES["Cape Canaveral"]
        cls.prog, cls.res, cls.ok = tune_ascent(DEFAULT_VEHICLE, cls.site, 400, 51.6, EP)

    def test_tuner_reaches_target_orbit(self):
        s = self.res.summary
        self.assertTrue(self.ok)
        self.assertAlmostEqual(s["perigee_alt_km"], 400, delta=10); self.assertAlmostEqual(s["apogee_alt_km"], 400, delta=10)
        self.assertAlmostEqual(s["inclination_deg"], 51.6, delta=0.3)

    def test_ascent_physics_sanity(self):
        s = self.res.summary
        self.assertTrue(30 < s["max_q_kpa"] < 120); self.assertTrue(5 < s["max_q_alt_km"] < 20)
        self.assertLess(s["max_accel_g"], 8); self.assertTrue(1.0 < s["total_losses_kms"] < 4.5)
        self.assertGreater(s["engine_dv_kms"], s["velocity_gain_kms"])
        names = [e[1] for e in self.res.events]; self.assertIn("Stage 1 separation", names); self.assertTrue(any("cutoff" in n for n in names))

    def test_mass_budget(self):
        m = self.res.samples["mass"]; v = DEFAULT_VEHICLE
        self.assertAlmostEqual(m[0], v.liftoff_mass, places=3)
        self.assertTrue(np.all(np.diff(m) <= 1e-9))
        used = self.res.summary["propellant_used_kg"]
        self.assertAlmostEqual(m[0] - m[-1], used + v.stages[0].dry_mass_kg, delta=1.0)

    def test_launch_to_orbit_handoff_is_continuous(self):
        cfg = config_from_launch(self.res, MissionConfig(duration_h=3))
        out = run_mission(cfg)
        self.assertEqual(cfg.epoch, self.res.burnout_epoch)
        self.assertLess(np.linalg.norm(out.prop.r[0] - self.res.burnout_r), 1e-9)
        self.assertLess(np.linalg.norm(out.prop.v[0] - self.res.burnout_v), 1e-12)
        self.assertAlmostEqual(out.summary["perigee_alt_km"], self.res.summary["perigee_alt_km"], delta=1e-6)
        self.assertLess(out.invariants["energy_rel_drift"], 1e-8)

    def test_downrange_and_altitude_continuity(self):
        s = self.res.samples
        self.assertTrue(np.all(np.abs(np.diff(s["alt_km"])) < 20))         # no jumps between 1 s samples
        self.assertGreater(s["downrange_km"][-1], 500)

    def test_failure_modes(self):
        weak = replace(DEFAULT_VEHICLE, stages=(replace(DEFAULT_VEHICLE.stages[0], thrust_sl_kn=3000, thrust_vac_kn=3300), DEFAULT_VEHICLE.stages[1]))
        with self.assertRaises(LaunchError): simulate_ascent(weak, self.site, AscentProgram(), EP)
        with self.assertRaises(LaunchError): tune_ascent(DEFAULT_VEHICLE, self.site, 400, 10.0, EP)

    def test_underpowered_vehicle_reports_not_reaching_orbit(self):
        small = replace(DEFAULT_VEHICLE, payload_kg=40000)
        r = simulate_ascent(small, self.site, replace(self.prog), EP, cutoff_a_km=RE + 400)
        self.assertFalse(r.reached_target_energy)
        a = evaluate_launch(r.summary, 400, 51.6); self.assertEqual(a.status, "infeasible")


class TestIntelligence(unittest.TestCase):
    def sev(self, el, **kw):
        a = evaluate_orbit(el, **kw); return {f.rule: f.severity for f in a.findings}, a

    def test_reentry(self):
        s, a = self.sev(ClassicalElements.from_altitudes(90, 90, 30)); self.assertEqual(s["atmo.reentry"], "error"); self.assertEqual(a.status, "infeasible")

    def test_orbit_inside_earth(self):
        s, a = self.sev(ClassicalElements(7000, 0.2, 0.5, 0, 0, 0)); self.assertEqual(s["orbit.validity"], "error")

    def test_sso_detection(self):
        s, a = self.sev(ClassicalElements.from_altitudes(700, 700, 98.19)); self.assertEqual(s["sso"], "ok")
        s, a = self.sev(ClassicalElements.from_altitudes(700, 700, 60)); self.assertEqual(s["sso"], "info")
        self.assertIn("98.", [f for f in a.findings if f.rule == "sso"][0].suggestion)

    def test_target_latitude_unreachable_and_suggestion(self):
        s, a = self.sev(ClassicalElements.from_altitudes(500, 500, 30), targets=(GroundTarget("Pole", 85.0, 0, 0, 10),))
        self.assertEqual(s["target.lat"], "error"); self.assertTrue([f for f in a.findings if f.rule == "target.lat"][0].suggestion)
        s, _ = self.sev(ClassicalElements.from_altitudes(500, 500, 98), targets=(GroundTarget("Pole", 85.0, 0, 0, 10),))
        self.assertNotIn("error", s.values())

    def test_launch_site_inclination(self):
        s, _ = self.sev(ClassicalElements.from_altitudes(500, 500, 10), launch_site=SITES["Cape Canaveral"]); self.assertEqual(s["launch.inc"], "error")
        s, _ = self.sev(ClassicalElements.from_altitudes(500, 500, 28.6), launch_site=SITES["Cape Canaveral"]); self.assertEqual(s["launch.inc"], "ok")

    def test_radiation_belt_warning(self):
        s, _ = self.sev(ClassicalElements.from_altitudes(3000, 3000, 55)); self.assertEqual(s["radiation"], "warning")
        s, _ = self.sev(ClassicalElements.from_altitudes(500, 500, 55)); self.assertNotIn("radiation", s)

    def test_geo_and_critical_inclination(self):
        s, _ = self.sev(ClassicalElements(42164.0, 0.0, 0.0, 0, 0, 0)); self.assertEqual(s["geo"], "ok")
        s, _ = self.sev(ClassicalElements.from_degrees(26560, 0.7, 63.4, 0, 270, 0)); self.assertIn("critical.inc", s)

    def test_lifetime_decreases_with_lower_altitude(self):
        l = [estimate_lifetime_days(h) for h in (250, 350, 450, 600)]
        self.assertTrue(all(l[k] < l[k + 1] for k in range(3))); self.assertLess(l[0], 100); self.assertEqual(estimate_lifetime_days(100), 0.0)

    def test_findings_are_explainable(self):
        _, a = self.sev(ClassicalElements.from_altitudes(150, 150, 30))
        for f in a.findings: self.assertTrue(f.title and f.message)
        for f in a.by_severity("error"): self.assertTrue(f.suggestion)


class TestAnalysisAndSimulation(unittest.TestCase):
    def test_validate_numerics_all_pass(self):
        rows = validate_numerics(ClassicalElements.from_altitudes(500, 900, 60))
        self.assertTrue(all(r["passed"] for r in rows), [r for r in rows if not r["passed"]])

    def test_twobody_vs_j2_drifts(self):
        el = ClassicalElements.from_degrees(7000, 0.02, 60, 0, 0, 0)
        c = compare_twobody_j2(*el.to_state(), 2 * 86400.0, 120.0)
        self.assertAlmostEqual(c["raan_rate_measured_deg_day"] / c["raan_rate_analytical_deg_day"], 1.0, delta=0.02)
        self.assertAlmostEqual(c["argp_rate_measured_deg_day"] / c["argp_rate_analytical_deg_day"], 1.0, delta=0.05)
        self.assertGreater(c["final_sep_km"], 100); self.assertLess(c["sep_km"][0], 1e-9)

    def test_sweeps(self):
        el = ClassicalElements.from_altitudes(500, 500, 60)
        df = parameter_sweep(el, "altitude_km", [300, 500, 1000]); self.assertTrue(df["period_min"].is_monotonic_increasing)
        self.assertTrue(df["raan_rate_deg_day"].abs().is_monotonic_decreasing)
        dfi = parameter_sweep(el, "inclination_deg", [30, 60, 90]); self.assertAlmostEqual(dfi["raan_rate_deg_day"].iloc[2], 0.0, places=6)
        dfe = parameter_sweep(el, "eccentricity", [0.0, 0.2]); self.assertAlmostEqual(dfe["perigee_alt_km"].iloc[1], 500, places=6)
        with self.assertRaises(ValueError): parameter_sweep(el, "bogus", [1])

    def test_mission_run_and_validation_errors(self):
        cfg = MissionConfig(targets=(GroundTarget("Patiala", 30.34, 76.39, 0.25, 10),), region=(5, 40, 65, 100), duration_h=12)
        r = run_mission(cfg)
        self.assertEqual(len(r.prop.t), len(r.track["lat"])); self.assertIn("Patiala", r.passes); self.assertIsNotNone(r.coverage)
        with self.assertRaises(ValueError): run_mission(MissionConfig(duration_h=0))
        with self.assertRaises(ValueError): run_mission(MissionConfig(dt_s=0.1))

    def test_reports(self):
        cfg = MissionConfig(targets=(GroundTarget("Patiala", 30.34, 76.39, 0.25, 10),), region=(5, 40, 65, 100))
        r = run_mission(cfg); val = validate_numerics(cfg.elements)
        md = report.to_markdown(r, None, val); self.assertIn("Mission feasibility assessment", md); self.assertIn("PASS", md)
        import json; d = json.loads(report.to_json(r)); self.assertAlmostEqual(d["orbit"]["a_km"], cfg.elements.a, places=3)
        csv = report.ephemeris_csv(r); self.assertEqual(len(csv.strip().splitlines()), len(r.prop.t) + 1)
        try:
            self.assertTrue(report.to_pdf(r, None, val).startswith(b"%PDF"))
        except ImportError: pass


if __name__ == "__main__":
    unittest.main()

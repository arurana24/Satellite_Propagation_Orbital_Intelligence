"""Integration layer: one MissionConfig in, one MissionResult out. Independent of any UI."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import numpy as np

from .elements import ClassicalElements, rv_to_coe
from .propagators import propagate, invariants_drift, PropagationResult
from .frames import gmst_at
from .groundtrack import GroundTarget, ground_track, compute_passes, revisit_stats, region_coverage, CoverageResult
from .intelligence import evaluate_orbit, Assessment
from .analysis import orbit_summary, osculating_series

DEFAULT_EPOCH = datetime(2026, 11, 17, 0, 0, 0)


@dataclass
class MissionConfig:
    name: str = "Mission-1"
    elements: ClassicalElements = field(default_factory=lambda: ClassicalElements.from_altitudes(600, 600, 97.8))
    epoch: datetime = DEFAULT_EPOCH
    duration_h: float = 24.0
    dt_s: float = 30.0
    model: str = "j2"
    method: str = "dop853"
    targets: tuple = ()
    half_fov_deg: float | None = 30.0
    region: tuple | None = None            # (lat_min, lat_max, lon_min, lon_max)
    grid_deg: float = 2.0
    area_to_mass: float = 0.01
    launch_site: object = None
    state0: tuple | None = None            # optional (r, v) ECI override, e.g. a launch burnout state

    def validate(self):
        if not (0 < self.duration_h <= 24 * 30): raise ValueError("Duration must be within (0, 720] hours.")
        if not (1.0 <= self.dt_s <= 600.0): raise ValueError("Time step must be within [1, 600] s.")
        n = self.duration_h * 3600 / self.dt_s
        if n > 200_000: raise ValueError(f"{n:.0f} output samples requested; increase the step or shorten the duration (max 200000).")


@dataclass
class MissionResult:
    config: MissionConfig
    prop: PropagationResult
    summary: dict
    track: dict
    passes: dict
    revisit: dict
    coverage: CoverageResult | None
    assessment: Assessment
    invariants: dict
    osculating: dict

    @property
    def epoch(self): return self.config.epoch


def run_mission(cfg: MissionConfig) -> MissionResult:
    cfg.validate()
    r0, v0 = cfg.state0 if cfg.state0 is not None else cfg.elements.to_state()
    el0 = rv_to_coe(r0, v0)
    duration = cfg.duration_h * 3600.0
    prop = propagate(r0, v0, duration, cfg.dt_s, cfg.model, cfg.method)
    track = ground_track(prop.t, prop.r, cfg.epoch)
    passes = {t.name: compute_passes(prop.t, track["r_ecef"], t) for t in cfg.targets}
    revisit = {n: revisit_stats(p, duration) for n, p in passes.items()}
    cov = None
    if cfg.region is not None and cfg.half_fov_deg:
        cov = region_coverage(prop.t, track["lat"], track["lon"], track["alt"], cfg.region, half_fov_deg=cfg.half_fov_deg, grid_deg=cfg.grid_deg)
    assess = evaluate_orbit(el0, cfg.targets, cfg.half_fov_deg, cfg.launch_site, cfg.area_to_mass)
    stride = max(1, len(prop.t) // 600)
    osc = osculating_series(prop.y[::stride]); osc["t"] = prop.t[::stride]
    return MissionResult(cfg, prop, orbit_summary(el0), track, passes, revisit, cov, assess, invariants_drift(prop), osc)


def config_from_launch(launch, base: MissionConfig) -> MissionConfig:
    """Hand-off: burnout epoch + ECI state become the initial condition of the orbit propagation."""
    h = launch.handoff()
    if h["elements"] is None:
        raise ValueError("Burnout state is not on a bound orbit; nothing to propagate.")
    return replace(base, epoch=h["epoch"], elements=h["elements"], state0=(h["r"], h["v"]), launch_site=launch.site)

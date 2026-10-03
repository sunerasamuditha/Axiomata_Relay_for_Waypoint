"""The stop predictor: the trained LightGBM boosters when present, a transparent heuristic otherwise."""

from __future__ import annotations

from pathlib import Path

import pytest
from relay_ml import FEATURES, Predictor, StopFeatures, get_predictor

MODELS = Path(__file__).resolve().parents[1] / "models"


def stop(**kw) -> StopFeatures:
    base = dict(
        brand="Fresh",
        district="Nuwara Eliya",
        dock_type="rear_dock",
        parking="normal",
        temp="chilled",
        vehicle_type="van",
        vehicle_temp="reefer",
        units=24,
        weight_kg=166.0,
        volume_m3=0.87,
        seq=1,
        n_stops=4,
        planned_arrival=330,
        window_open=300,
        window_close=450,
        leg_km=78.0,
        leg_min=150.0,
        cum_planned_min=150.0,
        dow=2,
        monsoon=0,
        speed_index=100.0,
        disruption_index=100.0,
        allowance=20,
    )
    base.update(kw)
    return StopFeatures(**base)


def test_heuristic_fallback_without_models(tmp_path):
    p = Predictor(model_dir=tmp_path)  # an empty folder: no boosters
    assert p.kind == "heuristic"
    [(svc, late)] = p.predict([stop()])
    assert 1.0 <= svc <= 120.0
    assert 0.0 < late < 1.0


def test_heuristic_is_monotone_in_slack(tmp_path):
    p = Predictor(model_dir=tmp_path)
    early, tight = p.predict([stop(planned_arrival=320), stop(planned_arrival=445)])
    assert tight[1] >= early[1]  # arriving near the end of the window is never safer


@pytest.mark.skipif(not (MODELS / "service_min.txt").exists(), reason="trained models are not in git (private training data)")
def test_trained_models_load_and_predict():
    p = Predictor(model_dir=MODELS)
    assert p.kind == "lightgbm"
    out = p.predict([stop(), stop(seq=3, cum_planned_min=260.0)])
    assert len(out) == 2 and all(1.0 <= s <= 240.0 and 0.0 < q < 1.0 for s, q in out)


def test_feature_list_matches_the_row():
    assert len(stop().row()) == len(FEATURES)
    assert get_predictor() is get_predictor()  # one shared instance per process

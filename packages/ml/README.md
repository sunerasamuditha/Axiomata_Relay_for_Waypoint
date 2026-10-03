# relay_ml

Stop predictions for planned deliveries, the weekly demand forecast, and outlet profiles. Used by the API
(`apps/api/relay_api/domain/eta.py`) and by the Datathon work.

## Predictor

```python
from relay_ml import StopFeatures, get_predictor

service_min, late_prob = get_predictor().predict([StopFeatures(...)])[0]
```

- **Trained boosters** (`models/service_min.txt`, `models/late_prob.txt`, `models/meta.json`): LightGBM models
  trained on the delivery history with features known at planning time only (`features.py`). Validation on
  deliveries after 2025-11-01: service-time MAE 4.73 min (the standard allowance: 7.30), lateness AUC 0.969.
- **Heuristic fallback:** without the model files (a fresh clone) the predictor uses the planning allowance plus a
  drop-size term, and a logistic lateness estimate from window slack, district risk, monsoon, stop position and
  traffic. `GET /api/health` reports which one is in use (`predictor`).

The model files are built from private competition data, so **git ignores them**. They are included in the
starter zip, in the Docker image (`COPY packages`) and in `make deploy` uploads (`.gcloudignore` keeps them).
`RELAY_MODEL_DIR` points the predictor at another folder. The GitHub Actions deploy can fetch them from a bucket
(`GCP_MODELS_BUCKET`, see `docs/DEPLOY_GCP.md` § 8).

## Commands (need `data/private/`)

| Command | Output |
|---|---|
| `make train` (`python -m relay_ml.train`) | `models/*.txt` and `models/meta.json` with the validation metrics |
| `make forecast` (`python -m relay_ml.forecast`) | `data/demo/forecast_weekly.json`: weekly demand per depot, brand and temperature (aggregates only) |
| `make profiles` (`python -m relay_ml.profiles`) | `data/demo/outlet_profiles.json`: per-outlet order-size profiles used by the demo seed (aggregates only) |

Tests: `packages/ml/tests/test_predict.py` (`make test-engine`).

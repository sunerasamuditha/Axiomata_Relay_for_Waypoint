"""Product lines the stores order and the dock loads. Unit weights and volumes are the medians of
the history (Fresh ~6.9 kg and 0.037 m3 per case, Style ~14.5 kg and 0.24 m3 per carton, Tech
~200 kg and 0.7 m3 per appliance)."""

from __future__ import annotations

CATALOG: dict[str, list[dict]] = {
    "Fresh:chilled": [
        {"sku": "FR-DAI", "name": "Dairy", "share": 0.40, "unit_kg": 6.9, "unit_m3": 0.0368, "uom": "cases"},
        {"sku": "FR-YOG", "name": "Yoghurt & curd", "share": 0.20, "unit_kg": 6.4, "unit_m3": 0.0350, "uom": "cases"},
        {"sku": "FR-MEA", "name": "Meat & fish", "share": 0.25, "unit_kg": 7.6, "unit_m3": 0.0372, "uom": "cases"},
        {"sku": "FR-CPR", "name": "Chilled produce", "share": 0.15, "unit_kg": 5.8, "unit_m3": 0.0395, "uom": "cases"},
    ],
    "Fresh:ambient": [
        {"sku": "FR-DRY", "name": "Dry groceries", "share": 0.55, "unit_kg": 7.4, "unit_m3": 0.0360, "uom": "cases"},
        {"sku": "FR-BEV", "name": "Beverages", "share": 0.30, "unit_kg": 7.9, "unit_m3": 0.0365, "uom": "cases"},
        {"sku": "FR-HSE", "name": "Household", "share": 0.15, "unit_kg": 3.9, "unit_m3": 0.0420, "uom": "cases"},
    ],
    "Style:ambient": [
        {"sku": "ST-HNG", "name": "Hanging garments", "share": 0.60, "unit_kg": 12.8, "unit_m3": 0.2600, "uom": "rails"},
        {"sku": "ST-CTN", "name": "Cartons", "share": 0.40, "unit_kg": 17.0, "unit_m3": 0.2150, "uom": "cartons"},
    ],
    "Tech:ambient": [
        {"sku": "TE-APP", "name": "Appliances", "share": 0.70, "unit_kg": 230.0, "unit_m3": 0.7600, "uom": "items"},
        {"sku": "TE-SML", "name": "Small electronics", "share": 0.30, "unit_kg": 140.0, "unit_m3": 0.5600, "uom": "items"},
    ],
}


def products() -> list[dict]:
    out = []
    for key, items in CATALOG.items():
        brand, temp = key.split(":")
        for i, p in enumerate(items):
            out.append({**p, "brand": brand, "temp": temp, "sort": i})
    return out

"""
mock_tools.py
-------------
Integration-ready mock tool registry for AutoCritic-EO.

Each public function is a thin wrapper that currently returns a hardcoded
JSON payload matching the API contract. To switch to the live FastAPI server,
set the environment variable  API_BASE_URL  and un-comment the
`requests.post()` block inside each function.

Mock contract
  check_availability   → { status, data: { images_found } }
  load_imagery         → { status, data: { file_list } }
  compute_mask         → { status, data: { computed_masks, trend_analysis } }
  adversarial_optical  → { status, data: { file_list } }  [diverse semantic anomalies]
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Shared configuration
# ---------------------------------------------------------------------------

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")  # flip the switch here

logger = logging.getLogger(__name__)


def _post(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generic POST helper — currently DISABLED (mock mode).
    Un-comment the block below and remove the `raise` to enable live calls.
    """
    # import requests                                      # noqa: ERA001
    # url = f"{API_BASE_URL}{endpoint}"                   # noqa: ERA001
    # resp = requests.post(url, json=payload, timeout=30) # noqa: ERA001
    # resp.raise_for_status()                             # noqa: ERA001
    # return resp.json()                                  # noqa: ERA001
    raise NotImplementedError("Mock mode: live HTTP calls are disabled.")


# ---------------------------------------------------------------------------
# Tool 1 — check_availability
# ---------------------------------------------------------------------------

def check_availability(**kwargs: Any) -> Dict[str, Any]:
    """
    Check whether satellite imagery is available for an AOI and date window.

    Accepts any kwargs — the LLM may use varied param names (region, aoi,
    disaster_type, location, etc.). All are silently accepted.

    Live endpoint (when enabled): POST /api/v1/check_availability
    """
    sensor = kwargs.get("sensor", "optical")
    logger.info("[mock] check_availability called: sensor=%s", sensor)
    time.sleep(0.05)

    # --- MOCK RETURN ---
    return {
        "status": "success",
        "data": {
            "images_found": 2,
        },
    }

    # --- LIVE CALL (un-comment to enable) ---
    # return _post("/api/v1/check_availability", kwargs)


def load_imagery(**kwargs: Any) -> Dict[str, Any]:
    """
    Load sequence of imagery for a disaster across a date range.

    Accepts any kwargs. Common keys: disaster_id, sensor, region, bands, date_range.

    Live endpoint (when enabled): POST /api/v1/load_imagery
    """
    sensor = kwargs.get("sensor", "optical")
    date_range = kwargs.get("date_range", ["2021-08-01", "2021-08-31"])
    logger.info("[mock] load_imagery called: sensor=%s date_range=%s kwargs=%s", sensor, date_range, list(kwargs))
    time.sleep(0.05)

    # --- MOCK RETURN ---
    return {
        "status": "success",
        "data": {
            "file_list": [
                "mock_sequence_1.png",
                "mock_sequence_2.png",
                "mock_sequence_3.png"
            ],
        },
    }

    # --- LIVE CALL (un-comment to enable) ---
    # return _post("/api/v1/load_imagery", kwargs)


def compute_mask(**kwargs: Any) -> Dict[str, Any]:
    """
    Compute change-detection masks across an image time-series.

    Accepts any kwargs. Common keys: file_list, index, threshold.

    Live endpoint (when enabled): POST /api/v1/compute_mask
    """
    index = kwargs.get("index", "NBR")
    threshold = kwargs.get("threshold", -0.2)
    file_list = kwargs.get("file_list", [])
    logger.info("[mock] compute_mask called: length=%d index=%s", len(file_list), index)
    time.sleep(0.05)

    # --- MOCK RETURN ---
    return {
        "status": "success",
        "data": {
            "computed_masks": [
                "mock_mask_1.png",
                "mock_mask_2.png"
            ],
            "trend_analysis": "Disaster logic expands chronologically over 2 intervals.",
        },
    }

    # --- LIVE CALL (un-comment to enable) ---
    # return _post("/api/v1/compute_mask", kwargs)


def adversarial_optical(**kwargs: Any) -> Dict[str, Any]:
    """
    Intentionally returns an array of images containing diverse semantic anomalies.

    Accepts any kwargs. Common keys: disaster_id, sensor.

    Live endpoint (when enabled): POST /api/v1/load_imagery
    """
    logger.info("[mock] adversarial_optical called: returning mixed anomalous image sequence")
    time.sleep(0.05)

    # --- MOCK RETURN (intentionally adversarial) ---
    return {
        "status": "success",   # HTTP 200 masks the semantic failure
        "data": {
            "file_list": [
                "mock_base.png",
                "CLOUD_OBSCURED.png",
                "NODATA_STRIPES.png",
                "NDVI_EXCEEDS_1.png"
            ]
        },
    }

    # --- LIVE CALL (un-comment to enable) ---
    # return _post("/api/v1/load_imagery", {**kwargs, "cloud_simulation": True})



# ---------------------------------------------------------------------------
# Tool Registry — maps tool-name strings to callables
# ---------------------------------------------------------------------------

TOOL_REGISTRY: Dict[str, Any] = {
    "check_availability": check_availability,
    "load_imagery": load_imagery,
    "compute_mask": compute_mask,
    "adversarial_optical": adversarial_optical,
}


def call_tool(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Dispatch a tool call by name.

    Raises KeyError if tool_name is not in the registry.
    Raises RuntimeError if the tool itself returns a non-success status.
    """
    if tool_name not in TOOL_REGISTRY:
        raise KeyError(f"Unknown tool: '{tool_name}'. Available: {list(TOOL_REGISTRY)}")

    result = TOOL_REGISTRY[tool_name](**params)

    if result.get("status") != "success":
        raise RuntimeError(
            f"Tool '{tool_name}' returned non-success status: {result.get('status')}"
        )

    return result

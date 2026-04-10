from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

import requests

BACKEND_GIS_URL = os.getenv("BACKEND_GIS_URL", "http://localhost:8000")
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() in ("true", "1", "yes")

logger = logging.getLogger(__name__)


def _post(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{BACKEND_GIS_URL}{endpoint}"
    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("[api_tools] POST %s failed: %s", url, exc)
        if MOCK_MODE:
            logger.warning("[api_tools] Backend unavailable; falling back to mock mode")
            return _mock_fallback(endpoint, payload)
        return {
            "status": "error",
            "message": f"API Request Failed: {exc}",
        }


def _mock_fallback(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fallback mock returns when backend is unavailable."""
    time.sleep(0.05)
    
    if endpoint == "/api/v1/check_availability":
        return {
            "status": "success",
            "data": {
                "images_found": 2,
            },
        }
    elif endpoint == "/api/v1/load_imagery":
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
    elif endpoint == "/api/v1/compute_mask":
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
    else:
        return {
            "status": "success",
            "data": {}
        }


def check_availability(**kwargs: Any) -> Dict[str, Any]:
    logger.info("[api_tools] check_availability payload=%s", kwargs)
    return _post("/api/v1/check_availability", kwargs)


def load_imagery(**kwargs: Any) -> Dict[str, Any]:
    logger.info("[api_tools] load_imagery payload=%s", kwargs)
    return _post("/api/v1/load_imagery", kwargs)


def compute_mask(**kwargs: Any) -> Dict[str, Any]:
    logger.info("[api_tools] compute_mask payload=%s", kwargs)
    return _post("/api/v1/compute_mask", kwargs)


def adversarial_optical(**kwargs: Any) -> Dict[str, Any]:
    payload = {**kwargs, "adversarial": True}
    logger.info("[api_tools] adversarial_optical payload=%s", payload)
    return _post("/api/v1/load_imagery", payload)


TOOL_REGISTRY: Dict[str, Any] = {
    "check_availability": check_availability,
    "load_imagery": load_imagery,
    "compute_mask": compute_mask,
    "adversarial_optical": adversarial_optical,
}


def call_tool(tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name not in TOOL_REGISTRY:
        raise KeyError(f"Unknown tool: '{tool_name}'. Available: {list(TOOL_REGISTRY)}")

    result = TOOL_REGISTRY[tool_name](**params)

    if result.get("status") != "success":
        raise RuntimeError(
            f"Tool '{tool_name}' returned non-success status: {result.get('status')} - {result.get('message')}"
        )

    return result

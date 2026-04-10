from __future__ import annotations

import json
import requests


def main() -> None:
    url = "http://localhost:8001/api/run_agent"
    payload = {
        "query": "Analyze the flooding in Valencia on 2024-10-30 to track continuous changes."
    }

    response = requests.post(url, json=payload, timeout=120)
    response.raise_for_status()

    data = response.json()
    keys = list(data.keys())

    print("Response keys:", keys)
    print(json.dumps(data, indent=2))

    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    assert all(key in data for key in ["trajectory", "report", "images"]), (
        "Response JSON is missing one or more expected keys: trajectory, report, images"
    )

    print("\nOrchestrator verification passed. Payload structure contains trajectory, report, and images.")


if __name__ == "__main__":
    main()

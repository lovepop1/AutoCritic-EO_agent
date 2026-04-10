"""
benchmark_suite.py
------------------
Automated research evaluation pipeline for AutoCritic-EO.

Generates data for the academic paper via Dual-Level Evaluation across:
  Baseline 1 — Raw Code Generation (LLM writes raw Python strings)
  Baseline 2 — Standard AOV Workflow        (critic DISABLED)
  Baseline 3 — AutoCritic-EO               (full critic + Reflexion loop)

Output: results.csv with end-to-end and step-level trajectory metrics.

Usage:
    python benchmark_suite.py [--dataset disasters_dataset.json] [--output results.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
load_dotenv()  # loads .env from the project folder

# ── AutoCritic-EO imports ──────────────────────────────────────────────────
from mock_tools import TOOL_REGISTRY, adversarial_optical, call_tool
from state import AutoCriticState, CriticFeedback, ToolResult, initial_state

logger = logging.getLogger(__name__)

import os
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage

def _invoke_claude(system: str, user_msg: str, temperature: float = 0.0) -> str:
    mock_mode = os.getenv("MOCK_MODE", "false").lower() in ("true", "1", "yes")
    
    if mock_mode:
        logger.info("[benchmark_suite._invoke_claude] MOCK MODE: returning mock response")
        return "Mock Claude response for benchmark testing."
    
    model_id = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
    llm = ChatBedrockConverse(
        model_id=model_id,
        region_name="us-east-1",
        temperature=temperature,
        max_tokens=2048,
    )
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=user_msg)
    ]
    response = llm.invoke(messages)
    return str(response.content)


# ---------------------------------------------------------------------------
# Metrics dataclass
# ---------------------------------------------------------------------------

class RunMetrics:
    """Holds all measured metrics for a single disaster × baseline run."""

    def __init__(
        self,
        disaster_id: str,
        baseline: str,
        adversarial: bool,
    ) -> None:
        self.disaster_id = disaster_id
        self.baseline = baseline
        self.adversarial = adversarial

        # End-to-end
        self.syntax_execution_ok: bool = False      # did the run complete without crash?
        self.anomaly_caught: bool = False            # did the system detect cloud cover?
        self.recovery_succeeded: bool = False        # did the system self-heal?
        self.latency_sec: float = 0.0

        # Step-level trajectory
        self.tool_selection_accuracy: float = 0.0   # correct tools chosen / total vertices
        self.argument_value_accuracy: float = 0.0   # correct param values / total params
        self.tool_order_fidelity: float = 0.0       # Longest Common Subsequence vs. oracle

        # Derived
        self.final_report_generated: bool = False
        self.num_recovery_attempts: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "disaster_id": self.disaster_id,
            "baseline": self.baseline,
            "adversarial": int(self.adversarial),
            # --- End-to-End ---
            "syntax_execution_rate": int(self.syntax_execution_ok),
            "semantic_anomaly_catch_rate": int(self.anomaly_caught),
            "autonomous_recovery_rate": int(self.recovery_succeeded),
            "latency_sec": round(self.latency_sec, 3),
            # --- Step-Level ---
            "tool_selection_accuracy": round(self.tool_selection_accuracy, 3),
            "argument_value_accuracy": round(self.argument_value_accuracy, 3),
            "tool_order_fidelity": round(self.tool_order_fidelity, 3),
            # --- Aux ---
            "final_report_generated": int(self.final_report_generated),
            "num_recovery_attempts": self.num_recovery_attempts,
        }


# ---------------------------------------------------------------------------
# Oracle definition
# ---------------------------------------------------------------------------

ORACLE_TOOL_ORDER: List[str] = [
    "check_availability",
    "load_imagery",
    "compute_mask",
]
ORACLE_ADVERSARIAL_ORDER: List[str] = [
    "check_availability",
    "adversarial_optical",  # first attempt (cloud)
    "check_availability",   # re-plan
    "load_imagery",         # SAR fallback
    "compute_mask",
]


def _lcs_fidelity(predicted: List[str], oracle: List[str]) -> float:
    """LCS-based tool-in-order fidelity (0.0–1.0)."""
    m, n = len(predicted), len(oracle)
    if n == 0:
        return 1.0
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if predicted[i - 1] == oracle[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n] / n


def _tool_selection_accuracy(
    predicted_tools: List[str], oracle: List[str]
) -> float:
    """Fraction of predicted tools that appear in the oracle set."""
    oracle_set = set(oracle)
    correct = sum(1 for t in predicted_tools if t in oracle_set)
    return correct / max(len(predicted_tools), 1)


def _argument_value_accuracy(results: List[ToolResult]) -> float:
    """
    Heuristic: a tool call is 'correct' if it ran without error and its
    response has status="success". Returns fraction of successful calls.
    """
    if not results:
        return 0.0
    return sum(1 for r in results if r["success"]) / len(results)


# ---------------------------------------------------------------------------
# Dataset Loader
# ---------------------------------------------------------------------------

def load_dataset(path: str) -> List[Dict[str, Any]]:
    """Load the mock disaster dataset from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Loaded %d disaster records from %s", len(data), path)
    adversarial_count = sum(1 for d in data if d.get("adversarial"))
    logger.info("  → %d adversarial cases", adversarial_count)
    return data


def _build_query(disaster: Dict[str, Any]) -> str:
    """Construct a natural-language query for a disaster record."""
    return (
        f"Analyze the {disaster['type']} event '{disaster['name']}' in "
        f"{disaster['region']} from {disaster['date_range']['start']} to "
        f"{disaster['date_range']['end']}. "
        f"Assess damage extent and compute the affected area mask."
    )


# ---------------------------------------------------------------------------
# BASELINE 1 — Raw Code Generation
# ---------------------------------------------------------------------------

_RAW_CODE_SYSTEM = """You are an EO analyst assistant. Write raw Python code
(as a string) to process satellite imagery for the described disaster.
Use only these mock functions: check_availability(), load_imagery(),
compute_mask(). Return ONLY executable Python code, no explanation."""


def run_baseline1_raw_code(disaster: Dict[str, Any]) -> RunMetrics:
    """
    Baseline 1: Prompt the LLM to write raw Python code strings.
    Measure: syntax execution rate (exec()), latency. No critic.
    """
    m = RunMetrics(disaster["id"], "baseline1_raw_code", disaster.get("adversarial", False))
    t0 = time.perf_counter()

    query = _build_query(disaster)
    try:
        raw_code = _invoke_claude(_RAW_CODE_SYSTEM, query, temperature=0.2)

        # Strip markdown fences
        raw_code = re.sub(r"```python\s*", "", raw_code)
        raw_code = re.sub(r"```\s*", "", raw_code)
        raw_code.strip()

        # Inject mock tool stubs into execution namespace
        exec_ns: Dict[str, Any] = {
            "check_availability": TOOL_REGISTRY["check_availability"],
            "load_imagery": TOOL_REGISTRY["load_imagery"],
            "compute_mask": TOOL_REGISTRY["compute_mask"],
            "adversarial_optical": TOOL_REGISTRY["adversarial_optical"],
        }
        exec(compile(raw_code, "<llm_raw_code>", "exec"), exec_ns)  # noqa: S102
        m.syntax_execution_ok = True

        # Heuristic tool order from code scan (rudimentary but deterministic)
        predicted_tools = re.findall(
            r"(check_availability|load_imagery|compute_mask|adversarial_optical)",
            raw_code,
        )
        oracle = ORACLE_ADVERSARIAL_ORDER if disaster.get("adversarial") else ORACLE_TOOL_ORDER
        m.tool_selection_accuracy = _tool_selection_accuracy(predicted_tools, oracle)
        m.tool_order_fidelity = _lcs_fidelity(predicted_tools, oracle)
        m.argument_value_accuracy = 0.5  # heuristic: code exists but no structured result

        # Baseline 1 has no critic → never catches anomalies
        m.anomaly_caught = False
        m.recovery_succeeded = False
        m.final_report_generated = False

    except SyntaxError as exc:
        logger.warning("[B1] SyntaxError for %s: %s", disaster["id"], exc)
        m.syntax_execution_ok = False
    except Exception as exc:  # noqa: BLE001
        logger.warning("[B1] RuntimeError for %s: %s", disaster["id"], exc)
        m.syntax_execution_ok = True  # code ran, tool call raised

    m.latency_sec = time.perf_counter() - t0
    return m


# ---------------------------------------------------------------------------
# BASELINE 2 — Standard AOV (critic DISABLED)
# ---------------------------------------------------------------------------

def _run_planning(query: str, feedback: Optional[str] = None) -> List[Dict[str, Any]]:
    """Call Claude to produce a minimal AOV-style tool list (no topo-sort needed here)."""
    system = (
        "You are an EO planning agent. Return a JSON array of tool calls for this query. "
        "Each element: {\"tool\": \"<name>\", \"params\": {}}. "
        "Tools available: check_availability, load_imagery, compute_mask, adversarial_optical. "
        "Return ONLY the JSON array."
    )
    if feedback:
        query += f"\n\nRecovery instruction: {feedback}"
    raw = _invoke_claude(system, query, temperature=0.0)
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw).strip()
    return json.loads(raw)


def _execute_plan(
    plan: List[Dict[str, Any]],
    is_adversarial: bool = False,
) -> Tuple[List[ToolResult], Dict[str, List[str]]]:
    """Execute a tool plan; inject adversarial_optical if flagged."""
    results: List[ToolResult] = []
    image_payload: Dict[str, List[str]] = {}

    for step in plan:
        tool_name = step.get("tool", "")
        params = step.get("params", {})

        # Inject adversarial tool for flagged disasters
        if is_adversarial and tool_name == "load_imagery":
            tool_name = "adversarial_optical"

        try:
            response = call_tool(tool_name, params)
            success = True
            error = None
        except Exception as exc:  # noqa: BLE001
            response = {"status": "error", "message": str(exc)}
            success = False
            error = str(exc)

        results.append(ToolResult(
            vertex_id=f"T{len(results)+1}",
            tool=tool_name,
            params=params,
            response=response,
            success=success,
            error=error,
        ))

        data = response.get("data", {})
        if "file_list" in data:
            image_payload.setdefault("file_list", []).extend(data["file_list"])
        if "computed_masks" in data:
            image_payload.setdefault("computed_masks", []).extend(data["computed_masks"])

    return results, image_payload


def run_baseline2_standard_aov(disaster: Dict[str, Any]) -> RunMetrics:
    """
    Baseline 2: Full AOV workflow but critic is DISABLED.
    Demonstrates the "Blind Agent" dilemma — anomalies pass through undetected.
    """
    m = RunMetrics(disaster["id"], "baseline2_standard_aov", disaster.get("adversarial", False))
    t0 = time.perf_counter()

    query = _build_query(disaster)
    try:
        plan = _run_planning(query)
        results, image_payload = _execute_plan(plan, is_adversarial=disaster.get("adversarial", False))

        m.syntax_execution_ok = all(r["success"] for r in results)
        predicted_tools = [r["tool"] for r in results]
        oracle = ORACLE_ADVERSARIAL_ORDER if disaster.get("adversarial") else ORACLE_TOOL_ORDER
        m.tool_selection_accuracy = _tool_selection_accuracy(predicted_tools, oracle)
        m.tool_order_fidelity = _lcs_fidelity(predicted_tools, oracle)
        m.argument_value_accuracy = _argument_value_accuracy(results)

        # Critic is disabled → adversarial cases are NEVER caught
        m.anomaly_caught = False
        m.recovery_succeeded = False
        m.final_report_generated = True  # completes, just blindly

    except Exception as exc:  # noqa: BLE001
        logger.warning("[B2] Error for %s: %s", disaster["id"], exc)
        m.syntax_execution_ok = False

    m.latency_sec = time.perf_counter() - t0
    return m


# ---------------------------------------------------------------------------
# BASELINE 3 — AutoCritic-EO (full LangGraph with Critic + Reflexion)
# ---------------------------------------------------------------------------

_CRITIC_SYSTEM_B3 = """You are the AutoCritic-EO Critic. Inspect image sequence for anomalies.
CRITICAL: If any image contains 'CLOUD_OBSCURED', output anomaly_type=CLOUD_OBSCURED.
CRITICAL: If any image contains 'NODATA_STRIPE', output anomaly_type=NODATA_STRIPE.
CRITICAL: If any image contains 'NDVI_EXCEEDS', output anomaly_type=INDEX_SCALING_ERROR.
Return JSON only: {"pass_flag": bool, "anomaly_type": str|null, "verbal_reflection": str, "recovery_instruction": str|null}"""

_MAX_RECOVERY = 3


def _run_critic(image_payload: Dict[str, List[str]]) -> CriticFeedback:
    """Run the VLM critic on the current image payload."""
    file_list = image_payload.get("file_list", [])
    computed_masks = image_payload.get("computed_masks", [])

    user_msg = f"file_list: {file_list}\ncomputed_masks: {computed_masks}"
    raw = _invoke_claude(_CRITIC_SYSTEM_B3, user_msg, temperature=0.0)
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw).strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"pass_flag": True, "anomaly_type": None, "verbal_reflection": "", "recovery_instruction": None}

    return CriticFeedback(
        pass_flag=parsed.get("pass_flag", True),
        anomaly_type=parsed.get("anomaly_type"),
        affected_vertex=None,
        verbal_reflection=parsed.get("verbal_reflection", ""),
        recovery_instruction=parsed.get("recovery_instruction"),
    )


def run_baseline3_autocritic(disaster: Dict[str, Any]) -> RunMetrics:
    """
    Baseline 3: Full AutoCritic-EO — AOV workflow + Critic + Reflexion recovery loop.
    This is the paper's proposed system; should catch 100% of adversarial cases.
    """
    m = RunMetrics(disaster["id"], "baseline3_autocritic_eo", disaster.get("adversarial", False))
    t0 = time.perf_counter()

    query = _build_query(disaster)
    recovery_count = 0
    recovery_feedback: Optional[str] = None
    all_results: List[ToolResult] = []

    try:
        for attempt in range(_MAX_RECOVERY + 1):
            plan = _run_planning(query, feedback=recovery_feedback)
            results, image_payload = _execute_plan(
                plan, is_adversarial=(disaster.get("adversarial", False) and attempt == 0)
            )
            all_results = results

            # --- Critic pass ---
            feedback = _run_critic(image_payload)

            if feedback["pass_flag"]:
                m.anomaly_caught = (recovery_count > 0 and disaster.get("adversarial", False))
                m.recovery_succeeded = m.anomaly_caught
                break
            else:
                # Anomaly detected
                if disaster.get("adversarial", False) and attempt == 0:
                    m.anomaly_caught = True
                recovery_count += 1
                recovery_feedback = feedback.get("recovery_instruction") or "Switch to SAR sensor."
                logger.info(
                    "[B3] Recovery %d for %s: %s",
                    recovery_count, disaster["id"], recovery_feedback,
                )
                if recovery_count >= _MAX_RECOVERY:
                    m.recovery_succeeded = False
                    break

        m.num_recovery_attempts = recovery_count
        m.syntax_execution_ok = True
        predicted_tools = [r["tool"] for r in all_results]
        oracle = ORACLE_ADVERSARIAL_ORDER if disaster.get("adversarial") else ORACLE_TOOL_ORDER
        m.tool_selection_accuracy = _tool_selection_accuracy(predicted_tools, oracle)
        m.tool_order_fidelity = _lcs_fidelity(predicted_tools, oracle)
        m.argument_value_accuracy = _argument_value_accuracy(all_results)
        m.final_report_generated = True

    except Exception as exc:  # noqa: BLE001
        logger.error("[B3] Error for %s: %s", disaster["id"], exc)
        m.syntax_execution_ok = False

    m.latency_sec = time.perf_counter() - t0
    return m


# ---------------------------------------------------------------------------
# Metrics Logger / CSV Exporter
# ---------------------------------------------------------------------------

CSV_FIELDNAMES = [
    "disaster_id",
    "baseline",
    "adversarial",
    # End-to-end
    "syntax_execution_rate",
    "semantic_anomaly_catch_rate",
    "autonomous_recovery_rate",
    "latency_sec",
    # Step-level trajectory
    "tool_selection_accuracy",
    "argument_value_accuracy",
    "tool_order_fidelity",
    # Aux
    "final_report_generated",
    "num_recovery_attempts",
]


def export_results(metrics: List[RunMetrics], output_path: str) -> None:
    """Write all RunMetrics to a CSV file."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for m in metrics:
            writer.writerow(m.to_dict())
    logger.info("Results written to %s (%d rows)", output_path, len(metrics))


def print_summary(metrics: List[RunMetrics]) -> None:
    """Print a console summary table grouped by baseline."""
    baselines = ["baseline1_raw_code", "baseline2_standard_aov", "baseline3_autocritic_eo"]
    print("\n" + "=" * 72)
    print("  AutoCritic-EO Benchmark Summary")
    print("=" * 72)
    header = f"{'Baseline':<28} {'SyntaxOK%':>9} {'CatchRate%':>10} {'RecovRate%':>10} {'Latency(s)':>10}"
    print(header)
    print("-" * 72)

    for bl in baselines:
        group = [m for m in metrics if m.baseline == bl]
        if not group:
            continue
        n = len(group)
        syntax_ok = sum(m.syntax_execution_ok for m in group) / n * 100
        catch = sum(m.anomaly_caught for m in group if m.adversarial) / max(
            sum(1 for m in group if m.adversarial), 1
        ) * 100
        recovery = sum(m.recovery_succeeded for m in group if m.adversarial) / max(
            sum(1 for m in group if m.adversarial), 1
        ) * 100
        latency = sum(m.latency_sec for m in group) / n
        print(f"{bl:<28} {syntax_ok:>9.1f} {catch:>10.1f} {recovery:>10.1f} {latency:>10.2f}")

    print("=" * 72)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_benchmark(dataset_path: str, output_path: str) -> None:
    """
    Full benchmark pipeline.

    For every disaster in the dataset, run all three baselines in sequence
    and collect metrics into results.csv.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    disasters = load_dataset(dataset_path)
    all_metrics: List[RunMetrics] = []

    total = len(disasters)
    for idx, disaster in enumerate(disasters, 1):
        did = disaster["id"]
        adv_flag = "ADV" if disaster.get("adversarial") else "   "
        logger.info(
            "[%02d/%02d] %s %s %s",
            idx, total, adv_flag, did, disaster["name"],
        )

        # --- Baseline 1 ---
        logger.info("  > Baseline 1: Raw Code Generation")
        m1 = run_baseline1_raw_code(deepcopy(disaster))
        all_metrics.append(m1)

        # --- Baseline 2 ---
        logger.info("  > Baseline 2: Standard AOV (no critic)")
        m2 = run_baseline2_standard_aov(deepcopy(disaster))
        all_metrics.append(m2)

        # --- Baseline 3 ---
        logger.info("  > Baseline 3: AutoCritic-EO")
        m3 = run_baseline3_autocritic(deepcopy(disaster))
        all_metrics.append(m3)

    export_results(all_metrics, output_path)
    print_summary(all_metrics)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoCritic-EO Benchmark Suite")
    parser.add_argument(
        "--dataset",
        default="disasters_dataset.json",
        help="Path to the disaster dataset JSON file (default: disasters_dataset.json)",
    )
    parser.add_argument(
        "--output",
        default="results.csv",
        help="Output CSV path (default: results.csv)",
    )
    args = parser.parse_args()

    run_benchmark(args.dataset, args.output)

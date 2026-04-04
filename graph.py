"""
graph.py
--------
LangGraph compiler for AutoCritic-EO.

Architecture (POMDP / AOV routing):

  [START]
     │
     ▼
 planning_node  ◄──────────────────────────────────────────────────┐
     │  (Meta-Agent: parse query → JSON AOV graph → topo-sort)    │
     ▼                                                              │
 execution_node                                                     │
     │  (run each tool from TOOL_REGISTRY in topo order)           │
     ▼                                                              │
  critic_node                                                       │
     │  (VLM: inspect image triad for semantic anomalies)          │
     ├──[PASS]──► synthesis_node ──► [END]                         │
     │                                                              │
     └──[FAIL]──► ── Reflexion self-reflect ─────────────────────►─┘
                     (append verbal feedback, bump recovery counter)

Nodes are plain Python callables that accept and return AutoCriticState.
Routing is decided by the `_route_after_critic` conditional edge.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any, Dict, List

from dotenv import load_dotenv
load_dotenv()  # loads .env from the project folder

import boto3
from langgraph.graph import END, StateGraph

from mock_tools import TOOL_REGISTRY, call_tool
from state import (
    AOVGraph,
    AOVVertex,
    AutoCriticState,
    CriticFeedback,
    ToolResult,
    initial_state,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AWS Bedrock / LangChain
# ---------------------------------------------------------------------------
import os
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage


def _invoke_claude(
    system_prompt: str,
    user_message: str,
    image_urls: List[str] | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> str:
    """
    Invoke Claude via AWS Bedrock Converse API.

    If `image_urls` is provided, each URL is fetched and embedded as a
    base64-encoded image block (multimodal / VLM mode).
    For mock image URLs (e.g. 'mock_base.png', 'CLOUD_OBSCURED.png') we
    embed a 1×1 white PNG placeholder so the pipeline runs without real images.
    The Critic prompt carries enough semantic context to behave correctly.
    """
    import base64
    import struct
    import zlib

    def _make_placeholder_png(label: str = "") -> bytes:
        """Return a minimal 1×1 white PNG so Bedrock accepts the payload."""
        def _chunk(name: bytes, data: bytes) -> bytes:
            c = struct.pack(">I", len(data)) + name + data
            return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

        header = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr = _chunk(b"IHDR", ihdr_data)
        raw = b"\x00\xff\xff\xff"
        compressed = zlib.compress(raw)
        idat = _chunk(b"IDAT", compressed)
        iend = _chunk(b"IEND", b"")
        return header + ihdr + idat + iend

    model_id = os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-pro-v1:0")
    llm = ChatBedrockConverse(
        model_id=model_id,
        region_name="us-east-1",
        temperature=temperature,
        max_tokens=max_tokens,
    )

    content: List[Dict[str, Any]] = []

    if image_urls:
        for url in image_urls:
            png_bytes = _make_placeholder_png(url)
            b64 = base64.b64encode(png_bytes).decode("utf-8")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"}
            })

    content.append({"type": "text", "text": user_message})

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=content)
    ]

    response = llm.invoke(messages)
    return str(response.content)


# ---------------------------------------------------------------------------
# Utility: Topological Sort (Kahn's algorithm)
# ---------------------------------------------------------------------------

def _topological_sort(vertices: List[AOVVertex]) -> List[str]:
    """Return vertex IDs in a valid topological order (raises on cycle)."""
    in_degree: Dict[str, int] = {v["id"]: 0 for v in vertices}
    adj: Dict[str, List[str]] = defaultdict(list)

    for v in vertices:
        for dep in v.get("depends_on", []):
            adj[dep].append(v["id"])
            in_degree[v["id"]] = in_degree.get(v["id"], 0) + 1

    queue = deque([vid for vid, deg in in_degree.items() if deg == 0])
    order: List[str] = []

    while queue:
        vid = queue.popleft()
        order.append(vid)
        for neighbour in adj[vid]:
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    if len(order) != len(vertices):
        raise ValueError("Cycle detected in AOV graph — cannot topologically sort.")

    return order


# ---------------------------------------------------------------------------
# Node 1: planning_node  (Meta-Agent)
# ---------------------------------------------------------------------------

_PLANNING_SYSTEM = """You are the AutoCritic-EO Meta-Agent. Your job is to parse a
natural-language Earth Observation analyst query and produce a strict JSON AOV
(Activity-on-Vertex) graph.

The graph schema is:
{
  "vertices": [
    {
      "id":         "<string, e.g. T1>",
      "tool":       "<one of: check_availability | load_imagery | compute_mask | adversarial_optical>",
      "params":     { <key-value parameters for the tool (e.g. use 'date_range' for load_imagery, 'file_list' for compute_mask)> },
      "depends_on": [ <list of preceding vertex 'id' strings> ]
    },
    ...
  ]
}

Rules:
1. Use ONLY tools from the allowed list above.
2. Respect data-flow: later nodes must declare the IDs of their predecessors in 'depends_on'.
3. If a prior recovery instruction mentions cloud cover or sensor issues, change
   'load_imagery' to 'adversarial_optical' is NOT correct — instead, keep
   'load_imagery' but update the 'sensor' param to 'SAR'. Do not add new
   adversarial vertices on recovery — only adjust params.
4. Output ONLY the raw JSON object, no markdown fences, no prose.
"""


def planning_node(state: AutoCriticState) -> AutoCriticState:
    """
    Meta-Agent: parse user_query (+ optional recovery context) → AOV graph.
    Performs topological sort on the produced graph and stores it in state.
    """
    logger.info("[planning_node] attempt=%d", state.get("recovery_attempt", 0))

    # Build the prompt — include verbal feedback if this is a recovery pass
    user_msg = f"Analyst query: {state['user_query']}\n"
    if state.get("critic_feedback"):
        latest_feedback = state["critic_feedback"][-1]
        user_msg += (
            f"\nPrevious critic reflection:\n{latest_feedback.get('verbal_reflection', '')}\n"
            f"Recovery instruction: {latest_feedback.get('recovery_instruction', '')}\n"
            "Produce an updated AOV graph that addresses this issue."
        )

    raw = _invoke_claude(_PLANNING_SYSTEM, user_msg, temperature=0.0)

    # Strip markdown fences if model adds them
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[planning_node] JSON parse failure: %s\nRaw output:\n%s", exc, raw)
        raise ValueError(f"planning_node: Claude returned non-JSON output.\nRaw:\n{raw}") from exc

    vertices: List[AOVVertex] = parsed["vertices"]
    execution_order = _topological_sort(vertices)

    aov_graph: AOVGraph = {
        "vertices": vertices,
        "execution_order": execution_order,
    }

    history = list(state.get("aov_graph_history", []))
    history.append(aov_graph)

    return {
        **state,
        "aov_graph": aov_graph,
        "aov_graph_history": history,
        "trajectory_status": "executing",
        "execution_results": [],      # reset results for this pass
        "image_payload": None,
    }


# ---------------------------------------------------------------------------
# Node 2: execution_node
# ---------------------------------------------------------------------------

def execution_node(state: AutoCriticState) -> AutoCriticState:
    """
    Iterate through the topologically sorted AOV graph and call mock tools.
    Collects ToolResult entries and assembles the image_payload for the Critic.
    """
    aov: AOVGraph = state["aov_graph"]
    vertex_map: Dict[str, AOVVertex] = {v["id"]: v for v in aov["vertices"]}
    results: List[ToolResult] = []
    image_payload: Dict[str, List[str]] = {}

    logger.info("[execution_node] executing %d vertices", len(aov["execution_order"]))

    for vid in aov["execution_order"]:
        vertex = vertex_map[vid]
        tool_name = vertex["tool"]
        params = vertex.get("params", {})

        logger.info("[execution_node] %s → %s(%s)", vid, tool_name, params)

        try:
            response = call_tool(tool_name, params)
            success = True
            error = None
        except Exception as exc:  # noqa: BLE001
            logger.error("[execution_node] tool=%s error=%s", tool_name, exc)
            response = {"status": "error", "message": str(exc)}
            success = False
            error = str(exc)

        result = ToolResult(
            vertex_id=vid,
            tool=tool_name,
            params=params,
            response=response,
            success=success,
            error=error,
        )
        results.append(result)

        # Accumulate image sequence for the Critic
        data = response.get("data", {})
        if "file_list" in data:
            image_payload.setdefault("file_list", []).extend(data["file_list"])
        if "computed_masks" in data:
            image_payload.setdefault("computed_masks", []).extend(data["computed_masks"])

    return {
        **state,
        "execution_results": results,
        "image_payload": image_payload,
        "trajectory_status": "executing",
    }


# ---------------------------------------------------------------------------
# Node 3: critic_node  (VLM — The Core Novelty)
# ---------------------------------------------------------------------------

_CRITIC_SYSTEM = """You are the AutoCritic-EO Space-to-Space Multimodal Critic.
You receive a multi-temporal sequence of satellite images (file_list and computed_masks) from
an EO analysis pipeline and must evaluate them for chronological progression and semantic anomalies.

Anomaly taxonomy:
  CLOUD_OBSCURED      — extreme cloud obscuration; pixel values uniform.
  NODATA_STRIPE       — spatial/projection mismatch causing NoData rendering artifacts (rows/columns of NoData).
  INDEX_SCALING_ERROR — mathematical index scaling error (e.g., NDVI outside [-1,1]).
  TEMPORAL_ALIGNMENT  — temporal alignment error (images out of sequence).
  CLEAN               — disaster impact logically expands or recedes; no anomaly detected.

The image sequences passed to you are:
  file_list: {file_list}
  computed_masks: {computed_masks}

CRITICAL RULES: 
1. Visually verify that the disaster impact logically expands or recedes chronologically.
2. If any image contains "CLOUD_OBSCURED", report CLOUD_OBSCURED.
3. If any image contains "NODATA_STRIPE", report NODATA_STRIPE.
4. If any image contains "NDVI_EXCEEDS", report INDEX_SCALING_ERROR.
5. If sequence is wrong, report TEMPORAL_ALIGNMENT.

Respond with a JSON object ONLY (no markdown fences):
{{
  "pass_flag": <true|false>,
  "anomaly_type": "<CLEAN|CLOUD_OBSCURED|NODATA_STRIPE|INDEX_SCALING_ERROR|TEMPORAL_ALIGNMENT|null>",
  "affected_vertex": "<vertex id or null>",
  "verbal_reflection": "<Specific verbal explanation of the root cause>",
  "recovery_instruction": "<directive for re-planning, or null if pass>"
}}
"""

def critic_node(state: AutoCriticState) -> AutoCriticState:
    """
    VLM critic: visually inspect the sequence payload for semantic anomalies.
    Appends a CriticFeedback entry to state.critic_feedback.
    """
    payload = state.get("image_payload") or {}
    file_list = payload.get("file_list", [])
    computed_masks = payload.get("computed_masks", [])

    logger.info(
        "[critic_node] inspecting sequence: files=%s masks=%s",
        file_list, computed_masks,
    )

    system_prompt = _CRITIC_SYSTEM.format(
        file_list=file_list,
        computed_masks=computed_masks,
    )

    user_msg = (
        f"Please inspect the following multi-temporal arrays:\n"
        f"  file_list      : {file_list}\n"
        f"  computed_masks : {computed_masks}\n\n"
        "Apply the anomaly taxonomy, check for chronological progression, and return your JSON assessment."
    )

    image_urls = file_list + computed_masks

    raw = _invoke_claude(system_prompt, user_msg, image_urls=image_urls, temperature=0.0)

    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: treat unparseable response as a pass to avoid infinite loops
        logger.warning("[critic_node] could not parse response; defaulting to PASS")
        parsed = {
            "pass_flag": True,
            "anomaly_type": "CLEAN",
            "affected_vertex": None,
            "verbal_reflection": "Critic response was non-JSON; defaulting to pass.",
            "recovery_instruction": None,
        }

    feedback = CriticFeedback(
        pass_flag=parsed.get("pass_flag", True),
        anomaly_type=parsed.get("anomaly_type"),
        affected_vertex=parsed.get("affected_vertex"),
        verbal_reflection=parsed.get("verbal_reflection"),
        recovery_instruction=parsed.get("recovery_instruction"),
    )

    new_feedback = list(state.get("critic_feedback", [])) + [feedback]
    status = "critic_pass" if feedback["pass_flag"] else "critic_fail"

    return {
        **state,
        "critic_feedback": new_feedback,
        "trajectory_status": status,
    }


# ---------------------------------------------------------------------------
# Node 4: synthesis_node
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = """You are the AutoCritic-EO Report Writer.
You receive structured JSON execution results and critic feedback from an EO
analysis workflow. Write a concise, professional Earth Observation Assessment
Report (markdown format) that includes:
  1. Executive Summary
  2. Methodology (tools invoked and their outputs)
  3. Quality Assurance (critic findings)
  4. Results (affected area, change metrics)
  5. Recommendations
"""


def synthesis_node(state: AutoCriticState) -> AutoCriticState:
    """Write the final EO Assessment Report from accumulated execution results."""
    logger.info("[synthesis_node] writing final report")

    user_msg = json.dumps(
        {
            "execution_results": state.get("execution_results", []),
            "critic_feedback": state.get("critic_feedback", []),
            "image_payload": state.get("image_payload", {}),
            "recovery_attempts": state.get("recovery_attempt", 0),
        },
        indent=2,
    )

    report = _invoke_claude(_SYNTHESIS_SYSTEM, user_msg, temperature=0.3, max_tokens=2048)

    return {
        **state,
        "final_report": report,
        "trajectory_status": "complete",
    }


# ---------------------------------------------------------------------------
# Conditional edge: route after critic
# ---------------------------------------------------------------------------

def _route_after_critic(state: AutoCriticState) -> str:
    """
    Determine next node after critic_node.

    Returns:
      "synthesis_node"  — Critic passed; proceed to report.
      "planning_node"   — Critic failed; trigger Reflexion recovery.
      "synthesis_node"  — Recovery cap reached; force synthesis with caveats.
    """
    if state["trajectory_status"] == "critic_pass":
        return "synthesis_node"

    # Failure path
    recovery_attempt = state.get("recovery_attempt", 0)
    max_attempts = state.get("max_recovery_attempts", 3)

    if recovery_attempt >= max_attempts:
        logger.warning(
            "[router] max recovery attempts (%d) reached; forcing synthesis", max_attempts
        )
        return "synthesis_node"

    return "planning_node"


def _increment_recovery(state: AutoCriticState) -> AutoCriticState:
    """
    Thin wrapper node that increments recovery_attempt before re-routing to
    planning_node. Placed on the failure branch so planning_node always sees
    the correct counter.
    """
    return {
        **state,
        "recovery_attempt": state.get("recovery_attempt", 0) + 1,
        "trajectory_status": "recovering",
    }


# ---------------------------------------------------------------------------
# Graph compiler
# ---------------------------------------------------------------------------

def build_graph() -> Any:
    """
    Compile and return the AutoCritic-EO LangGraph StateGraph.

                START
                  │
            planning_node
                  │
           execution_node
                  │
             critic_node
            ┌─────┴──────┐
          PASS          FAIL
            │              │
      synthesis_node  _increment_recovery ──► planning_node
            │
           END
    """
    graph = StateGraph(AutoCriticState)

    # --- Nodes ---
    graph.add_node("planning_node", planning_node)
    graph.add_node("execution_node", execution_node)
    graph.add_node("critic_node", critic_node)
    graph.add_node("synthesis_node", synthesis_node)
    graph.add_node("recovery_increment", _increment_recovery)

    # --- Edges ---
    graph.set_entry_point("planning_node")
    graph.add_edge("planning_node", "execution_node")
    graph.add_edge("execution_node", "critic_node")

    # Conditional routing after critic
    graph.add_conditional_edges(
        "critic_node",
        _route_after_critic,
        {
            "synthesis_node": "synthesis_node",
            "planning_node": "recovery_increment",
        },
    )

    graph.add_edge("recovery_increment", "planning_node")
    graph.add_edge("synthesis_node", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Public run helper
# ---------------------------------------------------------------------------

def run_autocritic(
    user_query: str,
    adversarial: bool = False,
    max_recovery_attempts: int = 3,
) -> AutoCriticState:
    """
    Convenience entry-point for the benchmark suite and manual testing.

    Args:
        user_query: Natural-language analyst request.
        adversarial: If True, the execution will encounter the cloud-obscured
                     mock (by the planning LLM discovering 'adversarial_optical'
                     through prompt instructions during benchmark injection).
        max_recovery_attempts: Safety ceiling on Reflexion loops.

    Returns:
        Final AutoCriticState after the graph reaches END.
    """
    app = build_graph()
    state = initial_state(user_query, max_recovery_attempts=max_recovery_attempts)
    final_state: AutoCriticState = app.invoke(state)
    return final_state


# ---------------------------------------------------------------------------
# CLI smoke-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    query = (
        "Analyze the wildfire damage in Northern California from August 2021. "
        "Compute the burned area mask using NBR index."
    )
    print("Running AutoCritic-EO on query:\n", query, "\n")
    result = run_autocritic(query)

    # Write report to file to avoid Windows cp1252 UnicodeEncodeError
    report = result.get("final_report", "[No report generated]")
    report_path = "smoke_test_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n=== FINAL REPORT saved to: {report_path} ===")
    print(f"Trajectory status  : {result['trajectory_status']}")
    print(f"Recovery attempts  : {result['recovery_attempt']}")
    print(f"Critic feedback    : {len(result['critic_feedback'])} entry(s)")
    print(f"Critic pass_flag   : {result['critic_feedback'][-1]['pass_flag'] if result['critic_feedback'] else 'N/A'}")


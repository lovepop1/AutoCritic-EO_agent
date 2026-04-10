"""
state.py
--------
Defines the typed State dictionary for the AutoCritic-EO POMDP environment.

The state flows through the LangGraph nodes and accumulates:
  - user_query          : raw natural language input from the analyst
  - aov_graph           : the structured Activity-on-Vertex (AOV) graph G=(V,E,A,O)
                          produced (and re-produced on recovery) by the planning_node
  - execution_results   : per-tool call results collected by execution_node
  - critic_feedback     : structured feedback from the critic_node (list of CriticFeedback)
  - trajectory_status   : one of "planning" | "executing" | "critic_pass" | "critic_fail"
                          | "recovering" | "complete" | "error"
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


# ---------------------------------------------------------------------------
# Sub-schemas (for type-safety in code; also validated at runtime in nodes)
# ---------------------------------------------------------------------------

class AOVVertex(TypedDict):
    """A single task vertex in the AOV graph."""
    id: str                    # e.g. "T1"
    tool: str                  # tool name in mock_tools registry
    params: Dict[str, Any]     # O — explicit API parameters
    depends_on: List[str]      # E — list of predecessor vertex IDs


class AOVGraph(TypedDict):
    """G = (V, E, A, O) — Activity-on-Vertex graph."""
    vertices: List[AOVVertex]   # V — task nodes
    execution_order: List[str]  # topologically sorted vertex IDs


class ToolResult(TypedDict, total=False):
    """Result produced by a single mock-tool invocation."""
    vertex_id: str
    tool: str
    params: Dict[str, Any]
    response: Dict[str, Any]   # raw JSON response from the mock tool
    success: bool
    error: Optional[str]
    skipped: bool


class CriticFeedback(TypedDict):
    """Structured output from the critic_node VLM inspection."""
    pass_flag: bool                        # True → all images valid
    anomaly_type: Optional[str]            # e.g. "CLOUD_OBSCURED", "NODATA_STRIPE"
    affected_vertex: Optional[str]         # vertex whose output triggered the flag
    verbal_reflection: Optional[str]       # Reflexion reasoning string
    recovery_instruction: Optional[str]    # directive for the re-planning step


# ---------------------------------------------------------------------------
# Primary State definition
# ---------------------------------------------------------------------------

TrajectoryStatus = Literal[
    "planning",
    "executing",
    "critic_pass",
    "critic_fail",
    "recovering",
    "complete",
    "error",
]


class AutoCriticState(TypedDict, total=False):
    """
    The single, mutable state object passed between every LangGraph node.

    Fields marked Optional are absent on the very first pass and populated
    progressively as the graph executes.
    """

    # --- Input ---
    user_query: str                          # Raw analyst query

    # --- Planning artefacts ---
    aov_graph: Optional[AOVGraph]            # AOV graph from planning_node
    aov_graph_history: List[AOVGraph]        # All AOV iterations (for recovery audit)

    # --- Execution artefacts ---
    execution_results: List[ToolResult]      # Ordered results per vertex
    image_payload: Optional[Dict[str, List[str]]]  # {file_list, computed_masks}

    # --- Critic artefacts ---
    critic_feedback: List[CriticFeedback]    # All critic passes (for paper metrics)

    # --- Control flow ---
    trajectory_status: TrajectoryStatus
    recovery_attempt: int                    # How many times re-planning was triggered
    max_recovery_attempts: int               # Safety ceiling (default 3)

    # --- Output ---
    final_report: Optional[str]             # Written by synthesis_node


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def initial_state(user_query: str, max_recovery_attempts: int = 3) -> AutoCriticState:
    """Return a fresh state for a new analyst query."""
    return AutoCriticState(
        user_query=user_query,
        aov_graph=None,
        aov_graph_history=[],
        execution_results=[],
        image_payload=None,
        critic_feedback=[],
        trajectory_status="planning",
        recovery_attempt=0,
        max_recovery_attempts=max_recovery_attempts,
        final_report=None,
    )

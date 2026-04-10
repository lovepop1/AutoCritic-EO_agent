from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from graph import build_graph
from state import AutoCriticState, initial_state

load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class UserRequest(BaseModel):
    query: str


GRAPH = build_graph()


@app.post("/api/run_agent")
async def run_agent(request: UserRequest) -> dict:
    state: AutoCriticState = initial_state(request.query)
    final_state = GRAPH.invoke(state)
    return {
        "trajectory": final_state.get("aov_graph"),
        "report": final_state.get("critic_feedback"),
        "images": final_state.get("execution_results"),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)

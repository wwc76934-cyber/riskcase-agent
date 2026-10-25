"""Offline lesson: distinguish local execution from observation feedback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.agent.react_agent import Agent
from app.llm.fake_client import FakeLLMClient, make_tool_call_message
from app.tools.basic_tools import BasicTools


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--missing",
        action="store_true",
        help="Read a missing fixture to observe tool failure feedback.",
    )
    args = parser.parse_args()
    # Restrict file tools to this lesson's examples directory.
    root = Path(__file__).resolve().parents[2] / "examples"
    filename = "missing-protocol-note.txt" if args.missing else "protocol-note.txt"
    fake = FakeLLMClient([
        make_tool_call_message("call_read_note", "read_file", {"path": filename}),
        {
            "role": "assistant",
            "content": "预设回复：本轮协议演示结束。这句话不证明读取成功。",
        },
    ])
    agent = Agent(fake, BasicTools(root))
    result = agent.run("读取教学夹具，并结束本轮。")

    print("Fake responses are predefined; no API request is sent.")
    for number, request in enumerate(fake.requests, start=1):
        roles = [message["role"] for message in request["messages"]]
        print(f"request {number} roles: {' -> '.join(roles)}")

    # The second request is the evidence that the observation reached the client.
    observation = fake.requests[1]["messages"][-1]
    tool_result = json.loads(observation["content"])
    print(f"tool_call_id: {observation['tool_call_id']}")
    print("observation: " + json.dumps(tool_result, ensure_ascii=False, indent=2))
    print(f"agent status: {result.status}")
    print(f"tool success: {tool_result['ok']}")
    print(f"model calls: {fake.call_count}; iterations: {result.iterations}")
    print(result.content)
    # The missing-file lesson is intentional, so the demo exits normally.
    # A production task needs its own business validation and exit semantics.


if __name__ == "__main__":
    main()

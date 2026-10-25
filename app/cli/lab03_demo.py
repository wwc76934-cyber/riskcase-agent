from __future__ import annotations

import getpass
import os
from pathlib import Path
from uuid import uuid4

import typer
from rich.console import Console

from app.agent.react_agent import Agent
from app.llm.fake_client import (
    FakeLLMClient,
    build_lab03_fake_responses,
)
from app.llm.glm_client import (
    DEFAULT_GLM_URL,
    GLMClient,
    LLMError,
)
from app.tools.basic_tools import BasicTools


console = Console()


def create_live_client() -> GLMClient:
    """Create a live client from environment configuration."""
    model = os.environ.get("GLM_MODEL")

    if not model:
        console.print(
            "[red]GLM_MODEL is not configured.[/red]"
        )
        raise typer.Exit(code=2)

    base_url = os.environ.get(
        "GLM_BASE_URL",
        DEFAULT_GLM_URL,
    )

    api_key = os.environ.get("GLM_API_KEY")

    if not api_key:
        api_key = getpass.getpass("GLM API Key: ")

    return GLMClient(
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


def run_live_chat() -> None:
    """Run one real model request without tools."""
    client = create_live_client()
    prompt = typer.prompt("You")

    try:
        message = client.chat([
            {
                "role": "user",
                "content": prompt,
            }
        ])
    except LLMError as exc:
        console.print(
            f"[red]GLM request failed: {exc}[/red]"
        )
        raise typer.Exit(code=1) from exc

    console.print("[bold green]Assistant:[/bold green]")
    console.print(message["content"])


def print_trace(
    messages: list[dict[str, object]],
) -> None:
    """Print the observable Agent trajectory."""
    console.print("\n[bold cyan]Trace[/bold cyan]")

    for index, message in enumerate(
        messages,
        start=1,
    ):
        role = message.get("role")

        if role == "system":
            console.print(
                f"{index}. [dim]system prompt[/dim]"
            )
            continue

        if role == "user":
            console.print(
                f"{index}. [blue]user[/blue]: "
                f"{message.get('content')}"
            )
            continue

        if role == "assistant":
            tool_calls = message.get("tool_calls")

            if isinstance(tool_calls, list) and tool_calls:
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue

                    function = tool_call.get("function")

                    if not isinstance(function, dict):
                        continue

                    console.print(
                        f"{index}. [magenta]assistant "
                        f"tool request[/magenta]: "
                        f"{function.get('name')} "
                        f"{function.get('arguments')}"
                    )
            else:
                console.print(
                    f"{index}. [green]assistant[/green]: "
                    f"{message.get('content')}"
                )

            continue

        if role == "tool":
            console.print(
                f"{index}. [yellow]tool[/yellow] "
                f"{message.get('name')} "
                f"id={message.get('tool_call_id')}: "
                f"{message.get('content')}"
            )


def run_fake_agent(
    trace: bool,
) -> None:
    """Run the deterministic four-tool demonstration."""
    report_name = (
        f"report-{uuid4().hex[:8]}.txt"
    )

    fake = FakeLLMClient(
        build_lab03_fake_responses(report_name)
    )

    tools = BasicTools("demo_lab03")

    agent = Agent(
        client=fake,
        tools=tools,
        max_iterations=8,
    )

    goal = (
        "列出目录，读取 notes.txt，写一份简短报告，"
        "执行 echo lab03-shell-ok，最后汇报真实结果。"
    )

    console.print(
        "[bold yellow]FAKE mode:[/bold yellow] "
        "model decisions are predefined; "
        "local tools are real."
    )

    result = agent.run(goal)

    console.print(
        f"[bold]status:[/bold] {result.status}"
    )
    console.print(
        f"[bold]iterations:[/bold] "
        f"{result.iterations}"
    )
    console.print(
        f"[bold]model calls:[/bold] "
        f"{fake.call_count}"
    )
    console.print(
        f"[bold]final:[/bold] {result.content}"
    )

    report_file = (
        Path("demo_lab03") / report_name
    )

    console.print(
        f"[bold]report exists:[/bold] "
        f"{report_file.exists()}"
    )
    console.print(
        f"[bold]report path:[/bold] "
        f"{report_file}"
    )

    if trace:
        print_trace(result.messages)


def run_live_agent(
    trace: bool,
) -> None:
    """Run the real GLM model with the four local tools."""
    client = create_live_client()
    tools = BasicTools("demo_lab03")

    agent = Agent(
        client=client,
        tools=tools,
        max_iterations=8,
    )

    report_name = (
        f"report-{uuid4().hex[:8]}.txt"
    )

    goal = (
        "请严格完成以下任务，并使用提供的工具取得真实结果：\n"
        "1. 使用 list_dir 列出当前教学目录。\n"
        "2. 使用 read_file 读取 notes.txt。\n"
        "3. 根据读取到的内容写一份简短报告，"
        f"使用 write_file 保存为 {report_name}。\n"
        "4. 使用 execute_command 执行完全匹配的命令 "
        "echo lab03-shell-ok。\n"
        "5. 完成上述操作后，汇报报告路径、"
        "文件写入结果和命令的真实输出。\n"
        "不要只描述将要执行的操作，必须实际调用四种工具。"
    )

    console.print(
        "[bold green]LIVE Agent mode[/bold green]"
    )
    console.print(
        f"model: {client.model}"
    )
    console.print(
        f"endpoint: {client.base_url}"
    )
    console.print(
        f"target report: {report_name}"
    )

    try:
        result = agent.run(goal)
    except LLMError as exc:
        console.print(
            f"[red]Agent stopped: {exc}[/red]"
        )
        raise typer.Exit(code=1) from exc

    console.print(
        f"[bold]status:[/bold] {result.status}"
    )
    console.print(
        f"[bold]iterations:[/bold] "
        f"{result.iterations}"
    )
    console.print(
        f"[bold]final:[/bold] {result.content}"
    )

    report_file = (
        Path("demo_lab03") / report_name
    )

    console.print(
        f"[bold]report exists:[/bold] "
        f"{report_file.exists()}"
    )
    console.print(
        f"[bold]report path:[/bold] "
        f"{report_file}"
    )

    if trace:
        print_trace(result.messages)

    while True:
        follow_up = typer.prompt(
            "\nFollow-up (/exit to quit)"
        )

        if follow_up.strip().lower() == "/exit":
            console.print("Live Agent session ended.")
            break

        try:
            follow_up_result = agent.run(follow_up)
        except LLMError as exc:
            console.print(
                f"[red]Agent stopped: {exc}[/red]"
            )
            break

        console.print(
            f"[bold]status:[/bold] "
            f"{follow_up_result.status}"
        )
        console.print(
            f"[bold]iterations:[/bold] "
            f"{follow_up_result.iterations}"
        )
        console.print(
            f"[bold]final:[/bold] "
            f"{follow_up_result.content}"
        )

        if trace:
            print_trace(
                follow_up_result.messages
            )


def main(
    live: bool = typer.Option(
        False,
        "--live",
        help="Use a real GLM-compatible API.",
    ),
    fake: bool = typer.Option(
        False,
        "--fake",
        help="Use deterministic model responses.",
    ),
    chat_only: bool = typer.Option(
        False,
        "--chat-only",
        help="Run one live conversation without tools.",
    ),
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Show the observable Agent trajectory.",
    ),
) -> None:
    """Run the Lab03 GLM and Agent demonstration."""

    if live == fake:
        console.print(
            "[red]Choose exactly one of "
            "--live or --fake.[/red]"
        )
        raise typer.Exit(code=2)

    if fake:
        if chat_only:
            console.print(
                "[red]--chat-only cannot be used "
                "with --fake.[/red]"
            )
            raise typer.Exit(code=2)

        run_fake_agent(trace)
        return

    if chat_only:
        run_live_chat()
        return

    run_live_agent(trace)


if __name__ == "__main__":
    typer.run(main)
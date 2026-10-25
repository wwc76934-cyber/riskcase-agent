import typer
from rich.console import Console

app = typer.Typer()
console = Console()


@app.command()
def hello(name: str = typer.Argument("Developer")) -> None:
    """Output a personalized welcome message."""
    console.print("[bold green]AI Coding Agent CLI v0.1[/bold green]")
    console.print(f"Hello, {name}! Welcome to AI Coding Agent CLI.")

@app.command()
def version() -> None:
    """Output the current application version."""
    console.print("AI Coding Agent CLI v0.1")

@app.command()
def help() -> None:
    """Show available commands and their descriptions."""
    console.print("[bold cyan]Available commands:[/bold cyan]")
    console.print("  hello [name]  - Output a personalized welcome message.")
    console.print("  version       - Output the current application version.")
    console.print("  help          - Show this help message.")

if __name__ == "__main__":
    app()

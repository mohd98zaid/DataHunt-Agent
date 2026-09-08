import argparse
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from datahunt import APP_NAME, __version__
from datahunt.db import run_migrations, RunRepository, TaskRepository, RecordRepository, ExportRepository
from datahunt.models import RunStatus, VerificationStatus
from datahunt.orchestrator import ResearchOrchestrator
from datahunt.tools import ExportTool

console = Console()

def cmd_migrate(args):
    console.print(f"[bold cyan]{APP_NAME}[/bold cyan] Running SQLite migrations...")
    applied = run_migrations()
    if applied:
        console.print(f"[bold green]✓[/bold green] Applied migrations: {', '.join(applied)}")
    else:
        console.print("[bold green]✓[/bold green] Database schema is up to date.")

def cmd_run(args):
    console.print(Panel(f"[bold cyan]{APP_NAME}[/bold cyan] Starting Research Run\n[italic]{args.query}[/italic]", border_style="cyan"))
    
    orch = ResearchOrchestrator()
    try:
        task, run = orch.create_task_and_run(
            request_text=args.query,
            max_records=args.max_records,
            freshness_days=args.freshness,
            output_format=args.format,
            allowed_domains=args.allowed_domains,
            blocked_domains=args.blocked_domains,
        )
        console.print(f"Task ID: [bold]{task.id}[/bold] | Run ID: [bold]{run.id}[/bold]")
        
        with console.status("[bold green]Executing research pipeline...[/bold green]"):
            result = orch.execute_run(run.id)

        status_color = "green" if result["status"] == "completed" else "yellow"
        console.print(f"\nRun Finished: [{status_color}][bold]{result['status'].upper()}[/bold][/{status_color}]")
        
        # Summary Table
        table = Table(title="Run Summary", border_style="cyan")
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_row("Verified Records", str(result["records_verified"]))
        table.add_row("Rejected Records", str(result["records_rejected"]))
        table.add_row("Duplicates Collapsed", str(result["records_duplicate"]))
        table.add_row("Pages Fetched", str(result["pages_fetched"]))
        table.add_row("Pages Failed", str(result["pages_failed"]))
        table.add_row("Export File", str(result.get("export_file") or "None"))
        if result.get("export_path"):
            table.add_row("Export Path", str(result.get("export_path")))
        console.print(table)

        if result.get("warnings"):
            console.print("\n[bold yellow]Warnings:[/bold yellow]")
            for w in result["warnings"]:
                console.print(f"  • {w}")

        console.print(f"\n[italic]{result['summary']}[/italic]\n")

    except Exception as e:
        console.print(f"[bold red]✗ Run failed:[/bold red] {e}")
        sys.exit(1)

def cmd_status(args):
    run_repo = RunRepository()
    task_repo = TaskRepository()
    export_repo = ExportRepository()
    
    run = run_repo.get_run(args.run_id)
    if not run:
        console.print(f"[bold red]Run {args.run_id} not found.[/bold red]")
        sys.exit(1)
        
    task = task_repo.get_task(run.task_id)
    exports = export_repo.list_exports_for_run(run.id)
    
    table = Table(title=f"Run Details: {run.id}", border_style="cyan")
    table.add_column("Property", style="bold")
    table.add_column("Value")
    table.add_row("Task ID", run.task_id)
    table.add_row("Query", task.request_text if task else "Unknown")
    table.add_row("Status", run.status.value.upper())
    table.add_row("Started At", run.started_at or "N/A")
    table.add_row("Finished At", run.finished_at or "N/A")
    table.add_row("Pages Fetched", str(run.counters.pages_fetched))
    table.add_row("Records Verified", str(run.counters.records_verified))
    table.add_row("Records Duplicate", str(run.counters.records_duplicate))
    if exports:
        table.add_row("Exports", ", ".join(e.file_name for e in exports))
    if run.error_message:
        table.add_row("Error", run.error_message)
    console.print(table)

def cmd_list(args):
    run_repo = RunRepository()
    runs = run_repo.list_recent_runs(limit=args.limit)
    if not runs:
        console.print("[dim]No research runs recorded yet.[/dim]")
        return
        
    table = Table(title=f"Recent {APP_NAME} Runs", border_style="cyan")
    table.add_column("Run ID", style="bold")
    table.add_column("Status")
    table.add_column("Pages")
    table.add_column("Verified")
    table.add_column("Created At")
    
    for r in runs:
        status_color = "green" if r.status == RunStatus.COMPLETED else "yellow" if r.status == RunStatus.PARTIAL else "red"
        table.add_row(
            r.id,
            f"[{status_color}]{r.status.value}[/{status_color}]",
            str(r.counters.pages_fetched),
            str(r.counters.records_verified),
            r.created_at[:19].replace("T", " ")
        )
    console.print(table)

def main():
    parser = argparse.ArgumentParser(prog="datahunt", description=f"{APP_NAME} - Autonomous Research AI Agent (v{__version__})")
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # migrate
    sub_migrate = subparsers.add_parser("migrate", help="Run SQLite database migrations")
    sub_migrate.set_defaults(func=cmd_migrate)

    # run
    sub_run = subparsers.add_parser("run", help="Start a new research run")
    sub_run.add_argument("query", type=str, help="Research query (e.g. 'Find 20 AI jobs in Dubai')")
    sub_run.add_argument("--max-records", type=int, default=50, help="Maximum records to retrieve")
    sub_run.add_argument("--freshness", type=int, default=7, help="Freshness window in days")
    sub_run.add_argument("--format", type=str, choices=["json", "csv", "xlsx"], default="json", help="Export format")
    sub_run.add_argument("--allowed-domains", nargs="*", default=None, help="Allowed domains")
    sub_run.add_argument("--blocked-domains", nargs="*", default=None, help="Blocked domains")
    sub_run.set_defaults(func=cmd_run)

    # status
    sub_status = subparsers.add_parser("status", help="Check run status")
    sub_status.add_argument("run_id", type=str, help="Research Run ID")
    sub_status.set_defaults(func=cmd_status)

    # list
    sub_list = subparsers.add_parser("list", help="List recent runs")
    sub_list.add_argument("--limit", type=int, default=20, help="Number of runs to show")
    sub_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

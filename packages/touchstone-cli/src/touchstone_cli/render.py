"""Rich-based renderers for CLI output."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from touchstone.qa.differ import EnvironmentDiff
from touchstone.qa.pr import PRImpactReport
from touchstone.qa.profiler import TableProfile
from touchstone.qa.test_gen import GeneratedExpectations
from touchstone.qa.validator import ValidationReport


def render_profile(console: Console, prof: TableProfile) -> None:
    console.print(Panel.fit(
        f"[bold]{prof.table.qualified()}[/bold]   {prof.row_count:,} rows",
        title="Table profile",
    ))

    t = Table(show_header=True, header_style="bold")
    t.add_column("Column"); t.add_column("Type"); t.add_column("Semantic")
    t.add_column("Null %", justify="right"); t.add_column("Distinct", justify="right")
    t.add_column("Top values")
    for col in prof.columns:
        null_pct = f"{col.null_rate * 100:.1f}" if col.null_rate is not None else "-"
        dist = f"{col.distinct_count:,}" if col.distinct_count is not None else "-"
        top = ", ".join(f"{v}({c})" for v, c in (col.top_values or [])[:3]) or "-"
        t.add_row(col.name, col.type, col.semantic_type, null_pct, dist, top)
    console.print(t)

    if prof.pii_summary:
        bits = ", ".join(f"{k}={v}" for k, v in prof.pii_summary.items())
        console.print(f"[yellow]PII detected in sample:[/yellow] {bits}")


def render_diff(console: Console, d: EnvironmentDiff) -> None:
    console.print(Panel.fit(
        f"[bold]{d.table.qualified()}[/bold]   {d.left} → {d.right}",
        title="Environment diff",
    ))
    if d.schema.added:
        console.print("[green]added columns:[/green] " +
                      ", ".join(f"{n} ({t})" for n, t in d.schema.added))
    if d.schema.removed:
        console.print("[red]removed columns:[/red] " +
                      ", ".join(f"{n} ({t})" for n, t in d.schema.removed))
    if d.schema.retyped:
        for n, lt, rt in d.schema.retyped:
            console.print(f"[yellow]retyped[/yellow] {n}: {lt} → {rt}")
    if d.row_count:
        rc = d.row_count
        arrow = "→" if rc.delta == 0 else ("↑" if rc.delta > 0 else "↓")
        console.print(
            f"\nrow counts: {rc.left_count:,} {arrow} {rc.right_count:,} "
            f"(Δ {rc.delta:+,}, {rc.delta_pct:+.2f}%)"
        )
        if rc.grouped:
            t = Table(show_header=True)
            t.add_column("group"); t.add_column("left", justify="right")
            t.add_column("right", justify="right"); t.add_column("Δ", justify="right")
            for k, l, r, delta in rc.grouped[:20]:
                t.add_row(str(k), f"{l:,}", f"{r:,}", f"{delta:+,}")
            console.print(t)
    if d.value:
        v = d.value
        console.print(
            f"\nvalue-diff (approx): only-left={v.rows_only_left:,}  "
            f"only-right={v.rows_only_right:,}  changed={v.rows_changed:,}"
        )


def render_validation_report(console: Console, report: ValidationReport) -> None:
    ok = sum(1 for r in report.results if r.passed)
    failed = len(report.results) - ok
    color = "green" if failed == 0 else "red"
    console.print(Panel.fit(
        f"[bold]{report.table}[/bold]  ({report.connection})\n"
        f"[{color}]{ok}/{len(report.results)} expectations passed[/{color}]",
        title="Data-quality report",
    ))
    t = Table(show_header=True)
    t.add_column("Expectation"); t.add_column("Observed"); t.add_column("Expected")
    t.add_column("Status")
    for r in report.results:
        status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        t.add_row(r.name, str(r.observed), str(r.expected), status)
    console.print(t)


def render_test_suggestions(console: Console, gen: GeneratedExpectations) -> None:
    console.print(Panel.fit(
        f"[bold]Suggested expectations for {gen.table}[/bold]\n\n{gen.to_yaml()}",
        title="Test suggestions",
    ))
    if gen.rationale:
        console.print(Text("\n".join(f"• {r}" for r in gen.rationale), style="dim"))


def render_pr_report(console: Console, repo: str, pr_num: int, report: PRImpactReport) -> None:
    console.print(Panel.fit(
        f"[bold]{repo} #{pr_num}[/bold] — {report.files_analyzed} file(s) analyzed",
        title="PR data-impact report",
    ))
    if report.tables:
        for tc in report.tables:
            color = {"created": "green", "dropped": "red", "mutated": "yellow"}[tc.kind]
            console.print(f"[{color}]{tc.kind}[/{color}] table: {tc.name}")
    if report.columns:
        t = Table(show_header=True)
        t.add_column("Table"); t.add_column("Column"); t.add_column("Change")
        t.add_column("Before"); t.add_column("After")
        for c in report.columns:
            t.add_row(c.table, c.column, c.kind, c.before or "-", c.after or "-")
        console.print(t)
    if report.downstream_risks:
        console.print("\n[yellow]Downstream risks:[/yellow]")
        for r in report.downstream_risks:
            console.print(f"  • {r}")
    if report.suggested_tests:
        console.print("\n[cyan]Suggested tests to add:[/cyan]")
        for s in report.suggested_tests:
            console.print(f"  • {s}")
    if report.parse_failures:
        console.print("\n[red]SQL parse failures:[/red]")
        for p in report.parse_failures:
            console.print(f"  • {p}")

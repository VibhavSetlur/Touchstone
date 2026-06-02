/**
 * Pull-request webhook handler.
 *
 * Flow:
 *   1. Decide whether this PR is interesting (touches SQL / dbt / migrations).
 *   2. Create a check-run in `in_progress` state.
 *   3. Spawn `touchstone pr --repo X --pr N --json` in the configured environment.
 *   4. Convert the JSON report into a Markdown comment + check-run conclusion.
 */

import type { Context } from "probot";
import { execa } from "execa";
import { formatPRReport } from "../lib/format-report.js";
import { PRReportSchema } from "../lib/schema.js";

const TOUCHSTONE_CLI = process.env.TOUCHSTONE_CLI ?? "touchstone";
const CHECK_RUN_NAME = process.env.CHECK_RUN_NAME ?? "touchstone";

const INTERESTING_PATTERNS = [
  /\.sql$/i,
  /^models\//,
  /^migrations\//,
  /^db\/migrate\//,
  /^prisma\/migrations\//,
  /dbt_project\.yml$/,
  /schema\.(rb|sql|prisma)$/i,
];

export async function handlePullRequest(
  context: Context<"pull_request.opened" | "pull_request.synchronize">,
): Promise<void> {
  const { owner, repo } = context.repo();
  const pr = context.payload.pull_request;
  const files = await context.octokit.paginate(
    context.octokit.pulls.listFiles,
    { owner, repo, pull_number: pr.number, per_page: 100 },
  );

  const interesting = files.filter((f) =>
    INTERESTING_PATTERNS.some((p) => p.test(f.filename)),
  );
  if (interesting.length === 0) {
    return;
  }

  const checkRun = await context.octokit.checks.create({
    owner, repo,
    name: CHECK_RUN_NAME,
    head_sha: pr.head.sha,
    status: "in_progress",
    started_at: new Date().toISOString(),
  });

  let conclusion: "success" | "neutral" | "failure" = "neutral";
  let summary = "Touchstone analyzed this PR but found nothing actionable.";
  let body = "";

  try {
    const { stdout } = await execa(TOUCHSTONE_CLI, [
      "pr",
      "--repo", `${owner}/${repo}`,
      "--pr", String(pr.number),
      "--json",
    ], { timeout: 5 * 60_000 });

    const parsed = PRReportSchema.safeParse(JSON.parse(stdout));
    if (!parsed.success) {
      conclusion = "neutral";
      summary = "Touchstone returned an unrecognized report shape.";
      body = "```\n" + parsed.error.toString() + "\n```";
    } else {
      const report = parsed.data;
      body = formatPRReport(report);
      if (report.downstream_risks.length > 0 || report.parse_failures.length > 0) {
        conclusion = "failure";
        summary = `${report.downstream_risks.length} risk(s), ${report.parse_failures.length} parse failure(s).`;
      } else if (report.columns.length > 0 || report.tables.length > 0) {
        conclusion = "neutral";
        summary = `${report.columns.length} column change(s), ${report.tables.length} table change(s).`;
      } else {
        conclusion = "success";
        summary = "No data-impact issues detected.";
      }
    }
  } catch (err: unknown) {
    conclusion = "failure";
    summary = "Touchstone CLI invocation failed.";
    body = "```\n" + (err instanceof Error ? err.message : String(err)) + "\n```";
  }

  await context.octokit.checks.update({
    owner, repo, check_run_id: checkRun.data.id,
    status: "completed",
    completed_at: new Date().toISOString(),
    conclusion,
    output: { title: "Touchstone — data-impact report", summary, text: body },
  });

  if (body) {
    await context.octokit.issues.createComment({
      owner, repo, issue_number: pr.number,
      body: `### Touchstone — data-impact report\n\n${body}`,
    });
  }
}

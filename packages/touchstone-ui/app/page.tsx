import { readAuditTail } from "@/app/lib/audit";

/**
 * Audit-log explorer. Server-rendered for the first paint, then SWR refreshes
 * client-side.
 */
export default async function Page() {
  const records = await readAuditTail(100);
  return (
    <div>
      <h1 className="text-2xl font-semibold">Recent audit records</h1>
      <p className="mt-1 text-sm text-zinc-600">
        Each line is one tool call. Click any record for the full context.
      </p>

      <div className="mt-6 overflow-hidden rounded-lg border border-zinc-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-4 py-3 font-medium">Time</th>
              <th className="px-4 py-3 font-medium">Assistant</th>
              <th className="px-4 py-3 font-medium">Tool</th>
              <th className="px-4 py-3 font-medium">Connection</th>
              <th className="px-4 py-3 font-medium">Verdict</th>
              <th className="px-4 py-3 font-medium text-right">Rows</th>
              <th className="px-4 py-3 font-medium text-right">PII</th>
              <th className="px-4 py-3 font-medium text-right">Latency</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {records.map((r, i) => (
              <tr key={i} className="hover:bg-zinc-50">
                <td className="px-4 py-2.5 font-mono text-xs text-zinc-500">
                  {new Date(r.ts).toLocaleTimeString()}
                </td>
                <td className="px-4 py-2.5">{r.assistant_id}</td>
                <td className="px-4 py-2.5 font-mono text-xs">{r.tool}</td>
                <td className="px-4 py-2.5">{r.connection}</td>
                <td className="px-4 py-2.5">
                  <VerdictBadge verdict={r.policy_verdict?.verdict} />
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">{r.rows ?? 0}</td>
                <td className="px-4 py-2.5 text-right text-xs">
                  {Object.entries(r.pii_summary ?? {})
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" ")}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums text-xs text-zinc-500">
                  {Math.round(r.latency_ms ?? 0)}ms
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function VerdictBadge({ verdict }: { verdict?: string }) {
  const map: Record<string, string> = {
    permit: "bg-emerald-100 text-emerald-800",
    deny: "bg-red-100 text-red-800",
    consent_required: "bg-amber-100 text-amber-800",
  };
  const cls = map[verdict ?? ""] ?? "bg-zinc-100 text-zinc-700";
  return (
    <span className={`inline-block rounded px-2 py-0.5 text-xs ${cls}`}>
      {verdict ?? "—"}
    </span>
  );
}

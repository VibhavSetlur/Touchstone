/**
 * Policy explorer — read-only view of currently-loaded rules. Editing is
 * scoped out of OSS v0.1 because we want policy changes to flow through
 * version control, not the UI. The view is here so operators can verify
 * the effective ruleset.
 */
export default function Policies() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Policies</h1>
      <p className="mt-1 text-sm text-zinc-600">
        Read-only view of currently-loaded rules. Edit policy files in your
        repository and reload Touchstone to apply changes.
      </p>
      <div className="mt-6 rounded-lg border border-dashed border-zinc-300 bg-white p-12 text-center text-sm text-zinc-500">
        Policy view stub. Wire to <code>GET /api/policies</code> on the
        Touchstone server.
      </div>
    </div>
  );
}

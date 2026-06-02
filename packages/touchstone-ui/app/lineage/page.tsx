/**
 * Lineage explorer — a graph view of column-level lineage. Stub for v0.1.
 * Planned: render LineageGraph from the explain_lineage tool with reactflow.
 */
export default function Lineage() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Lineage</h1>
      <p className="mt-1 text-sm text-zinc-600">
        Column-level lineage explorer. Pick a column to see what flows into it.
      </p>
      <div className="mt-6 rounded-lg border border-dashed border-zinc-300 bg-white p-12 text-center text-sm text-zinc-500">
        Lineage view stub. Wire to <code>POST /api/lineage</code>.
      </div>
    </div>
  );
}

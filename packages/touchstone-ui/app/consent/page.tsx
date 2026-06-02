/**
 * Consent queue — pending requests from the Touchstone gateway that need a
 * human decision. Click Approve/Deny to flip a flag the gateway polls.
 *
 * Stubbed: the actual approval store lives in /api/consent (memory-backed in
 * dev; Redis-backed in prod).
 */
export default function ConsentQueue() {
  return (
    <div>
      <h1 className="text-2xl font-semibold">Consent queue</h1>
      <p className="mt-1 text-sm text-zinc-600">
        Pending sensitive operations awaiting human approval.
      </p>
      <div className="mt-6 rounded-lg border border-dashed border-zinc-300 bg-white p-12 text-center text-sm text-zinc-500">
        No pending requests. When an AI assistant calls a tool that policy
        marks <code className="rounded bg-zinc-100 px-1.5 py-0.5">consent</code>,
        it lands here.
      </div>
    </div>
  );
}

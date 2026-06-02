import "./globals.css";
import type { ReactNode } from "react";

export const metadata = {
  title: "Touchstone — Triage",
  description: "Human-in-the-loop review for Touchstone findings.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-zinc-50 text-zinc-900 antialiased">
        <header className="border-b border-zinc-200 bg-white">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
            <div className="flex items-center gap-3">
              <div className="h-8 w-8 rounded-lg bg-zinc-900" />
              <span className="font-semibold">Touchstone Triage</span>
            </div>
            <nav className="flex items-center gap-6 text-sm text-zinc-600">
              <a href="/" className="hover:text-zinc-900">Audit</a>
              <a href="/consent" className="hover:text-zinc-900">Consent queue</a>
              <a href="/policies" className="hover:text-zinc-900">Policies</a>
              <a href="/lineage" className="hover:text-zinc-900">Lineage</a>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}

"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) {
      setError(
        error.message === "Invalid login credentials"
          ? "That email and password don't match. Please try again."
          : error.message,
      );
      return;
    }
    router.replace("/");
    router.refresh();
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-6 py-12">
      <div className="mb-10 flex flex-col items-center text-center">
        <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-brand text-white">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M11 20A7 7 0 0 1 4 13c4 0 7 3 7 7Z" fill="currentColor" stroke="none" />
            <path d="M11 20c0-6 3-11 9-13-1 8-4 12-9 13Z" fill="currentColor" stroke="none" />
          </svg>
        </div>
        <h1 className="text-2xl font-bold text-ink">PantryAI</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Sign in to plan meals and catch the best deals.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-ink">Email</span>
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className="h-12 rounded-2xl border border-hairline bg-surface px-4 text-base text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium text-ink">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••"
            className="h-12 rounded-2xl border border-hairline bg-surface px-4 text-base text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand/20"
          />
        </label>

        {error && (
          <p className="rounded-xl bg-warn-soft px-4 py-3 text-sm text-warn" role="alert">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="mt-2 flex h-12 items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white transition active:scale-[.99] disabled:opacity-60"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

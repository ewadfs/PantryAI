"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createClient } from "@/lib/supabase";

/** Only ever redirect to a same-origin path. */
function safeNext(raw: string | null): string {
  if (!raw) return "/";
  return raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [usePassword, setUsePassword] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(
    params.get("error") === "link"
      ? "That sign-in link expired or was already used. Request a fresh one below."
      : null,
  );
  const [loading, setLoading] = useState(false);

  async function sendMagicLink(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    const redirect = `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: redirect, shouldCreateUser: true },
    });
    setLoading(false);
    if (error) {
      setError(error.message);
      return;
    }
    setSentTo(email);
  }

  async function signInWithPassword(e: React.FormEvent) {
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
    router.replace(next);
    router.refresh();
  }

  const inputClass =
    "h-12 rounded-2xl border border-hairline bg-surface px-4 text-base text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand/20";

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
          Real dinners from your store&apos;s real deals.
        </p>
      </div>

      {sentTo ? (
        <div className="flex flex-col items-center gap-4 text-center" role="status">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-brand/10 text-2xl">
            📬
          </div>
          <h2 className="text-lg font-semibold text-ink">Check your email</h2>
          <p className="text-sm text-ink-soft">
            We sent a sign-in link to <span className="font-medium text-ink">{sentTo}</span>.
            Tap it on this device and you&apos;re in — no password needed.
          </p>
          <button
            type="button"
            onClick={() => setSentTo(null)}
            className="text-sm font-medium text-brand underline-offset-2 hover:underline"
          >
            Use a different email
          </button>
        </div>
      ) : (
        <form
          onSubmit={usePassword ? signInWithPassword : sendMagicLink}
          className="flex flex-col gap-4"
        >
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink">Email</span>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className={inputClass}
            />
          </label>

          {usePassword && (
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink">Password</span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className={inputClass}
              />
            </label>
          )}

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
            {loading
              ? usePassword
                ? "Signing in…"
                : "Sending link…"
              : usePassword
                ? "Sign in"
                : "Email me a sign-in link"}
          </button>

          <button
            type="button"
            onClick={() => {
              setUsePassword(!usePassword);
              setError(null);
            }}
            className="mx-auto text-sm font-medium text-ink-soft underline-offset-2 hover:text-ink hover:underline"
          >
            {usePassword ? "Email me a link instead" : "Sign in with a password instead"}
          </button>
        </form>
      )}
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

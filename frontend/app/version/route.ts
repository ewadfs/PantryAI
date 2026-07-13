export const dynamic = "force-dynamic";

/**
 * Public deploy-introspection endpoint: which commit is serving this frontend,
 * and which commit is serving the backend it points at (proxied server-side —
 * the backend URL never leaves the server env). Railway injects
 * RAILWAY_GIT_COMMIT_SHA at build/deploy time.
 */
export async function GET() {
  const frontend_commit = process.env.RAILWAY_GIT_COMMIT_SHA ?? null;
  const api = process.env.NEXT_PUBLIC_API_URL;
  let backend: unknown = null;
  if (api) {
    try {
      const res = await fetch(`${api}/health`, {
        cache: "no-store",
        signal: AbortSignal.timeout(5000),
      });
      backend = await res.json();
    } catch {
      backend = { status: "unreachable" };
    }
  }
  return Response.json({ frontend_commit, backend });
}

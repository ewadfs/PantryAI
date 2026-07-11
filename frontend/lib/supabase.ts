import { createBrowserClient } from "@supabase/ssr";

/**
 * Browser Supabase client. Uses @supabase/ssr so the session is stored in
 * cookies and is therefore readable by the proxy (middleware) for route
 * protection. Create per-call — the underlying client is memoized internally.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}

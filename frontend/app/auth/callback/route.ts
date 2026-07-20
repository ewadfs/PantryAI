import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Magic-link / OAuth landing (P40 A). Supabase redirects here with a PKCE
 * ?code=; we exchange it for a session (cookies set via @supabase/ssr) and
 * forward to the preserved destination. Runs unauthenticated by design —
 * the proxy exempts /auth/*.
 */
export async function GET(request: NextRequest) {
  const url = request.nextUrl;
  const code = url.searchParams.get("code");
  // Only same-origin paths — never redirect a user off-site from a link param.
  const rawNext = url.searchParams.get("next") ?? "/";
  const next = rawNext.startsWith("/") && !rawNext.startsWith("//") ? rawNext : "/";

  if (code) {
    const cookieStore = await cookies();
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() {
            return cookieStore.getAll();
          },
          setAll(cookiesToSet) {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          },
        },
      },
    );
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(new URL(next, url.origin));
    }
  }

  // Bad/expired link: back to login, destination preserved.
  const loginUrl = new URL("/login", url.origin);
  if (next !== "/") loginUrl.searchParams.set("next", next);
  loginUrl.searchParams.set("error", "link");
  return NextResponse.redirect(loginUrl);
}

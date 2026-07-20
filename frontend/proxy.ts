import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

/**
 * Proxy (Next.js 16's renamed Middleware). Protects every app route: if there
 * is no authenticated Supabase session, redirect to /login. Signed-in users
 * who hit /login are bounced back to Home. Session cookies are refreshed on
 * each request per the @supabase/ssr contract.
 */
export async function proxy(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          response = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const pathname = request.nextUrl.pathname;
  const isLogin = pathname.startsWith("/login");
  // /auth/* (magic-link callback) must be reachable without a session.
  const isAuthCallback = pathname.startsWith("/auth");

  if (!user && !isLogin && !isAuthCallback) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Preserve the deep link so login lands the user where they were headed.
    const dest = pathname + request.nextUrl.search;
    url.search = dest !== "/" ? `?next=${encodeURIComponent(dest)}` : "";
    return NextResponse.redirect(url);
  }

  if (user && isLogin) {
    const url = request.nextUrl.clone();
    const next = request.nextUrl.searchParams.get("next") ?? "/";
    const safe = next.startsWith("/") && !next.startsWith("//") ? next : "/";
    const [pathOnly, query = ""] = safe.split("?");
    url.pathname = pathOnly;
    url.search = query ? `?${query}` : "";
    return NextResponse.redirect(url);
  }

  return response;
}

export const config = {
  // Run on everything except Next internals, the manifest, static assets, and
  // the public deploy-introspection route.
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|manifest.json|sw.js|icons/|version|.*\\.(?:png|jpg|jpeg|svg|ico)$).*)",
  ],
};

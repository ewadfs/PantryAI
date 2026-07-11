# PantryAI — Frontend

Mobile-first PWA (Next.js 16 App Router, TypeScript, Tailwind v4). Light theme
only. Talks to the PantryAI backend and Supabase for auth.

## Getting started

```bash
npm install
npm run dev
```

Open http://localhost:3000. You'll be redirected to `/login` until you sign in
(email + password against Supabase).

### Environment (`.env.local`)

```
NEXT_PUBLIC_SUPABASE_URL=https://<project-ref>.supabase.co   # project root, NOT /rest/v1
NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon or publishable key>
NEXT_PUBLIC_API_URL=<backend base URL>
```

In Supabase → Authentication → URL Configuration, make sure the **Site URL** (and
redirect allow-list) includes `http://localhost:3000`.

## Testing the camera on a real phone

Browsers only allow `getUserMedia`/`<input capture>` camera access over **HTTPS
or `localhost`**. `http://<your-lan-ip>:3000` from your phone will **not** open
the camera. Two ways to test on a device:

1. **Tunnel (recommended)** — expose your local dev server over HTTPS:

   ```bash
   npm run dev            # terminal 1

   # terminal 2 — pick one:
   npx cloudflared tunnel --url http://localhost:3000
   # or
   npx ngrok http 3000
   ```

   Open the printed `https://…` URL on your phone. Then add that exact HTTPS
   origin to Supabase's Site URL / redirect allow-list, or auth callbacks will be
   rejected.

2. **Same-Wi-Fi without a tunnel** — the file picker (gallery) still works over
   plain HTTP on your LAN IP; only the live *camera capture* needs HTTPS.

> Note: the app reads `NEXT_PUBLIC_API_URL` for backend calls. If that backend
> isn't reachable, scanning/pantry actions will error — point it at a running
> API (local `http://localhost:8000` or your deployed URL).

## Structure

- `app/(app)/*` — authenticated screens behind the bottom tab bar
  (Home, Recipes, Scan, List, Settings) plus `/pantry` (linked, not a tab).
- `app/login` — email + password sign-in.
- `proxy.ts` — route protection (Next 16's renamed Middleware).
- `lib/` — Supabase client, typed API wrapper, image compression, pantry API.

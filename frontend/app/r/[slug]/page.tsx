import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

/**
 * Public read-only recipe page (P41 B): /r/{slug}. No auth, no pantry data —
 * the backend strips ownership info. noindex (links are for friends, not
 * crawlers), OG card rendered server-side by the backend. The CTA routes into
 * the P40 ZIP-first onboarding.
 */

const API = process.env.NEXT_PUBLIC_API_URL ?? "";

type PublicIngredient = {
  name: string | null;
  quantity: string | number | null;
  unit: string | null;
  on_sale: boolean;
  sale_price: string | number | null;
  sale_store: string | null;
};

type PublicRecipe = {
  slug: string;
  title: string;
  description: string | null;
  first_name: string | null;
  store_name: string | null;
  difficulty: string | null;
  total_time_min: number | null;
  servings: number | null;
  ingredients: PublicIngredient[];
  instructions: string[];
  nutrition_per_serving: Record<string, number> | null;
  market_anchor: {
    name: string | null;
    sale_price: string | number | null;
    price_unit: string | null;
    store: string | null;
  } | null;
};

async function fetchRecipe(slug: string): Promise<PublicRecipe | null> {
  try {
    const res = await fetch(`${API}/api/v1/public/r/${encodeURIComponent(slug)}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as PublicRecipe;
  } catch {
    return null;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const r = await fetchRecipe(slug);
  if (!r) return { title: "PantryAI", robots: { index: false, follow: false } };
  const desc =
    r.description ??
    "A dinner built from this week's real grocery deals.";
  return {
    title: `${r.title} — PantryAI`,
    description: desc,
    robots: { index: false, follow: false },
    openGraph: {
      title: r.title,
      description: desc,
      images: [`${API}/api/v1/public/r/${encodeURIComponent(slug)}/og.png`],
    },
  };
}

function price(v: string | number | null): string | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : null;
}

export default async function SharedRecipePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const r = await fetchRecipe(slug);
  if (!r) notFound();

  const n = r.nutrition_per_serving ?? {};
  const anchorPrice = r.market_anchor ? price(r.market_anchor.sale_price) : null;

  return (
    <main className="mx-auto min-h-dvh max-w-md px-5 py-8">
      <header className="mb-6 flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand text-lg text-white">
          🥬
        </span>
        <span className="text-base font-bold text-ink">PantryAI</span>
      </header>

      {r.first_name && (
        <p className="text-sm text-ink-soft">{r.first_name} shared a dinner with you</p>
      )}
      <h1 className="mt-1 text-2xl font-bold text-ink">{r.title}</h1>
      {r.description && <p className="mt-2 text-sm text-ink-soft">{r.description}</p>}

      <p className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-sm text-ink-soft">
        {r.total_time_min && <span>⏱ {r.total_time_min} min</span>}
        {r.servings && <span>🍽 {r.servings} servings</span>}
        {r.difficulty && <span className="capitalize">📈 {r.difficulty}</span>}
      </p>

      {r.market_anchor?.name && anchorPrice && (
        <p className="mt-3 rounded-xl bg-warn-soft px-4 py-3 text-sm font-medium text-warn">
          🏷️ Built on {r.market_anchor.name} at {anchorPrice}
          {r.market_anchor.price_unit ? `/${r.market_anchor.price_unit}` : ""}
          {(r.market_anchor.store || r.store_name) &&
            ` — this week's flyer at ${r.market_anchor.store ?? r.store_name}`}
        </p>
      )}

      <section className="mt-6">
        <h2 className="text-base font-bold text-ink">Ingredients</h2>
        <ul className="mt-2 divide-y divide-hairline rounded-2xl border border-hairline bg-surface px-4">
          {r.ingredients.map((ing, i) => (
            <li key={i} className="flex items-center justify-between gap-3 py-2.5 text-sm">
              <span className="min-w-0 text-ink">
                {ing.name}
                {ing.quantity ? (
                  <span className="text-ink-faint">
                    {" "}
                    — {ing.quantity}
                    {ing.unit ? ` ${ing.unit}` : ""}
                  </span>
                ) : null}
              </span>
              {ing.on_sale && price(ing.sale_price) && (
                <span className="shrink-0 rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-bold text-brand-dark">
                  {price(ing.sale_price)} on sale
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>

      {r.instructions.length > 0 && (
        <section className="mt-6">
          <h2 className="text-base font-bold text-ink">Instructions</h2>
          <ol className="mt-2 flex flex-col gap-3">
            {r.instructions.map((step, i) => (
              <li key={i} className="flex gap-3 text-sm text-ink">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-soft text-xs font-bold text-brand-dark">
                  {i + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {(n.calories || n.protein_g) && (
        <section className="mt-6">
          <h2 className="text-base font-bold text-ink">Per serving</h2>
          <div className="mt-2 grid grid-cols-4 gap-2 text-center">
            {(
              [
                ["Calories", n.calories],
                ["Protein g", n.protein_g],
                ["Carbs g", n.carbs_g],
                ["Fat g", n.fat_g],
              ] as const
            ).map(([label, val]) => (
              <div key={label} className="rounded-xl border border-hairline bg-surface p-2">
                <p className="text-base font-bold text-ink">
                  {val == null ? "—" : Math.round(Number(val))}
                </p>
                <p className="text-[11px] text-ink-soft">{label}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="mt-8 rounded-2xl border border-brand/25 bg-brand-soft/60 p-5 text-center">
        <p className="text-sm font-semibold text-ink">
          This dinner was generated from {r.store_name ? `${r.store_name}'s` : "a real store's"}{" "}
          weekly flyer.
        </p>
        <Link
          href={`/welcome?ref=${encodeURIComponent(r.slug)}`}
          className="mt-4 flex h-12 w-full items-center justify-center rounded-2xl bg-brand text-base font-semibold text-white active:scale-[.99]"
        >
          Get dinners from YOUR store&apos;s flyer
        </Link>
        <p className="mt-2 text-xs text-ink-faint">
          ZIP in, dinners out — takes about a minute.
        </p>
      </div>
    </main>
  );
}

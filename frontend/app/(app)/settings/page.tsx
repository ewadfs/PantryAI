"use client";

import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";

export default function SettingsPage() {
  const router = useRouter();

  async function signOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <div className="px-5 pt-8">
      <h1 className="text-2xl font-bold text-ink">Settings</h1>
      <p className="mt-1 text-sm text-ink-soft">
        Your profile, dietary goals, and store preferences will live here.
      </p>

      <div className="mt-6 rounded-2xl border border-hairline bg-surface p-2">
        <button
          onClick={signOut}
          className="flex h-12 w-full items-center justify-center rounded-xl text-base font-semibold text-warn transition active:scale-[.99]"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}

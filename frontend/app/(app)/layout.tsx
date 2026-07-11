import BottomTabBar from "@/components/BottomTabBar";
import { ToastProvider } from "@/components/ui/Toast";

/**
 * App shell for the authenticated experience: a scrollable content area with
 * the fixed bottom tab bar. Route protection is handled by proxy.ts.
 */
export default function AppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <ToastProvider>
      <div className="mx-auto flex min-h-dvh max-w-md flex-col">
        <main className="flex-1 pb-24">{children}</main>
        <BottomTabBar />
      </div>
    </ToastProvider>
  );
}

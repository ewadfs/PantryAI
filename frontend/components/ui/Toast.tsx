"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

type Toast = {
  id: number;
  message: string;
  kind: "error" | "info";
  onRetry?: () => void;
};

type ToastInput = { message: string; kind?: "error" | "info"; onRetry?: () => void };

const ToastCtx = createContext<{
  show: (t: ToastInput) => void;
  error: (message: string, onRetry?: () => void) => void;
} | null>(null);

export function useToast() {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (t: ToastInput) => {
      const id = ++counter.current;
      const toast: Toast = { id, message: t.message, kind: t.kind ?? "info", onRetry: t.onRetry };
      setToasts((prev) => [...prev, toast]);
      if (!t.onRetry) setTimeout(() => dismiss(id), 4000);
    },
    [dismiss],
  );

  const error = useCallback(
    (message: string, onRetry?: () => void) => show({ message, kind: "error", onRetry }),
    [show],
  );

  return (
    <ToastCtx.Provider value={{ show, error }}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-20 z-[70] mx-auto flex max-w-md flex-col gap-2 px-4">
        {toasts.map((t) => (
          <ToastCard key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => {
    // errors with a retry action persist until dismissed/retried
  }, []);
  const isError = toast.kind === "error";
  return (
    <div
      role="status"
      className={`pointer-events-auto flex items-center gap-3 rounded-2xl border px-4 py-3 shadow-lg ${
        isError ? "border-warn/30 bg-warn-soft" : "border-hairline bg-surface"
      }`}
    >
      <p className={`min-w-0 flex-1 text-sm ${isError ? "text-warn" : "text-ink"}`}>
        {toast.message}
      </p>
      {toast.onRetry && (
        <button
          onClick={() => {
            toast.onRetry?.();
            onDismiss();
          }}
          className="shrink-0 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white"
        >
          Retry
        </button>
      )}
      <button
        onClick={onDismiss}
        aria-label="Dismiss"
        className="shrink-0 text-ink-faint"
      >
        <span className="text-base leading-none">✕</span>
      </button>
    </div>
  );
}

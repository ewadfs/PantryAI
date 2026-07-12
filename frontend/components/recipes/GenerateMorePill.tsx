"use client";

/**
 * Bottom "Generate more" pill (Prompt 29). Shown only when a batch exists and
 * the top setup panel is scrolled out of view. Sits above the centered camera
 * FAB apex (~88px from bottom) and right-aligned, so it never overlaps the FAB
 * or the bottom tab bar. The ✏️ affix jumps back to the setup panel.
 */
export default function GenerateMorePill({
  onGenerateMore,
  onEditBrief,
  generating,
}: {
  onGenerateMore: () => void;
  onEditBrief: () => void;
  generating: boolean;
}) {
  return (
    <div
      className="fixed inset-x-0 z-40 mx-auto flex max-w-md justify-end px-4"
      // Above the FAB apex (tab bar 4rem + 1.5rem overhang) + safe area + a
      // 1rem clearance gap, so the pill never touches the FAB or tab bar.
      style={{ bottom: "calc(env(safe-area-inset-bottom) + 6.5rem)" }}
    >
      <div className="flex items-stretch overflow-hidden rounded-full bg-brand shadow-lg shadow-brand/30">
        <button
          onClick={onGenerateMore}
          disabled={generating}
          className="flex items-center gap-1.5 py-2.5 pl-4 pr-3 text-sm font-semibold text-white transition active:scale-[.98] disabled:opacity-80"
        >
          {generating ? (
            <>
              <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />
              Generating…
            </>
          ) : (
            <>✨ Generate more</>
          )}
        </button>
        <button
          onClick={onEditBrief}
          disabled={generating}
          aria-label="New brief — edit setup"
          className="flex items-center border-l border-white/25 px-3 text-white transition active:scale-[.98] disabled:opacity-80"
        >
          ✏️
        </button>
      </div>
    </div>
  );
}

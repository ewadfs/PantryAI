"use client";

import { useEffect, useState } from "react";

const EMOJI = ["🎉", "✨", "🥕", "🍅", "🧅", "🥦", "🎊", "🌿"];

/**
 * Lightweight, dependency-free confetti burst. Renders for ~1.5s then calls
 * onDone. Positions use Math.random (client-only component).
 */
export default function Confetti({ onDone }: { onDone?: () => void }) {
  const [pieces] = useState(() =>
    Array.from({ length: 24 }, (_, i) => ({
      id: i,
      left: Math.random() * 100,
      delay: Math.random() * 0.3,
      duration: 1.1 + Math.random() * 0.6,
      emoji: EMOJI[i % EMOJI.length],
    })),
  );

  useEffect(() => {
    const t = setTimeout(() => onDone?.(), 1600);
    return () => clearTimeout(t);
  }, [onDone]);

  return (
    <div className="pointer-events-none fixed inset-0 z-[60] overflow-hidden" aria-hidden>
      {pieces.map((p) => (
        <span
          key={p.id}
          className="absolute top-0 text-xl"
          style={{
            left: `${p.left}%`,
            animation: `confetti-fall ${p.duration}s ease-out ${p.delay}s forwards`,
          }}
        >
          {p.emoji}
        </span>
      ))}
    </div>
  );
}

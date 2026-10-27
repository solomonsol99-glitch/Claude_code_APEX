/** 3×3 dot grid forming a "C" (7 dots); the first dot is the yellow signal color. */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden="true">
      <circle cx="5" cy="5" r="3.2" fill="var(--signal)" />
      <circle cx="15" cy="5" r="3.2" fill="currentColor" />
      <circle cx="25" cy="5" r="3.2" fill="currentColor" />
      <circle cx="5" cy="15" r="3.2" fill="currentColor" />
      <circle cx="5" cy="25" r="3.2" fill="currentColor" />
      <circle cx="15" cy="25" r="3.2" fill="currentColor" />
      <circle cx="25" cy="25" r="3.2" fill="currentColor" />
    </svg>
  );
}

export function Brand({ size = 28 }: { size?: number }) {
  return (
    <span className="flex items-center gap-2.5 font-display text-xl font-bold tracking-tight">
      <LogoMark size={size} />
      coreset
    </span>
  );
}

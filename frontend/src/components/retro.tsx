/** Shared building blocks for Unpaper's retro-desktop look. */

import type { ReactNode } from "react";

export type BarColor = "yellow" | "pink" | "purple" | "orange";

const BAR: Record<BarColor, string> = {
  yellow: "bg-yellow",
  pink: "bg-pink",
  purple: "bg-purple",
  orange: "bg-orange",
};

/** Decorative minimise / maximise / close glyphs; the window chrome is not interactive. */
export function WindowControls({ size = 14 }: { size?: number }) {
  const s = size;
  return (
    <span aria-hidden="true" className="flex items-center gap-2 text-navy">
      <svg width={s} height={s} viewBox="0 0 14 14"><path d="M2 11h10" stroke="currentColor" strokeWidth="2.4" /></svg>
      <svg width={s} height={s} viewBox="0 0 14 14"><rect x="2" y="2" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="2.2" /></svg>
      <svg width={s} height={s} viewBox="0 0 14 14"><path d="M2.5 2.5l9 9M11.5 2.5l-9 9" stroke="currentColor" strokeWidth="2.4" /></svg>
    </span>
  );
}

interface WindowProps {
  title: string;
  color?: BarColor;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  role?: string;
  labelledBy?: string;
}

/** A retro window: navy outline, coloured title bar, hard offset shadow. */
export function Window({ title, color = "yellow", children, className = "", bodyClassName = "", role, labelledBy }: WindowProps) {
  return (
    <div role={role} aria-labelledby={labelledBy} className={`border-[3px] border-navy bg-paper shadow-hard ${className}`}>
      <div className={`flex items-center justify-between gap-3 border-b-[3px] border-navy px-3 py-1.5 ${BAR[color]}`}>
        <span className="truncate text-[0.8125rem] font-bold tracking-[0.06em] uppercase">{title}</span>
        <WindowControls size={13} />
      </div>
      <div className={bodyClassName}>{children}</div>
    </div>
  );
}

/** Flat document icon with a folded corner, as on the brand art's desktop. */
export function DocIcon({ fill = "#ffffff", width = 48, label }: { fill?: string; width?: number; label?: string }) {
  return (
    <svg width={width} height={width * 1.2} viewBox="0 0 50 60" aria-hidden={label ? undefined : true} role={label ? "img" : undefined} aria-label={label}>
      <path d="M8 6h24l12 12v38H8z" fill="#1d1048" transform="translate(3 3)" />
      <path d="M5 3h24l12 12v38H5z" fill={fill} stroke="#1d1048" strokeWidth="2.5" strokeLinejoin="round" />
      <path d="M29 3v12h12" fill="#ffffff" stroke="#1d1048" strokeWidth="2.5" strokeLinejoin="round" />
    </svg>
  );
}

/** Four-pointed sparkle. */
export function Sparkle({ size = 22, className = "" }: { size?: number; className?: string }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" className={className}>
      <path d="M12 0c.9 6.4 5.6 11.1 12 12-6.4.9-11.1 5.6-12 12-.9-6.4-5.6-11.1-12-12C6.4 11.1 11.1 6.4 12 0z" fill="#ffffff" />
    </svg>
  );
}

const BUTTON_BASE =
  "inline-flex min-h-12 items-center justify-center gap-2 border-[3px] border-navy px-6 font-bold text-navy shadow-hard-sm transition-[transform,box-shadow] duration-100 hover:-translate-x-px hover:-translate-y-px hover:shadow-[4px_4px_0_var(--navy)] active:translate-x-[3px] active:translate-y-[3px] active:shadow-none disabled:pointer-events-none disabled:opacity-60";

export const buttonClass = {
  yellow: `${BUTTON_BASE} bg-yellow`,
  pink: `${BUTTON_BASE} bg-pink`,
  white: `${BUTTON_BASE} bg-paper`,
};

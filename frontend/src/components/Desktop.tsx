import type { ReactNode } from "react";

import { DocIcon, Sparkle } from "./retro";

const STACK = ["bg-purple", "bg-pink", "bg-orange", "bg-yellow"];

/** Decorative desktop around the app: icons, a window cascade, sparkles and pixel patches. */
export default function Desktop({ children }: { children: ReactNode }) {
  return (
    <div className="relative min-h-screen overflow-hidden">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0">
        <div className="pixels absolute -top-6 -left-6 size-40 opacity-90" />
        <div className="pixels absolute right-[-30px] bottom-[-20px] size-48 opacity-90" />
        <div className="pixels absolute top-[46%] left-[-40px] hidden size-32 md:block" />

        <Sparkle size={26} className="absolute top-[7%] left-[38%]" />
        <Sparkle size={18} className="absolute top-[22%] right-[12%]" />
        <Sparkle size={30} className="absolute top-[58%] left-[6%]" />
        <Sparkle size={16} className="absolute bottom-[14%] left-[30%]" />
        <Sparkle size={22} className="absolute right-[6%] bottom-[38%]" />

        {/* Desktop icons, wide screens only. */}
        <div className="absolute top-12 left-10 hidden flex-col gap-7 xl:flex">
          {[
            ["#ffffff", "REPORT.PDF"],
            ["#f9d949", "REPORT.DOCX"],
            ["#ef72c3", "NOTES.DOCX"],
          ].map(([fill, label]) => (
            <div key={label} className="flex w-28 flex-col items-center gap-1.5">
              <DocIcon fill={fill} width={54} />
              <span className="text-[0.6875rem] font-bold tracking-[0.06em] text-navy">{label}</span>
            </div>
          ))}
        </div>

        {/* A cascade of windows, as in the brand art (top right, clear of the app window). */}
        <div className="absolute top-[150px] right-[calc(50%-380px-60px-260px)] hidden h-[200px] w-[260px] xl:block">
          {STACK.map((bar, i) => (
            <div
              key={bar}
              className="absolute h-36 w-52 border-[3px] border-navy bg-paper"
              style={{ right: `${(STACK.length - 1 - i) * 18}px`, bottom: `${(STACK.length - 1 - i) * 18}px` }}
            >
              <div className={`h-6 border-b-[3px] border-navy ${bar}`} />
            </div>
          ))}
        </div>
      </div>
      <div className="relative">{children}</div>
    </div>
  );
}

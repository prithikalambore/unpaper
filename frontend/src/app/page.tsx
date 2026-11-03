import Link from "next/link";

import Converter from "@/components/Converter";
import Desktop from "@/components/Desktop";
import { Window } from "@/components/retro";

export default function Home() {
  return (
    <Desktop>
      <main className="mx-auto w-full max-w-[760px] px-4 pt-10 pb-16 sm:pt-16">
        <header className="mb-8 sm:mb-10">
          <h1 className="text-[3.75rem] leading-none font-extrabold tracking-[-0.03em] text-paper [-webkit-text-stroke:3px_var(--navy)] [paint-order:stroke_fill] [text-shadow:5px_5px_0_var(--navy)] sm:text-[5.5rem]">
            Unpaper
          </h1>
          <p className="mt-4 max-w-[34ch] text-xl leading-snug font-semibold sm:text-2xl">
            Turn any PDF into a Word document you can actually edit.
          </p>
          <p className="mt-2 max-w-[56ch] text-navy-soft">
            Text, headings, lists, tables and images come through as real Word content. Opens in Microsoft Word,
            Pages, Google Docs and LibreOffice. Free, no sign-up.
          </p>
        </header>

        <Window title="Unpaper.exe" color="yellow" bodyClassName="p-5 sm:p-8">
          <Converter />
        </Window>

        <footer className="mt-10 max-w-[62ch] border-[3px] border-navy bg-paper/85 px-4 py-3 text-sm leading-relaxed">
          Files are converted on this server, never sent to third-party or AI services, and deleted as soon as you
          download the result. Results that are never downloaded are removed automatically after a few minutes.{" "}
          <Link href="/privacy" className="font-bold underline decoration-2 underline-offset-4">
            Privacy details
          </Link>
        </footer>
      </main>
    </Desktop>
  );
}

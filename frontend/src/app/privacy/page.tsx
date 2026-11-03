import type { Metadata } from "next";
import Link from "next/link";

import Desktop from "@/components/Desktop";
import { Window } from "@/components/retro";

export const metadata: Metadata = {
  title: "Privacy: Unpaper",
  description: "How Unpaper handles your files and data.",
};

export default function Privacy() {
  return (
    <Desktop>
      <main className="mx-auto w-full max-w-[760px] px-4 pt-10 pb-16 sm:pt-16">
        <p>
          <Link href="/" className="font-bold underline decoration-[2.5px] underline-offset-4">
            Back to Unpaper
          </Link>
        </p>
        <Window title="Privacy.txt" color="pink" className="mt-6" bodyClassName="px-5 py-6 sm:px-8">
          <h1 className="text-[2.25rem] leading-[1.1] font-extrabold tracking-[-0.02em]">Privacy</h1>
          <div className="mt-5 space-y-5 text-[1.0625rem] leading-relaxed [&_h2]:mt-9 [&_h2]:text-xl [&_h2]:font-bold">
            <p>
              This is a free tool with no accounts and no cookies. Here is exactly what happens to your file and what we
              record.
            </p>

            <h2>Your files</h2>
            <p>
              Your PDF is converted on our own server. It is never sent to third-party services and never used to train
              AI. The PDF is deleted as soon as the conversion finishes, whether it succeeds or fails. The Word document
              is deleted from the server the moment you download it. If you never download it, it is deleted
              automatically after a few minutes. Files are held in memory, not on disk, and are never backed up.
            </p>

            <h2>What we count</h2>
            <p>
              To learn whether the tool is useful, we keep anonymous daily totals: the number of visitors, files
              converted, pages, which mode was used, how long conversions took, and which kinds of errors happened.
              These totals contain no IP addresses, file names or document content.
            </p>
            <p>
              Your IP address is used only briefly, in memory, to limit how many files one person can convert per hour
              and to avoid counting the same visitor twice in a day. It is never written to disk.
            </p>

            <h2>Feedback</h2>
            <p>
              If you answer &ldquo;did the conversion work for you?&rdquo;, we store your answer and any comment you
              type, with the conversion mode and page count. Please don&apos;t include personal or confidential
              information in comments.
            </p>

            <h2>Logs</h2>
            <p>
              Server logs record technical events such as a random job number, file size, page count, duration and error
              type. They never contain file names or document text. Like any web server, our hosting proxy may briefly
              log IP addresses for security.
            </p>

            <h2>Limits</h2>
            <p>
              The tool is provided as-is while we test it. Conversion is automatic and may not be perfect, so please
              check important documents. Don&apos;t upload documents you aren&apos;t allowed to share with a third-party
              service.
            </p>
          </div>
        </Window>
      </main>
    </Desktop>
  );
}

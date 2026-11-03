import type { Metadata, Viewport } from "next";
import { Poppins } from "next/font/google";
import "./globals.css";

// next/font downloads the font at build time and self-hosts it: no runtime requests to Google.
const poppins = Poppins({
  variable: "--font-poppins",
  subsets: ["latin"],
  weight: ["500", "600", "700", "800"],
});

export const metadata: Metadata = {
  title: "Unpaper: PDF to Word",
  description: "Turn any PDF into a Word document you can edit. Free, no sign-up.",
  robots: { index: false },
};

export const viewport: Viewport = {
  themeColor: "#6ad4f7",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${poppins.variable} h-full antialiased`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}

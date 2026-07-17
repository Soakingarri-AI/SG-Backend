import { Navbar } from "@/components/Navbar";
import { Footer } from "@/components/Footer";

/**
 * Shared chrome for the apex marketing domain (soakingarri.com).
 * Product subdomains are rewritten by middleware.ts into their own segments and
 * use ProductShell instead, so they never inherit this layout.
 */
export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <Navbar />
      <div className="flex-1">{children}</div>
      <Footer />
    </div>
  );
}

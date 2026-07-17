import Image from "next/image";
import Link from "next/link";

export function Logo({ size = 40 }: { size?: number }) {
  return (
    <Link href="/" className="flex items-center gap-3">
      <Image
        src="/logo.png"
        alt="SoakinGarri AI"
        width={size}
        height={size}
        priority
        className="rounded-lg"
      />
      <span className="font-display text-lg font-semibold tracking-tight">
        SoakinGarri<span className="gradient-text"> AI</span>
      </span>
    </Link>
  );
}

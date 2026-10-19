"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Strategies" },
  { href: "/screener", label: "Live Screener" },
  { href: "/breadth", label: "Breadth" },
  { href: "/daily-byte", label: "Daily Byte" },
  { href: "/history", label: "History" },
  { href: "/admin", label: "Admin" },
];

/** Shared top navigation header for the standalone pages (home, strategy
 * feeds, breadth). `right` renders in the trailing slot (e.g. the NIFTY
 * spot quote on the home page). */
export default function AppHeader({ right }: { right?: ReactNode }) {
  const pathname = usePathname();
  return (
    <div
      style={{ borderBottom: "1px solid #1F2630", background: "#12161C" }}
      className="flex flex-shrink-0 items-center gap-5 px-6 py-3"
    >
      <div className="flex items-center gap-2.5">
        <div
          className="flex h-7 w-7 items-center justify-center rounded-[7px] text-sm font-bold text-[#08110C]"
          style={{ background: "linear-gradient(135deg,#3DD68C,#1F8F5C)" }}
        >
          O
        </div>
        <div className="text-[15px] font-semibold tracking-[0.2px]">OptionScreen</div>
      </div>
      <div className="h-7 w-px bg-[#1F2630]" />
      <nav className="flex items-center gap-5">
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className="text-xs font-semibold"
              style={{ color: active ? "#E8EAED" : "#8B96A5" }}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="flex-grow" />
      {right}
    </div>
  );
}

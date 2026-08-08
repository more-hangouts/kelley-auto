import Link from "next/link";

// How Kelley sells: most of the lot is buy-here-pay-here (dealer carries
// the note, shopper cares about the down payment), a smaller set are cash
// cars sold outright. Shoppers arrive knowing which one they are, so the
// listing lets them say so in one tap.
//
// Rendered as links to sibling routes rather than client-side state: each
// tab is then a real, indexable, linkable URL — a rep can text
// "kelleyautoplex.com/cash-cars" straight to someone — and the whole
// inventory page stays a static server component.
//
// "All" is deliberately first and includes both. Flagging a car as a cash
// car narrows nothing: it must never quietly drop out of the main listing.

export type SaleTypeTab = "all" | "bhph" | "cash";

const TABS: { key: SaleTypeTab; label: string; href: string }[] = [
  { key: "all", label: "All Inventory", href: "/cars-for-sale" },
  { key: "bhph", label: "Buy Here, Pay Here", href: "/buy-here-pay-here" },
  { key: "cash", label: "Cash Cars", href: "/cash-cars" },
];

export default function SaleTypeTabs({ active }: { active: SaleTypeTab }) {
  return (
    <nav aria-label="Filter inventory by how it's sold" className="mt-6">
      <ul className="flex flex-wrap gap-2">
        {TABS.map((tab) => {
          const isActive = tab.key === active;
          return (
            <li key={tab.key}>
              <Link
                href={tab.href}
                // aria-current is what tells a screen reader which tab is
                // the one being shown; the color alone doesn't.
                aria-current={isActive ? "page" : undefined}
                className={`inline-flex items-center rounded-full px-4 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-primary text-white"
                    : "bg-white/15 text-white hover:bg-white/25"
                }`}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

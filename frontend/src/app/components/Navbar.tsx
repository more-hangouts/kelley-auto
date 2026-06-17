"use client";

import Link from "next/link";
import Image from "next/image";
import { useState } from "react";

export default function Navbar({
  light = false,
  phone,
}: {
  light?: boolean;
  phone?: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const linkColor = light ? "text-neutral-50" : "text-neutral-600";
  const displayPhone = phone || "(123) 333-1212";
  const telHref = `tel:+1${displayPhone.replace(/\D/g, "")}`;

  return (
    <nav
      className={`relative flex items-center justify-between px-5 md:px-10 lg:px-20 py-4 md:py-5 ${
        light ? "text-white" : "text-neutral-700"
      }`}
    >
      {/* Logo */}
      <Link href="/">
        {light ? (
          <Image
            src="/images/logo-white.png"
            alt="Reliable Used Cars"
            width={160}
            height={28}
            className="object-contain"
          />
        ) : (
          <Image
            src="/images/logo-dark.png"
            alt="Reliable Used Cars"
            width={160}
            height={28}
            className="object-contain"
          />
        )}
      </Link>

      {/* Desktop nav links */}
      <div className={`hidden lg:flex items-center gap-10 text-base ${linkColor}`}>
        <Link href="/" className="hover:opacity-80 transition-opacity">
          Home
        </Link>
        <Link href="/shop" className="hover:opacity-80 transition-opacity">
          Inventory
        </Link>
        <Link href="/about" className="hover:opacity-80 transition-opacity">
          About Us
        </Link>
        <Link href="/financing" className="hover:opacity-80 transition-opacity">
          Financing
        </Link>
        <Link href="/contact" className="hover:opacity-80 transition-opacity">
          Contact Us
        </Link>
      </div>

      {/* Desktop right side — phone CTA */}
      <div className="hidden lg:flex items-center gap-4">
        <a
          href={telHref}
          className={`flex items-center gap-2 text-sm font-medium ${linkColor} hover:opacity-80 transition-opacity`}
        >
          <svg className="size-4 shrink-0" fill="none" viewBox="0 0 18 18">
            <path
              d="M4.5 3.5c-.5 0-1 .3-1.3.8L2 6.5C2 12.3 7.7 18 13.5 18l2.2-1.2c.5-.3.8-.8.8-1.3v-2.2c0-.6-.4-1-.9-1.1l-2.5-.5c-.5-.1-1 .1-1.3.5l-.7 1c-1.3-.7-2.5-1.9-3.2-3.2l1-.7c.4-.3.6-.8.5-1.3L8.8 4.4c-.1-.5-.5-.9-1.1-.9H4.5z"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          {displayPhone}
        </a>
        <Link
          href="/shop"
          className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white hover:bg-primary-dark transition-colors"
        >
          View Inventory
        </Link>
      </div>

      {/* Mobile hamburger */}
      <button
        className="lg:hidden flex flex-col gap-1.5 p-2"
        onClick={() => setMenuOpen(!menuOpen)}
        aria-label="Toggle menu"
      >
        <span
          className={`block h-0.5 w-6 transition-transform ${
            light ? "bg-white" : "bg-neutral-700"
          } ${menuOpen ? "translate-y-2 rotate-45" : ""}`}
        />
        <span
          className={`block h-0.5 w-6 ${light ? "bg-white" : "bg-neutral-700"} ${
            menuOpen ? "opacity-0" : ""
          }`}
        />
        <span
          className={`block h-0.5 w-6 transition-transform ${
            light ? "bg-white" : "bg-neutral-700"
          } ${menuOpen ? "-translate-y-2 -rotate-45" : ""}`}
        />
      </button>

      {/* Mobile menu dropdown */}
      {menuOpen && (
        <div className="absolute top-full left-0 right-0 z-50 bg-white shadow-lg border-t border-neutral-100 lg:hidden">
          <div className="flex flex-col px-5 py-4 gap-1">
            <Link
              href="/"
              className="py-3 text-base text-neutral-700 hover:text-primary"
              onClick={() => setMenuOpen(false)}
            >
              Home
            </Link>
            <Link
              href="/shop"
              className="py-3 text-base text-neutral-700 hover:text-primary"
              onClick={() => setMenuOpen(false)}
            >
              Inventory
            </Link>
            <Link
              href="/about"
              className="py-3 text-base text-neutral-700 hover:text-primary"
              onClick={() => setMenuOpen(false)}
            >
              About Us
            </Link>
            <Link
              href="/financing"
              className="py-3 text-base text-neutral-700 hover:text-primary"
              onClick={() => setMenuOpen(false)}
            >
              Financing
            </Link>
            <Link
              href="/contact"
              className="py-3 text-base text-neutral-700 hover:text-primary"
              onClick={() => setMenuOpen(false)}
            >
              Contact Us
            </Link>
            <hr className="my-2 border-neutral-100" />
            <a
              href={telHref}
              className="py-3 text-base text-neutral-700 hover:text-primary"
              onClick={() => setMenuOpen(false)}
            >
              Call Us: {displayPhone}
            </a>
            <Link
              href="/shop"
              className="mt-1 flex items-center justify-center rounded-lg bg-primary px-4 py-2.5 text-base font-semibold text-white"
              onClick={() => setMenuOpen(false)}
            >
              View Inventory
            </Link>
          </div>
        </div>
      )}
    </nav>
  );
}

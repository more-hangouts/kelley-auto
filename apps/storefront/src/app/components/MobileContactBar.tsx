import { resolveNap } from "@/lib/nap";

/**
 * Fixed call / directions bar, mobile only.
 *
 * Most of this site's traffic arrives on a phone from social, lands on one
 * page, and wants one of two things: to ring the lot, or to drive to it.
 * Before this the number lived behind the hamburger menu and the address only
 * in the footer, so both errands cost a hunt. This puts them one thumb-tap
 * away from anywhere on the site.
 *
 * Hidden from `lg` up, where the top bar already shows the phone and the
 * address, and a persistent bar would just eat viewport.
 *
 * Height is published as `--mobile-cta-h` (see globals.css) so the chat bubble
 * can lift itself clear instead of sitting on top of the buttons.
 */
export default async function MobileContactBar() {
  const nap = await resolveNap();
  if (!nap.telHref && !nap.directionsHref) return null;

  return (
    <div
      className="lg:hidden fixed inset-x-0 bottom-0 z-[9998] flex items-stretch gap-px border-t border-neutral-700 bg-neutral-800 pb-[env(safe-area-inset-bottom)]"
      /* Below the chat bubble's 9999 on purpose: if they ever do overlap,
         the conversation someone already opened wins. */
    >
      {nap.telHref && (
        <a
          href={nap.telHref}
          className="flex flex-1 items-center justify-center gap-2 bg-primary px-3 py-3.5 text-sm font-semibold text-white active:bg-primary-dark"
        >
          <svg className="size-4 shrink-0" fill="none" viewBox="0 0 18 18" aria-hidden="true">
            <path
              d="M16.46 12.83v2.25a1.5 1.5 0 0 1-1.64 1.5 14.85 14.85 0 0 1-6.47-2.3 14.63 14.63 0 0 1-4.5-4.5A14.85 14.85 0 0 1 1.52 3.27 1.5 1.5 0 0 1 3 1.63h2.25a1.5 1.5 0 0 1 1.5 1.29 9.63 9.63 0 0 0 .53 2.1 1.5 1.5 0 0 1-.34 1.58l-.95.95a12 12 0 0 0 4.5 4.5l.95-.95a1.5 1.5 0 0 1 1.58-.34 9.63 9.63 0 0 0 2.1.53 1.5 1.5 0 0 1 1.29 1.52Z"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Call {nap.phoneDisplay}
        </a>
      )}
      {nap.directionsHref && (
        <a
          href={nap.directionsHref}
          target="_blank"
          rel="noopener noreferrer"
          className="flex shrink-0 items-center justify-center gap-2 px-5 py-3.5 text-sm font-semibold text-white active:bg-neutral-700"
        >
          <svg className="size-4 shrink-0" fill="none" viewBox="0 0 18 18" aria-hidden="true">
            <path
              d="M9 1.5C6.51 1.5 4.5 3.51 4.5 6c0 3.75 4.5 10.5 4.5 10.5S13.5 9.75 13.5 6c0-2.49-2.01-4.5-4.5-4.5ZM9 7.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Directions
        </a>
      )}
    </div>
  );
}

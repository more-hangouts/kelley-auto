import {
  LEAD_RECEIVED_BODY,
  LEAD_RECEIVED_HEADLINE,
  SCHEDULING_CTA_PREFIX,
  SCHEDULING_PHONE_DISPLAY,
  SCHEDULING_TEL_HREF,
} from "@/lib/scheduling";

/**
 * Post-submit panel for every public lead form.
 *
 * The site no longer self-schedules, so this deliberately does NOT promise a
 * confirmed appointment. It acknowledges the request and points the customer
 * at the one channel that can actually book a visit: a human on the phone.
 * Both forms render this so the copy can only ever say one thing.
 */
export default function LeadReceived({
  detail,
  compact = false,
}: {
  /** Optional extra line, e.g. the vehicle they asked about. */
  detail?: string;
  /** Tighter spacing for the in-listing widget. */
  compact?: boolean;
}) {
  return (
    <div
      className={`rounded-2xl bg-green-50 text-center ${compact ? "p-6" : "p-8"}`}
    >
      <div
        className={`mx-auto flex items-center justify-center rounded-full bg-green-100 ${
          compact ? "mb-3 size-12" : "mb-4 size-14"
        }`}
      >
        <svg
          className={`text-green-600 ${compact ? "size-6" : "size-7"}`}
          fill="none"
          viewBox="0 0 24 24"
        >
          <path
            d="M5 13l4 4L19 7"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <p
        className={`font-semibold text-green-800 ${compact ? "text-base" : "text-lg"}`}
      >
        {LEAD_RECEIVED_HEADLINE}
      </p>
      <p className="mt-1 text-sm text-green-700">
        {detail ? `${detail} ${LEAD_RECEIVED_BODY}` : LEAD_RECEIVED_BODY}
      </p>
      <p className="mt-4 border-t border-green-100 pt-4 text-sm text-green-700">
        {SCHEDULING_CTA_PREFIX}{" "}
        <a
          href={SCHEDULING_TEL_HREF}
          className="font-semibold text-green-800 underline underline-offset-2"
        >
          {SCHEDULING_PHONE_DISPLAY}
        </a>
        .
      </p>
    </div>
  );
}

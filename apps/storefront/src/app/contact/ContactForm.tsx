"use client";

import { useState } from "react";
import { submitLead } from "@/lib/publicApi";
import LeadReceived from "../components/LeadReceived";
import {
  SCHEDULING_PHONE_DISPLAY,
  SCHEDULING_TEL_HREF,
} from "@/lib/scheduling";

type FormState = {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  message: string;
};

export default function ContactForm() {
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A2P 10DLC: SMS consent — never pre-checked and never required to submit
  // (consent may not be a condition of service). Recorded server-side.
  const [smsConsent, setSmsConsent] = useState(false);
  const [form, setForm] = useState<FormState>({
    firstName: "",
    lastName: "",
    email: "",
    phone: "",
    message: "",
  });

  function update(field: keyof FormState) {
    return (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [field]: e.target.value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const result = await submitLead({
      name: `${form.firstName} ${form.lastName}`.trim(),
      email: form.email,
      phone: form.phone,
      message: form.message,
      smsConsent,
      sourcePage:
        typeof window !== "undefined" ? window.location.pathname : "/contact-us",
    });
    if (result.ok) {
      setSent(true);
    } else {
      setError(result.message || "Something went wrong. Please call us.");
    }
    setLoading(false);
  }

  const inputClass =
    "w-full rounded-xl border border-neutral-100 bg-neutral-25 px-4 py-3 text-sm text-neutral-700 placeholder-neutral-400 outline-none transition-colors focus:border-primary";

  if (sent) return <LeadReceived />;

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3">
        <input
          required
          placeholder="First name"
          value={form.firstName}
          onChange={update("firstName")}
          className={inputClass}
        />
        <input
          required
          placeholder="Last name"
          value={form.lastName}
          onChange={update("lastName")}
          className={inputClass}
        />
      </div>

      <input
        required
        type="email"
        placeholder="Email address"
        value={form.email}
        onChange={update("email")}
        className={inputClass}
      />

      <input
        required
        type="tel"
        placeholder="Phone number"
        value={form.phone}
        onChange={update("phone")}
        className={inputClass}
      />

      <textarea
        rows={3}
        placeholder="What can we help you with? (optional)"
        value={form.message}
        onChange={update("message")}
        className={`${inputClass} resize-none`}
      />

      {/* Scheduling happens on the phone, not on the site — the form only
          starts the conversation. */}
      <div className="rounded-xl border border-neutral-100 bg-neutral-25 p-4">
        <p className="text-sm font-semibold text-neutral-700">
          Want to schedule a test drive or visit?
        </p>
        <p className="mt-1 text-xs leading-5 text-neutral-500">
          Send this form and our team will follow up shortly — or call or text
          us at{" "}
          <a
            href={SCHEDULING_TEL_HREF}
            className="font-semibold text-primary underline underline-offset-2"
          >
            {SCHEDULING_PHONE_DISPLAY}
          </a>{" "}
          and we&apos;ll help get it set up.
        </p>
      </div>

      <label className="flex items-start gap-3 text-xs leading-5 text-neutral-500">
        <input
          type="checkbox"
          checked={smsConsent}
          onChange={(e) => setSmsConsent(e.target.checked)}
          className="mt-0.5 size-4 shrink-0 accent-primary"
        />
        <span>
          Optional: By checking this box, I agree to receive calls and text
          messages from Kelley Autoplex about my inquiry at the phone number
          provided, including via automated technology. Consent is not a
          condition of any purchase or service — you may submit this form
          without checking this box. Msg frequency varies. Msg &amp; data rates
          may apply. Reply STOP to opt out, HELP for help. See our{" "}
          <a href="/privacy-policy" target="_blank" className="font-medium text-primary underline">
            Privacy Policy
          </a>{" "}
          and{" "}
          <a href="/terms-and-conditions" target="_blank" className="font-medium text-primary underline">
            Terms
          </a>
          .
        </span>
      </label>

      {error && <p className="text-sm text-red-500">{error}</p>}

      <button
        type="submit"
        disabled={loading}
        className="rounded-xl bg-gradient-to-b from-[#f9896a] to-primary py-3.5 text-base font-semibold text-white transition-opacity hover:opacity-95 disabled:opacity-60"
      >
        {loading ? "Sending…" : "Send Inquiry"}
      </button>
    </form>
  );
}

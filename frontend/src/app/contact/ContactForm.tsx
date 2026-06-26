"use client";

import { useState } from "react";
import { submitLead } from "@/lib/publicApi";

type FormState = {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  message: string;
  preferredSlot: number | null;
};

const ALL_SLOTS = [10, 11, 12, 13, 14, 15, 16, 17];

function getAvailableSlots(): number[] {
  const hour = new Date().getHours();
  return hour >= 17 ? ALL_SLOTS.filter((h) => h >= 13) : ALL_SLOTS;
}


function formatSlot(h: number): string {
  if (h === 12) return "12:00 PM";
  return h > 12 ? `${h - 12}:00 PM` : `${h}:00 AM`;
}

function getTomorrow(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

export default function ContactForm() {
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>({
    firstName: "",
    lastName: "",
    email: "",
    phone: "",
    message: "",
    preferredSlot: null,
  });

  function update(field: keyof Omit<FormState, "preferredSlot">) {
    return (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [field]: e.target.value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.preferredSlot) {
      setError("Please select a preferred appointment time.");
      return;
    }
    setLoading(true);
    setError(null);
    const tomorrow = getTomorrow();
    const preferredTime = `${formatSlot(form.preferredSlot)} on ${tomorrow}`;
    const result = await submitLead({
      name: `${form.firstName} ${form.lastName}`.trim(),
      email: form.email,
      phone: form.phone,
      message: form.message,
      preferredTime,
      sourcePage:
        typeof window !== "undefined" ? window.location.pathname : "/contact",
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

  const slots = getAvailableSlots();

  if (sent) {
    return (
      <div className="rounded-2xl bg-green-50 p-8 text-center">
        <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-full bg-green-100">
          <svg className="size-7 text-green-600" fill="none" viewBox="0 0 24 24">
            <path
              d="M5 13l4 4L19 7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <p className="text-lg font-semibold text-green-800">Request received!</p>
        <p className="mt-1 text-sm text-green-600">
          We&apos;ll reach out shortly to confirm your appointment.
        </p>
      </div>
    );
  }

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

      {/* Time slot picker */}
      <div className="rounded-xl border border-neutral-100 bg-neutral-25 p-4">
        <p className="text-sm font-semibold text-neutral-700">
          Preferred appointment time
        </p>
        <p className="mt-1 text-xs text-neutral-400">
          By appointment only &middot; Next available:{" "}
          <span className="font-medium text-neutral-600">{getTomorrow()}</span>
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {slots.map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => setForm((f) => ({ ...f, preferredSlot: h }))}
              className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
                form.preferredSlot === h
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-neutral-100 bg-white text-neutral-600 hover:border-primary/40"
              }`}
            >
              {formatSlot(h)}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}

      <button
        type="submit"
        disabled={loading}
        className="rounded-xl bg-gradient-to-b from-[#f9896a] to-primary py-3.5 text-base font-semibold text-white transition-opacity hover:opacity-95 disabled:opacity-60"
      >
        {loading ? "Sending…" : "Request Appointment"}
      </button>
    </form>
  );
}

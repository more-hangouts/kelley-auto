# Bella's XV Fit Prep Tool Plan

## Purpose

The Fit Prep Tool is an optional education and appointment-prep experience for Bella's XV customers. It should help guests understand quinceanera formalwear sizing, arrive better prepared for their styling appointment, and give staff useful styling context before the guest walks in.

This tool must not replace the booking widget. Customers should always be able to book an appointment normally, whether or not they complete the Fit Prep Tool.

## Business Goals

- Differentiate Bella's XV from other dress shops by educating guests before the appointment.
- Set clear expectations around formalwear sizing, designer chart differences, alterations, timelines, and budget.
- Reduce guest anxiety about size numbers by explaining that quince/formalwear sizing often differs from everyday clothing.
- Help guests arrive prepared with measurements, shoes, undergarments, inspiration, and budget range.
- Give staff a useful prep summary so they can start appointments with better dress suggestions.
- Improve sales conversations by surfacing style direction, budget, event timing, fit preferences, and likely alteration needs.

## Product Positioning

The tool is:

- Optional.
- Educational.
- A preparation guide.
- A sales and styling support tool.
- A way to add helpful context to an appointment note.

The tool is not:

- Required before booking.
- A definitive dress-size calculator.
- A guarantee of fit.
- A replacement for stylist judgment.
- A replacement for designer-specific size charts.
- A promise that the customer should order a specific size.

## Required Language Guardrails

Use language like:

- "Estimated formalwear range"
- "You may fall around..."
- "Starting point for your appointment"
- "Your stylist will confirm using the designer's chart"
- "Formalwear sizing often runs 1-3 sizes smaller than everyday clothing"
- "Designer charts vary, so this is only a prep estimate"

Avoid language like:

- "Your dress size is..."
- "Guaranteed fit"
- "Find your perfect size"
- "Order this size"
- "Accurate size calculator"
- "Definitive size"

## Recommended Technical Approach

Build the tool as a lightweight embeddable JavaScript widget, matching the existing Bella's XV booking widget pattern.

Proposed file:

- `widgets/bellas-fit-prep-tool.js`

Proposed marketing page:

- `marketing/fit-prep.html`

Proposed homepage integration:

- Add a callout near the booking section on `marketing/index.html`.
- Link to the Fit Prep page with a clear optional CTA.
- Keep the existing appointment booking widget unchanged and always accessible.

This repo already uses a static marketing site plus embeddable widgets, so a standalone widget is the most natural fit. WordPress, Shopify, and a full Next.js app are unnecessary for the MVP.

## Customer Flow

1. Customer visits the Bella's XV site.
2. Customer can book immediately through the existing booking widget.
3. Customer also sees an optional prompt:
   "Not sure how quince dress sizing works? Try our Fit Prep Guide before your appointment."
4. Customer opens the Fit Prep Tool.
5. Tool collects measurements and styling preferences.
6. Tool educates the customer while they complete the form.
7. Tool shows an estimated formalwear size range and prep guidance.
8. Customer chooses whether to add the Fit Prep Summary to their appointment note.
9. If they book after completing the tool, the booking widget preloads the appointment note with the summary.
10. Customer can edit or remove the note before confirming.

## Core Customer Inputs

Required MVP inputs:

- Bust measurement
- Waist measurement
- Hip measurement
- Height
- Event date
- Preferred dress style
- Back preference: corset, zipper, not sure

Recommended sales-prep inputs:

- Budget range
- Favorite colors
- Styles or details the guest likes
- Styles or details the guest wants to avoid
- Inspiration notes or image/link, optional
- How soon the guest wants to choose a dress
- Who is coming to the appointment

## Core Customer Outputs

The customer-facing result should include:

- Estimated quince/formalwear size range.
- Explanation that formalwear sizing differs from everyday clothing.
- Explanation that designer charts vary.
- Reminder that the stylist confirms fit in store.
- Likely alteration areas.
- Appointment prep checklist.
- CTA to book the appointment.

Example customer-facing result:

```text
Based on your measurements, you may fall around a size 10 to 12 depending on designer, dress structure, and fit preference.

This is a preparation estimate, not a final dress size. Bella's XV carries multiple designers, and each designer may use a different chart. Your stylist will confirm the best size in store.
```

## Staff Prep Summary

The tool should generate a staff-friendly summary that can be inserted into the appointment note.

Example:

```text
Fit Prep / Styling Notes

Guest measurements:
Bust: 38"
Waist: 31"
Hips: 41"
Height: 5'3"
Event date: 2026-09-12

Estimated formalwear range:
Approx. size 10-12 depending on designer. Confirm with designer chart.

Style direction:
Preferred style: Ball gown
Back preference: Corset back
Budget range: $1,500-$2,000
Favorite colors: Red, champagne
Avoids: Mermaid silhouettes

Timeline:
Event is 5 months away. Normal ordering timeline likely okay.

Stylist prep:
Start with corset-back ball gowns around size 10-12.
Check hem length and bodice fit.
Discuss designer sizing differences early.
Likely alteration areas: hem, bodice, bust/waist fit.
```

The note must stay concise enough for the existing appointment note field. If the current backend limit remains 1000 characters, the generated summary should be compact.

## Sizing Logic

Use the provided Typical Quinceanera Size Chart as the foundation.

Known provided values:

| Size | Bust | Waist | Hips |
| --- | ---: | ---: | ---: |
| 0 | 32" | 23.5" | 35.5" |
| 18 | 44.5" | 36" | 48" |

The full chart from size 0 through 18 must be confirmed before final implementation.

Sizing rule:

- Map bust, waist, and hips separately to the smallest chart size that can accommodate each measurement.
- Select the largest resulting chart size.
- Present the result as an estimated range, not a single guaranteed size.

Example:

- Bust maps to size 10.
- Waist maps to size 12.
- Hips map to size 8.
- Tool recommends an estimated range around size 12.

Customer-facing wording:

```text
You may fall around a size 12 to 14 depending on designer, dress structure, and fit preference.
```

## Education Moments

The tool should teach throughout the flow, not only at the end.

Sizing education:

- Quinceanera dresses follow formalwear sizing.
- Formalwear often runs 1-3 sizes smaller than everyday clothing.
- The number on the tag is less important than fit, structure, and designer chart.

Designer education:

- Bella's XV carries multiple designers.
- Morilee, House of Wu, Dessy, and other designers may use different charts.
- A universal size chart can be misleading.

Back-style education:

- Corset backs can allow more flexibility through the bodice.
- Zipper backs usually require a more exact match to the designer's chart.

Alteration education:

- Most formal gowns need some adjustment.
- Common areas include hem, bodice, bust/waist fit, straps, and sleeves.

Timeline education:

- Earlier appointments usually give more options.
- Shorter timelines may require focusing on in-stock or faster-delivery options.

Budget education:

- Sharing a budget helps the stylist pull better options faster.
- Budget is used to guide the appointment, not to pressure the guest.

## Likely Alteration Logic

The tool can infer likely alteration areas in a careful, non-definitive way.

Possible rules:

- Height below standard gown length: hem likely.
- Taller height: length review or special length discussion may be needed.
- Large spread between bust, waist, and hip size mappings: bodice or waist adjustment likely.
- Corset back selected: note added flexibility through bodice.
- Zipper back selected: note that precise designer-chart confirmation is important.
- Strap, sleeve, off-shoulder, or fitted style selected: strap/sleeve/bodice review likely.

Use wording like:

```text
Common alteration areas to discuss with your stylist may include hem length, bodice fit, bust/waist fit, and straps or sleeves.
```

## Dress Suggestion Logic

The MVP does not need live inventory matching. It should generate general staff prep suggestions.

Possible suggestion rules:

- Corset back selected: start with corset-back gowns in the estimated range.
- Zipper selected: confirm designer-specific chart before recommending final size.
- Ball gown or princess selected: pull fuller quince silhouettes first.
- Fitted or mermaid selected: discuss mobility and hip fit carefully.
- Short event timeline: prioritize in-stock or faster-delivery options.
- Budget provided: pull dresses within range first.
- Guest is unsure: start with a mix of silhouettes to help them discover preferences.

Example staff suggestion:

```text
Start with corset-back ball gowns around size 10-12. Pull options within the stated budget first. Discuss designer sizing differences early and check hem/bodice fit.
```

## Appointment Note Integration

The existing booking widget already supports an optional customer note. The Fit Prep Tool should use this rather than blocking or replacing the booking flow.

Recommended MVP behavior:

- Fit Prep Tool generates a concise summary.
- Summary is saved to `localStorage`.
- Booking widget checks for saved fit prep summary.
- If present, it preloads the booking note field.
- Customer can edit or remove the note before submitting.

Suggested localStorage key:

- `bxv_fit_prep_summary`

Suggested tracking keys:

- `bxv_fit_prep_completed`
- `bxv_fit_prep_added_to_note`

Important behavior:

- Booking must work with no Fit Prep data.
- Booking must work with incomplete Fit Prep data.
- Fit Prep must never prevent appointment submission.
- Customer must see and control the note before submitting.

## Email Capture

Email capture is useful but should be secondary to appointment-note handoff.

Possible CTA:

- "Send me my fit prep guide"

MVP recommendation:

- Defer email capture until after the appointment-note integration is working.

Future implementation:

- Add backend endpoint: `POST /api/fit-prep/send`
- Reuse existing email transport.
- Send the customer their prep summary, checklist, and booking CTA.

## Spanish Version

Spanish support is valuable for Bella's XV customers.

MVP option:

- English only.

Recommended phase 2:

- Add language toggle inside the tool.
- Keep generated staff notes in English unless staff specifically wants bilingual notes.

Potential public page:

- `/fit-prep.html`
- Optional future route: `/prepara-tu-cita.html`

## MVP Scope

Build:

- `marketing/fit-prep.html`
- `widgets/bellas-fit-prep-tool.js`
- Homepage callout linking to Fit Prep page.
- Measurement and preference form.
- Educational copy during the flow.
- Estimated size range output.
- Likely alteration areas.
- Appointment checklist.
- Staff prep summary.
- LocalStorage handoff to booking widget note.
- Booking widget enhancement to preload fit prep summary into note.

Do not build in MVP:

- Live inventory matching.
- Required pre-appointment completion.
- Designer-specific final sizing.
- Customer account storage.
- PDF generation.
- Spanish version, unless explicitly prioritized.
- Email delivery, unless explicitly prioritized.

## Implementation Phases

### Phase 1: Product and Content Foundation

- Confirm complete size chart from size 0 through 18.
- Finalize customer-facing disclaimer language.
- Finalize staff note format.
- Finalize preferred style options and budget ranges.

### Phase 2: Fit Prep Widget MVP

- Create embeddable widget.
- Add form fields.
- Add validation.
- Add size range logic.
- Add educational result screen.
- Add "Add to appointment note" action.
- Store generated summary in localStorage.

### Phase 3: Marketing Page Integration

- Create `marketing/fit-prep.html`.
- Match Bella's XV visual style.
- Add CTA to book appointment.
- Add homepage callout to the fit prep page.

### Phase 4: Booking Widget Handoff

- Read Fit Prep summary from localStorage.
- Prefill appointment note if available.
- Show a small note that the summary was added and can be edited.
- Ensure booking still works normally without Fit Prep data.

### Phase 5: Staff and Sales Enhancements

- Add stronger staff prep suggestions.
- Add timeline urgency rules.
- Add budget and style direction flags.
- Consider admin display improvements if notes become too compressed.

### Phase 6: Optional Enhancements

- Spanish toggle.
- Email "send me my guide."
- Post-booking prep link.
- Designer-specific chart selection.
- Inventory-aware dress suggestions.
- Analytics for completion and booking conversion.

## Open Questions

- What is the full Typical Quinceanera Size Chart from size 0 through 18?
- What budget ranges should Bella's XV display?
- Which preferred dress styles should be offered?
- Should staff notes be English only or bilingual?
- Should customers be able to upload inspiration photos, or only paste a link/note for MVP?
- Should the fit prep result page embed the booking widget directly or link back to the homepage booking section?
- Should the generated appointment note include all measurements or only a compact summary?

## Success Criteria

- Customers can still book an appointment without using the tool.
- Customers who use the tool understand that the size range is only an estimate.
- The tool clearly explains formalwear sizing and designer variation.
- The tool produces a useful appointment note for staff.
- Staff can use the note to prepare dress suggestions before the appointment.
- The site feels more helpful and premium without adding friction to booking.

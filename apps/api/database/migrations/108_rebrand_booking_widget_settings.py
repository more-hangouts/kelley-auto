"""Rebrand the booking-widget singleton from Bella's XV to Kelley Autoplex.

The b99e212 rebrand swept code — admin SPA, emails, storefront, public
codes (088) — but the booking widget's branding is *data*: the singleton
row in ``booking_widget_theme_settings``, seeded with boutique defaults by
migration 011. Migration seeds are immutable, so 011 keeps its mauve
palette and "Bellas XV" copy forever; the only correct fix is a follow-up
migration that updates the row. Editing 011 in place would break replay
for anyone who already ran it (see the compat-package precedent in the
Phase 3 modularization).

Doing this as a migration rather than a one-off prod ``UPDATE`` matters
because a fresh database still runs 011 and would still come up branded as
a quinceañera boutique. Prod and a clean checkout have to converge.

**Copy keys are realigned, not just reworded.** The seeded key set had
drifted from what ``widgets/kelley-booking-widget.js`` actually reads —
the row carried ``step2_celebrant_label`` / ``step2_party_solo`` /
``step2_party_2_3`` / ``step2_party_4_plus``, none of which the widget
looks up, while the widget's real keys (``step2_celebrant_heading``,
``step2_parent_*``, ``step2_party_pair`` …) were absent and silently fell
back to hardcoded quinceañera strings. Rewording only the seeded keys
would have changed nothing visible in the widget. The four dead keys are
dropped and the widget's actual keys are populated.

``boutique_label`` is deliberately kept rather than replaced with
``location_label``: the widget prefers ``location_label`` and falls back
to ``boutique_label``, but the admin Widget settings page edits
``boutique_label``. Setting both would make the admin field a no-op — the
widget would keep showing the ``location_label`` value no matter what
staff typed. One key, edited where it is displayed.

**Fonts.** Playfair Display was never actually loaded by the embed, so
headings have been falling back to the browser's default serif on every
host page. The replacement stack is Inter/system-ui for both heading and
body — the storefront's display face (Bebas Neue) is a webfont the
embedding page has no reason to have, and naming it would reproduce the
same silent-fallback bug in a new color.

Colors are the storefront tokens from ``apps/storefront/src/app/globals.css``
so the widget matches the site it will be embedded on: primary #157A33,
primary-dark #0F5E26, neutral-800 text, neutral-500 muted, neutral-25 bg.

The update is guarded on ``header_brand = 'Bellas XV'`` — the untouched
seed. If someone has already customized the row by hand, theirs wins and
this migration is a no-op rather than an overwrite. ``updated_by`` is left
NULL because no user made this change.
"""

from sqlalchemy import text


THEME = """{
    "color_bg": "#F8F9FB",
    "color_surface": "#FFFFFF",
    "color_accent": "#157A33",
    "color_accent_dark": "#0F5E26",
    "color_text": "#15161E",
    "color_text_muted": "#525766",
    "font_heading": "Inter, system-ui, sans-serif",
    "font_body": "Inter, system-ui, sans-serif",
    "radius": "16px"
}"""

COPY = """{
    "header_brand": "Kelley Autoplex",
    "header_title": "Schedule a visit",
    "header_subtitle": "Pick a time to come see a vehicle. We''ll have it pulled up front and ready when you get here.",
    "step1_heading": "Pick a date and time",
    "step2_heading": "Who''s coming in?",
    "step2_parent_heading": "Your name",
    "step2_parent_hint": "Who should we contact about the appointment?",
    "step2_parent_first_name_label": "First name",
    "step2_parent_last_name_label": "Last name",
    "step2_celebrant_heading": "Who''s the vehicle for?",
    "step2_celebrant_hint": "First name of whoever will be driving it.",
    "step2_event_date_label": "Target purchase date (if known)",
    "step2_party_size_label": "Who''s coming to the appointment?",
    "step2_party_pair": "Two of us",
    "step2_party_3_4": "3-4 of us",
    "step2_party_5_plus": "5 or more",
    "step3_heading": "How do we reach you?",
    "step3_phone_label": "Phone number",
    "step3_email_label": "Email",
    "step3_note_label": "Anything you''d like us to know? (optional)",
    "marketing_consent_label": "Send me deals and new inventory updates",
    "submit_label": "Confirm appointment",
    "success_heading": "You''re booked.",
    "success_subtitle": "We just emailed your confirmation. See you at the lot.",
    "boutique_label": "Kelley Autoplex",
    "timezone_label": "America/Chicago"
}"""


def upgrade(connection) -> None:
    connection.execute(
        text(
            f"""
            UPDATE booking_widget_theme_settings
               SET theme = '{THEME}'::jsonb,
                   copy  = '{COPY}'::jsonb,
                   updated_at = NOW()
             WHERE id = 1
               AND copy->>'header_brand' = 'Bellas XV'
            """
        )
    )

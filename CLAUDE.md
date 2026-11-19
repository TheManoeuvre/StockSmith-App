# StockSmith — notes for Claude

See `README.md` for what the app is and `docs/roadmap.md` for where it's going.

## Every change: consider onboarding & documentation

StockSmith is heading for public distribution, so every feature and PR must consider its
impact on new users. Before finishing any change, check against `docs/plan-onboarding.md`:

- Does a fresh install need a default or seed for this (`backend/app/seed.py`)?
- Does it change the setup order or add a step to the onboarding checklist?
- Does it need or change an empty state, a page intro, or an in-app help doc?
- Does it make any existing doc, intro or seed wrong?

Fill in the "Onboarding & docs impact" section of the PR template. "None" is a valid
answer; leaving it blank is not.

StockSmith is for sellers anywhere — dates and numbers follow the computer's locale, and currency
is a setting. Its author tests in the UK, so UK defaults (fees, Royal Mail postage) are the ones
seeded first. Flag anything UK-specific so other markets can be handled, and never hard-code a
locale for display.

## User-facing help lives in `docs/help/`

Plain markdown, readable on GitHub and bundled into the app's Help page (see
`frontend/src/lib/help.ts`). When a change alters what a user sees or does, update the
matching page in the same PR. Adding a page means adding its file, its slug in `HELP_SECTIONS`
and a link from `docs/help/README.md` — `frontend/src/lib/help.test.ts` fails otherwise. Link
into help from the UI with `<HelpLink doc="…" anchor="…">`, which is typed against the page list.

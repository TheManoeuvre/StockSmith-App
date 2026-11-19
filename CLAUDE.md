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
answer; leaving it blank is not. Defaults are UK-only for now — flag anything UK-specific.

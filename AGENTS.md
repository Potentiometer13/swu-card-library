# Project conventions

This is an established Star Wars Unlimited card library and Twin Suns deck builder,
using Python, Streamlit Community Cloud, Supabase PostgreSQL/Auth, and GitHub Actions.
The current repository is the source of truth. Preserve working functionality and
make the smallest changes that satisfy the requested task. Do not implement future
ideas, rebuild, reorganize, or change export formatting without a specific request.

## Workflow

- Inspect relevant code and integration points before editing; explain the approach briefly.
- Use a development branch and provide a pull request when supported and authorized.
  Cloud tasks already have isolated checkouts: use the existing checkout and do not
  create a Git worktree unless the user explicitly requests one.
- Preserve existing user changes. Never reset or overwrite unrelated work.
- Run Python syntax checks and relevant tests (see README.md). Add meaningful
  regression coverage for deck rules, inventory, imports, exports, and authentication.
- Existing offline scripts use module stubs; run each in a separate Python process.
- Report files changed, reasons, test results, and remaining live-verification limits.
  Do not present mocked/offline tests as proof that Supabase or browser behavior works.

## Architecture and behavior to preserve

- app.py: eight numbered tabs, gallery queries, filters, and navigation.
- swu_grouping.py, swu_leaders.py, swu_bases.py: grouped printings and galleries.
- swu_twin_suns.py: deck rules, main/sideboard state, previews, statistics, buying lists.
- swu_deck_storage.py: authentication, snapshots, online saves, and export UI.
- swu_swudb_json.py: SWUDB-compatible import/export.
- swu_collection_progress.py: per-deck physical progress.
- swu_inventory.py, swu_my_collection.py: account-wide inventory and collection UI.
- swu_prices.py, swu_price_ui.py: cached TCGCSV prices and explicit printing selection.
- import_cards.py: scheduled database updates; running it writes data.

Preserve gallery filters, alternate-printing selection, aspect compatibility, niche
set controls, and direct deck/sideboard/collection actions. Twin Suns uses two distinct
compatible leaders, one base, at least 80 main-deck cards, and singleton limits except
explicit card-text exceptions. Apply copy limits across main deck and sideboard.
Moving copies between zones must preserve physical Added status. Statistics count
main-deck quantities only.

Deck physical progress and account inventory are separate concepts. Group inventory
by gameplay identity, not printing. Bulk transfers must validate quantities and be
atomic. Maintain SWUDB metadata/leader/secondleader/base/deck/sideboard fields and the
swuCardLibrary extension, including progress in both zones. Preserve official roster,
buylist, and set-organized export formatting, scopes, rarity filters, release ordering,
multiple-printing references, and missing quantities.

## Security and data safety

- Never hardcode, print, commit, or expose passwords, tokens, or secret values.
  Use private Streamlit secrets/environment settings; do not request secrets in chat.
- The interactive app uses the Supabase publishable key and authenticated per-user
  clients. Never place service-role/secret credentials in the app or browser.
- Preserve Supabase row-level security and account isolation. Client-side user filters
  are insufficient security: database policies and RPCs must enforce identity.
- Do not alter production data, run database import jobs, deploy destructive SQL
  migrations, or change database permissions without explicit user approval.
- Provide proposed migration SQL and explain its effects when database changes are needed.
  Do not invent the existing schema or claim policies were verified without inspecting them.
- Preserve Remember Me encryption, token validation/rotation, expiry, and secure cookie
  settings. Never store plaintext passwords or tokens in browser cookies.
- Inspect credential names/presence only. Preserve TLS, artifact integrity, and package
  verification during setup. Keep local credential files out of Git.

## Interface conventions

Keep controls compact, tables dense, buttons appropriately narrow, and card galleries
consistent. Avoid horizontal row scrolling, excessive spacing, unnecessary reruns,
and unrelated style changes. Preserve cursor-position card previews and the shared
Show Cards selector. All Decks is reserved for future sharing; do not change privacy
or add sharing until requested.

## Pricing

Use TCGCSV USD market prices per copy, not an invented listing average. Preserve
explicit printing choices and physical progress when updating price defaults.
Never guess prices for unmatched printings or treat missing quotes as zero. Keep
price requests server-side and cached; follow TCGCSV's daily refresh, timestamp,
custom User-Agent, and request-spacing guidelines. No production schema change is
required for the local price cache.

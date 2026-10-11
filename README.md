# SWU Card Library

Star Wars Unlimited card search, Twin Suns deck builder, and collection tracker.
Python/Streamlit provides the UI; Supabase provides PostgreSQL and authentication.
See AGENTS.md for permanent development conventions and safety requirements.

## Local development

Python 3.12 is validated for local development. The card-import GitHub Action uses
Python 3.11. From the repository root:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip check
python -m streamlit run app.py --server.headless=true --browser.gatherUsageStats=false
```

requirements.txt describes direct dependencies; requirements-lock.txt records the
resolved versions validated on Python 3.12/Linux. Refresh the latter intentionally
in a clean virtual environment after dependency changes and rerun the checks.
Streamlit Community Cloud continues to use requirements.txt.

Create the ignored `.streamlit/secrets.toml` locally using secure configuration:

```toml
SUPABASE_URL = "https://your-development-project.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "<development-project publishable key>"

[SWU_ACCOUNT_EMAILS]
Josh = "<provisioned account email>"
Nick = "<provisioned account email>"
# Account 3 through Account 6 may also be mapped.
```

For Remember Me, add a private random `SWU_REMEMBER_SECRET` of at least 32 characters
at the top level, before `[SWU_ACCOUNT_EMAILS]`. Without it, persistent sign-in is
disabled. Never use the Supabase service-role/secret key for the interactive app.
Keep credentials out of Git. Use a development database for write testing.

## Checks

Run these scripts individually; their stubs are isolated by separate processes:

```sh
python test_collection_progress.py
python test_official_deck_list.py
python test_simplified_bases.py
python test_saved_decks.py
python test_prices.py
python -m compileall -q app.py swu_*.py import_cards.py test_*.py
```

Offline checks cover progress, JSON compatibility, sideboard moves and copy limits,
text exports, base filtering, and saved-deck pagination/inventory totals. They do not
verify database policies or actual browser cookies. Live validation requires configured
Supabase access: galleries, separate-user sign-in/save/load, inventory updates,
failed bulk-transfer rollback, and Remember Me across browser restarts.

## Supabase prerequisites

The repository does not contain authoritative migration SQL. Obtain the existing
schema/policies from the project administrator before provisioning a new database;
do not infer or recreate production definitions from Python callers.

The app references:

- Tables: `card_sets`, `card_printings`, `swu_saved_decks`, `swu_owned_cards`.
- Views: `swu_grouped_cards`, `swu_grouped_leaders`, `swu_grouped_bases`,
  `swu_filter_values`.
- RPCs: `swu_adjust_owned_card(p_gameplay_id, p_delta)` and
  `swu_adjust_owned_cards_bulk(p_changes)`.
- Provisioned Supabase Auth users matching the private account-email mapping.

RLS must isolate saved decks and inventory by authenticated user. Inventory RPCs must
verify identity, reject invalid/negative totals, and make bulk updates transactional.
These database guarantees require separate inspection and live tests.

## Card import job

`.github/workflows/import-cards.yml` runs `import_cards.py` manually or daily. It reads
`SUPABASE_URL` and `SUPABASE_SECRET_KEY` from GitHub Actions secrets and downloads from
`api.swuapi.com` and `api.swu-db.com`. It upserts card sets, printings, and
`card_double_aspect_overrides`. It is a database write operation, not a development
smoke test; do not run it against production without explicit approval.

## TCGCSV prices and printing selection

The deck and sideboard tables show **Price** after Remove: TCGplayer USD market price
per copy, supplied by [TCGCSV](https://tcgcsv.com/docs). This is a market estimate,
not an average of listings or a guaranteed purchase price; shipping is excluded.

New cards default to the cheapest quoted printing available in the application's
printing catalog. Click a card image in the Cards or Deck Builder gallery to select
a specific printing. Explicit choices persist through native saves and moves between
main deck and sideboard. Imported/older decks preserve their specified printings.
Each deck-zone row uses one selected printing for all its copies. A manual gallery
selection updates that card's printing in both zones, without changing quantities.

`swu_prices.py` syncs public Star Wars Unlimited category 79 products and prices to
an ignored `.cache/tcgcsv-prices.json` file. No API key or database migration is needed.
The server initializes this cache on first use; it can also be populated before startup:

```sh
python swu_prices.py
```

The cache is refreshed at most daily, checking `last-updated.txt` first, identifying
requests as `SWU-Card-Library/1.0`, and spacing them by 100 ms as required by TCGCSV.
Keep the cache directory writable. Allow HTTPS access to `tcgcsv.com` in restricted
cloud environments. Only the backend fetches prices; the browser reads cached data.
Existing quotes remain available after a refresh failure. A dash means no exact
match/quote; unpriced printings are excluded from cheapest-price comparisons.

Matching uses set abbreviation, collector number, full card title, and Normal/Foil
finish. Unmatched promotional set codes are left unpriced rather than guessed.
`test_prices.py` includes an isolated Streamlit test of the price column and manual
printing selection, with mocked database/catalog data; it does not test Supabase.

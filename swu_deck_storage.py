"""Stage 2G.2: private, account-based deck saves plus portable JSON backups.

No service-role keys. Read/write uses a dedicated per-session Supabase Auth
client; row-level security isolates each person's decks.
"""

from datetime import datetime, timezone
import time
import json
import re

from swu_swudb_json import export_swudb, parse_swudb_json, resolve_swudb
from swu_collection_progress import (
    PROGRESS_SESSION_KEY, normalize_progress_map, required_cards, progress_totals,
)
from swu_twin_suns import (
    card_copy_limit, card_identity, get_selected_leaders, leaders_can_pair,
    tcgplayer_missing_text,
    all_needed_candidates, fetch_bulk_printing_info, bulk_needed_text,
    official_deck_list_text,
)

SAVE_TABLE = "swu_saved_decks"
FORMAT = "Twin Suns"
SCHEMA_VERSION = 1
MAX_FILE_BYTES = 2_000_000
MAX_ENTRIES = 500
MAX_QUANTITY = 1000
MAX_NAME_LENGTH = 80
ACCOUNT_NAMES = ("Josh", "Nick", "Account 3", "Account 4", "Account 5", "Account 6")
MIN_PASSWORD_LENGTH = 6  # Supabase Auth hosted projects reject shorter passwords.
FIELDS = (
    "uuid", "gameplay_id", "name", "subtitle", "set_code",
    "collector_number", "variant_type", "front_image_url", "back_image_url",
    "aspects", "traits", "keywords", "card_type", "arena", "cost", "power",
    "hp", "rarity", "rules_text",
)
ARRAY_FIELDS = {"aspects", "traits", "keywords"}
NUMERIC_FIELDS = {"cost", "power", "hp"}


def _safe_card(value):
    if not isinstance(value, dict):
        raise ValueError("Card data must be an object.")
    card = {}
    for key in FIELDS:
        item = value.get(key)
        if key in ARRAY_FIELDS:
            card[key] = [str(v)[:100] for v in (item or [])[:60]] if isinstance(item, list) else []
        elif key in NUMERIC_FIELDS:
            if item is None or item == "":
                card[key] = None
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                card[key] = item
            elif isinstance(item, str) and item.lstrip("-").isdigit():
                card[key] = int(item)
            else:
                card[key] = None
        else:
            card[key] = str(item or "")[:10000 if key == "rules_text" else 512]
    for key in ("front_image_url", "back_image_url"):
        if card[key] and not card[key].startswith("https://"):
            card[key] = ""
    if not card.get("uuid") or not card_identity(card):
        raise ValueError("A card or leader is missing its database identity.")
    return card


def normalize_snapshot(data):
    """Validate file/db data before allowing it to replace a live deck."""
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise ValueError("Not a supported Twin Suns deck file (version 1 required).")
    if data.get("format") != FORMAT:
        raise ValueError("Only Twin Suns decks can be loaded here.")
    name = str(data.get("name") or "").strip()
    if not name or len(name) > MAX_NAME_LENGTH:
        raise ValueError("Deck name must be between 1 and 80 characters.")
    leaders_raw = data.get("leaders")
    if not isinstance(leaders_raw, list) or len(leaders_raw) > 2:
        raise ValueError("Twin Suns has at most two leaders.")
    leaders = []
    for raw in leaders_raw:
        leader = _safe_card(raw)
        allowed, reason = leaders_can_pair(leaders, leader)
        if not allowed:
            raise ValueError("Invalid leader combination: " + reason)
        leaders.append(leader)
    base_raw = data.get("base")
    base = _safe_card(base_raw) if base_raw is not None else None
    rows = data.get("cards")
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise ValueError("Deck has too many or malformed card entries.")
    entries = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("A draw-deck entry is not an object.")
        card = _safe_card(row.get("card"))
        qty = row.get("count")
        if isinstance(qty, bool) or not isinstance(qty, int) or not (1 <= qty <= MAX_QUANTITY):
            raise ValueError("Card quantities must be positive whole numbers.")
        gid = card_identity(card)
        if gid in entries:
            raise ValueError("Deck file contains the same gameplay card twice.")
        limit = card_copy_limit(card)
        if limit is not None and qty > limit:
            raise ValueError(f"Copy limit exceeded for {card.get('name')}.")
        entries[gid] = {"card": card, "count": qty}
    author = str(data.get("author") or "").strip()
    if len(author) > 120:
        raise ValueError("Author name must be at most 120 characters.")
    card_rows = [entries[gid] for gid in sorted(entries)]
    required = required_cards(leaders, base, card_rows)
    progress = normalize_progress_map(data.get("card_progress", {}), required)
    return {
        "version": SCHEMA_VERSION, "format": FORMAT, "name": name, "author": author,
        "leaders": leaders, "base": base,
        "cards": card_rows, "card_progress": progress,
    }


def snapshot_from_session(session, name):
    raw = {
        "version": SCHEMA_VERSION, "format": FORMAT, "name": name,
        "author": session.get("swu_deck_author", ""),
        "leaders": get_selected_leaders(session),
        "base": session.get("swu_selected_base"),
        "cards": list((session.get("swu_twin_suns_cards") or {}).values()),
        "card_progress": session.get(PROGRESS_SESSION_KEY) or {},
    }
    return normalize_snapshot(raw)


def restore_snapshot(session, snapshot):
    data = normalize_snapshot(snapshot)
    session["swu_selected_leaders"] = data["leaders"]
    if data["leaders"]:
        session["swu_selected_leader"] = data["leaders"][0]
    else:
        session.pop("swu_selected_leader", None)
    if data["base"]:
        session["swu_selected_base"] = data["base"]
    else:
        session.pop("swu_selected_base", None)
    session["swu_twin_suns_cards"] = {
        card_identity(entry["card"]): entry for entry in data["cards"]
    }
    session["swu_deck_name"] = data["name"]
    session["swu_deck_author"] = data.get("author", "")
    session[PROGRESS_SESSION_KEY] = data.get("card_progress", {})
    # Remove old progress widget values so importing another deck displays
    # its values, instead of retaining the prior deck's toggles.
    for key in list(session):
        if key.startswith(("swu_progress_in_", "swu_progress_owned_")):
            session.pop(key, None)
    # Refresh leader-dependent defaults in the Bases filter; don't touch
    # card search filters or user layout preferences.
    for key in ("swu_base_last_leader_signature", "swu_base_last_leader_ids"):
        session.pop(key, None)
    return data


def backup_bytes(snapshot):
    return json.dumps(normalize_snapshot(snapshot), indent=2, ensure_ascii=False).encode("utf-8")


def upload_snapshot(raw):
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Deck file is too large (2 MB maximum).")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Not a valid UTF-8 JSON deck file.") from exc
    return normalize_snapshot(data)


def _client_from_session(st, make_client):
    state = st.session_state
    if not state.get("swu_auth_access") or not state.get("swu_auth_refresh"):
        return None
    client = make_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_PUBLISHABLE_KEY"])
    try:
        response = client.auth.set_session(state["swu_auth_access"], state["swu_auth_refresh"])
        user = client.auth.get_user().user
        if not user or str(user.id) != state.get("swu_auth_user_id"):
            raise RuntimeError("User session could not be verified.")
        if response.session:
            state["swu_auth_access"] = response.session.access_token
            state["swu_auth_refresh"] = response.session.refresh_token
    except Exception:
        _clear_auth(state)
        raise RuntimeError("Your sign-in has expired. Please sign in again.") from None
    return client


def _clear_auth(state):
    for key in ("swu_auth_access", "swu_auth_refresh", "swu_auth_user_id",
                "swu_auth_email", "swu_auth_name", "swu_decks_cache",
                "swu_confirm_deck_delete", "swu_cloud_selected_deck",
                "swu_owned_cache"):
        state.pop(key, None)


def _set_auth(state, response):
    if not response.user or not response.session:
        return False
    state["swu_auth_access"] = response.session.access_token
    state["swu_auth_refresh"] = response.session.refresh_token
    state["swu_auth_user_id"] = str(response.user.id)
    state["swu_auth_email"] = str(response.user.email or "")
    return True


def _deck_filename(name):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")[:70]
    return (cleaned or "twin_suns_deck") + ".json"


def added_cards_for_collection(session):
    """Copies physically marked Added in this deck, keyed by gameplay ID.

    Includes the leaders/base if their own In-deck trackers are checked.
    Excludes cards merely present in the digital deck list.
    """
    selections = required_cards(
        get_selected_leaders(session), session.get("swu_selected_base"),
        list((session.get("swu_twin_suns_cards") or {}).values()),
    )
    progress = normalize_progress_map(session.get(PROGRESS_SESSION_KEY) or {}, selections)
    return {
        gid: record["inDeck"]
        for gid, (_, quantity) in selections.items()
        if (record := progress.get(gid, {"inDeck": 0}))["inDeck"] > 0
    }


def render_deck_storage(st, make_client):
    """A standalone Streamlit UI; call before render_deck_builder(st)."""
    state = st.session_state
    # Apply restored decks BEFORE rendering keyed name/author text inputs.
    # Streamlit disallows overwriting their session-state values after rendering.
    pending = state.pop("swu_pending_deck_restore", None)
    if pending is not None:
        restore_snapshot(state, pending)
        # All four tabs must redraw from the newly restored deck.
        st.rerun()
    st.subheader("My Twin Suns Decks")
    st.caption("SWUDB-compatible JSON also saves card collection progress. Online saves preserve it too.")
    deck_name = st.text_input(
        "Deck name", value="Untitled Twin Suns Deck", key="swu_deck_name",
        max_chars=MAX_NAME_LENGTH,
    )
    author = st.text_input(
        "Author", value="Potentiometer13", key="swu_deck_author", max_chars=120,
    )
    try:
        snapshot = snapshot_from_session(state, deck_name)
    except ValueError as exc:
        st.warning(str(exc))
        snapshot = None
    # Streamlit calls this only when the uploaded file changes, not on every
    # widget interaction or rerun. This prevents a previous upload from
    # unexpectedly overwriting changes made to the deck afterward.
    def _auto_import_swudb():
        state.pop("swu_import_error", None)
        state.pop("swu_import_success", None)
        uploaded = state.get("swu_deck_import")
        if uploaded is None:
            return
        try:
            parsed = parse_swudb_json(uploaded.getvalue())
            db = make_client(
                st.secrets["SUPABASE_URL"],
                st.secrets["SUPABASE_PUBLISHABLE_KEY"],
            )
            resolved = resolve_swudb(db, parsed)
        except Exception as exc:
            # The active deck is left untouched if parsing or lookup fails.
            state["swu_import_error"] = str(exc)
        else:
            # At the start of the next render this is restored in one step,
            # before the deck name/author inputs are initialized.
            state["swu_pending_deck_restore"] = resolved
            state["swu_import_success"] = f"Imported '{parsed['name']}' successfully."

    # Import/export and collection transfer are separate deck operations.
    import_tab, export_tab, modify_collection_tab = st.tabs(
        ["Import", "Export", "Modify Collection"]
    )
    with import_tab:
        st.file_uploader(
            "Upload JSON",
            type=["json"],
            key="swu_deck_import",
            on_change=_auto_import_swudb,
            help="Automatically imports a SWUDB-compatible deck JSON file.",
        )
        if state.get("swu_import_error"):
            st.error(f"Import failed: {state['swu_import_error']}")
        elif state.get("swu_import_success"):
            st.success(state["swu_import_success"])

    with export_tab:
        st.caption(
            "Download your deck JSON, official-style TXT roster, TCGplayer "
            "shopping list, or set-organized missing-card lists."
        )
        json_col, official_col, shopping_col = st.columns(3, gap="small")
        with json_col:
            if snapshot is not None:
                try:
                    swudb_bytes = export_swudb(snapshot, author=author)
                except ValueError as exc:
                    st.warning(f"Deck JSON export unavailable: {exc}")
                else:
                    st.download_button(
                        "Export Deck JSON",
                        swudb_bytes,
                        file_name=_deck_filename(deck_name),
                        mime="application/json",
                        key="swu_export_swudb",
                        use_container_width=True,
                    )
            else:
                st.button("Export Deck JSON", disabled=True, use_container_width=True)

        with official_col:
            if snapshot is not None:
                try:
                    official_text = official_deck_list_text(snapshot)
                except (ValueError, TypeError, KeyError) as exc:
                    st.warning(f"Official deck-list export unavailable: {exc}")
                    official_text = ""
            else:
                official_text = ""
            st.download_button(
                "Export Official Deck List",
                data=official_text.encode("utf-8"),
                file_name=(
                    _deck_filename(deck_name).removesuffix(".json")
                    + "_official_deck_list.txt"
                ),
                mime="text/plain",
                key="swu_export_official_deck_list",
                use_container_width=True,
                disabled=not official_text,
                help=(
                    "Plain-text deck roster: two leaders, base, main-deck total, "
                    "cards sorted by type and cost, and sideboard total."
                ),
            )

        selections, progress = {}, {}
        try:
            # Include both leaders and the base alongside the draw deck.
            selections = required_cards(
                get_selected_leaders(state),
                state.get("swu_selected_base"),
                list((state.get("swu_twin_suns_cards") or {}).values()),
            )
            progress = normalize_progress_map(
                state.get(PROGRESS_SESSION_KEY) or {}, selections
            )
            shopping_text = tcgplayer_missing_text(selections, progress)
        except ValueError as exc:
            st.warning(f"Needed-card export unavailable: {exc}")
            shopping_text = ""

        with shopping_col:
            st.download_button(
                "Export Needed Card List",
                data=shopping_text.encode("utf-8"),
                file_name=_deck_filename(deck_name).removesuffix(".json") + "_needed_cards.txt",
                mime="text/plain",
                key="swu_shopping_text",
                use_container_width=True,
                disabled=not shopping_text,
                help="One line per missing card, formatted for TCGplayer Mass Entry.",
            )

        # One cross-printing lookup shared by all three set-grouped downloads.
        # The cache depends on gameplay IDs and card types, not ownership state,
        # while the generated text recalculates on every rerun.
        grouped_exports = {"bulk": "", "nonbulk": "", "all": ""}
        try:
            candidates = all_needed_candidates(selections, progress)
            if candidates:
                key = tuple(sorted(
                    (str(gid), str(card.get("card_type") or ""))
                    for gid, (card, _) in candidates.items()
                ))
                cached = state.get("swu_bulk_printing_cache")
                if (isinstance(cached, dict) and cached.get("key") == key
                        and time.time() - cached.get("created_at", 0) < 3600):
                    printing_info = cached["printing_info"]
                else:
                    db = make_client(
                        st.secrets["SUPABASE_URL"],
                        st.secrets["SUPABASE_PUBLISHABLE_KEY"],
                    )
                    printing_info = fetch_bulk_printing_info(db, candidates)
                    state["swu_bulk_printing_cache"] = {
                        "key": key, "created_at": time.time(),
                        "printing_info": printing_info,
                    }
                for category in grouped_exports:
                    grouped_exports[category] = bulk_needed_text(
                        candidates, progress, printing_info, rarity_filter=category
                    )
        except Exception as exc:
            st.warning(f"Set-organized export unavailable: {exc}")
            grouped_exports = {"bulk": "", "nonbulk": "", "all": ""}

        bulk_col, nonbulk_col, all_col = st.columns(3, gap="small")
        stem = _deck_filename(deck_name).removesuffix(".json")
        for column, category, label, filename, widget_key, explanation in (
            (
                bulk_col, "bulk", "Export Needed Bulk", "_needed_bulk.txt",
                "swu_shopping_bulk_text",
                "Missing cards that are not Rare or Legendary, grouped by set.",
            ),
            (
                nonbulk_col, "nonbulk", "Export Needed Non-Bulk",
                "_needed_nonbulk.txt", "swu_shopping_nonbulk_text",
                "Missing Rare and Legendary cards, grouped by set.",
            ),
            (
                all_col, "all", "Export All Needed", "_all_needed_by_set.txt",
                "swu_shopping_all_needed_set_text",
                "All missing cards (Bulk + Non-Bulk), grouped by set.",
            ),
        ):
            with column:
                content = grouped_exports[category]
                st.download_button(
                    label,
                    data=content.encode("utf-8"),
                    file_name=stem + filename,
                    mime="text/plain", key=widget_key,
                    use_container_width=True, disabled=not content,
                    help=explanation,
                )
        st.caption(
            "Bulk: missing non-Rare/non-Legendary. Non-Bulk: missing "
            "Rare/Legendary. All Needed: both combined. Lists use set sections, "
            "niche-set codes and ((repeat references)) without counting extra copies."
        )
    with modify_collection_tab:
        st.write("**Modify your account's collection using this deck**")
        st.caption(
            "Only copies marked **Added** (physically in this deck) are transferred. "
            "That includes leaders and your base if marked In deck. "
            "Unmarked deck-list cards are ignored."
        )
        transfer = st.radio(
            "Collection action",
            ["Add to collection", "Remove from collection"],
            horizontal=True, key="swu_bulk_inventory_action",
        )
        if transfer == "Add to collection":
            st.write(
                "**Add to collection** increases your account's Owned quantities "
                "for every marked copy in this deck."
            )
        else:
            st.write(
                "**Remove from collection** subtracts those marked copies from "
                "your account's Owned quantities—for example, when giving "
                "away this physical deck. The transfer is canceled if you "
                "don't own enough of any card."
            )
        changes = added_cards_for_collection(state)
        copy_count = sum(changes.values())
        st.caption(f"Marked cards: {copy_count} copies across {len(changes)} unique cards.")
        if not state.get("swu_auth_user_id"):
            st.info("Sign in on **0. Sign in** to modify your collection.")
        elif not changes:
            st.info("Mark cards as Added in the Deck section first.")
        else:
            st.caption(
                "Each successful transfer changes Owned quantities once. "
                "Clicking Apply again repeats the transfer. The deck's "
                "Added statuses are not changed, and saved decks are not "
                "automatically resaved."
            )
        apply_changes = st.button(
            "Apply to collection", key="swu_apply_deck_inventory",
            type="primary", disabled=not changes or not state.get("swu_auth_user_id"),
        )
        if apply_changes:
            try:
                from swu_inventory import adjust_owned_bulk
                signed = {gid: n if transfer == "Add to collection" else -n
                          for gid, n in changes.items()}
                adjust_owned_bulk(st, make_client, signed)
                action_word = "Added" if transfer == "Add to collection" else "Removed"
                st.success(
                    f"{action_word} {copy_count} copies across "
                    f"{len(changes)} card types "
                    f"{'to' if transfer == 'Add to collection' else 'from'} "
                    "your collection."
                )
            except Exception as exc:
                st.error(f"Collection transfer failed; no cards were changed: {exc}")
    st.divider()
    st.subheader("Save deck online")
    if not state.get("swu_auth_user_id"):
        st.info("Sign in on the **0. Sign in** tab to save this deck to your account.")
    else:
        nickname = state.get("swu_auth_name") or "your account"
        st.caption(f"Saving to {nickname}. Existing decks with the same name are updated.")
        if st.button(
            "Save current deck", key="swu_cloud_save", type="primary",
            disabled=snapshot is None,
        ):
            try:
                save_deck_to_account(st, make_client, snapshot)
                state["swu_save_notice"] = f"Saved '{snapshot['name']}' to {nickname}."
                st.rerun()
            except Exception as exc:
                st.error(f"Save failed: {exc}")
        notice = state.pop("swu_save_notice", None)
        if notice:
            st.success(notice)
        st.caption("Browse, load, or delete saved decks in **5. My Decks**.")


def _account_emails(st):
    """Map friendly account names to existing Supabase Auth email identities.

    Email mappings are configured in private Streamlit secrets, never in git.
    They are identifiers, not passwords. Each must be a provisioned Auth user.
    """
    try:
        configured = st.secrets.get("SWU_ACCOUNT_EMAILS", {})
        return {
            name: str(configured.get(name) or "").strip()
            for name in ACCOUNT_NAMES
        }
    except (AttributeError, TypeError, KeyError):
        return {name: "" for name in ACCOUNT_NAMES}


def _current_account_name(state, emails):
    if state.get("swu_auth_name") in ACCOUNT_NAMES:
        return state["swu_auth_name"]
    signed_in_email = str(state.get("swu_auth_email") or "").casefold()
    for name, email in emails.items():
        if email.casefold() == signed_in_email and email:
            state["swu_auth_name"] = name
            return name
    return "Existing account"  # Preserve sessions belonging to old email users.


def render_sign_in(st, make_client):
    """Six fixed names, each backed by an existing private Supabase Auth user."""
    state = st.session_state
    emails = _account_emails(st)
    st.header("Sign in")
    st.caption("Choose an account and enter its password to access its saved decks.")

    if state.get("swu_auth_user_id"):
        name = _current_account_name(state, emails)
        st.success(f"Signed in as {name}")
        if st.button("Sign out", key="swu_sign_out"):
            try:
                client = _client_from_session(st, make_client)
                if client is not None:
                    # Avoid revoking sessions on other devices sharing this account.
                    client.auth.sign_out(options={"scope": "local"})
            except Exception:
                pass
            _clear_auth(state)
            st.rerun()

        with st.expander("Change password"):
            with st.form("swu_password_change_form", clear_on_submit=True):
                current_password = st.text_input("Current password", type="password")
                new_password = st.text_input("New password", type="password")
                confirm_password = st.text_input("Confirm new password", type="password")
                change = st.form_submit_button("Change password")
            if change:
                if not all((current_password, new_password, confirm_password)):
                    st.error("Fill in all three password fields.")
                elif new_password != confirm_password:
                    st.error("The new passwords do not match.")
                elif len(new_password) < MIN_PASSWORD_LENGTH:
                    st.error("Supabase passwords must be at least six characters long.")
                elif current_password == new_password:
                    st.error("Choose a different password.")
                else:
                    try:
                        email = state["swu_auth_email"]
                        # Explicitly verify the old password before changing it.
                        client = make_client(
                            st.secrets["SUPABASE_URL"],
                            st.secrets["SUPABASE_PUBLISHABLE_KEY"],
                        )
                        login = client.auth.sign_in_with_password({
                            "email": email, "password": current_password,
                        })
                        if not login.session or str(login.user.id) != state["swu_auth_user_id"]:
                            raise ValueError("Current password is incorrect.")
                        client.auth.update_user({"password": new_password})
                        # The new access/refresh tokens may have changed. Request
                        # a fresh session using the NEW password.
                        refreshed = client.auth.sign_in_with_password({
                            "email": email, "password": new_password,
                        })
                        if not _set_auth(state, refreshed):
                            raise RuntimeError("Password updated; sign in again.")
                        state["swu_auth_name"] = name if name in ACCOUNT_NAMES else None
                        st.success("Password changed successfully.")
                    except Exception as exc:
                        st.error(f"Password change failed: {exc}")
        return

    # Put the account radio to the LEFT and password field to the RIGHT.
    with st.form("swu_named_login_form", clear_on_submit=True):
        account_col, password_col = st.columns([1, 1.2], gap="large")
        with account_col:
            chosen = st.radio("Account", ACCOUNT_NAMES, key="swu_named_account")
        with password_col:
            password = st.text_input("Password", type="password")
            sign_in = st.form_submit_button("Sign in", type="primary")
    if sign_in:
        email = emails.get(chosen)
        if not email:
            st.error("This account has not been configured yet. Ask the app administrator to set it up.")
        elif not password:
            st.error("Enter the account password.")
        elif sum(1 for other in emails.values()
                 if other and other.casefold() == email.casefold()) != 1:
            st.error("Two account names point to the same email. Ask the administrator to fix the account mapping.")
        else:
            try:
                client = make_client(
                    st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_PUBLISHABLE_KEY"],
                )
                response = client.auth.sign_in_with_password({
                    "email": email, "password": password,
                })
                if not _set_auth(state, response):
                    raise RuntimeError("Sign-in could not be verified.")
                # Protect against accidentally routing a different Auth user
                # to this fixed friendly account name.
                if str(response.user.email or "").casefold() != email.casefold():
                    _clear_auth(state)
                    raise RuntimeError("Account identity mismatch.")
                state["swu_auth_name"] = chosen
                st.rerun()
            except Exception as exc:
                st.error(f"Sign-in failed: {exc}")


def save_deck_to_account(st, make_client, snapshot):
    """Upsert the current Twin Suns snapshot under the authenticated user's ID."""
    if snapshot is None:
        raise ValueError("There is no valid deck to save.")
    snapshot = normalize_snapshot(snapshot)
    client = _client_from_session(st, make_client)
    if client is None:
        raise RuntimeError("Sign in before saving a deck.")
    user_id = st.session_state["swu_auth_user_id"]
    client.table(SAVE_TABLE).upsert(
        {
            "user_id": user_id,
            "name": snapshot["name"],
            "format": FORMAT,
            "deck_data": snapshot,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="user_id,name",
    ).execute()
    st.session_state.pop("swu_decks_cache", None)


def _saved_decks(st, make_client):
    """Retrieve only this Supabase Auth user's own saves, with a brief TTL."""
    state = st.session_state
    user_id = state["swu_auth_user_id"]
    cache = state.get("swu_decks_cache")
    if (isinstance(cache, dict) and cache.get("user_id") == user_id
            and time.monotonic() - cache.get("time", 0) < 20):
        return cache["rows"]
    client = _client_from_session(st, make_client)
    if client is None:
        raise RuntimeError("You must sign in to view saved decks.")
    rows = (client.table(SAVE_TABLE)
            .select("id,name,updated_at,deck_data")
            .eq("user_id", user_id)
            .order("updated_at", desc=True)
            .limit(200)
            .execute().data or [])
    state["swu_decks_cache"] = {
        "user_id": user_id, "time": time.monotonic(), "rows": rows,
    }
    return rows


def render_all_decks(st, make_client):
    """Account-owned decks, with physical progress from each save."""
    state = st.session_state
    st.header("My Decks")
    if not state.get("swu_auth_user_id"):
        st.info("Sign in on **0. Sign in** to view and manage your saved decks.")
        return
    name = _current_account_name(state, _account_emails(st))
    st.caption(f"Saved decks belonging to {name}. Other accounts' decks remain private.")
    if st.button("Refresh saved decks", key="swu_decks_refresh"):
        state.pop("swu_decks_cache", None)
    try:
        rows = _saved_decks(st, make_client)
    except Exception as exc:
        st.error(f"Couldn't load your decks: {exc}")
        return
    if not rows:
        st.info("No saved decks yet. Create a deck in **4. Deck Builder** and save it there.")
        return
    by_id = {str(row["id"]): row for row in rows}
    ids = list(by_id)
    # A deleted deck may still be selected in the old selectbox widget state.
    if state.get("swu_cloud_selected_deck") not in ids:
        state.pop("swu_cloud_selected_deck", None)
    selected_id = st.selectbox(
        "Saved decks", ids,
        format_func=lambda rid: by_id[rid]["name"],
        key="swu_cloud_selected_deck",
    )
    chosen = by_id[selected_id]
    modified = str(chosen.get("updated_at") or "")[:16].replace("T", " ")
    st.caption(f"Last saved: {modified} UTC")
    try:
        snapshot = normalize_snapshot(chosen["deck_data"])
        selections = required_cards(
            snapshot["leaders"], snapshot["base"], snapshot["cards"],
        )
        totals = progress_totals(selections, snapshot["card_progress"])
        st.write(f"**Leaders:** {', '.join(x['name'] for x in snapshot['leaders']) or 'None'}")
        st.write(f"**Base:** {snapshot['base']['name'] if snapshot['base'] else 'None'}")
        st.write(f"**Main deck:** {sum(x['count'] for x in snapshot['cards'])} cards")
        st.write(
            f"**Collection:** {totals['inDeck']} in physical deck · "
            f"{totals['ownedElsewhere']} owned elsewhere · "
            f"{totals['needToBuy']} still needed"
        )
    except (ValueError, TypeError, KeyError) as exc:
        snapshot = None
        st.warning(f"This save can't be loaded: {exc}")
    left, right = st.columns(2)
    with left:
        if st.button("Load selected deck", key="swu_cloud_load", disabled=snapshot is None):
            # Restore at the start of the next run before text input widgets.
            state["swu_pending_deck_restore"] = snapshot
            state["swu_navigate_to_deck"] = True
            st.rerun()
    with right:
        if st.button("Delete selected deck", key="swu_delete_begin"):
            state["swu_confirm_deck_delete"] = selected_id
    if state.get("swu_confirm_deck_delete") == selected_id:
        st.warning(f"Delete saved deck '{chosen['name']}' permanently?")
        yes, no = st.columns(2)
        with yes:
            if st.button("Yes, delete", key="swu_delete_confirm"):
                try:
                    client = _client_from_session(st, make_client)
                    client.table(SAVE_TABLE).delete().eq("id", selected_id).eq(
                        "user_id", state["swu_auth_user_id"]
                    ).execute()
                    state.pop("swu_confirm_deck_delete", None)
                    state.pop("swu_cloud_selected_deck", None)
                    state.pop("swu_decks_cache", None)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Delete failed: {exc}")
        with no:
            if st.button("Cancel", key="swu_delete_cancel"):
                state.pop("swu_confirm_deck_delete", None)
                st.rerun()

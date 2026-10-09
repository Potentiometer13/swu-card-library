"""Stage 2G.2: private, account-based deck saves plus portable JSON backups.

No service-role keys. Read/write uses a dedicated per-session Supabase Auth
client; row-level security isolates each person's decks.
"""

from datetime import datetime, timezone
import json
import re

from swu_swudb_json import export_swudb, parse_swudb_json, resolve_swudb
from swu_collection_progress import (
    PROGRESS_SESSION_KEY, normalize_progress_map, required_cards,
)
from swu_twin_suns import (
    card_copy_limit, card_identity, get_selected_leaders, leaders_can_pair,
)

SAVE_TABLE = "swu_saved_decks"
FORMAT = "Twin Suns"
SCHEMA_VERSION = 1
MAX_FILE_BYTES = 2_000_000
MAX_ENTRIES = 500
MAX_QUANTITY = 1000
MAX_NAME_LENGTH = 80
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
    for key in ("swu_auth_access", "swu_auth_refresh", "swu_auth_user_id", "swu_auth_email"):
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
    if snapshot is not None:
        try:
            swudb_bytes = export_swudb(snapshot, author=author)
        except ValueError as exc:
            st.caption(f"SWUDB export: {exc}")
        else:
            st.download_button(
                "Export SWUDB JSON", swudb_bytes,
                file_name=_deck_filename(deck_name), mime="application/json",
                key="swu_export_swudb", use_container_width=False,
            )

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

    with st.expander("Import SWUDB JSON", expanded=False):
        st.file_uploader(
            "Choose a SWUDB deck (.json)",
            type=["json"],
            key="swu_deck_import",
            on_change=_auto_import_swudb,
        )
        if state.get("swu_import_error"):
            st.error(f"Import failed: {state['swu_import_error']}")
        elif state.get("swu_import_success"):
            st.success(state["swu_import_success"])

    with st.expander("Online deck saves (account required)", expanded=True):
        if not state.get("swu_auth_user_id"):
            st.caption("Make a free account to save multiple named decks privately across devices.")
            kind = st.radio("Account action", ["Sign in", "Create account"], horizontal=True, key="swu_auth_kind")
            with st.form("swu_login_form", clear_on_submit=True):
                email = st.text_input("Email")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button(kind)
            if submitted:
                if not email.strip() or not password:
                    st.error("Enter an email address and password.")
                else:
                    try:
                        client = make_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_PUBLISHABLE_KEY"])
                        if kind == "Sign in":
                            result = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
                        else:
                            result = client.auth.sign_up({"email": email.strip(), "password": password})
                        if _set_auth(state, result):
                            st.rerun()
                        else:
                            st.success("Check your email to confirm your account, then sign in.")
                    except Exception as exc:
                        st.error(f"Account request failed: {exc}")
            return
        st.caption(f"Signed in as {state.get('swu_auth_email', '')}")
        if st.button("Sign out", key="swu_sign_out"):
            try:
                client = _client_from_session(st, make_client)
                client.auth.sign_out()
            except Exception:
                pass
            _clear_auth(state)
            st.rerun()
        try:
            client = _client_from_session(st, make_client)
            user_id = state["swu_auth_user_id"]
            rows = (
                client.table(SAVE_TABLE)
                .select("id,name,updated_at,deck_data")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .limit(200)
                .execute().data or []
            )
        except Exception as exc:
            st.error(f"Couldn't load saved decks: {exc}")
            st.info("If this is your first time, run the included SQL in Supabase to create swu_saved_decks.")
            return
        if snapshot is not None:
            if st.button("Save current deck to my account", type="primary", key="swu_cloud_save"):
                try:
                    client.table(SAVE_TABLE).upsert(
                        {"user_id": user_id, "name": snapshot["name"], "format": FORMAT,
                         "deck_data": snapshot, "updated_at": datetime.now(timezone.utc).isoformat()},
                        on_conflict="user_id,name",
                    ).execute()
                    st.success("Deck saved. Saving again under the same name updates it.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Save failed: {exc}")
        if not rows:
            st.caption("No saved decks yet.")
            return
        by_id = {str(row["id"]): row for row in rows}
        selected_id = st.selectbox(
            "Saved decks", list(by_id),
            format_func=lambda rid: by_id[rid]["name"],
            key="swu_cloud_selected_deck",
        )
        chosen = by_id[selected_id]
        modified = str(chosen.get("updated_at") or "")[:16].replace("T", " ")
        st.caption(f"Last saved: {modified} UTC")
        left, right = st.columns(2)
        with left:
            if st.button("Load selected deck", key="swu_cloud_load", use_container_width=True):
                try:
                    data = normalize_snapshot(chosen["deck_data"])
                    state["swu_pending_deck_restore"] = data
                    st.rerun()
                except ValueError as exc:
                    st.error(f"Cannot load saved deck: {exc}")
        with right:
            if st.button("Delete selected deck", key="swu_delete_begin", use_container_width=True):
                state["swu_confirm_deck_delete"] = selected_id
        if state.get("swu_confirm_deck_delete") == selected_id:
            st.warning(f"Delete saved deck '{chosen['name']}' permanently?")
            yes, no = st.columns(2)
            with yes:
                if st.button("Yes, delete", key="swu_delete_confirm"):
                    try:
                        client.table(SAVE_TABLE).delete().eq("id", selected_id).eq("user_id", user_id).execute()
                        state.pop("swu_confirm_deck_delete", None)
                        state.pop("swu_cloud_selected_deck", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Delete failed: {exc}")
            with no:
                if st.button("Cancel", key="swu_delete_cancel"):
                    state.pop("swu_confirm_deck_delete", None)
                    st.rerun()

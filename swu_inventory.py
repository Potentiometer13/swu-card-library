"""Account-owned physical SWU inventory, separate from per-deck progress.

Inventory entries use gameplay_id, not printing UUID, so foil/promotional
printings don't inflate quantities. All database writes require Supabase Auth.
"""

from collections import Counter
import time

from swu_deck_storage import _client_from_session, _saved_decks

INVENTORY_TABLE = "swu_owned_cards"
INVENTORY_RPC = "swu_adjust_owned_card"
CACHE_SECONDS = 25


def owned_quantities(st, make_client):
    """Get this signed-in user's owned quantities, cached per Streamlit session."""
    state = st.session_state
    user_id = state.get("swu_auth_user_id")
    if not user_id:
        return {}
    cache = state.get("swu_owned_cache")
    if (isinstance(cache, dict) and cache.get("user_id") == user_id
            and time.monotonic() - cache.get("time", 0) < CACHE_SECONDS):
        return cache["rows"]
    client = _client_from_session(st, make_client)
    if client is None:
        return {}
    rows = {}
    offset = 0
    while True:
        batch = (client.table(INVENTORY_TABLE)
                 .select("gameplay_id,quantity")
                 .eq("user_id", user_id)
                 .range(offset, offset + 999).execute().data or [])
        for item in batch:
            if int(item["quantity"]) > 0:
                rows[str(item["gameplay_id"])] = int(item["quantity"])
        if len(batch) < 1000:
            break
        offset += len(batch)
    state["swu_owned_cache"] = {
        "user_id": user_id, "time": time.monotonic(), "rows": rows,
    }
    return rows


def adjust_owned(st, make_client, gameplay_id, delta):
    """Atomic +/- one ownership adjustment; server enforces nonnegative counts."""
    if delta not in (-1, 1):
        raise ValueError("Collection changes must be +1 or -1.")
    gid = str(gameplay_id or "")
    if not gid or len(gid) > 128:
        raise ValueError("Invalid gameplay card identity.")
    client = _client_from_session(st, make_client)
    if client is None:
        raise ValueError("Sign in to edit your collection.")
    response = client.rpc(INVENTORY_RPC, {
        "p_gameplay_id": gid, "p_delta": delta,
    }).execute()
    count = int(response.data)
    # Update the session cache immediately so a click does not force a
    # second roundtrip to fetch the full inventory.
    state = st.session_state
    cache = state.get("swu_owned_cache")
    if isinstance(cache, dict) and cache.get("user_id") == state.get("swu_auth_user_id"):
        if count:
            cache["rows"][gid] = count
        else:
            cache["rows"].pop(gid, None)
        cache["time"] = time.monotonic()
    else:
        state.pop("swu_owned_cache", None)
    return count


def adjust_owned_bulk(st, make_client, changes):
    """Atomically change many card quantities with one authenticated RPC.

    Signed changes are absolute numbers of copies to add or remove. The
    database aborts the entire transaction if any removal lacks inventory.
    """
    if not isinstance(changes, dict) or not changes or len(changes) > 600:
        raise ValueError("Choose 1–600 card types to transfer.")
    verified = {}
    for gameplay_id, delta in changes.items():
        gid = str(gameplay_id or "")
        if not gid or len(gid) > 128 or type(delta) is not int or not 1 <= abs(delta) <= 1000:
            raise ValueError("Invalid card ID or transfer quantity (limit: 1000 per card).")
        verified[gid] = delta
    client = _client_from_session(st, make_client)
    if client is None:
        raise ValueError("Sign in to edit your collection.")
    result = client.rpc("swu_adjust_owned_cards_bulk", {
        "p_changes": verified,
    }).execute()
    # Any SQL error aborts the entire transaction; invalidate cached totals
    # only after the server reports success.
    st.session_state.pop("swu_owned_cache", None)
    return result.data


def cards_in_saved_decks(st, make_client):
    """Count copies explicitly marked in physical decks across saved decks.

    Unsaved edits and unmarked deck-list cards are deliberately not included.
    """
    if not st.session_state.get("swu_auth_user_id"):
        return {}
    counts = Counter()
    for row in _saved_decks(st, make_client):
        snapshot = row.get("deck_data") or {}
        progress = snapshot.get("card_progress") or {}
        if not isinstance(progress, dict):
            continue
        for gid, record in progress.items():
            if isinstance(record, dict):
                try:
                    amount = int(record.get("inDeck", 0))
                except (ValueError, TypeError):
                    amount = 0
                if amount > 0:
                    counts[str(gid)] += amount
    return dict(counts)

"""Twin Suns (2026) rules and session-only deck builder.

Two distinct leaders; no combined Heroism/Villainy; one base;
minimum 80 draw-deck cards; singleton unless specific card text overrides.
"""

from collections import Counter
from html import escape
import inspect
from urllib.parse import urlsplit
import math
import re

from swu_collection_progress import (
    PROGRESS_SESSION_KEY, game_id, get_progress, set_progress,
    normalize_progress_map, needs_to_buy, progress_totals, required_cards,
)

DECK_MINIMUM = 80
SIDEBOARD_KEY = "swu_twin_suns_sideboard"
SIDEBOARD_PROGRESS_KEY = "swu_sideboard_progress"
ALIGNMENT = ("Heroism", "Villainy")
ASPECT_ORDER = ("Vigilance", "Command", "Aggression", "Cunning", "Heroism", "Villainy")
ICON = {"Vigilance": "🔵", "Command": "🟢", "Aggression": "🔴", "Cunning": "🟡", "Heroism": "⚪", "Villainy": "⚫"}


def card_identity(card):
    """Game identity, not printing identity: variants never evade copy limits."""
    return str(card.get("gameplay_id") or card.get("uuid") or "")


def get_selected_leaders(session):
    """Use the new pair; also migrate the pre-Twin-Suns single-leader state."""
    pair = session.get("swu_selected_leaders")
    if isinstance(pair, list):
        return [x for x in pair[:2] if isinstance(x, dict)]
    old = session.get("swu_selected_leader")
    return [old] if isinstance(old, dict) and old else []


def leaders_can_pair(existing, candidate):
    """Return (valid, reason). Based on 2026 faceup-side aspect rule."""
    if not candidate:
        return False, "Choose a leader."
    cgid = card_identity(candidate)
    if any(card_identity(leader) == cgid for leader in existing):
        return False, "That leader is already selected. Choose a different gameplay card."
    if len(existing) >= 2:
        return False, "Remove a leader before selecting a different one."
    icons = [aspect for leader in existing + [candidate] for aspect in (leader.get("aspects") or [])]
    if "Heroism" in icons and "Villainy" in icons:
        return False, "Twin Suns leaders cannot combine Heroism and Villainy."
    return True, ""


def choose_leader(session, leader):
    pair = get_selected_leaders(session)
    gid = card_identity(leader)
    for i, old in enumerate(pair):
        if card_identity(old) == gid:
            # Same gameplay card, different printing is a cosmetic change.
            pair[i] = leader
            session["swu_selected_leaders"] = pair
            session["swu_selected_leader"] = pair[0]
            return True, "Leader printing updated."
    allowed, reason = leaders_can_pair(pair, leader)
    if not allowed:
        return False, reason
    pair.append(leader)
    session["swu_selected_leaders"] = pair
    session["swu_selected_leader"] = pair[0]
    return True, "Leader added."


def remove_leader(session, index):
    pair = get_selected_leaders(session)
    if 0 <= index < len(pair):
        pair.pop(index)
    session["swu_selected_leaders"] = pair
    if pair:
        session["swu_selected_leader"] = pair[0]
    else:
        session.pop("swu_selected_leader", None)


def change_deck_selection(session, kind, index=None):
    """Clear one selection and navigate to the corresponding library tab.

    Called by a Streamlit button callback, before widgets are created on
    the rerun. The main tabs must use key='swu_active_main_tab' and
    on_change='rerun' for programmatic navigation to work.
    """
    if kind == "leader":
        remove_leader(session, index)
        session["swu_active_main_tab"] = "1. Leaders"
    elif kind == "base":
        session.pop("swu_selected_base", None)
        session["swu_active_main_tab"] = "2. Bases"
    else:
        raise ValueError("Unknown deck selection type")


def deck_entries(session):
    return session.setdefault("swu_twin_suns_cards", {})


def sideboard_entries(session):
    """Sideboard uses the same gameplay identity as main deck."""
    return session.setdefault(SIDEBOARD_KEY, {})


def _total_zone_copies(session, gid):
    return (int(deck_entries(session).get(gid, {}).get("count", 0))
            + int(sideboard_entries(session).get(gid, {}).get("count", 0)))


def add_sideboard_card(session, card):
    """Add one new sideboard copy, respecting limits across both zones."""
    gid = card_identity(card)
    if not gid:
        return False, "This card has no gameplay ID."
    limit = card_copy_limit(card)
    if limit is not None and _total_zone_copies(session, gid) >= limit:
        return False, "Copy limit reached across main deck and sideboard. Move an existing copy instead."
    entries = sideboard_entries(session)
    count = int(entries.get(gid, {}).get("count", 0))
    if entries.get(gid, {}).get("card", {}).get("price_selection") == "manual" and card.get("price_selection") != "manual":
        card = entries[gid]["card"]
    entries[gid] = {"card": dict(card), "count": count + 1}
    return True, "Added to sideboard."


def _zone_progress(session, zone, gid, quantity):
    key = PROGRESS_SESSION_KEY if zone == "main" else SIDEBOARD_PROGRESS_KEY
    return normalize_progress_map(session.get(key) or {}, {gid: ({}, quantity)}).get(
        gid, {"inDeck": 0, "ownedElsewhere": 0}
    )


def set_zone_added(session, zone, gid, quantity, mark_added=None):
    """Toggle the physically Added count for a single deck zone."""
    key = PROGRESS_SESSION_KEY if zone == "main" else SIDEBOARD_PROGRESS_KEY
    rec = _zone_progress(session, zone, gid, quantity)
    new_count = (0 if rec["inDeck"] >= quantity else quantity) if mark_added is None else (quantity if mark_added else 0)
    session.setdefault(key, {})[gid] = {"inDeck": new_count, "ownedElsewhere": 0}
    if zone == "main":
        _sync_progress_widget_values(session, gid, quantity)


def _move_zone_copy(session, gid, source, target):
    """Move one copy, transferring its physical status between zones."""
    source_rows = deck_entries(session) if source == "main" else sideboard_entries(session)
    target_rows = deck_entries(session) if target == "main" else sideboard_entries(session)
    if gid not in source_rows:
        return False, "Card not found."
    old_count = int(source_rows[gid]["count"])
    card = source_rows[gid]["card"]
    # Moving does not increase the combined copy count.
    src_rec = _zone_progress(session, source, gid, old_count)
    marked = int(src_rec["inDeck"] > 0)
    elsewhere = int(not marked and src_rec["ownedElsewhere"] > 0)
    new_src = {"inDeck": src_rec["inDeck"] - marked,
               "ownedElsewhere": src_rec["ownedElsewhere"] - elsewhere}
    src_key = PROGRESS_SESSION_KEY if source == "main" else SIDEBOARD_PROGRESS_KEY
    dst_key = PROGRESS_SESSION_KEY if target == "main" else SIDEBOARD_PROGRESS_KEY
    if old_count <= 1:
        del source_rows[gid]
        session.setdefault(src_key, {}).pop(gid, None)
        if source == "main":
            for field in ("inDeck", "ownedElsewhere"):
                session.pop(_progress_widget_key(gid, field), None)
    else:
        source_rows[gid]["count"] = old_count - 1
        session.setdefault(src_key, {})[gid] = new_src
        if source == "main":
            _sync_progress_widget_values(session, gid, old_count - 1)
    old_target = int(target_rows.get(gid, {}).get("count", 0))
    dst_rec = _zone_progress(session, target, gid, old_target)
    target_rows[gid] = {"card": dict(card), "count": old_target + 1}
    session.setdefault(dst_key, {})[gid] = {
        "inDeck": dst_rec["inDeck"] + marked,
        "ownedElsewhere": dst_rec["ownedElsewhere"] + elsewhere,
    }
    if target == "main":
        _sync_progress_widget_values(session, gid, old_target + 1)
    return True, "Card moved."


def move_to_sideboard(session, gid):
    return _move_zone_copy(session, gid, "main", "sideboard")


def move_to_main_deck(session, gid):
    return _move_zone_copy(session, gid, "sideboard", "main")


def remove_sideboard_card(session, gid):
    rows = sideboard_entries(session)
    if gid not in rows:
        return
    qty = int(rows[gid]["count"])
    if qty <= 1:
        del rows[gid]
        session.setdefault(SIDEBOARD_PROGRESS_KEY, {}).pop(gid, None)
    else:
        rows[gid]["count"] = qty - 1
        rec = _zone_progress(session, "sideboard", gid, qty - 1)
        session.setdefault(SIDEBOARD_PROGRESS_KEY, {})[gid] = rec


def export_requirements(session, scope="both"):
    """Return (required, progress) for main, sideboard or both.

    Sum quantities and statuses by gameplay ID. Leaders/base belong to main.
    """
    if scope not in {"main", "sideboard", "both"}:
        raise ValueError("Invalid export scope.")
    combined, combined_progress = {}, {}
    zones = []
    if scope in ("main", "both"):
        zones.append((
            required_cards(get_selected_leaders(session), session.get("swu_selected_base"),
                           list((session.get("swu_twin_suns_cards") or {}).values())),
            PROGRESS_SESSION_KEY,
        ))
    if scope in ("sideboard", "both"):
        zones.append((
            required_cards([], None, list((session.get(SIDEBOARD_KEY) or {}).values())),
            SIDEBOARD_PROGRESS_KEY,
        ))
    for required, key in zones:
        progress = normalize_progress_map(session.get(key) or {}, required)
        for gid, (card, qty) in required.items():
            if gid in combined:
                old_card, old_count = combined[gid]
                combined[gid] = (old_card, old_count + qty)
            else:
                combined[gid] = (card, qty)
            previous = combined_progress.setdefault(gid, {"inDeck": 0, "ownedElsewhere": 0})
            current = progress.get(gid, {})
            for field in ("inDeck", "ownedElsewhere"):
                previous[field] += int(current.get(field, 0))
    return combined, combined_progress


def exception_copy_limit(rules_text):
    """Recognize explicit deck-construction permissions ONLY, not ordinary copy text.

    None means uncapped by copy-count (e.g., 'any number of copies').
    Integer >= 2 means an explicit maximum. Return 1 for regular cards.
    Unrecognized exception wording needs review, rather than guessing.
    """
    text = " ".join(str(rules_text or "").split()).lower()
    # Wording must apply to including/having copies IN THE DECK.
    any_number = (
        r"\b(?:include|have|put)\s+any\s+number\s+of\s+copies\s+of\s+(?:this|[\w -]+?)\s+card\s+in\s+your\s+deck\b",
        r"\b(?:include|have)\s+as\s+many\s+copies\s+of\s+(?:this|[\w -]+?)\s+card\s+in\s+your\s+deck\b",
    )
    if any(re.search(p, text) for p in any_number):
        return None
    # "A deck can have up to 15 copies of this card" (Swarming Vulture Droid)
    # as well as direct "include up to N copies ... in your deck" wording.
    direct = re.search(
        r"\b(?:a|your)\s+deck\s+(?:can|may)\s+have\s+up\s+to\s+(\d+)\s+copies\s+of\s+this\s+card\b",
        text,
    )
    if direct:
        return max(1, int(direct.group(1)))
    number = r"(\d+|two|three|four|five|six|seven|eight|nine|ten)"
    match = re.search(
        rf"\b(?:include|have)\s+(?:up\s+to\s+)?{number}\s+copies\s+of\s+(?:this|[\w -]+?)\s+card\s+in\s+your\s+deck\b",
        text,
    )
    if match:
        words = {"two":2, "three":3, "four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10}
        return max(1, int(match.group(1)) if match.group(1).isdigit() else words[match.group(1)])
    return 1


def card_copy_limit(card):
    return exception_copy_limit(card.get("rules_text") or "")


def add_card(session, card):
    gid = card_identity(card)
    if not gid:
        return False, "This card has no gameplay ID."
    entries = deck_entries(session)
    current = entries.get(gid, {})
    count = current.get("count", 0)
    limit = card_copy_limit(card)
    if limit is not None and _total_zone_copies(session, gid) >= limit:
        return False, "Copy limit reached for this card."
    if entries.get(gid, {}).get("card", {}).get("price_selection") == "manual" and card.get("price_selection") != "manual":
        card = entries[gid]["card"]
    entries[gid] = {"card": dict(card), "count": count + 1}
    # A copy-limit exception can change a checkbox into a numeric input.
    # Keep any existing widget state aligned with the new quantity.
    _sync_progress_widget_values(session, gid, count + 1)
    return True, "Added to the draw deck."


def remove_card(session, gid):
    entries = deck_entries(session)
    if gid not in entries:
        return
    entry = entries[gid]
    if entry["count"] <= 1:
        del entries[gid]
        (session.get(PROGRESS_SESSION_KEY) or {}).pop(gid, None)
        for field in ("inDeck", "ownedElsewhere"):
            session.pop(_progress_widget_key(gid, field), None)
    else:
        entry["count"] -= 1
        _sync_progress_widget_values(session, gid, entry["count"])


def aspect_supply(leaders, base):
    supply = Counter()
    for item in list(leaders) + ([base] if base else []):
        supply.update(item.get("aspects") or [])
    return supply


def missing_aspect_icons(card, supply):
    wanted = Counter(card.get("aspects") or [])
    return sum(max(0, qty - supply.get(aspect, 0)) for aspect, qty in wanted.items())


def deck_validation(session):
    leaders = get_selected_leaders(session)
    base = session.get("swu_selected_base")
    entries = deck_entries(session)
    count = sum(int(entry["count"]) for entry in entries.values())
    errors = []
    if len(leaders) != 2:
        errors.append("Select exactly 2 different leaders.")
    elif card_identity(leaders[0]) == card_identity(leaders[1]):
        errors.append("Both leader slots contain the same gameplay card.")
    if any("Heroism" in (l.get("aspects") or []) for l in leaders) and any("Villainy" in (l.get("aspects") or []) for l in leaders):
        errors.append("Leaders cannot mix Heroism and Villainy.")
    if not base:
        errors.append("Select one base.")
    if count < DECK_MINIMUM:
        errors.append(f"Add at least {DECK_MINIMUM - count} more draw-deck cards.")
    for gid, entry in {**entries, **sideboard_entries(session)}.items():
        limit = card_copy_limit(entry["card"])
        if limit is not None and _total_zone_copies(session, gid) > limit:
            errors.append(f"Copy limit exceeded across zones: {entry['card'].get('name', 'Unnamed card')}.")
    return count, errors


def display_card_name(card):
    name = str(card.get("name") or "Unknown card")
    subtitle = str(card.get("subtitle") or "").strip()
    return f"{name} — {subtitle}" if subtitle else name


def _progress_widget_key(gid, field):
    return f"swu_progress_{'in' if field == 'inDeck' else 'owned'}_{gid}"


def _sync_progress_widget_values(session, gid, quantity):
    if gid not in (session.get(PROGRESS_SESSION_KEY) or {}):
        return
    record = get_progress(session, gid, quantity)
    session[PROGRESS_SESSION_KEY][gid] = record
    for field in ("inDeck", "ownedElsewhere"):
        key = _progress_widget_key(gid, field)
        if key in session:
            session[key] = bool(record[field]) if quantity == 1 else record[field]


def _on_progress_widget_change(session, gid, quantity, changed):
    """Keep physical/owned counts mutually bounded by deck quantity."""
    key_in = _progress_widget_key(gid, "inDeck")
    key_owned = _progress_widget_key(gid, "ownedElsewhere")
    record = set_progress(
        session, gid, quantity,
        int(session.get(key_in, 0)), int(session.get(key_owned, 0)),
        changed=changed,
    )
    # Callbacks run before the next render; widget state is safe to update here.
    for field, key in (("inDeck", key_in), ("ownedElsewhere", key_owned)):
        session[key] = bool(record[field]) if quantity == 1 else record[field]


def _progress_widget(st, session, gid, quantity, field):
    record = get_progress(session, gid, quantity)
    key = _progress_widget_key(gid, field)
    default = bool(record[field]) if quantity == 1 else record[field]
    # Widget state is initialized before rendering; when importing another
    # deck restore_snapshot clears these keys to avoid stale checkbox values.
    if key not in session:
        session[key] = default
    if quantity == 1:
        st.checkbox(
            "In deck" if field == "inDeck" else "Owned elsewhere",
            key=key, label_visibility="collapsed",
            on_change=_on_progress_widget_change,
            args=(session, gid, quantity, field),
        )
    else:
        st.number_input(
            "In deck" if field == "inDeck" else "Owned elsewhere",
            min_value=0, max_value=quantity, step=1,
            key=key, label_visibility="collapsed",
            on_change=_on_progress_widget_change,
            args=(session, gid, quantity, field),
        )



def toggle_deck_card_status(session, gid, quantity):
    """Mark every required copy as physically In Deck or Out of Deck.

    Existing imported progress remains readable, including partial quantities.
    A user click intentionally moves the card to a simple all-in/all-out
    status and clears the old `ownedElsewhere` designation for that card.
    """
    current = get_progress(session, gid, quantity)
    new_in_deck = 0 if current["inDeck"] >= quantity else quantity
    set_progress(
        session, gid, quantity,
        in_deck=new_in_deck,
        owned_elsewhere=0,
        changed="inDeck",
    )
    _sync_progress_widget_values(session, gid, quantity)


def set_all_draw_deck_status(session, mark_added):
    """Mark all draw-deck copies Added or Not Added, without changing inventory.

    Leaders and base have their own progress controls and are deliberately
    excluded. Uses the same progress transition as a single status button.
    """
    updated = 0
    for gid, entry in deck_entries(session).items():
        quantity = int(entry["count"])
        previous = get_progress(session, gid, quantity)
        desired = quantity if mark_added else 0
        if previous["inDeck"] != desired or previous["ownedElsewhere"]:
            set_progress(
                session, gid, quantity, in_deck=desired,
                owned_elsewhere=0, changed="inDeck",
            )
            _sync_progress_widget_values(session, gid, quantity)
            updated += 1
    session["swu_bulk_added_notice"] = (
        f"{'Added' if mark_added else 'Removed'} status updated for "
        f"{updated} card type{'s' if updated != 1 else ''}. "
        "Save your deck online to keep these changes."
    )
    return updated


def bulk_needed_candidates(required, progress):
    """Missing cards except those explicitly listed as Rare or Legendary.

    Supplied printing rarity is the preliminary filter; the cross-printing
    lookup below catches rare cards selected using a Special promo printing.
    """
    banned = {"rare", "legendary"}
    result = {}
    for gid, (card, quantity) in required.items():
        record = get_progress({PROGRESS_SESSION_KEY: progress}, gid, quantity)
        if needs_to_buy(quantity, record) == 0:
            continue
        if str(card.get("rarity") or "").strip().casefold() in banned:
            continue
        result[gid] = (card, quantity)
    return result


def all_needed_candidates(required, progress):
    """Return every missing gameplay card, regardless of rarity.

    Includes missing leaders and bases as well as draw-deck cards.
    This is the shared input for Bulk, Non-Bulk and All exports.
    """
    result = {}
    for gid, (card, quantity) in required.items():
        record = get_progress({PROGRESS_SESSION_KEY: progress}, gid, quantity)
        if needs_to_buy(quantity, record) > 0:
            result[gid] = (card, quantity)
    return result


def fetch_bulk_printing_info(db, candidates):
    """Fetch all matching printings across Cards, Leaders and Bases views.

    Uses gameplay IDs to identify matching printings, not SET_NUMBER, so all
    sets (including niche/promotional releases) can be returned. The query is
    batched and paged to avoid Supabase's default 1000-row API cap.
    """
    table_for_type = {
        "unit": "swu_grouped_cards",
        "event": "swu_grouped_cards",
        "upgrade": "swu_grouped_cards",
        "leader": "swu_grouped_leaders",
        "base": "swu_grouped_bases",
    }
    by_table = {}
    for gid, (card, _) in candidates.items():
        kind = str(card.get("card_type") or "").strip().casefold()
        if kind in table_for_type:
            lookup_tables = (table_for_type[kind],)
        else:
            # Some SWU API/import records (including Origin Tree — Shyyyo)
            # have a blank card_type. Search the three existing printing views
            # by gameplay ID instead of aborting every set-organized export.
            # This also handles legacy imported leaders/bases with no type.
            lookup_tables = tuple(dict.fromkeys(table_for_type.values()))
        for table in lookup_tables:
            by_table.setdefault(table, []).append(gid)
    result = {gid: {"sets": set(), "rarities": set()} for gid in candidates}
    for table, gids in by_table.items():
        for start in range(0, len(gids), 40):
            batch = gids[start:start + 40]
            offset = 0
            while True:
                response = (
                    db.table(table)
                    .select("gameplay_id,set_code,rarity", count="exact")
                    .in_("gameplay_id", batch)
                    .order("gameplay_id")
                    .order("set_code")
                    .order("uuid")
                    .range(offset, offset + 499)
                    .execute()
                )
                records = response.data or []
                if not records:
                    break
                for record in records:
                    gid = str(record.get("gameplay_id") or "")
                    if gid not in result:
                        continue
                    code = str(record.get("set_code") or "").strip().upper()
                    rarity = str(record.get("rarity") or "").strip().casefold()
                    if code:
                        result[gid]["sets"].add(code)
                    if rarity:
                        result[gid]["rarities"].add(rarity)
                offset += len(records)
                # Use the total count when the server has a lower row cap.
                if response.count is not None and offset >= response.count:
                    break
                if response.count is None and len(records) < 500:
                    break
                if offset > 100000:
                    raise ValueError("Printing lookup exceeded the expected record limit.")
    for gid, info in result.items():
        if not info["sets"]:
            raise ValueError(
                f"Could not determine all printing sets for {display_card_name(candidates[gid][0])}."
            )
    return result


# Major expansion codes and titles, in release order. Keep this aligned with
# MAIN_EXPANSION_SET_CODES in app.py when new sets are added.
MAIN_EXPANSION_NAMES = {
    "SOR": "Spark of Rebellion",
    "SHD": "Shadows of the Galaxy",
    "TWI": "Twilight of the Republic",
    "JTL": "Jump to Lightspeed",
    "LOF": "Legends of the Force",
    "SEC": "Secrets of Power",
    "LAW": "A Lawless Time",
    "ASH": "Ashes of the Empire",
    "HMW": "Homeworlds",
    "IC27": "Icons 2027",
}


def bulk_parent_set(code):
    """Return the main expansion associated with a set code, if clear.

    Codes like SORP, SOROP, SOR-WPP and PSOR belong in the SOR section;
    generic conventions/promos (C24, P25, TS26) don't identify a parent by
    themselves. Do not guess which expansion a generic promo belongs to.
    """
    code = str(code or "").strip().upper()
    if code in MAIN_EXPANSION_NAMES:
        return code
    for main in MAIN_EXPANSION_NAMES:
        if code.startswith(main) and len(code) > len(main):
            return main
        # Explicit set-specific prerelease, event and token prefixes.
        if any(code.startswith(prefix + main) for prefix in ("P", "E", "T")):
            return main
    return None


def bulk_needed_text(candidates, progress, printing_info, rarity_filter="bulk"):
    """Group a missing-card shopping list under *every* applicable main set.

    The first expansion in release order carries the purchase quantity and
    an ``also in ...`` reminder. Subsequent expansions list the same card as
    ``((Name))`` without a quantity, to avoid double-counting purchases.
    Set-specific promotional printing codes are shown beside the entry for
    that expansion. Cards found only in unassociated niche/promo sets appear
    under Miscellaneous.

    rarity_filter: "bulk" (exclude Rare/Legendary), "nonbulk" (only
    Rare/Legendary), or "all" (every missing card). The filters use all
    known printing rarities so a Special promotional printing cannot make
    a normally Rare card appear on the bulk list.
    """
    if rarity_filter not in {"bulk", "nonbulk", "all"}:
        raise ValueError("Unknown set-list export category.")
    sections = {code: [] for code in MAIN_EXPANSION_NAMES}
    miscellaneous = []

    for gid, (card, quantity) in candidates.items():
        record = get_progress({PROGRESS_SESSION_KEY: progress}, gid, quantity)
        missing = needs_to_buy(quantity, record)
        if not missing:
            continue
        data = printing_info.get(gid)
        if not data or not data.get("sets"):
            raise ValueError(f"Missing set list for {display_card_name(card)}.")
        # Classify from both the chosen and every other known printing.
        # This makes Bulk + Non-Bulk an exact partition of All Needed.
        all_rarities = {str(x).strip().casefold() for x in data.get("rarities", ())}
        chosen_rarity = str(card.get("rarity") or "").strip().casefold()
        if chosen_rarity:
            all_rarities.add(chosen_rarity)
        is_nonbulk = bool(all_rarities & {"rare", "legendary"})
        if rarity_filter == "bulk" and is_nonbulk:
            continue
        if rarity_filter == "nonbulk" and not is_nonbulk:
            continue

        codes = {str(x).strip().upper() for x in data["sets"] if str(x).strip()}
        by_main = {main: set() for main in MAIN_EXPANSION_NAMES}
        generic_niche = set()
        for code in codes:
            parent = bulk_parent_set(code)
            if parent:
                by_main[parent].add(code)
            else:
                generic_niche.add(code)

        name = " ".join(display_card_name(card).replace("—", "-").split())
        present_mains = [main for main in MAIN_EXPANSION_NAMES if by_main[main]]
        if not present_mains:
            # Generic promos and convention cards with no known expansion.
            line = f"{missing} {name} — " + ", ".join(sorted(codes))
            miscellaneous.append((name.casefold(), line))
            continue

        primary = present_mains[0]
        for main in present_mains:
            first = main == primary
            line = f"{missing} {name}" if first else f"(({name}))"
            notes = []
            # Promo codes belonging to this specific expansion; the regular
            # set code is implied by the section heading.
            niche_codes = set(by_main[main]) - {main}
            # Unassociated generic promos are noted once, on the first line.
            if first:
                niche_codes |= generic_niche
            if niche_codes:
                notes.append(", ".join(sorted(niche_codes)))
            if first and len(present_mains) > 1:
                other_sets = []
                for other in present_mains[1:]:
                    variants = sorted(by_main[other] - {other})
                    reminder = other
                    if variants:
                        reminder += " (" + ", ".join(variants) + ")"
                    other_sets.append(reminder)
                notes.append("also in " + ", ".join(other_sets))
            if notes:
                line += " — " + "; ".join(notes)
            sections[main].append((name.casefold(), line))

    output = []
    for code, title in MAIN_EXPANSION_NAMES.items():
        if not sections[code]:
            continue
        if output:
            output.append("")
        output.append(f"{title} ({code}):")
        output.extend(line for _, line in sorted(sections[code]))
    if miscellaneous:
        if output:
            output.append("")
        output.append("Miscellaneous")
        output.extend(line for _, line in sorted(miscellaneous))
    return "\n".join(output) + ("\n" if output else "")


def tcgplayer_missing_text(required, progress, include_owned=False):
    """TCGplayer Mass Entry text for needed copies, or the entire deck.

    With include_owned=True, list every required copy regardless of physical
    deck/other-ownership progress. Default behavior remains missing-only.
    This includes leaders and base; Mass Entry expects no header.
    """
    lines = []
    for gid, (card, qty) in sorted(
        required.items(),
        key=lambda item: display_card_name(item[1][0]).casefold(),
    ):
        record = get_progress({PROGRESS_SESSION_KEY: progress}, gid, qty)
        needed = qty if include_owned else needs_to_buy(qty, record)
        if not needed:
            continue
        name = str(card.get("name") or "").strip()
        subtitle = str(card.get("subtitle") or "").strip()
        if not name:
            # Never silently produce an unidentifiable shopping-list entry.
            raise ValueError("A card needed for your shopping list has no name.")
        title = f"{name} - {subtitle}" if subtitle else name
        # Keep physical characters legible and avoid one card becoming several
        # Mass Entry rows if imported text contains line breaks.
        title = " ".join(title.split())
        lines.append(f"{needed} {title}")
    return ("\n".join(lines) + "\n") if lines else ""



def _deck_section(card):
    """Display all four main deck categories in the user's preferred order."""
    card_type = str(card.get("card_type") or "").casefold()
    arena = str(card.get("arena") or "").casefold()
    if card_type == "unit":
        if arena == "ground":
            return "Ground Units"
        if arena == "space":
            return "Space Units"
        return "Other Units"  # Keep cards with missing arena instead of dropping them.
    if card_type == "event":
        return "Events"
    if card_type == "upgrade":
        return "Upgrades"
    return "Other Cards"


def _cost_sort_key(card):
    """Numeric card cost, with absent/X costs after numeric costs."""
    value = card.get("cost")
    try:
        return (0, float(value)) if value is not None and str(value).strip() else (1, 0)
    except (ValueError, TypeError):
        return (1, 0)



def _safe_card_image_url(card):
    """Only allow public HTTP(S) art URLs in our generated HTML markup."""
    url = str(card.get("front_image_url") or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in ("https", "http") or not parsed.netloc:
        return ""
    return url


def _hoverable_card_name(card):
    """An accessible, CSS-only artwork preview. No extra Streamlit widgets."""
    name = escape(display_card_name(card), quote=True)
    image = _safe_card_image_url(card)
    if not image:
        return (
            f'<span title="{name}" class="swu-deck-name-text">{name}</span>'
        )
    return (
        f'<span class="swu-deck-name-hover" tabindex="0" aria-label="{name}">'
        f'<span title="{name}" class="swu-deck-name-text">{name}</span>'
        f'<span class="swu-deck-art-preview" aria-hidden="true">'
        f'<img src="{escape(image, quote=True)}" alt="" loading="lazy"></span>'
        f'</span>'
    )



# Streamlit >= 1.52 lets st.html execute trusted, static JavaScript directly
# in the app document. This shared portal is intentionally NOT created inside
# a deck row: row overflow/scroll containers would clip the card artwork.
_DECK_CURSOR_PREVIEW_HTML = r"""
<style>
/* The script activates this class after installing the cursor preview. */
html.swu-deck-cursor-preview-active .swu-deck-art-preview {
    display: none !important;
}
.swu-deck-cursor-preview {
    position: fixed !important;
    display: none;
    left: 0;
    top: 0;
    z-index: 2147483647;
    pointer-events: none;
    padding: 0.25rem;
    width: min(250px, 52vw);
    max-height: calc(100dvh - 24px);
    overflow: hidden;
    box-sizing: border-box;
    border-radius: 0.45rem;
    background: var(--background-color, #fff);
    box-shadow: 0 6px 30px rgba(0,0,0,.45);
}
.swu-deck-cursor-preview img {
    display: block;
    width: 100%;
    height: auto;
    max-height: calc(100dvh - 32px);
    border-radius: 0.25rem;
    object-fit: contain;
}
</style>
<script>
(() => {
    "use strict";
    const key = "__swuDeckCursorPreviewV1";
    if (window[key]) {
        // Event delegation survives Streamlit fragment rerenders. Hide the old
        // card if the rows are about to be replaced, but don't add listeners.
        window[key].hide();
        return;
    }
    const portal = document.createElement("div");
    portal.className = "swu-deck-cursor-preview";
    const artwork = document.createElement("img");
    artwork.alt = "Card artwork preview";
    portal.appendChild(artwork);
    document.body.appendChild(portal);
    document.documentElement.classList.add("swu-deck-cursor-preview-active");

    let current = null;
    let lastX = 0;
    let lastY = 0;
    function hide() {
        current = null;
        portal.style.display = "none";
    }
    function position(x, y) {
        lastX = x;
        lastY = y;
        const margin = 12;
        const offset = 16;
        const bounds = portal.getBoundingClientRect();
        const width = bounds.width;
        const height = bounds.height;
        let left = x + offset;
        let top = y + offset;
        // Keep the complete card in view, including near window edges.
        if (left + width > window.innerWidth - margin)
            left = x - width - offset;
        if (top + height > window.innerHeight - margin)
            top = y - height - offset;
        left = Math.min(Math.max(margin, left), Math.max(margin, window.innerWidth - width - margin));
        top = Math.min(Math.max(margin, top), Math.max(margin, window.innerHeight - height - margin));
        portal.style.left = left + "px";
        portal.style.top = top + "px";
    }
    function show(el, x, y) {
        const img = el.querySelector(".swu-deck-art-preview img");
        if (!img) { hide(); return; }
        const src = img.getAttribute("src");
        if (!src) { hide(); return; }
        if (current !== el || artwork.getAttribute("src") !== src) {
            current = el;
            artwork.src = src;
        }
        portal.style.display = "block";
        position(x, y);
    }
    // Listen on document, not individual rows: Streamlit regularly replaces
    // row nodes on rerun, while these delegated listeners stay valid.
    document.addEventListener("pointermove", (event) => {
        const el = event.target instanceof Element
            ? event.target.closest(".swu-deck-name-hover") : null;
        if (!el) { hide(); return; }
        show(el, event.clientX, event.clientY);
    }, true);
    document.addEventListener("pointerout", (event) => {
        if (current && (!event.relatedTarget || !current.contains(event.relatedTarget)))
            hide();
    }, true);
    document.addEventListener("focusin", (event) => {
        const el = event.target instanceof Element
            ? event.target.closest(".swu-deck-name-hover") : null;
        if (!el) return;
        const rect = el.getBoundingClientRect();
        show(el, rect.right, rect.top);
    }, true);
    document.addEventListener("focusout", (event) => {
        if (current && !current.contains(event.relatedTarget)) hide();
    }, true);
    document.addEventListener("scroll", hide, true);
    window.addEventListener("blur", hide);
    artwork.addEventListener("load", () => {
        if (current) position(lastX, lastY);
    });
    window[key] = {hide};
})();
</script>
"""


def _install_deck_cursor_preview(st):
    """Enable cursor-following full card art; older Streamlit gets CSS fallback."""
    html = getattr(st, "html", None)
    if not callable(html):
        return
    try:
        supports_js = "unsafe_allow_javascript" in inspect.signature(html).parameters
    except (TypeError, ValueError):
        supports_js = False
    if supports_js:
        # Static trusted JS, no card/user content interpolated into this script.
        html(_DECK_CURSOR_PREVIEW_HTML, unsafe_allow_javascript=True)

def _gallery_cards(main_entries, side_entries):
    """One artwork per gameplay card, main deck first, then sideboard-only."""
    categories = (
        "Ground Units", "Space Units", "Events", "Upgrades",
        "Other Units", "Other Cards",
    )
    rank = {label: index for index, label in enumerate(categories)}
    seen = set()
    ordered = []
    for group in (main_entries, side_entries):
        values = sorted(group.items(), key=lambda item: (
            rank.get(_deck_section(item[1]["card"]), len(rank)),
            _cost_sort_key(item[1]["card"]),
            display_card_name(item[1]["card"]).casefold(),
        ))
        for gid, entry in values:
            if gid in seen or int(entry.get("count", 0)) <= 0:
                continue
            seen.add(gid)
            ordered.append(entry["card"])
    return ordered


def _cost_group(card):
    """Format each printed cost as a distribution bucket, including X costs."""
    raw = card.get("cost")
    if raw is None or not str(raw).strip():
        return "Unknown"
    raw = str(raw).strip()
    try:
        numeric = float(raw)
        if math.isfinite(numeric) and numeric >= 0:
            return str(int(numeric)) if numeric.is_integer() else f"{numeric:g}"
    except (ValueError, OverflowError):
        pass
    return raw.upper() if raw.upper() == "X" else "Other"


def _deck_statistics(main_entries):
    """Counts and percentages are copy-weighted and exclude the sideboard."""
    kinds = Counter({"Ground units": 0, "Space units": 0, "Non-units": 0})
    costs = Counter()
    total = 0
    for entry in main_entries.values():
        quantity = max(0, int(entry.get("count", 0)))
        if not quantity:
            continue
        card = entry["card"]
        kind = str(card.get("card_type") or "").strip().casefold()
        arena = str(card.get("arena") or "").strip().casefold()
        if kind in ("event", "upgrade"):
            category = "Non-units"
        elif arena == "ground" and kind in ("unit", ""):
            category = "Ground units"
        elif arena == "space" and kind in ("unit", ""):
            category = "Space units"
        elif kind == "unit":
            category = "Other units"  # Unit with missing arena.
        else:
            category = "Unknown type"  # Missing or unrecognized metadata.
        kinds[category] += quantity
        costs[_cost_group(card)] += quantity
        total += quantity
    def cost_key(label):
        try:
            return (0, float(label))
        except ValueError:
            return (1, label)
    ordered_costs = [(label, costs[label]) for label in sorted(costs, key=cost_key)]
    ordered_kinds = [(label, kinds[label]) for label in (
        "Ground units", "Space units", "Non-units", "Other units", "Unknown type"
    ) if label in kinds and (label in ("Ground units", "Space units", "Non-units") or kinds[label] > 0)]
    return total, ordered_kinds, ordered_costs


def _deck_stats_bar(label, quantity, total):
    percent = quantity / total * 100 if total else 0
    name = escape(str(label))
    return (
        '<div class="swu-stats-bar-row">'
        f'<div class="swu-stats-bar-heading"><span>{name}</span>'
        f'<span>{percent:.1f}% <small>({quantity})</small></span></div>'
        '<div class="swu-stats-track">'
        f'<div class="swu-stats-fill" style="width:{percent:.4f}%"></div>'
        '</div></div>'
    )


def _render_deck_right_panel(st, session, entries, filter_choice="All cards"):
    """Visual gallery obeys the shared Show cards filter; statistics are full main deck."""
    view = st.radio(
        "View", ("Card Images", "Statistics"),
        horizontal=True, key="swu_deck_right_view",
    )
    if view == "Card Images":
        main_visible = {
            gid: entry for gid, entry in entries.items()
            if _matches_progress_filter(session, gid, entry, filter_choice, "main")
        }
        side_visible = {
            gid: entry for gid, entry in sideboard_entries(session).items()
            if _matches_progress_filter(session, gid, entry, filter_choice, "sideboard")
        }
        cards = _gallery_cards(main_visible, side_visible)
        printing_options = session.get("swu_deck_printing_options")
        if printing_options and cards:
            from swu_grouping import show_grouped_card
            columns = st.columns(4, gap="small")
            for index, card in enumerate(cards):
                with columns[index % 4]:
                    show_grouped_card(card, printing_options.get(card_identity(card), [card]),
                                      key_prefix="swu_deck_art", show_printing_id=False)
            return
        images = [(_safe_card_image_url(card), display_card_name(card)) for card in cards]
        images = [(url, name) for url, name in images if url]
        if not images:
            st.info("No card images match this filter." if (entries or sideboard_entries(session))
                    else "Add cards to see their images here.")
            return
        tiles = "".join(
            '<div class="swu-panel-art-tile">'
            f'<img src="{escape(url, quote=True)}" '
            f'alt="{escape(name, quote=True)}" '
            f'title="{escape(name, quote=True)}" loading="lazy" decoding="async">'
            '</div>'
            for url, name in images
        )
        st.markdown(
            '<style>'
            '.swu-panel-art-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));'
            'gap:0.45rem;align-items:start;}'
            '.swu-panel-art-tile{min-width:0;}'
            '.swu-panel-art-tile img{display:block;width:100%;height:auto;'
            'border-radius:0.35rem;}'
            '@media(max-width:850px){.swu-panel-art-grid{grid-template-columns:repeat(3,minmax(0,1fr));}}'
            '</style>'
            f'<div class="swu-panel-art-grid">{tiles}</div>',
            unsafe_allow_html=True,
        )
        return

    total, kinds, costs = _deck_statistics(entries)
    if not total:
        st.info("Add cards to see deck statistics.")
        return
    st.caption(f"Main deck only · {total} cards (copies counted)")
    type_bars = "".join(_deck_stats_bar(label, qty, total) for label, qty in kinds)
    cost_bars = "".join(_deck_stats_bar(f"Cost {label}", qty, total) for label, qty in costs)
    st.markdown(
        '<style>'
        '.swu-stats-group{margin-bottom:1.3rem;}'
        '.swu-stats-group h4{margin:0 0 0.65rem 0;font-size:1.02rem;}'
        '.swu-stats-bar-row{margin:0.65rem 0;}'
        '.swu-stats-bar-heading{display:flex;justify-content:space-between;gap:0.75rem;'
        'font-size:0.89rem;margin-bottom:0.26rem;}'
        '.swu-stats-bar-heading small{opacity:0.65;}'
        '.swu-stats-track{height:0.7rem;background:rgba(120,130,145,0.18);'
        'border-radius:0.35rem;overflow:hidden;}'
        '.swu-stats-fill{height:100%;background:#4388c7;border-radius:0.35rem;}'
        '</style>'
        f'<div class="swu-stats-group"><h4>Card types</h4>{type_bars}</div>'
        f'<div class="swu-stats-group"><h4>Card costs</h4>{cost_bars}</div>',
        unsafe_allow_html=True,
    )

def _matches_progress_filter(session, gid, entry, filter_choice, zone="main"):
    """Identical eligibility for main/sideboard list rows and right-hand gallery.

    Progress is zone-specific; a main-deck copy may be Added while the same
    gameplay card still needs a physical sideboard copy.
    """
    quantity = int(entry["count"])
    if filter_choice == "All cards":
        return True
    if zone == "main":
        record = get_progress(session, gid, quantity)
    elif zone == "sideboard":
        record = _zone_progress(session, "sideboard", gid, quantity)
    else:
        raise ValueError("Unknown deck zone")
    if filter_choice == "Need to buy":
        return bool(needs_to_buy(quantity, record))
    if filter_choice == "Not yet in physical deck":
        return record["inDeck"] < quantity
    raise ValueError("Unknown Show cards filter")


def grouped_deck_rows(entries, session, filter_choice):
    """Pure grouping and sorting logic; leaves collection progress unmodified."""
    section_order = (
        "Ground Units", "Space Units", "Events", "Upgrades",
        "Other Units", "Other Cards",
    )
    rows = {label: [] for label in section_order}
    for gid, entry in entries.items():
        card = entry["card"]
        quantity = int(entry["count"])
        if not _matches_progress_filter(session, gid, entry, filter_choice, "main"):
            continue
        rows[_deck_section(card)].append((gid, entry))
    for label in section_order:
        rows[label].sort(key=lambda item: (
            _cost_sort_key(item[1]["card"]),
            display_card_name(item[1]["card"]).casefold(),
            str(item[0]),
        ))
    return [(label, rows[label]) for label in section_order if rows[label]]


def official_deck_list_text(snapshot, include_sideboard=True):
    """Plain-text Twin Suns roster, sorted like the on-screen Deck list.

    Intentionally excludes ownership/shopping fields. Include the sideboard
    section when requested; the deck total always counts main cards only.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("A deck snapshot is required.")

    def card_name(card):
        if not isinstance(card, dict):
            return "Not selected"
        name = " ".join(str(card.get("name") or "").split())
        subtitle = " ".join(str(card.get("subtitle") or "").split())
        if not name:
            return "Not selected"
        return f"{name} - {subtitle}" if subtitle else name

    leaders = snapshot.get("leaders") or []
    base = snapshot.get("base")
    raw_cards = snapshot.get("cards") or []
    section_order = (
        "Ground Units", "Space Units", "Events", "Upgrades",
        "Other Units", "Other Cards",
    )
    by_section = {section: [] for section in section_order}
    main_total = 0
    for entry in raw_cards:
        card = entry["card"]
        quantity = int(entry["count"])
        if quantity < 1:
            raise ValueError("Deck quantities must be positive integers.")
        main_total += quantity
        section = _deck_section(card)
        by_section[section].append((card, quantity))

    # Same category order, numeric cost and alphabetical tie-break as the
    # on-screen grouped_deck_rows() function.
    for section in section_order:
        by_section[section].sort(key=lambda item: (
            _cost_sort_key(item[0]),
            display_card_name(item[0]).casefold(),
            str(item[0].get("gameplay_id") or item[0].get("uuid") or ""),
        ))

    lines = [
        f"Deck Name: {snapshot.get('name') or 'Untitled Twin Suns Deck'}",
        f"Leader 1: {card_name(leaders[0]) if len(leaders) > 0 else 'Not selected'}",
        f"Leader 2: {card_name(leaders[1]) if len(leaders) > 1 else 'Not selected'}",
        f"Base: {card_name(base)}",
        "",
        f"Main Deck Total: {main_total}",
    ]
    for section in section_order:
        for card, quantity in by_section[section]:
            lines.append(f"{quantity} {card_name(card)}")

    if not include_sideboard:
        return "\n".join(lines) + "\n"
    raw_sideboard = snapshot.get("sideboard") or []
    sideboard = []
    for entry in raw_sideboard:
        card = entry.get("card") if isinstance(entry, dict) else None
        qty = int(entry.get("count", 0)) if isinstance(entry, dict) else 0
        if card and qty > 0:
            sideboard.append((card, qty))
    sideboard.sort(key=lambda item: (
        section_order.index(_deck_section(item[0])),
        _cost_sort_key(item[0]),
        display_card_name(item[0]).casefold(),
    ))
    lines.extend(("", f"Sideboard Total: {sum(qty for _, qty in sideboard)}"))
    lines.extend(f"{qty} {card_name(card)}" for card, qty in sideboard)
    return "\n".join(lines) + "\n"


def render_deck_builder(st, db=None):
    session = st.session_state
    if db is not None:
        from swu_price_ui import prepare_deck_prices
        prepare_deck_prices(session, db)
    leaders = get_selected_leaders(session)
    base = session.get("swu_selected_base")
    entries = deck_entries(session)
    count, errors = deck_validation(session)

    st.header("Twin Suns Deck Builder")
    st.caption("2 different leaders · 1 base · 80+ draw-deck cards · singleton unless card text overrides")
    # Three card slots at half their former width, with aspects on the right.
    leader1_col, leader2_col, base_col, aspects_col = st.columns(
        [1, 1, 1, 3], gap="small"
    )
    for index, col in enumerate((leader1_col, leader2_col)):
        with col:
            st.markdown(f"**Leader {index + 1}**")
            if index < len(leaders):
                image_url = leaders[index].get("front_image_url")
                if image_url:
                    st.image(image_url, width="stretch")
                else:
                    st.caption("Image unavailable")
            else:
                st.caption("No leader selected")
            st.button(
                "Change",
                key=f"swu_deck_change_leader_{index}",
                on_click=change_deck_selection,
                args=(session, "leader", index),
                use_container_width=True,
            )

    with base_col:
        st.markdown("**Base**")
        if base:
            image_url = base.get("front_image_url")
            if image_url:
                st.image(image_url, width="stretch")
            else:
                st.caption("Image unavailable")
        else:
            st.caption("No base selected")
        st.button(
            "Change",
            key="swu_deck_change_base",
            on_click=change_deck_selection,
            args=(session, "base"),
            use_container_width=True,
        )

    supply = aspect_supply(leaders, base)
    with aspects_col:
        st.markdown("**Deck Aspects**")
        chosen_aspects = [a for a in ASPECT_ORDER if supply.get(a)]
        if chosen_aspects:
            aspect_columns = st.columns(2, gap="small")
            for i, aspect in enumerate(chosen_aspects):
                with aspect_columns[i % 2]:
                    st.markdown(f"{ICON[aspect]} **{aspect}** ×{supply[aspect]}")
        else:
            st.caption("No aspects selected")

    st.metric("Draw-deck cards", f"{count} / {DECK_MINIMUM} minimum")
    if errors:
        for issue in errors:
            st.warning(issue)
    else:
        st.success("Twin Suns deck meets the implemented construction checks.")

    @st.fragment
    def render_collection_and_compact_deck():
        # Physical collection tracking includes the two leaders and selected base.
        required = required_cards(leaders, base, list(entries.values()))
        progress = normalize_progress_map(session.get(PROGRESS_SESSION_KEY, {}), required)
        totals = progress_totals(required, progress)

        st.subheader("Collection Progress")
        c_required, c_in, c_elsewhere, c_missing = st.columns(4, gap="small")
        with c_required:
            st.metric("Required", totals["required"])
        with c_in:
            st.metric("In deck", totals["inDeck"])
        with c_elsewhere:
            st.metric("Owned elsewhere", totals["ownedElsewhere"])
        with c_missing:
            st.metric("Need to buy", totals["needToBuy"])
        if totals["required"]:
            st.progress(totals["inDeck"] / totals["required"])
        st.caption("In deck = physically added · Owned elsewhere = available but not yet added · Need to buy = not owned")

        if leaders or base:
            with st.expander("Leader and base collection progress", expanded=False):
                st.caption("Track the physical copies of your selected leaders and base too.")
                label, in_col, owned_col, missing_col = st.columns([4, 1.3, 1.3, 1.2])
                with in_col:
                    st.caption("In deck")
                with owned_col:
                    st.caption("Owned elsewhere")
                with missing_col:
                    st.caption("Need to buy")
                selections = [(f"Leader {i + 1}: {display_card_name(card)}", card)
                              for i, card in enumerate(leaders)]
                if base:
                    selections.append((f"Base: {display_card_name(base)}", base))
                for title, card in selections:
                    gid = game_id(card)
                    rec = get_progress(session, gid, 1)
                    c_name, c_in, c_owned, c_missing = st.columns([4, 1.3, 1.3, 1.2])
                    with c_name:
                        st.write(title)
                    with c_in:
                        _progress_widget(st, session, gid, 1, "inDeck")
                    with c_owned:
                        _progress_widget(st, session, gid, 1, "ownedElsewhere")
                    with c_missing:
                        st.write(needs_to_buy(1, rec))


        # Hover artwork sits over the deck list without expanding row height.
        st.markdown("""
        <style>
        .st-key-swu_compact_deck_list [data-testid="stHorizontalBlock"],
        .st-key-swu_compact_sideboard_list [data-testid="stHorizontalBlock"] {
            min-width:0 !important;
            overflow:visible !important;
        }
        .st-key-swu_compact_deck_list [data-testid="stColumn"],
        .st-key-swu_compact_sideboard_list [data-testid="stColumn"],
        .st-key-swu_compact_deck_list [data-testid="stElementContainer"],
        .st-key-swu_compact_sideboard_list [data-testid="stElementContainer"] {
            min-width:0 !important;
            overflow:visible !important;
        }
        .st-key-swu_compact_deck_list [data-testid="stMarkdownContainer"],
        .st-key-swu_compact_sideboard_list [data-testid="stMarkdownContainer"] {
            min-width:0 !important;
        }
        .st-key-swu_compact_deck_list [data-testid="stHorizontalBlock"]:has(.swu-deck-name-hover:hover),
        .st-key-swu_compact_sideboard_list [data-testid="stHorizontalBlock"]:has(.swu-deck-name-hover:hover) {
            position:relative;z-index:100;
        }
        .swu-deck-name-hover {position:relative;display:block;min-width:0;
            cursor:zoom-in;outline-offset:2px;font-size:calc(0.8rem + 2px);
            line-height:1.14rem;}
        .swu-deck-name-text {display:block;overflow:hidden;text-overflow:ellipsis;
            white-space:nowrap;}
        /* CSS fallback for Streamlit versions without st.html JavaScript.
           In modern Streamlit, the cursor-following portal replaces this. */
        .swu-deck-art-preview {display:block;visibility:hidden;opacity:0;
            position:absolute;top:-0.35rem;left:calc(100% + 0.65rem);
            right:auto;width:220px;max-width:55vw;
            max-height:78vh;z-index:999999;pointer-events:none;
            background:var(--background-color,#fff);border-radius:0.45rem;
            box-shadow:0 6px 30px rgba(0,0,0,.45);padding:0.3rem;
            box-sizing:border-box;overflow:visible;}
        .swu-deck-art-preview img {display:block;width:100%;height:auto;
            max-height:calc(78vh - 0.6rem);object-fit:contain;border-radius:0.25rem;}
        .swu-deck-name-hover:hover .swu-deck-art-preview,
        .swu-deck-name-hover:focus .swu-deck-art-preview,
        .swu-deck-name-hover:focus-within .swu-deck-art-preview {
            visibility:visible;opacity:1;
        }
        </style>""", unsafe_allow_html=True)
        _install_deck_cursor_preview(st)

        st.subheader("Deck")
        # One selector controls the main/sideboard rows and the artwork panel.
        # It is intentionally placed above the two-column layout.
        filter_choice = st.selectbox(
            "Show cards",
            ["All cards", "Need to buy", "Not yet in physical deck"],
            key="swu_deck_progress_filter",
        )
        deck_left, deck_right = st.columns([2.4, 2.6], gap="medium")

        with deck_left:
            if not entries:
                st.info("No cards added yet.")
            else:
                sections = grouped_deck_rows(entries, session, filter_choice)
                if not sections:
                    st.info("No cards match this progress filter.")
                else:
                    # Scope all layout and button CSS to this one table.
                    st.markdown("""
                    <style>
                    .st-key-swu_compact_deck_list [data-testid="stHorizontalBlock"] {
                        gap: 0.18rem !important;
                        align-items: center !important;
                        min-height: 1.48rem !important;
                        overflow-x: visible !important;
                        overflow-y: visible !important;
                        min-width: 0 !important;
                    }
                    .st-key-swu_compact_deck_list [data-testid="stColumn"] {
                        min-width: 0 !important;
                    }
                    .st-key-swu_compact_deck_list [data-testid="stVerticalBlock"] {
                        gap: 0.02rem !important;
                    }
                    .st-key-swu_compact_deck_list [data-testid="stMarkdownContainer"] p {
                        margin: 0 !important;
                        line-height: 1.05rem !important;
                    }
                    .st-key-swu_compact_deck_list [data-testid="stButton"] {
                        margin: 0 !important;
                        padding: 0 !important;
                    }
                    .st-key-swu_compact_deck_list [data-testid="stButton"] button {
                        min-height: 1.38rem !important;
                        height: 1.38rem !important;
                        min-width: 1.44rem !important;
                        width: 1.44rem !important;
                        padding: 0 !important;
                        margin: 0 !important;
                        font-size: 0.74rem !important;
                        font-weight: 700 !important;
                        line-height: 1 !important;
                        border-radius: 4px !important;
                    }
                    .st-key-swu_compact_deck_list [class*="st-key-swu_deck_added_yes_"] button {
                        background-color: #15803d !important;
                        border-color: #166534 !important;
                        color: #ffffff !important;
                    }
                    .st-key-swu_compact_deck_list [class*="st-key-swu_deck_added_no_"] button {
                        background-color: #ba303b !important;
                        border-color: #9f2530 !important;
                        color: #ffffff !important;
                    }
                    .st-key-swu_compact_deck_list [class*="st-key-swu_move_sb_"] button {
                        width: 2.1rem !important;
                        min-width: 2.1rem !important;
                        font-size: 0.67rem !important;
                    }
                    .st-key-swu_compact_deck_list [class*="st-key-swu_deck_remove_"] button {
                        width: 1.85rem !important;
                        min-width: 1.85rem !important;
                        font-size: 0.69rem !important;
                        font-weight: 600 !important;
                    }
                    /* Bulk controls need full-width labels, unlike row toggles. */
                    .st-key-swu_compact_deck_list [class*="st-key-swu_deck_add_all"] button,
                    .st-key-swu_compact_deck_list [class*="st-key-swu_deck_remove_all"] button {
                        width: 100% !important;
                        min-width: 0 !important;
                        padding: 0 0.08rem !important;
                        font-size: 0.66rem !important;
                        white-space: nowrap !important;
                    }
                    </style>
                    """, unsafe_allow_html=True)

                    with st.container(key="swu_compact_deck_list"):
                        if filter_choice == "All cards":
                            # The Added column itself is deliberately narrow.
                            # Put the two short bulk buttons just above its
                            # header, spanning the right side of the table.
                            space, bulk_actions = st.columns([4.9, 1.55], gap="xxsmall")
                            with bulk_actions:
                                add_bulk, remove_bulk = st.columns(2, gap="xxsmall")
                                with add_bulk:
                                    st.button(
                                        "Add all", key="swu_deck_add_all",
                                        on_click=set_all_draw_deck_status,
                                        args=(session, True),
                                        help="Mark every draw-deck card as Added. Does not change My Collection.",
                                        use_container_width=True,
                                    )
                                with remove_bulk:
                                    st.button(
                                        "Remove all", key="swu_deck_remove_all",
                                        on_click=set_all_draw_deck_status,
                                        args=(session, False),
                                        help="Mark every draw-deck card as Not Added. Does not change My Collection.",
                                        use_container_width=True,
                                    )
                            bulk_notice = session.pop("swu_bulk_added_notice", None)
                            if bulk_notice:
                                st.caption(bulk_notice)
                        # Qty | Name | Aspects | Cost | Added | To sideboard | Remove | Price
                        widths = [0.42, 2.60, 0.78, 0.42, 0.47, 0.67, 0.60, 0.55]
                        headings = ("Qty", "Card name", "Aspects", "Cost", "Added", "To SB", "Remove", "Price")
                        header_cols = st.columns(
                            widths, gap="xxsmall", vertical_alignment="center", wrap=False
                        )
                        for col, heading in zip(header_cols, headings):
                            with col:
                                st.markdown(
                                    f'<span style="font-size:0.70rem;color:#808894">{heading}</span>',
                                    unsafe_allow_html=True,
                                )

                        for section_name, section_entries in sections:
                            section_total = sum(int(entry["count"]) for _, entry in section_entries)
                            st.markdown(
                                f"**{section_name}** · {section_total} "
                                f"card{'s' if section_total != 1 else ''}"
                            )
                            for gid, entry in section_entries:
                                card = entry["card"]
                                quantity = int(entry["count"])
                                record = get_progress(session, gid, quantity)
                                in_deck = record["inDeck"] >= quantity
                                off_aspect = (
                                    missing_aspect_icons(card, supply)
                                    if leaders and base else 0
                                )
                                qty_col, name_col, aspect_col, cost_col, added_col, side_col, remove_col, price_col = (
                                    st.columns(
                                        widths, gap="xxsmall",
                                        vertical_alignment="center", wrap=False,
                                    )
                                )
                                with qty_col:
                                    st.markdown(f"**{quantity}×**")
                                with name_col:
                                    st.markdown(_hoverable_card_name(card), unsafe_allow_html=True)
                                with aspect_col:
                                    icons = "".join(ICON.get(a, "") for a in (card.get("aspects") or []))
                                    tooltip = (
                                        f"Off-aspect penalty: +{off_aspect * 2} resources"
                                        if off_aspect else "Aspects"
                                    )
                                    st.markdown(
                                        f'<span title="{escape(tooltip, quote=True)}" '
                                        f'style="font-size:0.82rem;white-space:nowrap">'
                                        f'{escape(icons) if icons else "—"}'
                                        f'{" ⚠️" if off_aspect else ""}</span>',
                                        unsafe_allow_html=True,
                                    )
                                with cost_col:
                                    cost = card.get("cost")
                                    st.markdown(
                                        str(cost) if cost is not None and str(cost) != "" else "—"
                                    )
                                with price_col:
                                    from swu_prices import price_text
                                    st.markdown(price_text(card))
                                with added_col:
                                    st.button(
                                        "✓" if in_deck else "✕",
                                        key=f"swu_deck_added_{'yes' if in_deck else 'no'}_{gid}",
                                        on_click=toggle_deck_card_status,
                                        args=(session, gid, quantity),
                                        help=(
                                            "Added to physical deck — click to mark not added"
                                            if in_deck else
                                            "Not added to physical deck — click to mark added"
                                        ),
                                    )
                                with side_col:
                                    if st.button(
                                        "→ SB", key=f"swu_move_sb_{gid}",
                                        help="Move one copy from main deck to sideboard",
                                    ):
                                        move_to_sideboard(session, gid)
                                        st.rerun()
                                with remove_col:
                                    if st.button(
                                        "−1", key=f"swu_deck_remove_{gid}",
                                        help="Remove one copy from the deck list",
                                    ):
                                        remove_card(session, gid)
                                        # Ensure the draw-deck total at the top refreshes.
                                        st.rerun()

            all_side_rows = sideboard_entries(session)
            side_rows = {
                gid: entry for gid, entry in all_side_rows.items()
                if _matches_progress_filter(session, gid, entry, filter_choice, "sideboard")
            }
            total_side_copies = sum(int(e["count"]) for e in all_side_rows.values())
            visible_side_copies = sum(int(e["count"]) for e in side_rows.values())
            side_heading = f"**Sideboard** · {total_side_copies} cards"
            if filter_choice != "All cards":
                side_heading += f" ({visible_side_copies} shown)"
            st.markdown(side_heading)
            if side_rows:
                st.caption("Move copies back to the main deck or remove them entirely.")
                with st.container(key="swu_compact_sideboard_list"):
                    st.markdown("""
                    <style>
                    .st-key-swu_compact_sideboard_list [data-testid="stHorizontalBlock"] {
                        gap:0.18rem !important; min-height:1.45rem !important;
                        min-width:0 !important; overflow:visible !important;
                    }
                    .st-key-swu_compact_sideboard_list [data-testid="stColumn"] {
                        min-width:0 !important;
                    }
                    .st-key-swu_compact_sideboard_list [data-testid="stVerticalBlock"] { gap:0.03rem !important; }
                    .st-key-swu_compact_sideboard_list [data-testid="stMarkdownContainer"] p { margin:0 !important; line-height:1.07rem !important; }
                    .st-key-swu_compact_sideboard_list button { min-height:1.38rem !important; height:1.38rem !important; padding:0 0.2rem !important; font-size:0.69rem !important; }
                    .st-key-swu_compact_sideboard_list [class*="st-key-swu_sb_added_yes_"] button { background:#15803d !important; color:white !important; }
                    .st-key-swu_compact_sideboard_list [class*="st-key-swu_sb_added_no_"] button { background:#ba303b !important; color:white !important; }
                    </style>""", unsafe_allow_html=True)
                    sb_widths = [0.38, 2.55, 0.6, 0.8, 0.65, 0.55]
                    for col, heading in zip(st.columns(sb_widths, gap="xxsmall"),
                                            ("Qty", "Card", "Added", "To Deck", "Remove", "Price")):
                        with col:
                            st.caption(heading)
                    for gid, entry in sorted(side_rows.items(), key=lambda item: (
                        _cost_sort_key(item[1]["card"]),
                        display_card_name(item[1]["card"]).casefold(),
                    )):
                        quantity = int(entry["count"])
                        rec = _zone_progress(session, "sideboard", gid, quantity)
                        cols = st.columns(sb_widths, gap="xxsmall", vertical_alignment="center")
                        with cols[0]:
                            st.markdown(f"**{quantity}×**")
                        with cols[1]:
                            st.markdown(_hoverable_card_name(entry["card"]), unsafe_allow_html=True)
                        with cols[5]:
                            from swu_prices import price_text
                            st.markdown(price_text(entry["card"]))
                        with cols[2]:
                            st.button(
                                "✓" if rec["inDeck"] >= quantity else "✕",
                                key=f"swu_sb_added_{'yes' if rec['inDeck'] >= quantity else 'no'}_{gid}",
                                on_click=set_zone_added,
                                args=(session, "sideboard", gid, quantity),
                                help="Toggle physical Added status for this sideboard card",
                            )
                        with cols[3]:
                            if st.button("← Deck", key=f"swu_sb_to_main_{gid}",
                                         help="Move one copy back to main deck"):
                                move_to_main_deck(session, gid)
                                st.rerun()
                        with cols[4]:
                            if st.button("−1", key=f"swu_sb_remove_{gid}",
                                         help="Remove one sideboard copy"):
                                remove_sideboard_card(session, gid)
                                st.rerun()
            else:
                st.caption("No sideboard cards match this filter." if all_side_rows
                           else "No cards in sideboard.")

        with deck_right:
            _render_deck_right_panel(st, session, entries, filter_choice)

    render_collection_and_compact_deck()

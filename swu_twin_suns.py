"""Twin Suns (2026) rules and session-only deck builder.

Two distinct leaders; no combined Heroism/Villainy; one base;
minimum 80 draw-deck cards; singleton unless specific card text overrides.
"""

from collections import Counter
from html import escape
import re

from swu_collection_progress import (
    PROGRESS_SESSION_KEY, game_id, get_progress, set_progress,
    normalize_progress_map, needs_to_buy, progress_totals, required_cards,
)

DECK_MINIMUM = 80
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
    if limit is not None and count >= limit:
        return False, "Copy limit reached for this card."
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
    for entry in entries.values():
        limit = card_copy_limit(entry["card"])
        if limit is not None and entry["count"] > limit:
            errors.append(f"Copy limit exceeded: {entry['card'].get('name', 'Unnamed card')}.")
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
        if kind not in table_for_type:
            raise ValueError(f"Unknown card type for {display_card_name(card)}: {kind}")
        by_table.setdefault(table_for_type[kind], []).append(gid)
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


def tcgplayer_missing_text(required, progress):
    """Create TCGplayer Mass Entry lines for cards we still need to purchase.

    TCGplayer matches `quantity name - subtitle` without requiring a
    particular set or printing. Include leaders and base when missing.
    No header: Mass Entry treats each nonempty line as one requested card.
    """
    lines = []
    for gid, (card, qty) in sorted(
        required.items(),
        key=lambda item: display_card_name(item[1][0]).casefold(),
    ):
        record = get_progress({PROGRESS_SESSION_KEY: progress}, gid, qty)
        missing = needs_to_buy(qty, record)
        if not missing:
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
        lines.append(f"{missing} {title}")
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
        record = get_progress(session, gid, quantity)
        if filter_choice == "Need to buy" and not needs_to_buy(quantity, record):
            continue
        if filter_choice == "Not yet in physical deck" and record["inDeck"] >= quantity:
            continue
        rows[_deck_section(card)].append((gid, entry))
    for label in section_order:
        rows[label].sort(key=lambda item: (
            _cost_sort_key(item[1]["card"]),
            display_card_name(item[1]["card"]).casefold(),
            str(item[0]),
        ))
    return [(label, rows[label]) for label in section_order if rows[label]]


def official_deck_list_text(snapshot):
    """Plain-text Twin Suns roster, sorted like the on-screen Deck list.

    Intentionally excludes ownership/shopping fields. The current Twin Suns
    app has no sideboard editor; the section is present for future support.
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

    # Twin Suns does not currently expose a sideboard editor. Keep the
    # requested section in the export; any future sideboard data is supported
    # when it is present in the snapshot.
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


def render_deck_builder(st):
    session = st.session_state
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


        # A tighter left panel, with space on the right for the next interactive view.
        deck_left, deck_right = st.columns([2, 3], gap="medium")

        with deck_left:
            st.subheader("Deck")
            filter_choice = st.selectbox(
                "Show cards",
                ["All cards", "Need to buy", "Not yet in physical deck"],
                key="swu_deck_progress_filter",
            )
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
                        gap: 0.24rem !important;
                        align-items: center !important;
                        min-height: 1.48rem !important;
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
                        # Qty | Name | Aspects | Cost | Added | Remove
                        widths = [0.42, 3.65, 0.88, 0.47, 0.47, 0.60]
                        headings = ("Qty", "Card name", "Aspects", "Cost", "Added", "Remove")
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
                                qty_col, name_col, aspect_col, cost_col, added_col, remove_col = (
                                    st.columns(
                                        widths, gap="xxsmall",
                                        vertical_alignment="center", wrap=False,
                                    )
                                )
                                with qty_col:
                                    st.markdown(f"**{quantity}×**")
                                with name_col:
                                    name = escape(display_card_name(card), quote=True)
                                    st.markdown(
                                        f'<div title="{name}" '
                                        f'style="font-size:calc(0.8rem + 2px);'
                                        f'overflow:hidden;text-overflow:ellipsis;'
                                        f'white-space:nowrap;line-height:1.14rem">{name}</div>',
                                        unsafe_allow_html=True,
                                    )
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
                                with remove_col:
                                    if st.button(
                                        "−1", key=f"swu_deck_remove_{gid}",
                                        help="Remove one copy from the deck list",
                                    ):
                                        remove_card(session, gid)
                                        # Ensure the draw-deck total at the top refreshes.
                                        st.rerun()

        with deck_right:
            # Reserved for the dynamic card/details panel planned next.
            st.empty()

    render_collection_and_compact_deck()

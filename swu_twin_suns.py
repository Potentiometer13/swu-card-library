"""Twin Suns (2026) rules and session-only deck builder.

Two distinct leaders; no combined Heroism/Villainy; one base;
minimum 80 draw-deck cards; singleton unless specific card text overrides.
"""

from collections import Counter
from html import escape
from io import StringIO
import csv
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


def _shopping_csv(required, progress):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Card ID", "Card name", "Required", "In deck", "Owned elsewhere", "Need to buy"])
    for gid, (card, qty) in sorted(required.items(), key=lambda item: display_card_name(item[1][0]).casefold()):
        record = get_progress({PROGRESS_SESSION_KEY: progress}, gid, qty)
        missing = needs_to_buy(qty, record)
        if missing:
            number = str(card.get("collector_number") or "")
            code = str(card.get("set_code") or "")
            card_id = number if "_" in number else f"{code}_{number}"
            writer.writerow([card_id, display_card_name(card), qty, record["inDeck"],
                             record["ownedElsewhere"], missing])
    return output.getvalue().encode("utf-8-sig")


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

    st.download_button(
        "Download shopping list (CSV)",
        data=_shopping_csv(required, progress),
        file_name="twin_suns_shopping_list.csv",
        mime="text/csv",
        key="swu_shopping_csv",
        disabled=totals["needToBuy"] == 0,
    )

    st.subheader("Draw Deck")
    st.caption("Mark each physical copy as In deck or Owned elsewhere. The shopping list updates automatically.")
    filter_choice = st.selectbox(
        "Show cards", ["All cards", "Need to buy", "Not yet in physical deck"],
        key="swu_deck_progress_filter",
    )
    if not entries:
        st.info("No cards added yet.")
        return

    all_entries = sorted(entries.items(), key=lambda kv: (
        (kv[1]["card"].get("card_type") or ""),
        str(kv[1]["card"].get("cost") or "0").zfill(4),
        display_card_name(kv[1]["card"]).casefold(),
    ))
    st.caption("Card  ·  In deck  ·  Owned elsewhere  ·  Need to buy  ·  Remove")
    visible_count = 0
    for gid, entry in all_entries:
        card = entry["card"]
        quantity = int(entry["count"])
        record = get_progress(session, gid, quantity)
        to_buy = needs_to_buy(quantity, record)
        if filter_choice == "Need to buy" and not to_buy:
            continue
        if filter_choice == "Not yet in physical deck" and record["inDeck"] >= quantity:
            continue
        visible_count += 1
        missing = missing_aspect_icons(card, supply) if leaders and base else 0
        info, in_col, owned_col, missing_col, actions = st.columns(
            [4.5, 1.2, 1.35, 1.1, 1], gap="small"
        )
        with info:
            name = escape(display_card_name(card))
            st.markdown(f"**{quantity}× {name}**")
            details = f"{card.get('card_type') or 'Card'} · Cost {card.get('cost') if card.get('cost') is not None else '—'}"
            if missing:
                details += f" · Off-aspect penalty +{missing * 2} resources"
            limit = card_copy_limit(card)
            if limit != 1:
                details += f" · Copy exception: {'unlimited' if limit is None else str(limit)}"
            st.caption(details)
        with in_col:
            _progress_widget(st, session, gid, quantity, "inDeck")
        with owned_col:
            _progress_widget(st, session, gid, quantity, "ownedElsewhere")
        with missing_col:
            st.write(to_buy)
        with actions:
            st.button("− 1", key=f"swu_deck_remove_{gid}", on_click=remove_card,
                      args=(session, gid), use_container_width=True)
    if not visible_count:
        st.info("No cards match this progress filter.")

"""Stage 2D helpers for the SWU Streamlit gallery.

Use filtered Supabase query over the `swu_grouped_cards` view. The helper
preserves the existing search controls, groups only after those filters, and
paginates unique gameplay IDs rather than printing rows.
"""

from collections import OrderedDict, defaultdict
from copy import copy
from html import escape
import json
import re

import streamlit as st


BATCH_SIZE = 1000  # Supabase projects commonly cap one response at 1,000 rows.


def printing_sort_key(card):
    """Prefer canonical Standard art, then other versions."""
    variant = str(card.get("variant_type") or "").lower().strip()
    is_canonical = str(card.get("uuid")) == str(card.get("gameplay_id"))
    priority = {
        "standard": 0,
        "standard foil": 1,
        "hyperspace": 2,
        "hyperspace foil": 3,
        "showcase": 4,
    }
    return (
        priority.get(variant, 9),
        0 if is_canonical else 1,
        str(card.get("set_code") or ""),
        str(card.get("collector_number") or ""),
        str(card.get("uuid") or ""),
    )


def group_matching_printings(rows):
    """Return representatives after grouping *already-filtered* results.

    Filtering first is important: searching by a promo set must still find
    the group even if its default Standard printing is in a different set.
    """
    groups = OrderedDict()
    for card in rows:
        group_id = str(card.get("gameplay_id") or card["uuid"])
        groups.setdefault(group_id, []).append(card)
    result = []
    for group_id, matches in groups.items():
        representative = min(matches, key=printing_sort_key)
        result.append(representative)
    return result


def get_grouped_page(filtered_query, page, page_size, batch_size=BATCH_SIZE):
    """Finish existing filtered query; return (unique_page, unique_count).

    IMPORTANT: pass a builder BEFORE .range() or .execute().
    All matching printings must be read so unique counts/paging are correct.
    This is suitable for the ~10k-printing hobby-sized catalog, but a large
    high-traffic deployment should use a database-side filtered RPC instead.
    """
    page = max(1, int(page))
    page_size = max(1, int(page_size))
    batch_size = min(1000, max(1, int(batch_size)))

    filtered_query = filtered_query.order("name").order("uuid")
    all_rows = []
    offset = 0
    while True:
        response = copy(filtered_query).range(offset, offset + batch_size - 1).execute()
        chunk = response.data or []
        all_rows.extend(chunk)
        if len(chunk) < batch_size:
            break
        offset += len(chunk)

    representatives = group_matching_printings(all_rows)
    start = (page - 1) * page_size
    return representatives[start:start + page_size], len(representatives)


def load_printing_options(db, visible_cards):
    """Fetch all printing versions for visible cards in a few batched calls."""
    ids = list(dict.fromkeys(
        str(c.get("gameplay_id") or c["uuid"])
        for c in visible_cards
    ))
    grouped = defaultdict(list)
    fields = (
        "uuid,gameplay_id,collector_number,set_code,"
        "variant_type,front_image_url,name,subtitle"
    )
    # Chunk UUIDs to avoid very long REST URLs and project row limits.
    for offset in range(0, len(ids), 25):
        subset = ids[offset:offset + 25]
        response = (
            db.table("swu_grouped_cards")
              .select(fields)
              .in_("gameplay_id", subset)
              .limit(1000)
              .execute()
        )
        for card in (response.data or []):
            grouped[str(card["gameplay_id"])].append(card)
    for items in grouped.values():
        items.sort(key=printing_sort_key)
    return grouped


def printing_id(card):
    """Display IDs consistently as SET_NUMBER (unless already prefixed)."""
    number = str(card.get("collector_number") or "").strip()
    set_code = str(card.get("set_code") or "").strip()
    if not number:
        return "Unknown ID"
    if "_" in number or not set_code:
        return number
    return f"{set_code}_{number}"


def printing_label(card):
    return f"{printing_id(card)} · {card.get('variant_type') or 'Other printing'}"


@st.dialog("Card printings", width="large", on_dismiss="rerun")
def show_printing_dialog(card, printings):
    """Open a detail window; printing selection stays out of the gallery."""
    variants = sorted(list(printings or [card]), key=printing_sort_key)
    group_id = str(card.get("gameplay_id") or card["uuid"])
    widget_key = f"swu_printing_choice_{group_id}"
    by_id = {str(p["uuid"]): p for p in variants}

    if st.session_state.get(widget_key) not in by_id:
        preferred = str(card["uuid"])
        st.session_state[widget_key] = (
            preferred if preferred in by_id else next(iter(by_id))
        )

    name = card.get("name") or "Card"
    subtitle = card.get("subtitle")
    st.subheader(name)
    if subtitle:
        st.caption(subtitle)

    image_column, options_column = st.columns([3, 2], gap="large")

    with options_column:
        st.selectbox(
            "Printing",
            options=list(by_id),
            format_func=lambda uid: printing_label(by_id[uid]),
            key=widget_key,
        )
        chosen = by_id[st.session_state[widget_key]]
        st.caption(f"{len(variants)} available printing(s)")
        st.markdown(f"**Card ID:** {escape(printing_id(chosen))}")

    with image_column:
        if chosen.get("front_image_url"):
            st.image(chosen["front_image_url"], width="stretch")
        else:
            st.info("Image unavailable")


def show_grouped_card(card, printing_options, key_prefix="swu_gallery", show_printing_id=True):
    """Draw a clickable gallery card; printings appear only in its dialog.

    A native st.button is styled with the card image as its background, so a
    click calls st.dialog within the same Streamlit session (no extra packages).
    """
    variants = sorted(list(printing_options or [card]), key=printing_sort_key)
    group_id = str(card.get("gameplay_id") or card["uuid"])
    widget_key = f"swu_printing_choice_{group_id}"
    by_id = {str(p["uuid"]): p for p in variants}

    if st.session_state.get(widget_key) not in by_id:
        preferred = str(card["uuid"])
        st.session_state[widget_key] = (
            preferred if preferred in by_id else next(iter(by_id))
        )

    chosen = by_id[st.session_state[widget_key]]
    image = str(chosen.get("front_image_url") or "").strip()

    # Streamlit adds this key as a class to the button container.
    # Keep keys CSS-safe even if the API uses non-UUID identifiers.
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(card["uuid"]))
    button_key = f"{key_prefix}_card_{safe_id}"

    if image.startswith(("https://", "http://")):
        selector = f".st-key-{button_key} button"
        css_url = json.dumps(image).replace("<", "\\3c ")
        st.markdown(
            f"""
            <style>
            {selector}, {selector}:hover, {selector}:focus-visible {{
                width: 100% !important;
                height: auto !important;
                min-height: 0 !important;
                aspect-ratio: 5 / 7 !important;
                display: block !important;
                padding: 0 !important;
                border: none !important;
                border-radius: 8px !important;
                background: transparent url({css_url})
                    center center / contain no-repeat !important;
                box-shadow: none !important;
            }}
            {selector}:hover {{
                box-shadow: 0 0 0 2px #4B5563 !important;
                cursor: pointer !important;
            }}
            {selector}:focus-visible {{
                outline: 3px solid #6B7280 !important;
            }}
            {selector} p {{ opacity: 0 !important; }}
            </style>
            """,
            unsafe_allow_html=True,
        )

    if st.button(
        f"View {card.get('name') or 'card'} printings",
        key=button_key,
        use_container_width=True,
        help="Click card to view available printings",
    ):
        show_printing_dialog(card, variants)

    if show_printing_id:
        st.markdown(
            '<p style="text-align:center; font-weight:600; margin:0.25rem 0">'
            + escape(printing_id(chosen)) + '</p>',
            unsafe_allow_html=True,
        )

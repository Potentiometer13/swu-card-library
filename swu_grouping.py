"""Stage 2D helpers for the SWU Streamlit gallery.

Use filtered Supabase query over the `swu_grouped_cards` view. The helper
preserves the existing search controls, groups only after those filters, and
paginates unique gameplay IDs rather than printing rows.
"""

from collections import OrderedDict, defaultdict
from copy import copy
from html import escape

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
        "uuid,gameplay_id,collector_number,set_code,card_number,"
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
    code = str(card.get("collector_number") or "").strip()
    if code:
        return code
    set_code = str(card.get("set_code") or "").strip()
    number = str(card.get("card_number") or "").strip()
    return f"{set_code}_{number}" if set_code and number else "Unknown ID"


def printing_label(card):
    return f"{printing_id(card)} · {card.get('variant_type') or 'Other printing'}"


def show_grouped_card(card, printing_options):
    """Use inside the existing st.columns gallery slot.

    Displays only image and centered printing ID, with a small printing picker
    beneath cards with multiple versions.
    """
    variants = list(printing_options or [card])
    variants.sort(key=printing_sort_key)
    group_id = str(card.get("gameplay_id") or card["uuid"])
    widget_key = f"swu_printing_choice_{group_id}"

    variant_uuids = [str(p["uuid"]) for p in variants]
    preferred = str(card["uuid"])
    if widget_key not in st.session_state or st.session_state[widget_key] not in variant_uuids:
        st.session_state[widget_key] = preferred if preferred in variant_uuids else variant_uuids[0]

    chosen_id = st.session_state[widget_key]
    chosen = next(p for p in variants if str(p["uuid"]) == chosen_id)
    image = chosen.get("front_image_url")
    if image:
        st.image(image, use_container_width=True)
    else:
        st.caption("Image unavailable")

    st.markdown(
        '<p style="text-align:center; font-weight:600; margin:0.25rem 0">'
        + escape(printing_id(chosen)) + '</p>',
        unsafe_allow_html=True,
    )

    if len(variants) > 1:
        with st.popover(f"Printings ({len(variants)})", use_container_width=True):
            st.selectbox(
                "Choose printing",
                options=variant_uuids,
                format_func=lambda uid: printing_label(
                    next(p for p in variants if str(p["uuid"]) == uid)
                ),
                key=widget_key,
            )

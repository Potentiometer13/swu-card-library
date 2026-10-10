"""Four-column physical collection gallery, sharing Cards' grouping popup."""

from collections import defaultdict
import math

import streamlit as st

from swu_grouping import group_matching_printings, show_grouped_card, load_printing_options
from swu_inventory import adjust_owned, owned_quantities, cards_in_saved_decks

PAGE_SIZES = (100, 40, 20)
SELECT_FIELDS = (
    "uuid,gameplay_id,name,subtitle,set_code,collector_number,"
    "card_type,arena,cost,power,hp,rarity,aspects,traits,keywords,"
    "rules_text,front_image_url"
)


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_collection_cards(_db, ids):
    """Catalog records are public; ownership is NEVER stored in shared cache."""
    rows = []
    for offset in range(0, len(ids), 35):
        batch = list(ids[offset:offset + 35])
        response = (_db.table("swu_grouped_cards")
                    .select(SELECT_FIELDS)
                    .in_("gameplay_id", batch)
                    .limit(1000).execute())
        rows.extend(response.data or [])
    return rows


def _matches(card, name, contains, excludes, types, arena, aspects,
             rarities, sets, traits, keywords, min_cost, max_cost):
    text = (str(card.get("name") or "") + " " + str(card.get("subtitle") or "")).casefold()
    rules = str(card.get("rules_text") or "").casefold()
    if name and name not in text:
        return False
    if contains and contains not in rules:
        return False
    if excludes and excludes in rules:
        return False
    if types and card.get("card_type") not in types:
        return False
    if arena != "All" and card.get("card_type") == "Unit" and card.get("arena") != arena:
        return False
    if aspects and not set(aspects).issubset(set(card.get("aspects") or [])):
        return False
    if rarities and card.get("rarity") not in rarities:
        return False
    if sets and card.get("set_code") not in sets:
        return False
    if traits and not set(traits).intersection(card.get("traits") or []):
        return False
    if keywords and not set(keywords).intersection(card.get("keywords") or []):
        return False
    cost = card.get("cost")
    if cost is not None:
        try:
            if min_cost is not None and float(cost) < min_cost:
                return False
            if max_cost is not None and float(cost) > max_cost:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _sort_key(card, sort, owned, assigned):
    gid = str(card.get("gameplay_id") or card["uuid"])
    name = (card.get("name") or "").casefold()
    cost = card.get("cost")
    try:
        cost_num = int(cost) if cost is not None else 9999
    except (ValueError, TypeError):
        cost_num = 9999
    if sort == "Name Z–A":
        return (name, gid)
    if sort == "Owned: Most first":
        return (-owned.get(gid, 0), name)
    if sort == "Owned: Fewest first":
        return (owned.get(gid, 0), name)
    if sort == "In decks: Most first":
        return (-assigned.get(gid, 0), name)
    if sort == "Cost: Low to high":
        return (cost_num, name)
    if sort == "Cost: High to low":
        return (-cost_num, name)
    return (name, gid)


def render_my_collection(st, make_client, db, get_filter_options):
    st.header("My Collection")
    if not st.session_state.get("swu_auth_user_id"):
        st.info("Sign in on **0. Sign in** to view or manage your collection.")
        return
    try:
        owned = owned_quantities(st, make_client)
        assigned = cards_in_saved_decks(st, make_client)
    except Exception as exc:
        st.error(f"Could not load your collection: {exc}")
        return

    st.caption("In Decks counts physically added cards in your **saved** decks. "
               "Owned is your account-wide physical inventory, edited with + and −. "
               "Printings of the same gameplay card share one quantity.")
    st.metric("Distinct cards owned", len(owned))
    st.metric("Copies owned", sum(owned.values()))
    if not owned:
        st.info("Your collection is empty. Use + beside cards in **3. Cards** to add copies.")
        return

    try:
        all_printings = _fetch_collection_cards(db, tuple(sorted(owned)))
        sets, traits_available, keywords_available = get_filter_options()
    except Exception as exc:
        st.error(f"Could not retrieve your card gallery: {exc}")
        return

    filters, gallery = st.columns([1, 3], gap="large")
    with filters:
        st.subheader("Filters")
        if st.button("Clear Filters", key="swu_coll_clear", use_container_width=True):
            for key in ("swu_coll_name", "swu_coll_rules", "swu_coll_exclude"):
                st.session_state[key] = ""
            for key in ("swu_coll_type", "swu_coll_aspects", "swu_coll_rarities",
                        "swu_coll_sets", "swu_coll_traits", "swu_coll_keywords"):
                st.session_state[key] = []
            st.session_state["swu_coll_arena"] = "All"
            st.session_state["swu_coll_page"] = 1
            st.rerun()
        name = st.text_input("Card name / subtitle", key="swu_coll_name").strip().casefold()
        contains = st.text_input("Ability text contains", key="swu_coll_rules").strip().casefold()
        excludes = st.text_input("Exclude ability text", key="swu_coll_exclude").strip().casefold()
        with st.expander("Card Type & Stats", expanded=True):
            types = st.multiselect("Card type", ["Unit", "Event", "Upgrade", "Leader", "Base"],
                                   key="swu_coll_type")
            arena = st.selectbox("Arena", ["All", "Ground", "Space"], key="swu_coll_arena")
            low = st.number_input("Cost min", min_value=0, value=0, key="swu_coll_min_cost")
            high = st.number_input("Cost max", min_value=0, value=99, key="swu_coll_max_cost")
        with st.expander("Aspects"):
            aspects = st.multiselect("All selected aspects", ["Vigilance", "Command", "Aggression",
                                                             "Cunning", "Heroism", "Villainy"],
                                     key="swu_coll_aspects")
        with st.expander("Traits & Keywords"):
            traits = st.multiselect("Traits", traits_available, key="swu_coll_traits")
            keywords = st.multiselect("Keywords", keywords_available, key="swu_coll_keywords")
        with st.expander("Sets & Rarity"):
            set_map = {s["code"]: s["name"] for s in sets}
            selected_sets = st.multiselect("Sets", list(set_map),
                                            format_func=lambda c: f"{c} — {set_map[c]}",
                                            key="swu_coll_sets")
            rarities = st.multiselect("Rarity", ["Common", "Uncommon", "Rare",
                                                    "Legendary", "Special"],
                                      key="swu_coll_rarities")
    with gallery:
        sort = st.selectbox("Sort collection", ["Name A–Z", "Name Z–A", "Owned: Most first",
                                               "Owned: Fewest first", "In decks: Most first",
                                               "Cost: Low to high", "Cost: High to low"],
                            key="swu_coll_sort")
        page_size = st.selectbox("Cards per page", PAGE_SIZES, key="swu_coll_size")
        if low > high:
            st.warning("Minimum cost cannot exceed maximum cost.")
            return
        # Filter printings first, THEN group by gameplay identity. This keeps
        # niche/alternate set searches consistent with the main Cards gallery.
        cards = group_matching_printings(
            [c for c in all_printings if _matches(
                c, name, contains, excludes, types, arena, aspects, rarities,
                selected_sets, traits, keywords, low, high
            )]
        )
        cards.sort(key=lambda c: _sort_key(c, sort, owned, assigned), reverse=(sort == "Name Z–A"))
        pages = max(1, math.ceil(len(cards) / page_size))
        page = min(max(1, st.session_state.get("swu_coll_page", 1)), pages)
        st.session_state["swu_coll_page"] = page
        st.caption(f"{len(cards):,} matching gameplay cards")
        if not cards:
            st.info("No owned cards match these filters.")
            return
        page_cols = st.columns([1, 2, 1], gap="small")
        with page_cols[0]:
            if st.button("◀ Previous", key="swu_coll_prev", disabled=page <= 1):
                st.session_state["swu_coll_page"] = page - 1
                st.rerun()
        with page_cols[1]:
            st.markdown(f"<div style='text-align:center'>Page {page} of {pages}</div>",
                        unsafe_allow_html=True)
        with page_cols[2]:
            if st.button("Next ▶", key="swu_coll_next", disabled=page >= pages):
                st.session_state["swu_coll_page"] = page + 1
                st.rerun()
        shown = cards[(page-1)*page_size:page*page_size]
        try:
            versions = load_printing_options(db, shown)
        except Exception as exc:
            st.warning(f"Alternate printings unavailable: {exc}")
            versions = {}
        cols = st.columns(4)
        for i, card in enumerate(shown):
            gid = str(card.get("gameplay_id") or card["uuid"])
            with cols[i % 4]:
                show_grouped_card(card, versions.get(gid, [card]),
                                  key_prefix="swu_coll_gallery", show_printing_id=False)
                st.markdown(
                    f'<div style="text-align:center;font-size:0.82rem;font-weight:600;'
                    f'margin:0.15rem 0 0.25rem">In Decks/Owned: '
                    f'{assigned.get(gid, 0)}/{owned.get(gid, 0)}</div>',
                    unsafe_allow_html=True,
                )
                minus, plus = st.columns(2, gap="xxsmall")
                with minus:
                    if st.button("−1", key=f"swu_coll_down_{gid}",
                                 disabled=owned.get(gid, 0) <= 0, use_container_width=True):
                        try:
                            adjust_owned(st, make_client, gid, -1)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not remove copy: {exc}")
                with plus:
                    if st.button("+1", key=f"swu_coll_up_{gid}", use_container_width=True):
                        try:
                            adjust_owned(st, make_client, gid, 1)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not add copy: {exc}")

"""Account-owned card gallery with the same filter controls as 3. Cards.

Gallery printings are grouped by gameplay identity. Filter controls use their
own session-state keys; they must not change Card Search's filters or page.
"""

from collections import Counter
import math

import streamlit as st

from swu_grouping import group_matching_printings, show_grouped_card, load_printing_options
from swu_inventory import adjust_owned, owned_quantities, cards_in_saved_decks
from swu_twin_suns import get_selected_leaders, aspect_supply

PAGE_SIZES = (100, 40, 20)
ASPECTS = ("Vigilance", "Command", "Aggression", "Cunning", "Heroism", "Villainy")
ASPECT_MODES = ("Deck Compatibility", "All Selected", "Exact", "Exclude Selected")
ASPECT_ORDER = ("Heroism", "Villainy", "Vigilance", "Command", "Aggression", "Cunning")
ASPECT_ICONS = {
    "Heroism": "⚪", "Villainy": "⚫", "Vigilance": "🔵",
    "Command": "🟢", "Aggression": "🔴", "Cunning": "🟡",
}
ASPECT_PALETTE = {
    "Heroism": ("#FFFFFF", "#171717"),
    "Villainy": ("#252831", "#FFFFFF"),
    "Vigilance": ("#307FC1", "#FFFFFF"),
    "Command": ("#258852", "#FFFFFF"),
    "Aggression": ("#C23E48", "#FFFFFF"),
    "Cunning": ("#F0C746", "#202124"),
}
SELECT_FIELDS = (
    "uuid,gameplay_id,name,subtitle,set_code,collector_number,"
    "card_type,arena,cost,power,hp,rarity,aspects,traits,keywords,"
    "rules_text,ability_search_text,front_image_url,"
    "aspect_vigilance,aspect_command,aspect_aggression,aspect_cunning,"
    "aspect_heroism,aspect_villainy"
)


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_collection_cards(_db, ids):
    """Cache public catalog only; account-owned quantities are never shared."""
    rows = []
    for offset in range(0, len(ids), 35):
        batch = list(ids[offset:offset + 35])
        response = (_db.table("swu_grouped_cards")
                    .select(SELECT_FIELDS)
                    .in_("gameplay_id", batch)
                    .limit(1000).execute())
        rows.extend(response.data or [])
    return rows


def _reset_collection_page():
    st.session_state["swu_coll_page"] = 1


def _toggle_collection_aspect(aspect):
    key = f"swu_coll_aspect_level_{aspect.lower()}"
    current = st.session_state.get(key, 0)
    st.session_state[key] = 0 if current > 0 else 1
    _reset_collection_page()


def _toggle_collection_double_aspect(aspect):
    key = f"swu_coll_aspect_level_{aspect.lower()}"
    current = st.session_state.get(key, 0)
    st.session_state[key] = 1 if current == 2 else 2
    _reset_collection_page()


def _sync_collection_aspects_from_deck(force=False):
    """Follow Cards' automatic deck-aspect defaults, independently of Cards UI."""
    leaders = get_selected_leaders(st.session_state)
    base = st.session_state.get("swu_selected_base")
    if not isinstance(base, dict):
        base = None
    signature = (
        tuple((str(x.get("gameplay_id") or x.get("uuid") or ""),
               tuple(x.get("aspects") or [])) for x in leaders),
        (str(base.get("gameplay_id") or base.get("uuid") or ""),
         tuple(base.get("aspects") or [])) if base else None,
    )
    if (not force
            and st.session_state.get("swu_coll_aspects_defaults_version") == 1
            and st.session_state.get("swu_coll_aspects_deck_signature") == signature):
        return
    supplied = aspect_supply(leaders, base) if (leaders or base) else None
    for aspect in ASPECTS:
        maximum = 1 if aspect in ("Heroism", "Villainy") else 2
        level = maximum if supplied is None else min(maximum, max(0, int(supplied.get(aspect, 0))))
        st.session_state[f"swu_coll_aspect_level_{aspect.lower()}"] = level
    st.session_state["swu_coll_aspects_deck_signature"] = signature
    st.session_state["swu_coll_aspects_defaults_version"] = 1
    _reset_collection_page()


def _initialize_collection_filters(maxima):
    """One-time migration from the earlier simplified collection filters."""
    if st.session_state.get("swu_coll_filter_version") == 2:
        return
    st.session_state["swu_coll_type"] = ["Unit", "Event", "Upgrade"]
    st.session_state["swu_coll_arena_ground"] = True
    st.session_state["swu_coll_arena_space"] = True
    st.session_state["swu_coll_aspect_mode"] = "Deck Compatibility"
    st.session_state["swu_coll_include_niche_sets"] = False
    for stat in ("cost", "power", "hp"):
        st.session_state[f"swu_coll_{stat}_min"] = 0
        st.session_state[f"swu_coll_{stat}_max"] = maxima[stat]
    st.session_state["swu_coll_filter_version"] = 2
    _sync_collection_aspects_from_deck(force=True)


def _clear_collection_filters(maxima):
    for key in ("swu_coll_name", "swu_coll_rules", "swu_coll_exclude"):
        st.session_state[key] = ""
    for key in ("swu_coll_traits", "swu_coll_keywords", "swu_coll_sets", "swu_coll_rarities"):
        st.session_state[key] = []
    st.session_state["swu_coll_type"] = ["Unit", "Event", "Upgrade"]
    st.session_state["swu_coll_arena_ground"] = True
    st.session_state["swu_coll_arena_space"] = True
    st.session_state["swu_coll_aspect_mode"] = "Deck Compatibility"
    st.session_state["swu_coll_include_niche_sets"] = False
    for stat in ("cost", "power", "hp"):
        st.session_state[f"swu_coll_{stat}_min"] = 0
        st.session_state[f"swu_coll_{stat}_max"] = maxima[stat]
    _sync_collection_aspects_from_deck(force=True)
    _reset_collection_page()


def _numeric_pair(label, stat, maximum):
    """Same side-by-side min/max controls as the Cards tab."""
    left, right = st.columns(2)
    with left:
        minimum = st.number_input(
            f"{label} min", min_value=0, value=0, step=1,
            key=f"swu_coll_{stat}_min", on_change=_reset_collection_page,
        )
    with right:
        maximum_value = st.number_input(
            f"{label} max", min_value=0, max_value=maximum,
            value=maximum, step=1,
            key=f"swu_coll_{stat}_max", on_change=_reset_collection_page,
        )
    return minimum, maximum_value


def _render_collection_aspect_grid():
    """Mirror 3. Cards' colored and double-aspect button grid."""
    levels = {
        aspect: st.session_state.get(f"swu_coll_aspect_level_{aspect.lower()}", 0)
        for aspect in ASPECT_ORDER
    }
    css = """<style>
    [class*="st-key-swu_coll_aspect_main_"] button,
    [class*="st-key-swu_coll_aspect_double_"] button {
        min-height: 48px;
        width: 100%;
        border-radius: 9px !important;
        padding: 4px 2px !important;
        font-weight: 600 !important;
        transition: background-color 0.15s ease, box-shadow 0.15s ease;
    }
    [class*="st-key-swu_coll_aspect_main_"] button p,
    [class*="st-key-swu_coll_aspect_double_"] button p {
        font-size: 0.73rem !important;
        line-height: 1.2 !important;
        text-align: center !important;
    }
    """
    for aspect in ASPECT_ORDER:
        color, text_color = ASPECT_PALETTE[aspect]
        for kind in ("main", "double"):
            if kind == "double" and aspect in ("Heroism", "Villainy"):
                continue
            active = levels[aspect] > 0 if kind == "main" else levels[aspect] == 2
            foreground = text_color if active else "inherit"
            background = color if active else "rgba(107,114,128,0.14)"
            border = "2px solid #374151" if active else "2px solid transparent"
            shadow = "0 0 0 2px rgba(156,163,175,0.45)" if active else "none"
            selector = f".st-key-swu_coll_aspect_{kind}_{aspect.lower()} button"
            css += f"""
            {selector}, {selector}:hover, {selector}:focus-visible {{
                background-color: {background} !important;
                color: {foreground} !important;
                border: {border} !important;
                box-shadow: {shadow} !important;
                filter: none !important;
                opacity: 1 !important;
            }}
            {selector} p, {selector}:hover p, {selector}:focus-visible p {{
                color: {foreground} !important;
            }}
            """
    st.markdown(css + "</style>", unsafe_allow_html=True)

    top_left, top_right = st.columns(2, gap="small")
    with top_left:
        st.button("⚪ Heroism", key="swu_coll_aspect_main_heroism",
                  on_click=_toggle_collection_aspect, args=("Heroism",),
                  use_container_width=True, type="secondary")
    with top_right:
        st.button("⚫ Villainy", key="swu_coll_aspect_main_villainy",
                  on_click=_toggle_collection_aspect, args=("Villainy",),
                  use_container_width=True, type="secondary")
    for left_aspect, right_aspect in (("Vigilance", "Command"), ("Aggression", "Cunning")):
        row_columns = st.columns([3, 1, 3, 1], gap="small")
        for aspect, index in ((left_aspect, 0), (right_aspect, 2)):
            with row_columns[index]:
                st.button(f"{ASPECT_ICONS[aspect]} {aspect}",
                          key=f"swu_coll_aspect_main_{aspect.lower()}",
                          on_click=_toggle_collection_aspect, args=(aspect,),
                          use_container_width=True, type="secondary")
            with row_columns[index + 1]:
                st.button(ASPECT_ICONS[aspect] * 2,
                          key=f"swu_coll_aspect_double_{aspect.lower()}",
                          on_click=_toggle_collection_double_aspect, args=(aspect,),
                          use_container_width=True, type="secondary")
    return levels


def _aspect_count(card, aspect):
    """Prefer the DB view's counts; aspects list is a compatibility fallback."""
    value = card.get(f"aspect_{aspect.lower()}")
    if value is not None:
        return int(value)
    return Counter(card.get("aspects") or []).get(aspect, 0)


def _matches_aspects(card, mode, levels):
    counts = {a: _aspect_count(card, a) for a in ASPECTS}
    selected = [a for a in ASPECTS if levels.get(a, 0) > 0]
    if mode == "Deck Compatibility":
        return all(counts[a] <= levels.get(a, 0) for a in ASPECTS)
    if mode == "All Selected":
        return (all(counts[a] == 0 for a in ASPECTS) if not selected else
                all(counts[a] >= levels[a] for a in selected))
    if mode == "Exact":
        return all(counts[a] == levels.get(a, 0) for a in ASPECTS)
    if mode == "Exclude Selected":
        return (all(counts[a] == 0 for a in selected) if selected else
                any(counts[a] > 0 for a in ASPECTS))
    raise ValueError(f"Unknown aspect filter mode: {mode}")


def _matches(card, filters):
    """Local equivalent of Cards tab's database filters, applied to OWNED cards."""
    name = (str(card.get("name") or "") + " " + str(card.get("subtitle") or "")).casefold()
    rules = str(card.get("rules_text") or "").casefold()
    exclude_text = str(card.get("ability_search_text") or card.get("rules_text") or "").casefold()
    if filters["name"] and filters["name"] not in name:
        return False
    if filters["contains"] and filters["contains"] not in rules:
        return False
    if filters["excludes"] and filters["excludes"] in exclude_text:
        return False
    if card.get("card_type") not in filters["types"]:
        return False
    if filters["ground"] != filters["space"] and card.get("card_type") == "Unit":
        expected = "Ground" if filters["ground"] else "Space"
        if card.get("arena") != expected:
            return False
    if not _matches_aspects(card, filters["aspect_mode"], filters["levels"]):
        return False
    if filters["rarities"] and card.get("rarity") not in filters["rarities"]:
        return False
    if filters["sets"] and card.get("set_code") not in filters["sets"]:
        return False
    if filters["traits"] and not set(filters["traits"]).intersection(card.get("traits") or []):
        return False
    if filters["keywords"] and not set(filters["keywords"]).intersection(card.get("keywords") or []):
        return False
    for stat in ("cost", "power", "hp"):
        minimum, maximum, available_max = filters["ranges"][stat]
        # SQL compares numeric columns only when a limit is effective. Nulls
        # are excluded if a comparison is applied, just as in PostgREST.
        check_low, check_high = minimum > 0, maximum < available_max
        if not (check_low or check_high):
            continue
        value = card.get(stat)
        try:
            val = float(value)
        except (TypeError, ValueError):
            return False
        if (check_low and val < minimum) or (check_high and val > maximum):
            return False
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


def render_my_collection(st, make_client, db, get_filter_options, get_stat_maxima, is_niche_set):
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
        maxima = get_stat_maxima()
    except Exception as exc:
        st.error(f"Could not retrieve your card gallery: {exc}")
        return

    _initialize_collection_filters(maxima)
    _sync_collection_aspects_from_deck()
    filters_col, gallery = st.columns([1, 3], gap="large")
    with filters_col:
        st.subheader("Filters")
        st.button("Clear Filters", key="swu_coll_clear", use_container_width=True,
                  on_click=_clear_collection_filters, args=(maxima,))
        name = st.text_input("Card name / subtitle", key="swu_coll_name",
                             on_change=_reset_collection_page).strip().casefold()
        contains = st.text_input("Ability text contains", key="swu_coll_rules",
                                 on_change=_reset_collection_page).strip().casefold()
        excludes = st.text_input(
            "Exclude ability text", key="swu_coll_exclude",
            placeholder="Cards containing this text will be excluded",
            on_change=_reset_collection_page,
        ).strip().casefold()

        with st.expander("Card Type & Stats", expanded=True):
            types = st.multiselect("Card type", ["Unit", "Event", "Upgrade"],
                                   key="swu_coll_type", on_change=_reset_collection_page)
            st.markdown("**Arena**")
            arena_left, arena_right = st.columns(2)
            with arena_left:
                ground = st.checkbox("Ground", key="swu_coll_arena_ground",
                                     on_change=_reset_collection_page)
            with arena_right:
                space = st.checkbox("Space", key="swu_coll_arena_space",
                                    on_change=_reset_collection_page)
            ranges = {}
            for label, stat in (("Cost", "cost"), ("Power", "power"), ("HP", "hp")):
                low, high = _numeric_pair(label, stat, maxima[stat])
                ranges[stat] = (low, high, maxima[stat])

        with st.expander("Aspects", expanded=True):
            aspect_mode = st.selectbox("Aspect filter mode", ASPECT_MODES,
                                       key="swu_coll_aspect_mode", on_change=_reset_collection_page)
            levels = _render_collection_aspect_grid()

        with st.expander("Traits & Keywords"):
            traits = st.multiselect("Traits (match any selected)", traits_available,
                                    key="swu_coll_traits", on_change=_reset_collection_page)
            keywords = st.multiselect("Keywords (match any selected)", keywords_available,
                                      key="swu_coll_keywords", on_change=_reset_collection_page)

        with st.expander("Sets & Rarity"):
            set_names = {entry["code"]: entry["name"] for entry in sets}
            include_niche = st.session_state.get("swu_coll_include_niche_sets", False)
            available_sets = [code for code in set_names
                              if include_niche or not is_niche_set(code)]
            # Match Cards: remove hidden niche selections before rendering widget.
            if "swu_coll_sets" in st.session_state:
                st.session_state["swu_coll_sets"] = [code for code in st.session_state["swu_coll_sets"]
                                                     if code in available_sets]
            selected_sets = st.multiselect(
                "Sets", available_sets, format_func=lambda code: f"{code} — {set_names[code]}",
                key="swu_coll_sets", on_change=_reset_collection_page,
            )
            st.checkbox("Include niche / promotional sets", key="swu_coll_include_niche_sets",
                        on_change=_reset_collection_page,
                        help=("Show non-expansion sets in the Sets dropdown, including "
                              "C24, Weekly Play, Judge, and other promos. "
                              "Does not exclude cards when no Sets are chosen."))
            rarities = st.multiselect("Rarity", ["Common", "Uncommon", "Rare", "Legendary", "Special"],
                                      key="swu_coll_rarities", on_change=_reset_collection_page)

    with gallery:
        sort = st.selectbox("Sort collection", ["Name A–Z", "Name Z–A", "Owned: Most first",
                                                "Owned: Fewest first", "In decks: Most first",
                                                "Cost: Low to high", "Cost: High to low"],
                            key="swu_coll_sort", on_change=_reset_collection_page)
        page_size = st.selectbox("Cards per page", PAGE_SIZES, key="swu_coll_size",
                                 on_change=_reset_collection_page)
        if any(lo > hi for lo, hi, _ in ranges.values()):
            st.warning("A minimum value cannot exceed its maximum.")
            return
        if not types:
            st.info("Select at least one card type.")
            return

        filter_spec = dict(name=name, contains=contains, excludes=excludes,
                           types=types, ground=ground, space=space,
                           aspect_mode=aspect_mode, levels=levels,
                           traits=traits, keywords=keywords,
                           sets=selected_sets, rarities=rarities, ranges=ranges)
        # Filter printings before grouping, including niche-set printings.
        cards = group_matching_printings(
            [card for card in all_printings if _matches(card, filter_spec)]
        )
        cards.sort(key=lambda c: _sort_key(c, sort, owned, assigned),
                   reverse=(sort == "Name Z–A"))
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
        shown = cards[(page - 1) * page_size:page * page_size]
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

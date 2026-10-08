
import math
import streamlit as st
from supabase import create_client
from swu_grouping import (
    get_grouped_page,
    load_printing_options,
    show_grouped_card,
)
from swu_bases import (
    base_page_controls,
    load_base_printings,
    reset_base_page,
    selected_base_panel,
    show_base_gallery_card,
    load_complete_base_library,
    classify_base,
    base_aspect_names,
    base_planet,
    location_label,
    force_location_label,
    law_location_label,
    standard_location_sort,
    leader_primary_aspects,
    matches_base_filters,
    first_matching_printing,
    build_base_search_results,
    PRIMARY_BASE_ASPECTS,
)
from swu_deck_storage import render_deck_storage
from swu_twin_suns import (
    render_deck_builder, add_card, card_copy_limit, card_identity,
    deck_entries,
)
from swu_leaders import (
    leader_page_controls,
    load_leader_printings,
    reset_leader_page,
    selected_leader_panel,
    show_leader_gallery_card,
)

st.set_page_config(
    page_title="SWU Deck Builder",
    page_icon="🃏",
    layout="wide"
)

ASPECTS = [
    "Vigilance", "Command", "Aggression",
    "Cunning", "Heroism", "Villainy"
]

ASPECT_COLUMNS = {
    aspect: f"aspect_{aspect.lower()}"
    for aspect in ASPECTS
}

ASPECT_MODES = [
    "Deck Compatibility",
    "All Selected",
    "Exact",
    "Exclude Selected"
]


# Main booster expansions, including announced preview sets.
# Extend this list when the next major expansion launches.
MAIN_EXPANSION_SET_CODES = {
    "SOR", "SHD", "TWI", "JTL", "LOF", "SEC", "LAW",
    "ASH", "HMW", "IC27",
}


def is_niche_set(set_code):
    """Other sets: Weekly Play, promos, convention, Judge, starters, etc."""
    return (set_code or "").strip().upper() not in MAIN_EXPANSION_SET_CODES

if "page" not in st.session_state:
    st.session_state.page = 1


def reset_page():
    st.session_state.page = 1
    
def toggle_aspect(aspect):
    key = f"swu_aspect_level_{aspect.lower()}"
    current = st.session_state[key]

    st.session_state[key] = 0 if current > 0 else 1
    reset_page()


def toggle_double_aspect(aspect):
    key = f"swu_aspect_level_{aspect.lower()}"
    current = st.session_state[key]

    st.session_state[key] = 1 if current == 2 else 2
    reset_page()


# The leader filter uses the same aspect button visuals as Cards,
# but keeps completely separate selection state and pagination.
def clear_leader_filters():
    """Reset Leaders search filters, preserving the chosen leader and layout."""

    defaults = {
        "swu_leader_search": "",
        "swu_leader_ability_contains": "",
        "swu_leader_ability_excludes": "",
        "swu_leader_search_front": True,
        "swu_leader_search_back": True,
        "swu_leader_ground_only": True,
        "swu_leader_pilots": True,
        "swu_leader_aspect_mode_v1": "All Selected",
        "swu_leader_traits": [],
        "swu_leader_keywords": [],
        "swu_leader_sets": [],
        "swu_leader_include_niche": False,
        "swu_leader_rarities": [],
    }

    for key, value in defaults.items():
        st.session_state[key] = value

    for aspect in ASPECTS:
        st.session_state[f"swu_leader_aspect_level_{aspect.lower()}"] = 0

    reset_leader_page()


def toggle_leader_aspect(aspect):
    key = f"swu_leader_aspect_level_{aspect.lower()}"
    current = st.session_state[key]
    st.session_state[key] = 0 if current > 0 else 1
    reset_leader_page()


def toggle_leader_double_aspect(aspect):
    key = f"swu_leader_aspect_level_{aspect.lower()}"
    current = st.session_state[key]
    st.session_state[key] = 1 if current == 2 else 2
    reset_leader_page()


def leader_aspect_grid():
    """Same six colored aspect buttons used in the Cards gallery."""
    aspects = [
        "Heroism", "Villainy", "Vigilance", "Command",
        "Aggression", "Cunning"
    ]
    icons = {
        "Heroism": "⚪", "Villainy": "⚫",
        "Vigilance": "🔵", "Command": "🟢",
        "Aggression": "🔴", "Cunning": "🟡"
    }
    palette = {
        "Heroism": ("#FFFFFF", "#171717"),
        "Villainy": ("#252831", "#FFFFFF"),
        "Vigilance": ("#307FC1", "#FFFFFF"),
        "Command": ("#258852", "#FFFFFF"),
        "Aggression": ("#C23E48", "#FFFFFF"),
        "Cunning": ("#F0C746", "#202124")
    }
    levels = {}
    for aspect in aspects:
        key = f"swu_leader_aspect_level_{aspect.lower()}"
        # No aspects chosen by default. All Selected with no selections
        # returns all leaders, so browsing works immediately.
        if key not in st.session_state:
            st.session_state[key] = 0
        levels[aspect] = st.session_state[key]

    css = """
    <style>
    [class*="st-key-swu_leader_aspect_main_"] button,
    [class*="st-key-swu_leader_aspect_double_"] button {
        min-height: 48px;
        width: 100%;
        border-radius: 9px !important;
        padding: 4px 2px !important;
        font-weight: 600 !important;
    }
    [class*="st-key-swu_leader_aspect_main_"] button p,
    [class*="st-key-swu_leader_aspect_double_"] button p {
        font-size: .73rem !important;
        line-height: 1.2 !important;
        text-align: center !important;
    }
    """
    for aspect in aspects:
        slug = aspect.lower()
        color, text_color = palette[aspect]
        for kind in ("main", "double"):
            if aspect in ("Heroism", "Villainy") and kind == "double":
                continue
            active = (levels[aspect] > 0) if kind == "main" else (levels[aspect] == 2)
            selector = f".st-key-swu_leader_aspect_{kind}_{slug} button"
            background = color if active else "rgba(107,114,128,0.14)"
            foreground = text_color if active else "inherit"
            border = "2px solid #374151" if active else "2px solid transparent"
            shadow = "0 0 0 2px rgba(156,163,175,0.45)" if active else "none"
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

    left, right = st.columns(2, gap="small")
    for aspect, column in (("Heroism", left), ("Villainy", right)):
        with column:
            st.button(
                f"{icons[aspect]} {aspect}",
                key=f"swu_leader_aspect_main_{aspect.lower()}",
                on_click=toggle_leader_aspect,
                args=(aspect,),
                use_container_width=True, type="secondary"
            )
    for left_aspect, right_aspect in (
        ("Vigilance", "Command"), ("Aggression", "Cunning")
    ):
        cols = st.columns([3, 1, 3, 1], gap="small")
        for aspect, main_index in ((left_aspect, 0), (right_aspect, 2)):
            slug = aspect.lower()
            with cols[main_index]:
                st.button(
                    f"{icons[aspect]} {aspect}",
                    key=f"swu_leader_aspect_main_{slug}",
                    on_click=toggle_leader_aspect, args=(aspect,),
                    use_container_width=True, type="secondary"
                )
            with cols[main_index + 1]:
                st.button(
                    icons[aspect] * 2,
                    key=f"swu_leader_aspect_double_{slug}",
                    on_click=toggle_leader_double_aspect, args=(aspect,),
                    use_container_width=True, type="secondary"
                )
    return levels


# Bases have one aspect icon, so this grid uses only the four main colors.
# Base filter state is intentionally independent of Leaders and Cards.
BASE_ASPECTS = ["Vigilance", "Command", "Aggression", "Cunning"]


def sync_base_filters_from_leader(force=False):
    """Default to Deck Compatibility with colors not present on the leader(s).

    Preserve manual changes until the leader changes or Clear Filters is used.
    The version flag applies these new defaults once to existing sessions too.
    """
    leader_colors = leader_primary_aspects(st.session_state)
    available_colors = set(BASE_ASPECTS) - leader_colors
    leader = st.session_state.get("swu_selected_leader") or {}
    multi = st.session_state.get("swu_selected_leaders") or []
    leader_ids = [str(leader.get("gameplay_id") or leader.get("uuid") or "")]
    leader_ids += sorted(str(x.get("gameplay_id") or x.get("uuid") or "")
                         for x in multi if isinstance(x, dict))
    fingerprint = tuple(leader_ids)
    defaults_version = "deck_compatibility_outside_leader_v1"
    if (
        force
        or st.session_state.get("swu_base_synced_leader") != fingerprint
        or st.session_state.get("swu_base_defaults_version") != defaults_version
    ):
        st.session_state["swu_base_synced_leader"] = fingerprint
        st.session_state["swu_base_defaults_version"] = defaults_version
        for aspect in BASE_ASPECTS:
            st.session_state[f"swu_base_aspect_{aspect.lower()}"] = (
                aspect in available_colors
            )
        st.session_state["swu_base_aspect_mode"] = "Deck Compatibility"
        reset_base_page()


def toggle_base_aspect(aspect):
    key = f"swu_base_aspect_{aspect.lower()}"
    st.session_state[key] = not st.session_state.get(key, False)
    reset_base_page()


@st.cache_data(ttl=3600)
def get_base_hp_maximum():
    result = (
        get_database().table("card_printings")
        .select("hp")
        .eq("card_type", "Base")
        .order("hp", desc=True, nullsfirst=False)
        .limit(1)
        .execute()
    )
    hp = result.data[0]["hp"] if result.data else 0
    return max(0, int(hp or 0))


def clear_base_filters():
    """Restore base search defaults but keep the selected base and layout."""
    defaults = {
        "swu_base_search": "",
        "swu_base_ability_contains": "",
        "swu_base_ability_excludes": "",
        "swu_base_aspect_mode": "Deck Compatibility",
        "swu_base_traits": [],
        "swu_base_keywords": [],
        "swu_base_sets": [],
        "swu_base_include_niche": False,
        "swu_base_rarities": [],
        "swu_base_hp_min": 0,
        "swu_base_hp_max": get_base_hp_maximum(),
    }
    for key, default in defaults.items():
        st.session_state[key] = default
    sync_base_filters_from_leader(force=True)
    reset_base_page()


def base_aspect_grid():
    """Four primary SWU colors; selected buttons gain the charcoal border."""
    icons = {
        "Vigilance": "🔵", "Command": "🟢",
        "Aggression": "🔴", "Cunning": "🟡",
    }
    palette = {
        "Vigilance": ("#307FC1", "#FFFFFF"),
        "Command": ("#258852", "#FFFFFF"),
        "Aggression": ("#C23E48", "#FFFFFF"),
        "Cunning": ("#F0C746", "#202124"),
    }
    levels = {aspect: 0 for aspect in ASPECTS}
    css = "<style>"
    for aspect in BASE_ASPECTS:
        active = st.session_state.get(f"swu_base_aspect_{aspect.lower()}", False)
        levels[aspect] = 1 if active else 0
        color, foreground = palette[aspect]
        if not active:
            color, foreground = "rgba(107,114,128,0.14)", "inherit"
        border = "2px solid #374151" if active else "2px solid transparent"
        shadow = "0 0 0 2px rgba(156,163,175,0.45)" if active else "none"
        selector = f".st-key-swu_base_aspect_btn_{aspect.lower()} button"
        css += f"""
        {selector}, {selector}:hover, {selector}:focus-visible {{
            min-height: 48px !important;
            border-radius: 9px !important;
            background-color: {color} !important;
            color: {foreground} !important;
            border: {border} !important;
            box-shadow: {shadow} !important;
            filter: none !important;
            opacity: 1 !important;
            padding: 4px !important;
        }}
        {selector} p, {selector}:hover p, {selector}:focus-visible p {{
            color: {foreground} !important;
            font-size: .75rem !important;
            font-weight: 600 !important;
        }}
        """
    st.markdown(css + "</style>", unsafe_allow_html=True)
    for first, second in (("Vigilance", "Command"), ("Aggression", "Cunning")):
        left, right = st.columns(2, gap="small")
        for aspect, column in ((first, left), (second, right)):
            with column:
                st.button(
                    f"{icons[aspect]} {aspect}",
                    key=f"swu_base_aspect_btn_{aspect.lower()}",
                    on_click=toggle_base_aspect,
                    args=(aspect,),
                    use_container_width=True,
                    type="secondary",
                )
    return levels


def change_page(amount, total_pages):
    st.session_state.page = max(
        1,
        min(st.session_state.page + amount, total_pages)
    )



# --------------------------------------------------
# DATABASE CONNECTION
# --------------------------------------------------

@st.cache_resource
def get_database():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_PUBLISHABLE_KEY"]
    )


# --------------------------------------------------
# GET MAXIMUM CARD STATISTICS
# --------------------------------------------------

@st.cache_data(ttl=3600)
def get_stat_maxima():
    db = get_database()
    maxima = {}

    for column in ["cost", "power", "hp"]:
        response = (
            db.table("card_printings")
            .select(column)
            .in_("card_type", ["Unit", "Event", "Upgrade"])
            .order(column, desc=True, nullsfirst=False)
            .limit(1)
            .execute()
        )

        value = (
            response.data[0][column]
            if response.data else None
        )

        maxima[column] = max(0, int(value or 0))

    return maxima


    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_PUBLISHABLE_KEY"]
    )


@st.cache_data(ttl=3600)
def get_filter_options():
    db = get_database()

    sets = db.table("card_sets").select(
        "code,name"
    ).order("code").execute().data

    values = db.table("swu_filter_values").select(
        "category,value"
    ).execute().data

    traits = sorted({
        item["value"] for item in values
        if item["category"] == "trait" and item["value"]
    })

    keywords = sorted({
        item["value"] for item in values
        if item["category"] == "keyword" and item["value"]
    })

    return sets, traits, keywords


def is_neutral_filter():
    return "and(" + ",".join(
        f"{column}.eq.0"
        for column in ASPECT_COLUMNS.values()
    ) + ")"


def has_any_aspect_filter():
    return ",".join(
        f"{column}.gt.0"
        for column in ASPECT_COLUMNS.values()
    )



def apply_aspect_filters(query, mode, levels, all_selected_empty="neutral"):

    columns = ASPECT_COLUMNS

    # Aspects currently enabled by the user
    selected = [
        aspect for aspect in ASPECTS
        if levels.get(aspect, 0) > 0
    ]

    # -----------------------------------------
    # MODE 1: DECK COMPATIBILITY
    # -----------------------------------------
    # Each card's aspect count must fit within
    # the selected limits.
    # Neutral cards are always included.

    if mode == "Deck Compatibility":

        for aspect, column in columns.items():
            query = query.lte(
                column,
                levels.get(aspect, 0)
            )

    # -----------------------------------------
    # MODE 2: ALL SELECTED
    # -----------------------------------------
    # Every selected aspect must be present at least
    # the selected number of times (1 or 2).
    # Additional unselected aspects are allowed.
    # With no aspects selected:
    # Cards -> neutral only (as previously agreed).
    # Leaders -> all leaders (useful browsing default).

    elif mode == "All Selected":
        if not selected and all_selected_empty == "neutral":
            for column in columns.values():
                query = query.eq(column, 0)
        else:
            for aspect in selected:
                query = query.gte(
                    columns[aspect],
                    levels[aspect]
                )

    # -----------------------------------------
    # MODE 3: EXACT
    # -----------------------------------------
    # Require precisely the selected number
    # of icons for every aspect.
    # Nothing selected: neutral only.

    elif mode == "Exact":

        for aspect, column in columns.items():
            query = query.eq(
                column,
                levels.get(aspect, 0)
            )

    # -----------------------------------------
    # MODE 4: EXCLUDE SELECTED
    # -----------------------------------------
    # Exclude any card containing a selected
    # aspect, whether single or double.
    #
    # With selections: neutral cards included.
    # No selections: all non-neutral cards.

    elif mode == "Exclude Selected":

        if selected:

            for aspect in selected:
                query = query.eq(
                    columns[aspect], 0
                )

        else:

            conditions = [
                f"{column}.gt.0"
                for column in columns.values()
            ]

            query = query.or_(
                ",".join(conditions)
            )

    else:
        raise ValueError(
            f"Unknown aspect filter mode: {mode}"
        )

    return query




def apply_numeric_filter(
    query, column, minimum, maximum, available_max
):
    # Zero minimum does not restrict results.
    if minimum is not None and minimum > 0:
        query = query.gte(column, minimum)

    # Maximum only restricts results if reduced
    # below the highest available database value.
    if maximum is not None and maximum < available_max:
        query = query.lte(column, maximum)

    return query


def page_controls(total_pages, location):
    left, center, right = st.columns([1, 1, 1])

    with left:
        st.button(
            "⬅ Previous",
            key=f"{location}_previous",
            disabled=st.session_state.page <= 1,
            on_click=change_page,
            args=(-1, total_pages),
            use_container_width=True
        )

    with center:
        st.markdown(
            f"**Page {st.session_state.page:,} "
            f"of {total_pages:,}**",
            text_alignment="center"
        )

    with right:
        st.button(
            "Next ➡",
            key=f"{location}_next",
            disabled=st.session_state.page >= total_pages,
            on_click=change_page,
            args=(1, total_pages),
            use_container_width=True
        )




def number_range(label, highest):
    left, right = st.columns(2)

    with left:
        minimum = st.number_input(
            f"{label} min",
            min_value=0,
            value=0,
            step=1,
            key=f"{label}_min",
            on_change=reset_page
        )

    with right:
        maximum = st.number_input(
            f"{label} max",
            min_value=0,
            max_value=highest,
            value=highest,
            step=1,
            key=f"{label}_max_db",
            on_change=reset_page
        )

    return minimum, maximum





def show_card(card):
    # Display card image
    image_url = card.get("front_image_url")

    if image_url:
        st.image(image_url, width="stretch")
    else:
        st.info("Image unavailable")

    # Display only the card ID (e.g., LAW_689)
    card_id = card.get("collector_number")

    if card_id:
        card_id = str(card_id)

        # Add set code only if it isn't already included
        if "_" not in card_id:
            card_id = (
                f"{card.get('set_code')}_{card_id}"
            )
    else:
        card_id = "Unknown ID"

    st.markdown(
        f"<p style='text-align: center; font-weight: bold;'>{card_id}</p>",
        unsafe_allow_html=True
    )



def add_twin_suns_card(card):
    """Add the selected gameplay card to session-only Twin Suns draw deck."""
    success, message = add_card(st.session_state, card)
    if not success:
        st.toast(message)


# --------------------------------------------------
# MAIN APPLICATION
# --------------------------------------------------

st.title("Star Wars Unlimited Deck Builder")
st.caption("Search cards, build decks, track your collection")

leader_tab, base_tab, card_tab, deck_tab = st.tabs(
    ["1. Leaders", "2. Bases", "3. Cards", "4. Deck Builder"],
    default="3. Cards"
)

with leader_tab:
    st.header("Leader Library")
    selected_leader_panel()
    st.divider()

    try:
        leader_sets, leader_traits, leader_keywords = get_filter_options()
        leader_set_names = {
            item["code"]: item["name"] for item in leader_sets
        }
    except Exception as error:
        st.error(f"Could not load leader filters: {error}")
        st.stop()

    leader_filters, leader_results = st.columns([1, 3], gap="large")

    with leader_filters:
        st.subheader("Filters")
        st.button(
            "Clear Filters",
            key="swu_clear_leader_filters",
            on_click=clear_leader_filters,
            use_container_width=True,
        )
        leader_search = st.text_input(
            "Leader name / subtitle",
            key="swu_leader_search",
            on_change=reset_leader_page,
        )
        leader_ability_contains = st.text_input(
            "Ability text contains",
            key="swu_leader_ability_contains",
            on_change=reset_leader_page,
        )
        leader_ability_excludes = st.text_input(
            "Exclude ability text",
            key="swu_leader_ability_excludes",
            on_change=reset_leader_page,
        )
        side_left, side_right = st.columns(2)
        with side_left:
            include_front_abilities = st.checkbox(
                "Include front abilities",
                value=True,
                key="swu_leader_search_front",
                on_change=reset_leader_page,
            )
        with side_right:
            include_back_abilities = st.checkbox(
                "Include back abilities",
                value=True,
                key="swu_leader_search_back",
                on_change=reset_leader_page,
            )
        if (leader_ability_contains.strip() or leader_ability_excludes.strip()) and not (
            include_front_abilities or include_back_abilities
        ):
            st.caption("Select at least one card side to search ability text.")

        st.markdown("**Leader deployment**")
        ground_col, pilot_col = st.columns(2)
        with ground_col:
            show_ground_only = st.checkbox(
                "Ground-only leaders",
                value=True,
                key="swu_leader_ground_only",
                on_change=reset_leader_page,
            )
        with pilot_col:
            show_pilot_leaders = st.checkbox(
                "Pilot leaders",
                value=True,
                key="swu_leader_pilots",
                on_change=reset_leader_page,
                help="Leaders that can deploy as upgrades onto Vehicles",
            )

        with st.expander("Aspects", expanded=True):
            leader_aspect_mode = st.selectbox(
                "Aspect filter mode",
                ["All Selected", "Deck Compatibility", "Exact", "Exclude Selected"],
                index=0,
                key="swu_leader_aspect_mode_v1",
                on_change=reset_leader_page,
            )
            leader_levels = leader_aspect_grid()

        with st.expander("Traits & Keywords"):
            chosen_leader_traits = st.multiselect(
                "Traits (match any selected)",
                leader_traits,
                key="swu_leader_traits",
                on_change=reset_leader_page,
            )
            chosen_leader_keywords = st.multiselect(
                "Keywords (match any selected)",
                leader_keywords,
                key="swu_leader_keywords",
                on_change=reset_leader_page,
            )

        with st.expander("Sets & Rarity"):
            include_leader_niche = st.session_state.get(
                "swu_leader_include_niche", False
            )
            leader_available_sets = [
                code for code in leader_set_names
                if include_leader_niche or not is_niche_set(code)
            ]
            if "swu_leader_sets" in st.session_state:
                st.session_state["swu_leader_sets"] = [
                    code for code in st.session_state["swu_leader_sets"]
                    if code in leader_available_sets
                ]
            chosen_leader_sets = st.multiselect(
                "Sets",
                leader_available_sets,
                format_func=lambda code: f"{code} — {leader_set_names[code]}",
                key="swu_leader_sets",
                on_change=reset_leader_page,
            )
            st.checkbox(
                "Include niche / promotional sets",
                value=False,
                key="swu_leader_include_niche",
                on_change=reset_leader_page,
                help=(
                    "Show non-expansion sets in the Sets dropdown, "
                    "including C24, Weekly Play, Judge, and other promos. "
                    "Does not exclude cards when no Sets are chosen."
                ),
            )
            chosen_leader_rarities = st.multiselect(
                "Rarity",
                ["Common", "Uncommon", "Rare", "Legendary", "Special"],
                key="swu_leader_rarities",
                on_change=reset_leader_page,
            )

    with leader_results:
        st.subheader("Matching Leaders")
        page_col, row_col = st.columns(2, gap="medium")
        with page_col:
            leader_page_size = st.selectbox(
                "Leaders per page",
                [100, 40, 20],
                index=0,
                key="swu_leader_page_size",
                on_change=reset_leader_page,
            )
        with row_col:
            leaders_per_row = st.selectbox(
                "Leaders per row",
                [1, 2, 3, 4],
                index=1,
                key="swu_leaders_per_row",
            )
        if "swu_leader_page" not in st.session_state:
            st.session_state["swu_leader_page"] = 1

        try:
            db = get_database()
            leader_query = db.table("swu_grouped_leaders").select(
                "uuid,gameplay_id,name,subtitle,card_type,set_code,"
                "collector_number,variant_type,front_image_url,"
                "back_image_url,aspects,traits,keywords,rarity",
                count="exact",
            )
            if leader_search.strip():
                leader_query = leader_query.ilike(
                    "leader_search_text", f"%{leader_search.strip()}%"
                )

            # Search the chosen faces only. View columns are coalesced to
            # non-NULL strings, so exclusions also retain blank-text leaders.
            ability_column = (
                "leader_both_ability_text"
                if include_front_abilities and include_back_abilities
                else "leader_front_ability_text"
                if include_front_abilities
                else "leader_back_ability_text"
            )
            if include_front_abilities or include_back_abilities:
                if leader_ability_contains.strip():
                    leader_query = leader_query.ilike(
                        ability_column,
                        f"%{leader_ability_contains.strip()}%",
                    )
                if leader_ability_excludes.strip():
                    leader_query = leader_query.filter(
                        ability_column,
                        "not.ilike",
                        f"%{leader_ability_excludes.strip()}%",
                    )
            elif leader_ability_contains.strip():
                # A required match without either face enabled is impossible.
                leader_query = leader_query.eq(
                    "uuid", "00000000-0000-0000-0000-000000000000"
                )

            if not show_ground_only and not show_pilot_leaders:
                # No leader deployment category chosen.
                leader_query = leader_query.eq(
                    "uuid", "00000000-0000-0000-0000-000000000000"
                )
            elif show_ground_only and not show_pilot_leaders:
                leader_query = leader_query.eq("leader_is_pilot", False)
            elif show_pilot_leaders and not show_ground_only:
                leader_query = leader_query.eq("leader_is_pilot", True)

            leader_query = apply_aspect_filters(
                leader_query, leader_aspect_mode, leader_levels,
                all_selected_empty="all",
            )
            if chosen_leader_traits:
                leader_query = leader_query.overlaps(
                    "traits", chosen_leader_traits
                )
            if chosen_leader_keywords:
                leader_query = leader_query.overlaps(
                    "keywords", chosen_leader_keywords
                )
            if chosen_leader_sets:
                leader_query = leader_query.in_("set_code", chosen_leader_sets)
            if chosen_leader_rarities:
                leader_query = leader_query.in_("rarity", chosen_leader_rarities)

            leader_cards, leader_total = get_grouped_page(
                leader_query,
                st.session_state["swu_leader_page"],
                leader_page_size,
            )
            leader_total_pages = max(
                1, math.ceil(leader_total / leader_page_size)
            )
            if leader_total and st.session_state["swu_leader_page"] > leader_total_pages:
                st.session_state["swu_leader_page"] = leader_total_pages
                leader_cards, leader_total = get_grouped_page(
                    leader_query, leader_total_pages, leader_page_size
                )

            st.metric("Matching leaders", leader_total)
            if leader_total:
                first = (
                    (st.session_state["swu_leader_page"] - 1)
                    * leader_page_size + 1
                )
                last = min(
                    st.session_state["swu_leader_page"] * leader_page_size,
                    leader_total,
                )
                st.caption(
                    f"Showing {first:,}–{last:,} of {leader_total:,} unique leaders"
                )
                leader_page_controls(leader_total_pages, "top")
                st.divider()
                leader_printings = load_leader_printings(db, leader_cards)
                leader_columns = st.columns(leaders_per_row, gap="small")
                for index, leader in enumerate(leader_cards):
                    with leader_columns[index % leaders_per_row]:
                        group_id = str(
                            leader.get("gameplay_id") or leader["uuid"]
                        )
                        show_leader_gallery_card(
                            leader,
                            leader_printings.get(group_id, [leader]),
                        )
                st.divider()
                leader_page_controls(leader_total_pages, "bottom")
            else:
                st.info("No matching leaders found.")

        except Exception as error:
            st.error(f"Leader search failed: {error}")

with base_tab:
    st.header("Base Library")

    # Base defaults follow selected leader(s), and refresh when the defaults
    # version changes. This must run before Streamlit creates the widgets.
    sync_base_filters_from_leader()
    selected_base_panel()
    st.divider()

    try:
        all_sets, available_base_traits, available_base_keywords = get_filter_options()
        base_set_names = {item["code"]: item["name"] for item in all_sets}
    except Exception as error:
        st.error(f"Could not load base filter options: {error}")
        st.stop()

    base_filters, base_results = st.columns([1, 3], gap="large")

    with base_filters:
        st.subheader("Filters")
        st.button("Clear Filters", key="swu_clear_base_filters",
                  on_click=clear_base_filters, use_container_width=True)
        base_search = st.text_input(
            "Base name / location", key="swu_base_search", on_change=reset_base_page)
        base_ability_contains = st.text_input(
            "Ability text contains", key="swu_base_ability_contains", on_change=reset_base_page)
        base_ability_excludes = st.text_input(
            "Exclude ability text", key="swu_base_ability_excludes", on_change=reset_base_page)

        st.markdown("**Base HP**")
        max_base_hp = get_base_hp_maximum()
        hp_col1, hp_col2 = st.columns(2)
        with hp_col1:
            base_min_hp = st.number_input(
                "HP min", min_value=0, max_value=max_base_hp,
                value=0, step=1, key="swu_base_hp_min", on_change=reset_base_page)
        with hp_col2:
            base_max_hp = st.number_input(
                "HP max", min_value=0, max_value=max_base_hp,
                value=max_base_hp, step=1, key="swu_base_hp_max", on_change=reset_base_page)

        with st.expander("Aspects", expanded=True):
            # Only these three modes apply to bases. Cards and Leaders keep
            # their own independent aspect-mode choices.
            base_mode_choices = [
                "Deck Compatibility", "Exact", "Exclude Selected"
            ]
            if st.session_state.get("swu_base_aspect_mode") not in base_mode_choices:
                st.session_state["swu_base_aspect_mode"] = "Deck Compatibility"
            base_aspect_mode = st.selectbox(
                "Aspect filter mode",
                base_mode_choices,
                index=0, key="swu_base_aspect_mode", on_change=reset_base_page)
            base_levels = base_aspect_grid()
            st.caption("Defaults to colors not already on your selected leader(s). "
                       "Heroism and Villainy are ignored. Change these buttons anytime.")

        with st.expander("Traits & Keywords"):
            chosen_base_traits = st.multiselect(
                "Traits (match any selected)", available_base_traits,
                key="swu_base_traits", on_change=reset_base_page)
            chosen_base_keywords = st.multiselect(
                "Keywords (match any selected)", available_base_keywords,
                key="swu_base_keywords", on_change=reset_base_page)

        with st.expander("Sets & Rarity"):
            include_base_niche = st.session_state.get("swu_base_include_niche", False)
            available_base_sets = [
                code for code in base_set_names
                if include_base_niche or not is_niche_set(code)
            ]
            if "swu_base_sets" in st.session_state:
                st.session_state["swu_base_sets"] = [
                    code for code in st.session_state["swu_base_sets"]
                    if code in available_base_sets
                ]
            chosen_base_sets = st.multiselect(
                "Sets", available_base_sets,
                format_func=lambda code: f"{code} — {base_set_names[code]}",
                key="swu_base_sets", on_change=reset_base_page)
            st.checkbox(
                "Include niche / promotional sets", value=False,
                key="swu_base_include_niche", on_change=reset_base_page,
                help="Show specialty sets in this dropdown; no Set filter means all printings.")
            chosen_base_rarities = st.multiselect(
                "Rarity", ["Common", "Uncommon", "Rare", "Legendary", "Special"],
                key="swu_base_rarities", on_change=reset_base_page)

    with base_results:
        @st.cache_data(ttl=1800, show_spinner="Loading the base library...")
        def cached_base_library():
            return load_complete_base_library(get_database())

        st.subheader("Choose a Base")
        page_col, row_col = st.columns(2, gap="medium")
        with page_col:
            base_page_size = st.selectbox(
                "Bases per page", [100, 40, 20], index=0,
                key="swu_base_page_size", on_change=reset_base_page)
        with row_col:
            bases_per_row = st.selectbox(
                "Bases per row", [1, 2, 3, 4], index=2,
                key="swu_bases_per_row")

        if "swu_base_page" not in st.session_state:
            st.session_state["swu_base_page"] = 1

        if base_min_hp > base_max_hp:
            st.warning("Minimum base HP cannot exceed maximum base HP.")
        else:
            try:
                all_bases, all_printings = cached_base_library()
                filters_config = {
                    "name": base_search.strip(),
                    "ability": base_ability_contains.strip(),
                    "exclude_ability": base_ability_excludes.strip(),
                    "min_hp": base_min_hp, "max_hp": base_max_hp,
                    "absolute_max_hp": max_base_hp,
                    "mode": base_aspect_mode,
                    "selected_aspects": {
                        aspect for aspect in PRIMARY_BASE_ASPECTS
                        if base_levels.get(aspect, 0) > 0
                    },
                    "traits": chosen_base_traits,
                    "keywords": chosen_base_keywords,
                    "sets": chosen_base_sets,
                    "rarities": chosen_base_rarities,
                }
                # One unified search list: standard colors first, then neutral,
                # then bases with rules text. All participate in pagination.
                ordered_bases = build_base_search_results(
                    all_bases, all_printings, filters_config
                )
                total_bases = len(ordered_bases)
                total_pages = max(1, math.ceil(total_bases / base_page_size))
                if st.session_state["swu_base_page"] > total_pages:
                    st.session_state["swu_base_page"] = total_pages

                st.metric("Matching bases", total_bases)
                if total_bases:
                    current_page = st.session_state["swu_base_page"]
                    start = (current_page - 1) * base_page_size
                    visible = ordered_bases[start:start + base_page_size]
                    end = start + len(visible)
                    st.caption(
                        f"Showing {start + 1:,}–{end:,} of {total_bases:,} bases. "
                        "Standard colors first, then colorless, Force, LAW, and other ability bases."
                    )
                    base_page_controls(total_pages, "top")
                    st.divider()

                    # Build independent rows, rather than continuously stacking
                    # cards into the same columns. This keeps later rows aligned.
                    for row_start in range(0, len(visible), bases_per_row):
                        row_results = visible[row_start:row_start + bases_per_row]
                        row_has_dropdown = any(
                            result["kind"] in ("standard", "force", "law")
                            for result in row_results
                        )
                        columns = st.columns(bases_per_row, gap="small")
                        for column, result in zip(columns, row_results):
                            with column:
                                # Resolve the location before displaying the image,
                                # but render the dropdown beneath the image.
                                location_options = None
                                location_key = None
                                location_label_func = None
                                if result["kind"] in ("standard", "force", "law"):
                                    aspect = result["aspect"]
                                    location_options = {
                                        str(option.get("gameplay_id") or option["uuid"]): option
                                        for option in result["choices"]
                                    }
                                    if result["kind"] == "standard":
                                        location_key = f"swu_base_location_{aspect.lower()}"
                                        location_label_func = location_label
                                        # Keep Naboo as the standard-base default.
                                        preferred = next(
                                            (
                                                gid for gid, option in location_options.items()
                                                if base_planet(option) == "Naboo"
                                                or location_label(option).casefold() == "naboo"
                                            ),
                                            next(iter(location_options)),
                                        )
                                    elif result["kind"] == "force":
                                        location_key = f"swu_base_force_location_{aspect.lower()}"
                                        location_label_func = force_location_label
                                        preferred = next(iter(location_options))
                                    else:
                                        location_key = f"swu_base_law_location_{aspect.lower()}"
                                        location_label_func = law_location_label
                                        preferred = next(iter(location_options))

                                    # Search results may narrow the location options.
                                    # Never retain a location no longer in the list.
                                    if st.session_state.get(location_key) not in location_options:
                                        st.session_state[location_key] = preferred
                                    base = location_options[st.session_state[location_key]]
                                else:
                                    base = result["base"]

                                # Each location is a real base with its own
                                # gameplay_id. Its Standard/Foil/etc printings
                                # remain in the separate card-details popup.
                                group_id = str(base.get("gameplay_id") or base["uuid"])
                                show_base_gallery_card(
                                    base,
                                    all_printings.get(group_id, [base]),
                                    location_options=location_options,
                                    location_key=location_key,
                                    location_label_func=location_label_func,
                                    reserve_location_space=row_has_dropdown,
                                )

                    st.divider()
                    base_page_controls(total_pages, "bottom")
                else:
                    st.info("No matching bases found. Adjust the filters to see more.")
            except Exception as error:
                st.error(f"Base search failed: {error}")


# --------------------------------------------------
# CARD SEARCH TAB
# --------------------------------------------------

with card_tab:
    st.header("Card Search")

    try:
        sets, available_traits, available_keywords = (
            get_filter_options()
        )
    except Exception as error:
        st.error(f"Could not load filter options: {error}")
        st.stop()

    filters, results = st.columns([1, 3], gap="large")

    with filters:
        st.subheader("Filters")

        name = st.text_input(
            "Card name / subtitle",
            key="card_name",
            on_change=reset_page
        )

        ability = st.text_input(
            "Ability text contains",
            key="card_ability",
            on_change=reset_page
        )
        
        exclude_ability = st.text_input(
            "Exclude ability text",
            key="card_ability_exclude",
            placeholder="Cards containing this text will be excluded",
            on_change=reset_page
        )


        with st.expander("Card Type & Stats", expanded=True):
            card_types = st.multiselect(
                "Card type",
                ["Unit", "Event", "Upgrade"],
                default=["Unit", "Event", "Upgrade"],
                on_change=reset_page
            )

            st.markdown("**Arena**")

            arena_col1, arena_col2 = st.columns(2)

            with arena_col1:
                ground_selected = st.checkbox(
                    "Ground",
                    value=True,
                    key="arena_ground",
                    on_change=reset_page
                )

            with arena_col2:
                space_selected = st.checkbox(
                    "Space",
                    value=True,
                    key="arena_space",
                    on_change=reset_page
                )


            maxima = get_stat_maxima()
            min_cost, max_cost = number_range("Cost", maxima["cost"])
            min_power, max_power = number_range("Power", maxima["power"])
            min_hp, max_hp = number_range("HP", maxima["hp"])

        
        # -----------------------------------------
        # ASPECT FILTER - COLORED BUTTON GRID
        # -----------------------------------------

        with st.expander("Aspects", expanded=True):
            
            # Select how aspects are filtered
            aspect_mode = st.selectbox(
                "Aspect filter mode",
                ASPECT_MODES,
                index=0,
                key="aspect_filter_mode_v3",
                on_change=reset_page
            )


            aspect_order = [
                "Heroism", "Villainy",
                "Vigilance", "Command",
                "Aggression", "Cunning"
            ]

            aspect_icons = {
                "Heroism": "⚪",
                "Villainy": "⚫",
                "Vigilance": "🔵",
                "Command": "🟢",
                "Aggression": "🔴",
                "Cunning": "🟡"
            }

            aspect_palette = {
                "Heroism": ("#FFFFFF", "#171717"),
                "Villainy": ("#252831", "#FFFFFF"),
                "Vigilance": ("#307FC1", "#FFFFFF"),
                "Command": ("#258852", "#FFFFFF"),
                "Aggression": ("#C23E48", "#FFFFFF"),
                "Cunning": ("#F0C746", "#202124")
            }

            # -----------------------------------------
            # INITIALIZE ASPECT STATES
            # -----------------------------------------

            levels = {}

            for aspect in aspect_order:
                key = f"swu_aspect_level_{aspect.lower()}"

                if key not in st.session_state:
                    default = (
                        1 if aspect in ("Heroism", "Villainy")
                        else 2
                    )
                    st.session_state[key] = default

                levels[aspect] = st.session_state[key]

            # -----------------------------------------
            # BUTTON STYLING
            # -----------------------------------------

            css = """
            <style>
            [class*="st-key-swu_aspect_main_"] button,
            [class*="st-key-swu_aspect_double_"] button {
                min-height: 48px;
                width: 100%;
                border-radius: 9px !important;
                padding: 4px 2px !important;
                font-weight: 600 !important;
                transition:
                    background-color 0.15s ease,
                    box-shadow 0.15s ease;
            }

            [class*="st-key-swu_aspect_main_"] button p,
            [class*="st-key-swu_aspect_double_"] button p {
                font-size: 0.73rem !important;
                line-height: 1.2 !important;
                text-align: center !important;
            }
            """

            # -----------------------------------------
            # COLOR EACH BUTTON BASED ON ITS STATE
            # -----------------------------------------

            for aspect in aspect_order:

                slug = aspect.lower()
                level = levels[aspect]

                color, text_color = aspect_palette[aspect]

                for kind in ("main", "double"):

                    if kind == "double" and aspect in (
                        "Heroism", "Villainy"
                    ):
                        continue

                    # Main button active when level >= 1
                    # Double button active when level == 2

                    active = (
                        level > 0 if kind == "main"
                        else level == 2
                    )

                    selector = (
                        f".st-key-swu_aspect_{kind}_{slug} button"
                    )

                    if active:
                        background = color
                        foreground = text_color
                        effect = "none"
                        opacity = "1"

                        # Border ONLY when selected
                        border = "2px solid #374151"
                        shadow = (
                            "0 0 0 2px "
                            "rgba(156,163,175,0.45)"
                        )

                    else:
                        # Grey background, but full-color
                        # aspect symbols and readable text
                        background = "rgba(107,114,128,0.14)"
                        foreground = "inherit"
                        effect = "none"
                        opacity = "1"

                        border = "2px solid transparent"
                        shadow = "none"


                    css += f"""
                    {selector},
                    {selector}:hover,
                    {selector}:focus-visible {{
                        background-color: {background}
                            !important;

                        color: {foreground}
                            !important;

                        border: {border}
                            !important;

                        box-shadow: {shadow}
                            !important;

                        filter: {effect}
                            !important;

                        opacity: {opacity}
                            !important;
                    }}

                    {selector} p,
                    {selector}:hover p,
                    {selector}:focus-visible p {{
                        color: {foreground} !important;
                    }}
                    """

            css += "</style>"

            st.markdown(
                css,
                unsafe_allow_html=True
            )

            # -----------------------------------------
            # ROW 1: HEROISM AND VILLAINY
            # -----------------------------------------

            top_left, top_right = st.columns(
                2, gap="small"
            )

            with top_left:
                st.button(
                    "⚪ Heroism",
                    key="swu_aspect_main_heroism",
                    on_click=toggle_aspect,
                    args=("Heroism",),
                    use_container_width=True,
                    type="secondary"
                )

            with top_right:
                st.button(
                    "⚫ Villainy",
                    key="swu_aspect_main_villainy",
                    on_click=toggle_aspect,
                    args=("Villainy",),
                    use_container_width=True,
                    type="secondary"
                )

            # -----------------------------------------
            # ROWS 2-3: COLORED ASPECTS
            # -----------------------------------------

            color_pairs = [
                ("Vigilance", "Command"),
                ("Aggression", "Cunning")
            ]

            for left_aspect, right_aspect in color_pairs:

                row_columns = st.columns(
                    [3, 1, 3, 1],
                    gap="small"
                )

                for aspect, main_index in [
                    (left_aspect, 0),
                    (right_aspect, 2)
                ]:

                    slug = aspect.lower()

                    with row_columns[main_index]:
                        st.button(
                            f"{aspect_icons[aspect]} {aspect}",
                            key=f"swu_aspect_main_{slug}",
                            on_click=toggle_aspect,
                            args=(aspect,),
                            use_container_width=True,
                            type="secondary"
                        )

                    with row_columns[main_index + 1]:
                        st.button(
                            aspect_icons[aspect] * 2,
                            key=f"swu_aspect_double_{slug}",
                            on_click=toggle_double_aspect,
                            args=(aspect,),
                            use_container_width=True,
                            type="secondary"
                        )

           
        with st.expander("Traits & Keywords"):
            chosen_traits = st.multiselect(
                "Traits (match any selected)",
                available_traits,
                on_change=reset_page
            )

            chosen_keywords = st.multiselect(
                "Keywords (match any selected)",
                available_keywords,
                on_change=reset_page
            )

        


        with st.expander("Sets & Rarity"):

            set_names = {
                item["code"]: item["name"]
                for item in sets
            }

            # Keep the Sets dropdown focused on the main expansions.
            # Enable the checkbox to show niche/promotional sets as well.
            include_niche_sets = st.session_state.get(
                "include_niche_sets", False
            )

            available_sets = [
                code
                for code in set_names
                if include_niche_sets or not is_niche_set(code)
            ]

            if "selected_sets" in st.session_state:
                st.session_state["selected_sets"] = [
                    code
                    for code in st.session_state["selected_sets"]
                    if code in available_sets
                ]

            chosen_sets = st.multiselect(
                "Sets",
                available_sets,
                format_func=lambda code: (
                    f"{code} — {set_names[code]}"
                ),
                key="selected_sets",
                on_change=reset_page
            )

            st.checkbox(
                "Include niche / promotional sets",
                value=False,
                key="include_niche_sets",
                on_change=reset_page,
                help=(
                    "Show non-expansion sets in the Sets dropdown, "
                    "including C24, Weekly Play, Judge, and other promos. "
                    "Does not exclude cards when no Sets are chosen."
                ),
            )


            chosen_rarities = st.multiselect(
                "Rarity",
                [
                    "Common",
                    "Uncommon",
                    "Rare",
                    "Legendary",
                    "Special"
                ],
                on_change=reset_page
            )



    # --------------------------------------------------
    # BUILD DATABASE QUERY
    # --------------------------------------------------

    with results:
        st.subheader("Matching Cards")

        page_size = st.selectbox(
            "Cards per page",
            [100, 40, 20],
            index=0,
            on_change=reset_page
        )

        invalid_range = any(
            low is not None
            and high is not None
            and low > high
            for low, high in [
                (min_cost, max_cost),
                (min_power, max_power),
                (min_hp, max_hp)
            ]
        )

        if invalid_range:
            st.warning(
                "A minimum value cannot exceed its maximum."
            )

        elif not card_types:
            st.info("Select at least one card type.")

        else:
            try:
                db = get_database()

                query = db.table("swu_grouped_cards").select(
                    "uuid,gameplay_id,name,subtitle,set_code,"
                    "collector_number,card_type,arena,"
                    "cost,power,hp,rarity,aspects,traits,"
                    "keywords,rules_text,front_image_url",
                    count="exact"
                )

                if name.strip():
                    query = query.ilike(
                        "full_name",
                        f"%{name.strip()}%"
                    )

                if ability.strip():
                    query = query.ilike(
                        "rules_text",
                        f"%{ability.strip()}%"
                    )
                    
                
                if exclude_ability.strip():
                    query = query.filter(
                        "ability_search_text",
                        "not.ilike",
                        f"%{exclude_ability.strip()}%"
                    )
 


                query = query.in_("card_type", card_types)

                
                # Only filter units when exactly one
                # arena checkbox is selected.
                # Events and Upgrades remain included.

                if ground_selected != space_selected:
                    selected_arena = (
                        "Ground" if ground_selected
                        else "Space"
                    )

                    query = query.or_(
                        f"card_type.neq.Unit,"
                        f"arena.eq.{selected_arena}"
                    )


                if chosen_sets:
                    query = query.in_(
                        "set_code", chosen_sets
                    )

                if chosen_rarities:
                    query = query.in_(
                        "rarity", chosen_rarities
                    )

                if chosen_traits:
                    query = query.overlaps(
                        "traits", chosen_traits
                    )

                if chosen_keywords:
                    query = query.overlaps(
                        "keywords", chosen_keywords
                    )

                for column, minimum, maximum in [
                    ("cost", min_cost, max_cost),
                    ("power", min_power, max_power),
                    ("hp", min_hp, max_hp)
                ]:
                    query = apply_numeric_filter(
                        query, column, minimum, maximum, maxima[column]
                    )

                query = apply_aspect_filters(
                    query,
                    aspect_mode,
                    levels
                )

               
                # Group matching printings into unique gameplay cards.
                if query is None:
                    cards, total = [], 0
                else:
                    cards, total = get_grouped_page(
                        query,
                        st.session_state.page,
                        page_size
                    )

                # Correct an out-of-range page if filtering reduced results.
                total_pages = max(1, math.ceil(total / page_size))
                if total and st.session_state.page > total_pages:
                    st.session_state.page = total_pages
                    cards, total = get_grouped_page(
                        query,
                        st.session_state.page,
                        page_size
                    )

                st.metric("Matching cards", total)

                if total:
                    start = (st.session_state.page - 1) * page_size + 1
                    end = min(st.session_state.page * page_size, total)

                    st.caption(
                        f"Showing {start:,}–{end:,} "
                        f"of {total:,} unique cards"
                    )

                    page_controls(total_pages, "top")
                    st.divider()

                    # Load alternate printings for the cards on this page.
                    printing_options = load_printing_options(
                        get_database(),
                        cards
                    )

                    # Keep the existing four-column gallery.
                    columns = st.columns(4)
                    for index, card in enumerate(cards):
                        with columns[index % 4]:
                            group_id = str(
                                card.get("gameplay_id") or card["uuid"]
                            )
                            show_grouped_card(
                                card,
                                printing_options.get(group_id, [card])
                            )
                            # The image still opens printings; add directly below it.
                            versions = printing_options.get(group_id, [card])
                            chosen_uuid = st.session_state.get(
                                f"swu_printing_choice_{group_id}"
                            )
                            chosen_printing = next(
                                (v for v in versions if str(v["uuid"]) == str(chosen_uuid)),
                                card,
                            )
                            # Gameplay fields (including corrected aspects and
                            # rules text) are read from the filtered card row.
                            deck_card = {**card, **{
                                k: v for k, v in chosen_printing.items()
                                if k in ("uuid", "set_code", "collector_number",
                                         "variant_type", "front_image_url")
                            }}
                            deck_count = deck_entries(st.session_state).get(
                                group_id, {}
                            ).get("count", 0)
                            limit = card_copy_limit(deck_card)
                            can_add = limit is None or deck_count < limit
                            st.button(
                                "Add to Deck" if can_add else "✓ In Deck",
                                key=f"swu_twin_suns_add_{group_id}",
                                on_click=add_twin_suns_card, args=(deck_card,),
                                use_container_width=True,
                                disabled=not can_add,
                            )

                    st.divider()
                    page_controls(total_pages, "bottom")
                else:
                    st.info("No matching cards found.")

            except Exception as error:
                st.error(
                    f"Card search failed: {error}"
                )


# --------------------------------------------------
# STAGE 2G — TWIN SUNS DECK BUILDER
# --------------------------------------------------

with deck_tab:
    render_deck_storage(st, create_client)
    st.divider()
    render_deck_builder(st)

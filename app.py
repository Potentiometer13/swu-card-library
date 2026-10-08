
import math
import streamlit as st
from supabase import create_client

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
    "Any Selected",
    "Exact",
    "Exclude Selected"
]

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



def apply_aspect_filters(query, mode, levels):

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
    # MODE 2: ANY SELECTED
    # -----------------------------------------
    # Match at least one selected aspect.
    # Additional unselected aspects are allowed.
    # Neutral cards are excluded unless
    # nothing is selected.

    elif mode == "Any Selected":

        if selected:

            conditions = [
                f"{columns[aspect]}.gt.0"
                for aspect in selected
            ]

            query = query.or_(
                ",".join(conditions)
            )

        else:
            # Nothing selected: neutral only
            for column in columns.values():
                query = query.eq(column, 0)

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



# --------------------------------------------------
# MAIN APPLICATION
# --------------------------------------------------

st.title("Star Wars Unlimited Deck Builder")
st.caption("Search cards, build decks, track your collection")

leader_tab, base_tab, card_tab = st.tabs(
    ["1. Leaders", "2. Bases", "3. Cards"],
    default="3. Cards"
)

with leader_tab:
    st.header("Leaders")
    st.info("Leader selection will be added in Stage 2E.")

with base_tab:
    st.header("Bases")
    st.info("Base selection will be added in Stage 2F.")


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
                key="aspect_filter_mode_v2",
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

            weekly_play_codes = {
                code
                for code, set_name in set_names.items()
                if (
                    "weekly" in set_name.lower()
                    and "play" in set_name.lower()
                )
                or code.upper().endswith(("OP", "WP"))
            }

            
            # Read checkbox state before displaying the Sets dropdown
            include_weekly_play = st.session_state.get(
                "include_weekly_play", False
            )

            available_sets = [
                code
                for code in set_names
                if include_weekly_play
                or code not in weekly_play_codes
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
                "Include Weekly Play sets",
                value=False,
                key="include_weekly_play",
                on_change=reset_page
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

                query = db.table("swu_regular_cards").select(
                    "uuid,name,subtitle,set_code,"
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

                if query is None:
                    cards, total = [], 0

                else:
                    def fetch_page(page):
                        start = (page - 1) * page_size

                        return (
                            query
                            .order("name")
                            .order("subtitle")
                            .order("uuid")
                            .range(
                                start,
                                start + page_size - 1
                            )
                            .execute()
                        )

                    response = fetch_page(st.session_state.page)

                    cards = response.data or []
                    total = response.count or 0

                    total_pages = max(
                        1,
                        math.ceil(total / page_size)
                    )

                    if st.session_state.page > total_pages:
                        st.session_state.page = total_pages
                        response = fetch_page(total_pages)
                        cards = response.data or []

                st.metric("Matching printings", total)

                if total:
                    total_pages = math.ceil(total / page_size)

                    start = (
                        (st.session_state.page - 1)
                        * page_size + 1
                    )

                    end = min(
                        st.session_state.page * page_size,
                        total
                    )

                    st.caption(
                        f"Showing {start:,}–{end:,} "
                        f"of {total:,} printings"
                    )

                    page_controls(total_pages, "top")

                    st.divider()

                    columns = st.columns(4)

                    for index, card in enumerate(cards):
                        with columns[index % 4]:
                            show_card(card)

                    st.divider()

                    page_controls(total_pages, "bottom")

                else:
                    st.info("No matching cards found.")

            except Exception as error:
                st.error(
                    f"Card search failed: {error}"
                )

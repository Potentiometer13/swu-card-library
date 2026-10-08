
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
    "Selected only",
    "Any selected",
    "All selected",
    "Exact",
    "Exclude selected",
    "Deck compatibility"
]

if "page" not in st.session_state:
    st.session_state.page = 1


def reset_page():
    st.session_state.page = 1


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


def apply_aspect_filters(query, mode, levels, neutral):
    columns = ASPECT_COLUMNS
    empty = is_neutral_filter()

    if mode in ("Selected only", "Deck compatibility"):
        for aspect, column in columns.items():
            query = query.lte(column, levels[aspect])

        if not neutral:
            query = query.or_(has_any_aspect_filter())

    elif mode == "Any selected":
        conditions = [
            f"{columns[a]}.gt.0"
            for a in ASPECTS if levels[a] > 0
        ]

        if neutral:
            conditions.append(empty)

        if not conditions:
            return None

        query = query.or_(",".join(conditions))

    elif mode == "All selected":
        conditions = [
            f"{columns[a]}.gte.{levels[a]}"
            for a in ASPECTS if levels[a] > 0
        ]

        if conditions:
            if neutral:
                query = query.or_(
                    "and(" + ",".join(conditions) +
                    ")," + empty
                )
            else:
                for aspect in ASPECTS:
                    if levels[aspect] > 0:
                        query = query.gte(
                            columns[aspect], levels[aspect]
                        )
        elif neutral:
            for column in columns.values():
                query = query.eq(column, 0)
        else:
            return None

    elif mode == "Exact":
        if not any(levels.values()) and not neutral:
            return None

        conditions = [
            f"{columns[a]}.eq.{levels[a]}"
            for a in ASPECTS
        ]

        if neutral and any(levels.values()):
            query = query.or_(
                "and(" + ",".join(conditions) +
                ")," + empty
            )
        else:
            for aspect, column in columns.items():
                query = query.eq(column, levels[aspect])

    elif mode == "Exclude selected":
        for aspect, column in columns.items():
            if levels[aspect] > 0:
                query = query.eq(column, 0)

        if not neutral:
            query = query.or_(has_any_aspect_filter())

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
    image_url = card.get("front_image_url")

    if image_url:
        st.image(image_url, width="stretch")
    else:
        st.info("Image unavailable")

    name = card.get("name") or "Unknown"
    subtitle = card.get("subtitle")

    st.markdown(f"**{name}**")

    if subtitle:
        st.caption(subtitle)

    st.caption(
        f"{card.get('set_code') or '?'} | "
        f"{card.get('collector_number') or '?'}"
    )

    with st.expander("Details"):
        st.write("Type:", card.get("card_type"))
        st.write("Arena:", card.get("arena"))
        st.write("Cost:", card.get("cost"))
        st.write("Power:", card.get("power"))
        st.write("HP:", card.get("hp"))
        st.write("Aspects:", card.get("aspects") or [])
        st.write("Traits:", card.get("traits") or [])
        st.write("Keywords:", card.get("keywords") or [])
        st.write("Ability:", card.get("rules_text") or "None")


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
                    value=False,
                    key="arena_ground",
                    on_change=reset_page
                )

            with arena_col2:
                space_selected = st.checkbox(
                    "Space",
                    value=False,
                    key="arena_space",
                    on_change=reset_page
                )


            maxima = get_stat_maxima()
            min_cost, max_cost = number_range("Cost", maxima["cost"])
            min_power, max_power = number_range("Power", maxima["power"])
            min_hp, max_hp = number_range("HP", maxima["hp"])

        with st.expander("Aspects", expanded=True):
            st.caption(
                "None excludes a color. Single allows up to "
                "one icon. Double allows up to two."
            )

            levels = {}

            for aspect in ASPECTS[:4]:
                levels[aspect] = st.selectbox(
                    aspect,
                    options=[0, 1, 2],
                    index=2,
                    format_func=lambda n: {
                        0: "None",
                        1: "Single",
                        2: "Double"
                    }[n],
                    key=f"aspect_{aspect}",
                    on_change=reset_page
                )

            levels["Heroism"] = int(st.checkbox(
                "Heroism",
                value=True,
                key="aspect_heroism",
                on_change=reset_page
            ))

            levels["Villainy"] = int(st.checkbox(
                "Villainy",
                value=True,
                key="aspect_villainy",
                on_change=reset_page
            ))

            aspect_mode = st.selectbox(
                "Aspect filter mode",
                ASPECT_MODES,
                on_change=reset_page
            )

            include_neutral = st.checkbox(
                "Include neutral cards",
                value=True,
                on_change=reset_page
            )

            st.caption(
                "Neutral cards are included independently of "
                "the selected aspect mode when enabled."
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
                    excluded = (
                        exclude_ability.strip()
                        .replace("\\", "\\\\")
                        .replace('"', '\\"')
                    )

                    query = query.or_(
                        'rules_text.is.null,'
                        f'rules_text.not.ilike."*{excluded}*"'
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
                    levels,
                    include_neutral
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

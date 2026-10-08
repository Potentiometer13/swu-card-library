
import math
import streamlit as st
from supabase import create_client

# --------------------------------------------------
# WEBSITE SETTINGS
# --------------------------------------------------

st.set_page_config(
    page_title="SWU Card Library",
    page_icon="🃏",
    layout="wide"
)

# Maintain the current page between interactions
if "page" not in st.session_state:
    st.session_state.page = 1


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
# PAGINATION CONTROLS
# --------------------------------------------------

def reset_page():
    st.session_state.page = 1


def change_page(amount, total_pages):
    new_page = st.session_state.page + amount
    st.session_state.page = max(
        1, min(new_page, total_pages)
    )


def show_page_buttons(total_pages, location):
    previous, middle, next_button = st.columns(
        [1, 2, 1]
    )

    current_page = st.session_state.page

    with previous:
        st.button(
            "⬅ Previous",
            key=f"{location}_previous",
            disabled=current_page <= 1,
            on_click=change_page,
            args=(-1, total_pages),
            use_container_width=True
        )

    with middle:
        st.markdown(
            f"<p style='text-align:center;'>"
            f"Page {current_page:,} of {total_pages:,}"
            f"</p>",
            unsafe_allow_html=True
        )

    with next_button:
        st.button(
            "Next ➡",
            key=f"{location}_next",
            disabled=current_page >= total_pages,
            on_click=change_page,
            args=(1, total_pages),
            use_container_width=True
        )


# --------------------------------------------------
# DATABASE SEARCH
# --------------------------------------------------

@st.cache_data(ttl=600)
def search_cards(search_text, search_field, page, page_size):
    database = get_database()

    query = database.table("card_printings").select(
        "uuid,name,subtitle,set_code,collector_number,"
        "card_type,arena,cost,aspects,traits,rules_text,"
        "front_image_url,variant_type",
        count="exact"
    )

    if search_text:
        column = (
            "name" if search_field == "Card name"
            else "rules_text"
        )

        query = query.ilike(
            column, f"%{search_text}%"
        )

    start = (page - 1) * page_size

    response = (
        query
        .order("name")
        .order("uuid")
        .range(start, start + page_size - 1)
        .execute()
    )

    return response.data, response.count or 0


# --------------------------------------------------
# WEBSITE HEADER
# --------------------------------------------------

st.title("Star Wars Unlimited Card Library")
st.caption("Card Search | Deck Builder | Collection Tracker")

search_tab, deck_tab, collection_tab = st.tabs(
    ["🔍 Card Search", "🃏 Deck Builder", "📦 Collection"]
)


# --------------------------------------------------
# CARD SEARCH
# --------------------------------------------------

with search_tab:
    st.header("Card Search")

    # Search controls
    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        search = st.text_input(
            "Search cards",
            placeholder="Name or ability text",
            key="search_query",
            on_change=reset_page
        )

    with col2:
        field = st.selectbox(
            "Search field",
            ["Card name", "Ability text"],
            key="search_field",
            on_change=reset_page
        )

    with col3:
        page_size = st.selectbox(
            "Cards per page",
            [100, 40, 20],
            key="page_size",
            on_change=reset_page
        )

    try:
        cards, total = search_cards(
            search.strip(),
            field,
            st.session_state.page,
            page_size
        )

        total_pages = max(
            1, math.ceil(total / page_size)
        )

        # Handle a page that no longer exists
        if st.session_state.page > total_pages:
            st.session_state.page = total_pages
            cards, total = search_cards(
                search.strip(),
                field,
                st.session_state.page,
                page_size
            )

        st.divider()

        # Results summary
        st.metric("Matching card printings", total)

        if total > 0:
            first_card = (
                (st.session_state.page - 1) * page_size + 1
            )

            last_card = min(
                st.session_state.page * page_size,
                total
            )

            st.caption(
                f"Showing {first_card:,}–{last_card:,} "
                f"of {total:,} matching printings"
            )

            # Top page controls
            show_page_buttons(total_pages, "top")

            st.divider()

            # Card image gallery
            columns = st.columns(4)

            for index, card in enumerate(cards):
                with columns[index % 4]:

                    image_url = card.get("front_image_url")

                    if image_url:
                        st.image(
                            image_url,
                            width="stretch"
                        )
                    else:
                        st.info("Image unavailable")

                    st.markdown(
                        f"**{card.get('name') or 'Unknown Card'}**"
                    )

                    if card.get("subtitle"):
                        st.caption(card["subtitle"])

                    st.write(
                        f"Set: {card.get('set_code') or 'Unknown'}"
                    )

                    st.write(
                        f"Card ID: "
                        f"{card.get('collector_number') or 'N/A'}"
                    )

                    with st.expander("Card Details"):
                        st.write(
                            f"Type: {card.get('card_type') or 'N/A'}"
                        )
                        st.write(
                            f"Arena: {card.get('arena') or 'N/A'}"
                        )
                        st.write(
                            f"Cost: {card.get('cost')}"
                        )
                        st.write(
                            "Aspects:",
                            card.get("aspects") or []
                        )
                        st.write(
                            "Traits:",
                            card.get("traits") or []
                        )
                        st.write(
                            "Ability:",
                            card.get("rules_text") or "None"
                        )

            st.divider()

            # Bottom page controls
            show_page_buttons(total_pages, "bottom")

        else:
            st.info("No matching cards found.")

    except Exception:
        st.error(
            "Unable to retrieve cards. "
            "Check the database connection or application logs."
        )


# --------------------------------------------------
# DECK BUILDER
# --------------------------------------------------

with deck_tab:
    st.header("Deck Builder")
    st.info("Coming in Stage 3!")


# --------------------------------------------------
# COLLECTION
# --------------------------------------------------

with collection_tab:
    st.header("My Collection")
    st.info("Coming in Stage 4!")

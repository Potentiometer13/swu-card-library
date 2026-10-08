
import streamlit as st
from supabase import create_client

st.set_page_config(
    page_title="SWU Card Library",
    page_icon="🃏",
    layout="wide"
)

PAGE_SIZE = 24


# Connect to our Supabase database
@st.cache_resource
def get_database():
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_PUBLISHABLE_KEY"]
    )


# Search the complete card database
@st.cache_data(ttl=600)
def search_cards(search_text, search_field, page):
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
        query = query.ilike(column, f"%{search_text}%")

    start = (page - 1) * PAGE_SIZE

    response = (
        query
        .order("name")
        .range(start, start + PAGE_SIZE - 1)
        .execute()
    )

    return response.data, response.count


# Application header
st.title("Star Wars Unlimited Card Library")
st.caption("Card Search | Deck Builder | Collection Tracker")

search_tab, deck_tab, collection_tab = st.tabs(
    ["🔍 Card Search", "🃏 Deck Builder", "📦 Collection"]
)


with search_tab:
    st.header("Card Search")

    col1, col2 = st.columns([2, 1])

    with col1:
        search = st.text_input(
            "Search",
            placeholder="Enter a card name or ability text"
        )

    with col2:
        field = st.selectbox(
            "Search field",
            ["Card name", "Ability text"]
        )

    page = st.number_input(
        "Page", min_value=1, value=1, step=1
    )

    try:
        cards, total = search_cards(
            search.strip(), field, page
        )

        st.metric("Matching card printings", total or 0)

        if not cards:
            st.info("No cards found on this page.")

        columns = st.columns(4)

        for index, card in enumerate(cards):
            with columns[index % 4]:
                image_url = card.get("front_image_url")

                if image_url:
                    st.image(image_url, width="stretch")
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

    except Exception:
        st.error(
            "Could not load cards from Supabase. "
            "Check the database connection and app secrets."
        )


with deck_tab:
    st.header("Deck Builder")
    st.info("Coming in Stage 3!")


with collection_tab:
    st.header("My Collection")
    st.info("Coming in Stage 4!")


import streamlit as st
import requests

# Website configuration
st.set_page_config(
    page_title="SWU Card Library",
    page_icon="🃏",
    layout="wide"
)

API_URL = "https://api.swuapi.com/cards"


# Retrieve cards from the API
@st.cache_data(ttl=3600)
def get_cards(limit=24):
    response = requests.get(
        API_URL,
        params={"limit": limit},
        timeout=30
    )
    response.raise_for_status()

    data = response.json()

    if not isinstance(data, dict):
        raise ValueError("Unexpected API response")

    cards = data.get("cards")

    if not isinstance(cards, list):
        raise ValueError("No card list found in API response")

    return cards, data.get("pagination", {})


# Main website
st.title("Star Wars Unlimited Card Library")
st.caption("Card Search | Deck Builder | Collection Tracker")

search_tab, deck_tab, collection_tab = st.tabs(
    ["🔍 Card Search", "🃏 Deck Builder", "📦 Collection"]
)


# CARD SEARCH PAGE
with search_tab:
    st.header("Card Search")

    try:
        cards, pagination = get_cards()

    except (requests.RequestException, ValueError) as error:
        st.error(f"Unable to retrieve cards: {error}")
        cards = []

    if cards:
        st.success("Connected to the SWU API!")

        st.write(f"Cards loaded for preview: {len(cards)}")

        search = st.text_input(
            "Search card names or ability text"
        )

        if search:
            cards = [
                card for card in cards
                if search.lower() in (
                    str(card.get("name") or "") + " " +
                    str(card.get("text") or "")
                ).lower()
            ]

        st.caption(
            "Preview only: searches the cards loaded above."
        )

        columns = st.columns(4)

        for index, card in enumerate(cards):
            with columns[index % 4]:
                name = card.get("name") or "Unknown Card"
                subtitle = card.get("subtitle") or ""

                st.subheader(name)

                if subtitle:
                    st.caption(subtitle)

                image = (
                    card.get("frontImageUrl")
                    or card.get("thumbnailUrl")
                )

                if image:
                    st.image(image, width="stretch")
                else:
                    st.info("No image available")

                card_id = (
                    card.get("collector_number")
                    or card.get("id")
                    or "Unknown"
                )

                st.write(f"**ID:** {card_id}")
                st.write(
                    f"**Type:** {card.get('type', 'Unknown')}"
                )

                with st.expander("Card Details"):
                    st.write(
                        f"**Cost:** {card.get('cost', 'N/A')}"
                    )
                    st.write(
                        f"**Aspects:** {', '.join(card.get('aspects') or [])}"
                    )
                    st.write(
                        f"**Traits:** {', '.join(card.get('traits') or [])}"
                    )
                    st.write(
                        f"**Ability:** {card.get('text') or 'None'}"
                    )


# DECK BUILDER PAGE
with deck_tab:
    st.header("Deck Builder")
    st.info("Deck building functionality coming soon!")


# COLLECTION PAGE
with collection_tab:
    st.header("My Collection")
    st.info("Collection tracking coming soon!")


import streamlit as st

# Website settings
st.set_page_config(
    page_title="SWU Card Library",
    page_icon="🃏",
    layout="wide"
)

# Main heading
st.title("Star Wars Unlimited Card Library")
st.caption("Card Search | Deck Builder | Collection Tracker")

# Navigation
search_tab, deck_tab, collection_tab = st.tabs(
    ["🔍 Card Search", "🃏 Deck Builder", "📦 Collection"]
)

# Card searching
with search_tab:
    st.header("Card Search")

    search = st.text_input(
        "Search by card name, ID, or ability text"
    )

    st.info("Card database connection coming next!")

    if search:
        st.write(f"Search query: {search}")

# Deck building
with deck_tab:
    st.header("Deck Builder")
    st.write("Build, save, and analyze your SWU decks here.")

# Collection management
with collection_tab:
    st.header("My Collection")
    st.write("Track your owned cards and variants here.")

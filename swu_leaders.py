"""Stage 2E: searchable, grouped SWU leader gallery and leader selection.

Works alongside swu_grouping.py without changing the Cards tab. The Supabase
view `swu_grouped_leaders` must be created using stage_2e_supabase.sql.
"""

from collections import defaultdict
from html import escape
import json
import re

import streamlit as st

from swu_grouping import printing_id, printing_sort_key


LEADER_VIEW = "swu_grouped_leaders"
LEADER_FIELDS = (
    "uuid,gameplay_id,name,subtitle,set_code,collector_number,"
    "variant_type,front_image_url,back_image_url,aspects,traits,rarity"
)


def reset_leader_page():
    """Do not affect the independent pagination of the Cards tab."""
    st.session_state["swu_leader_page"] = 1


def change_leader_page(amount, total_pages):
    page = int(st.session_state.get("swu_leader_page", 1))
    st.session_state["swu_leader_page"] = max(1, min(page + amount, total_pages))


def leader_page_controls(total_pages, location):
    left, center, right = st.columns([1, 1, 1])
    page = int(st.session_state.get("swu_leader_page", 1))
    with left:
        st.button(
            "⬅ Previous",
            key=f"swu_leader_{location}_previous",
            disabled=page <= 1,
            on_click=change_leader_page,
            args=(-1, total_pages),
            use_container_width=True,
        )
    with center:
        st.markdown(f"**Page {page:,} of {total_pages:,}**", text_alignment="center")
    with right:
        st.button(
            "Next ➡",
            key=f"swu_leader_{location}_next",
            disabled=page >= total_pages,
            on_click=change_leader_page,
            args=(1, total_pages),
            use_container_width=True,
        )


def load_leader_printings(db, visible_leaders):
    """Fetch all alternative printings for only the leaders on this page."""
    group_ids = list(dict.fromkeys(
        str(leader.get("gameplay_id") or leader["uuid"])
        for leader in visible_leaders
    ))
    grouped = defaultdict(list)
    for start in range(0, len(group_ids), 20):
        batch = group_ids[start:start + 20]
        # Every matching printing in a group belongs to this leader-only view.
        response = (
            db.table(LEADER_VIEW)
              .select(LEADER_FIELDS)
              .in_("gameplay_id", batch)
              .limit(1000)
              .execute()
        )
        for row in response.data or []:
            grouped[str(row["gameplay_id"])].append(row)
    for printings in grouped.values():
        printings.sort(key=printing_sort_key)
    return grouped


def leader_display_name(leader):
    name = str(leader.get("name") or "Unknown leader").strip()
    subtitle = str(leader.get("subtitle") or "").strip()
    return f"{name} — {subtitle}" if subtitle else name


def leader_selection_record(leader):
    """Keep only the fields needed for a future deck and a selected banner."""
    return {
        "uuid": str(leader["uuid"]),
        "gameplay_id": str(leader.get("gameplay_id") or leader["uuid"]),
        "name": leader.get("name") or "Unknown leader",
        "subtitle": leader.get("subtitle") or "",
        "set_code": leader.get("set_code") or "",
        "collector_number": leader.get("collector_number") or "",
        "variant_type": leader.get("variant_type") or "",
        "front_image_url": leader.get("front_image_url") or "",
        "back_image_url": leader.get("back_image_url") or "",
        "aspects": list(leader.get("aspects") or []),
    }


def selected_leader_panel():
    """Selected leader survives gallery filters and pagination in this session."""
    leader = st.session_state.get("swu_selected_leader")
    if not leader:
        st.info("No leader selected yet. Use Add Leader beneath a leader’s images to choose one.")
        return

    st.markdown("**Selected Leader**")
    image_col, description_col, action_col = st.columns([1, 3, 1], gap="medium")
    with image_col:
        if leader.get("front_image_url"):
            st.image(leader["front_image_url"], width="stretch")
    with description_col:
        st.markdown(f"**{escape(leader_display_name(leader))}**")
        st.caption(f"{printing_id(leader)} · {leader.get('variant_type') or 'Printing'}")
        aspects = leader.get("aspects") or []
        st.write("Aspects: " + (", ".join(aspects) if aspects else "Neutral"))
    with action_col:
        if st.button("Remove Leader", key="swu_remove_selected_leader", use_container_width=True):
            st.session_state.pop("swu_selected_leader", None)
            st.rerun()


@st.dialog("Leader details", width="large", on_dismiss="rerun")
def show_leader_dialog(leader, printings):
    """The printing chooser is available only after clicking a leader."""
    versions = sorted(list(printings or [leader]), key=printing_sort_key)
    version_by_id = {str(version["uuid"]): version for version in versions}
    group_id = str(leader.get("gameplay_id") or leader["uuid"])
    selection_key = f"swu_leader_printing_{group_id}"
    if st.session_state.get(selection_key) not in version_by_id:
        preferred = str(leader["uuid"])
        st.session_state[selection_key] = (
            preferred if preferred in version_by_id else next(iter(version_by_id))
        )

    st.subheader(leader.get("name") or "Leader")
    if leader.get("subtitle"):
        st.caption(leader["subtitle"])

    # The widget is placed before the images so the dialog updates both sides
    # on its fragment rerun without an additional full-page refresh.
    st.selectbox(
        "Printing",
        options=list(version_by_id),
        format_func=lambda uid: (
            f"{printing_id(version_by_id[uid])} · "
            f"{version_by_id[uid].get('variant_type') or 'Other printing'}"
        ),
        key=selection_key,
    )
    current = version_by_id[st.session_state[selection_key]]
    st.caption(f"{len(versions)} available printing(s) · {printing_id(current)}")

    front_col, back_col = st.columns(2, gap="medium")
    with front_col:
        st.caption("Leader side")
        if current.get("front_image_url"):
            st.image(current["front_image_url"], width="stretch")
        else:
            st.info("Front image unavailable")
    with back_col:
        st.caption("Deployed side")
        if current.get("back_image_url"):
            st.image(current["back_image_url"], width="stretch")
        else:
            st.info("Back image unavailable")

    aspects = current.get("aspects") or []
    st.caption("Aspects: " + (", ".join(aspects) if aspects else "Neutral"))
    selected = st.session_state.get("swu_selected_leader") or {}
    already_selected = selected.get("gameplay_id") == group_id
    button_text = "Update Selected Leader" if already_selected else "Add Leader"
    if st.button(button_text, type="primary", use_container_width=True, key=f"swu_pick_leader_{group_id}"):
        st.session_state["swu_selected_leader"] = leader_selection_record(current)
        st.rerun()


def add_leader(leader):
    """Select a leader directly from the gallery without opening its dialog."""
    st.session_state["swu_selected_leader"] = leader_selection_record(leader)


def _leader_image_button(leader, versions, current, side, safe_id):
    """Draw one clickable card side; clicking keeps the printing dialog available."""
    button_key = f"swu_leader_gallery_{safe_id}_{side}"
    image_url = str(current.get(f"{side}_image_url") or "").strip()

    # Leader front: landscape (7:5), full gallery width.
    # Deployed back: portrait (5:7), centered at two-thirds of its
    # previous 71.43% width, i.e. 47.62% of the gallery width.
    image_width = "100%" if side == "front" else "47.62%"
    image_ratio = "7 / 5" if side == "front" else "5 / 7"

    if image_url.startswith(("https://", "http://")):
        selector = f".st-key-{button_key} button"
        css_url = json.dumps(image_url).replace("<", "\\3c ")
        st.markdown(
            f"""
            <style>
            /* Center the portrait image within its full-width gallery slot. */
            .st-key-{button_key} {{
                display: flex !important;
                justify-content: center !important;
                width: 100% !important;
            }}
            {selector}, {selector}:hover, {selector}:focus-visible {{
                width: {image_width} !important;
                flex: 0 0 {image_width} !important;
                max-width: {image_width} !important;
                height: auto !important;
                min-height: 0 !important;
                aspect-ratio: {image_ratio} !important;
                display: block !important;
                margin-left: auto !important;
                margin-right: auto !important;
                padding: 0 !important;
                border: none !important;
                border-radius: 8px !important;
                background: transparent url({css_url})
                    center center / contain no-repeat !important;
                box-shadow: none !important;
            }}
            {selector}:hover {{
                box-shadow: 0 0 0 2px #4B5563 !important;
                cursor: pointer !important;
            }}
            {selector}:focus-visible {{ outline: 3px solid #6B7280 !important; }}
            {selector} p {{
                opacity: 0 !important;
                font-size: 0 !important;
                line-height: 0 !important;
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.caption(f"{side.title()} image unavailable")

    if st.button(
        f"View {side} side of {leader_display_name(leader)}",
        key=button_key,
        use_container_width=True,
        help="Click to view all printings of this leader",
    ):
        show_leader_dialog(leader, versions)


def show_leader_gallery_card(leader, printings):
    """Clickable landscape front stacked above portrait back, with Add Leader."""
    versions = sorted(list(printings or [leader]), key=printing_sort_key)
    group_id = str(leader.get("gameplay_id") or leader["uuid"])
    selection_key = f"swu_leader_printing_{group_id}"
    version_by_id = {str(version["uuid"]): version for version in versions}
    if st.session_state.get(selection_key) not in version_by_id:
        preferred = str(leader["uuid"])
        st.session_state[selection_key] = (
            preferred if preferred in version_by_id else next(iter(version_by_id))
        )
    current = version_by_id[st.session_state[selection_key]]

    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(leader["uuid"]))

    # Put both images in a zero-gap container. No extra labels or whitespace
    # between the landscape front and the smaller, centered portrait back.
    with st.container(
        key=f"swu_leader_image_stack_{safe_id}",
        gap=None,
        border=False,
    ):
        _leader_image_button(leader, versions, current, "front", safe_id)
        _leader_image_button(leader, versions, current, "back", safe_id)

    st.markdown(
        '<p style="text-align:center; font-weight:600; margin:0.25rem 0">'
        + escape(printing_id(current)) + "</p>",
        unsafe_allow_html=True,
    )
    already_selected = (
        (st.session_state.get("swu_selected_leader") or {}).get("gameplay_id")
        == group_id
    )
    st.button(
        "✓ Added" if already_selected else "Add Leader",
        key=f"swu_add_leader_{safe_id}",
        on_click=add_leader,
        args=(current,),
        disabled=already_selected,
        type="secondary" if already_selected else "primary",
        use_container_width=True,
    )

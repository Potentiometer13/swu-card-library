"""Stage 2F: grouped Base gallery, printing dialogs, and session selection.

This module adds no changes to the existing Cards / Leaders helpers.
Requires Supabase view public.swu_grouped_bases (stage_2f_supabase.sql).
"""

from collections import defaultdict
from html import escape
import json
import re

import streamlit as st
from swu_grouping import printing_id, printing_sort_key

BASE_VIEW = "swu_grouped_bases"
BASE_FIELDS = (
    "uuid,gameplay_id,name,subtitle,set_code,collector_number,"
    "variant_type,front_image_url,back_image_url,aspects,hp,rarity"
)


def reset_base_page():
    st.session_state["swu_base_page"] = 1


def change_base_page(amount, total_pages):
    current = int(st.session_state.get("swu_base_page", 1))
    st.session_state["swu_base_page"] = max(1, min(current + amount, total_pages))


def base_page_controls(total_pages, location):
    left, middle, right = st.columns([1, 1, 1])
    page = int(st.session_state.get("swu_base_page", 1))
    with left:
        st.button(
            "⬅ Previous", key=f"swu_base_{location}_prev",
            disabled=page <= 1, on_click=change_base_page,
            args=(-1, total_pages), use_container_width=True,
        )
    with middle:
        st.markdown(f"**Page {page:,} of {total_pages:,}**", text_alignment="center")
    with right:
        st.button(
            "Next ➡", key=f"swu_base_{location}_next",
            disabled=page >= total_pages, on_click=change_base_page,
            args=(1, total_pages), use_container_width=True,
        )


def load_base_printings(db, visible_bases):
    """Fetch every printing for the visible gameplay groups, even from other sets."""
    ids = list(dict.fromkeys(
        str(base.get("gameplay_id") or base["uuid"])
        for base in visible_bases
    ))
    grouped = defaultdict(list)
    for start in range(0, len(ids), 20):
        response = (
            db.table(BASE_VIEW)
              .select(BASE_FIELDS)
              .in_("gameplay_id", ids[start:start + 20])
              .limit(1000)
              .execute()
        )
        for printing in response.data or []:
            grouped[str(printing["gameplay_id"])].append(printing)
    for variants in grouped.values():
        variants.sort(key=printing_sort_key)
    return grouped


def base_display_name(base):
    name = str(base.get("name") or "Unknown base").strip()
    subtitle = str(base.get("subtitle") or "").strip()
    return f"{name} — {subtitle}" if subtitle else name


def base_selection_record(base):
    """Store the chosen printing and aspects for the future deck builder."""
    return {
        "uuid": str(base["uuid"]),
        "gameplay_id": str(base.get("gameplay_id") or base["uuid"]),
        "name": base.get("name") or "Unknown base",
        "subtitle": base.get("subtitle") or "",
        "set_code": base.get("set_code") or "",
        "collector_number": base.get("collector_number") or "",
        "variant_type": base.get("variant_type") or "",
        "front_image_url": base.get("front_image_url") or "",
        "back_image_url": base.get("back_image_url") or "",
        "aspects": list(base.get("aspects") or []),
        "hp": base.get("hp"),
    }


def add_base(base):
    st.session_state["swu_selected_base"] = base_selection_record(base)


def remove_base():
    st.session_state.pop("swu_selected_base", None)


def selected_base_panel():
    base = st.session_state.get("swu_selected_base")
    if not base:
        st.info("No base selected yet. Use Add Base beneath a base to choose one.")
        return
    st.markdown("**Selected Base**")
    art_col, info_col, action_col = st.columns([1, 3, 1], gap="medium")
    with art_col:
        if base.get("front_image_url"):
            st.image(base["front_image_url"], width="stretch")
    with info_col:
        st.markdown(f"**{escape(base_display_name(base))}**")
        st.caption(f"{printing_id(base)} · {base.get('variant_type') or 'Printing'}")
        aspects = base.get("aspects") or []
        st.write("Aspects: " + (", ".join(aspects) if aspects else "Neutral"))
        if base.get("hp") is not None:
            st.write(f"HP: {base['hp']}")
    with action_col:
        st.button(
            "Remove Base", key="swu_remove_selected_base", on_click=remove_base,
            use_container_width=True,
        )


@st.dialog("Base details", width="large", on_dismiss="rerun")
def show_base_dialog(base, printings, gallery_group_id=None):
    variants = sorted(list(printings or [base]), key=printing_sort_key)
    by_id = {str(p["uuid"]): p for p in variants}
    group_id = gallery_group_id or str(base.get("gameplay_id") or base["uuid"])
    selection_key = f"swu_base_printing_{group_id}"
    if st.session_state.get(selection_key) not in by_id:
        preferred = str(base["uuid"])
        st.session_state[selection_key] = (
            preferred if preferred in by_id else next(iter(by_id))
        )

    st.subheader(base.get("name") or "Base")
    if base.get("subtitle"):
        st.caption(base["subtitle"])

    st.selectbox(
        "Printing", options=list(by_id),
        format_func=lambda uid: (
            f"{base_display_name(by_id[uid])} · "
            f"{printing_id(by_id[uid])} · "
            f"{by_id[uid].get('variant_type') or 'Other printing'}"
        ), key=selection_key,
    )
    current = by_id[st.session_state[selection_key]]
    st.caption(f"{len(variants)} available printing(s) · {printing_id(current)}")

    image_col, details_col = st.columns([3, 2], gap="medium")
    with image_col:
        if current.get("front_image_url"):
            st.image(current["front_image_url"], width="stretch")
        else:
            st.info("Base image unavailable")
    with details_col:
        aspects = current.get("aspects") or []
        st.write("Aspects: " + (", ".join(aspects) if aspects else "Neutral"))
        if current.get("hp") is not None:
            st.write(f"HP: {current['hp']}")
        if current.get("back_image_url"):
            with st.expander("Reverse / token artwork"):
                st.image(current["back_image_url"], width="stretch")

    selected = st.session_state.get("swu_selected_base") or {}
    button_text = (
        "Update Selected Base" if selected.get("uuid") in by_id
        else "Add Base"
    )
    if st.button(
        button_text, type="primary", use_container_width=True,
        key=f"swu_pick_base_{group_id}",
    ):
        add_base(current)
        st.rerun()


def show_base_gallery_card(
    base, printings, location_options=None, location_key=None,
    location_label_func=None, reserve_location_space=False,
    gallery_group_id=None
):
    """Clickable artwork, optional location dropdown, then Add Base."""
    variants = sorted(list(printings or [base]), key=printing_sort_key)
    group_id = gallery_group_id or str(base.get("gameplay_id") or base["uuid"])
    selection_key = f"swu_base_printing_{group_id}"
    by_id = {str(p["uuid"]): p for p in variants}
    if st.session_state.get(selection_key) not in by_id:
        preferred = str(base["uuid"])
        st.session_state[selection_key] = (
            preferred if preferred in by_id else next(iter(by_id))
        )
    current = by_id[st.session_state[selection_key]]
    image = str(current.get("front_image_url") or "").strip()
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(base["uuid"]))
    button_key = f"swu_base_gallery_{safe_id}"

    if image.startswith(("https://", "http://")):
        selector = f".st-key-{button_key} button"
        css_url = json.dumps(image).replace("<", "\\3c ")
        st.markdown(
            f"""
            <style>
            {selector}, {selector}:hover, {selector}:focus-visible {{
                width: 100% !important;
                height: auto !important;
                min-height: 0 !important;
                aspect-ratio: 7 / 5 !important;
                display: block !important;
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
            {selector} p {{ opacity: 0 !important; }}
            </style>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.caption("Image unavailable")

    if st.button(
        f"View printings of {base_display_name(base)}",
        key=button_key,
        use_container_width=True,
        help="Click the base image to see available printings",
    ):
        show_base_dialog(base, variants, gallery_group_id=gallery_group_id)

    # Standard, LOF Force, and LAW common bases offer real locations.
    # Render the selector below the image with no visible label.
    if location_options:
        st.selectbox(
            "Base location",
            options=list(location_options),
            format_func=lambda gid: (location_label_func or location_label)(
                location_options[gid]
            ),
            key=location_key,
            label_visibility="collapsed",
        )
    elif reserve_location_space:
        # Match the height of the location selector in mixed rows, so
        # all Add Base buttons line up without showing an empty dropdown.
        st.markdown(
            '<div style="height: 40px" aria-hidden="true"></div>',
            unsafe_allow_html=True,
        )

    already_selected = (
        (st.session_state.get("swu_selected_base") or {}).get("uuid")
        in by_id
    )
    st.button(
        "✓ Added" if already_selected else "Add Base",
        key=f"swu_add_base_{safe_id}",
        on_click=add_base,
        args=(current,),
        disabled=already_selected,
        type="secondary" if already_selected else "primary",
        use_container_width=True,
    )

# --------------------------------------------------
# Simplified base picker: common color/location choices
# --------------------------------------------------
PRIMARY_BASE_ASPECTS = ("Vigilance", "Command", "Aggression", "Cunning")
HOMEWORLD_PLANETS = ("Tatooine", "Naboo", "Kashyyyk", "Endor")
BASE_LIBRARY_FIELDS = (
    BASE_FIELDS + ",traits,keywords,base_search_text,base_ability_search_text,raw_data,"
    "aspect_vigilance,aspect_command,aspect_aggression,aspect_cunning,"
    "aspect_heroism,aspect_villainy"
)


def load_complete_base_library(db):
    """Small catalog, loaded in batches; never let PostgREST's 1000-row cap truncate it."""
    rows = []
    batch_size = 500
    for start in range(0, 25000, batch_size):
        result = (
            db.table(BASE_VIEW)
            .select(BASE_LIBRARY_FIELDS)
            .order("uuid")
            .range(start, start + batch_size - 1)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < batch_size:
            break
    else:
        raise RuntimeError("Base catalog exceeded 25,000 printings; increase fetch limit")

    grouped = defaultdict(list)
    for printing in rows:
        grouped[str(printing.get("gameplay_id") or printing["uuid"])].append(printing)

    canonical = []
    for versions in grouped.values():
        versions.sort(key=printing_sort_key)
        # Prefer the newest standard printing when it is relevant to homeworlds.
        representative = dict(versions[0])
        canonical.append(representative)
    canonical.sort(key=lambda b: (base_display_name(b).lower(), str(b["uuid"])))
    return canonical, dict(grouped)


def base_aspect_names(base):
    return set(str(a) for a in (base.get("aspects") or []))


def base_is_textless(base):
    return not str(base.get("base_ability_search_text") or "").strip()


def classify_base(base):
    """standard | neutral | special, retaining unusual textless bases in special."""
    aspect_set = base_aspect_names(base)
    if not aspect_set:
        return "neutral"
    if base_is_textless(base):
        if len(aspect_set) == 1 and next(iter(aspect_set)) in PRIMARY_BASE_ASPECTS:
            if int(base.get("hp") or 0) == 30:
                return "standard"
    return "special"


def base_planet(base):
    traits = {str(t).strip().casefold() for t in (base.get("traits") or [])}
    for planet in HOMEWORLD_PLANETS:
        if planet.casefold() in traits:
            return planet
    return None


def location_label(base):
    planet = base_planet(base)
    if planet:
        return planet
    # Older bases may not carry a planet trait. Show just their location/name,
    # without a promotional-set suffix or an "Other location" category prefix.
    name = base_display_name(base).strip()
    return re.sub(r"(?i)^other\s+location\s*[-–—:]\s*", "", name).strip()


# Legends of the Force has eight common 28-HP bases (two per aspect)
# with the identical Force token ability. Show one result per aspect and let
# the user choose either actual location/printing for their deck.
FORCE_COMMON_LOCATION_BY_NAME = {
    "nightsister lair": "Dathomir",
    "shadowed undercity": "Coruscant",
    "jedi temple": "Coruscant",
    "starlight temple": "Starlight Beacon",
    "fortress vader": "Mustafar",
    "strangled cliffs": "Dathomir",
    "crystal caves": "Ilum",
    "the holy city": "Jedha",
}


def is_force_common_base(base):
    """Identify the LOF common Force-token bases, not the rare 25-HP ones."""
    aspects = base_aspect_names(base)
    text = str(base.get("base_ability_search_text") or "").casefold()
    return (
        str(base.get("set_code") or "").upper() == "LOF"
        and str(base.get("rarity") or "").casefold() == "common"
        and int(base.get("hp") or 0) == 28
        and len(aspects) == 1
        and next(iter(aspects)) in PRIMARY_BASE_ASPECTS
        and "when a friendly force unit attacks" in text
        and "the force is with you" in text
    )


# These eight 27-HP LAW bases use a different named location for each color.
# The API can omit a separate subtitle; keep the planet names searchable.
LAW_COMMON_LOCATIONS = {
    "daimyo's palace": "Tatooine",
    "coaxium mine": "Kessel",
    "aldhani garrison": "Aldhani",
    "imperial command complex": "Lothal",
    "contested caverns": "Quarzite",
    "stygeon spire": "Stygeon Prime",
    "canto bight": "Cantonica",
    "partisan hideout": "Segra Milo",
}


def law_location_label(base):
    """Display LAW common-base locations alphabetically, with no set codes."""
    subtitle = str(base.get("subtitle") or "").strip()
    if subtitle:
        return subtitle
    name = str(base.get("name") or "").strip()
    short = re.split(r"\s+[-–—]\s+", name, maxsplit=1)[0].strip()
    return LAW_COMMON_LOCATIONS.get(short.casefold(), name)


def law_location_sort(base):
    return (
        law_location_label(base).casefold(),
        base_display_name(base).casefold(),
        str(base.get("uuid") or ""),
    )


def is_law_common_base(base):
    """Common 27-HP LAW bases with the ignore-one-aspect Epic Action."""
    text = str(base.get("base_ability_search_text") or "").casefold()
    aspects = base_aspect_names(base)
    return (
        str(base.get("set_code") or "").upper() == "LAW"
        and str(base.get("rarity") or "").casefold() == "common"
        and int(base.get("hp") or 0) == 27
        and len(aspects) == 1
        and next(iter(aspects)) in PRIMARY_BASE_ASPECTS
        and "play a card from your hand" in text
        and "ignoring 1" in text
        and "aspect" in text
    )


def force_location_label(base):
    """Label a Force base by location, not its building's full card name."""
    subtitle = str(base.get("subtitle") or "").strip()
    if subtitle:
        return subtitle
    raw_name = str(base.get("name") or "").strip()
    # Covers imports where the location is part of the name string.
    simple_name = re.split(r"\s+[-–—]\s+", raw_name, maxsplit=1)[0].strip()
    return FORCE_COMMON_LOCATION_BY_NAME.get(simple_name.casefold(), raw_name)


def force_location_sort(base):
    return (force_location_label(base).casefold(), base_display_name(base).casefold())


def standard_location_sort(base):
    # Sort exactly as the dropdown is displayed, alphabetically by location.
    # Use the set only as a hidden tie-breaker if names are identical.
    planet = base_planet(base)
    return (
        location_label(base).casefold(),
        0 if (base.get("set_code") or "").upper() == "HMW" and planet else 1,
        base_display_name(base).casefold(),
        str(base.get("uuid") or ""),
    )


def leader_primary_aspects(session):
    """Read selected Leader, ignoring Heroism/Villainy even for mixed-aspect leaders."""
    leaders = []
    single = session.get("swu_selected_leader")
    if isinstance(single, dict):
        leaders.append(single)
    multi = session.get("swu_selected_leaders")
    if isinstance(multi, list):
        leaders.extend(x for x in multi if isinstance(x, dict))
    return {
        aspect for leader in leaders
        for aspect in (leader.get("aspects") or [])
        if aspect in PRIMARY_BASE_ASPECTS
    }


def base_name_and_location_text(base):
    """Search the printed base name AND the planet/location (not ability text)."""
    raw = base.get("raw_data") or {}
    if not isinstance(raw, dict):
        raw = {}
    parts = [
        base.get("name"),
        base.get("subtitle"),  # The base location on most SWU data feeds
        base.get("base_search_text"),
        base_planet(base),
        location_label(base),
        law_location_label(base),
        force_location_label(base),
    ]
    for key in ("location", "planet", "world", "basePlanet", "baseLocation"):
        value = raw.get(key)
        if isinstance(value, str):
            parts.append(value)
    # Fallback for older printings with the location absent from the API.
    if str(base.get("name") or "").strip().casefold() == "lake country":
        parts.append("Naboo")
    return " ".join(str(part) for part in parts if part).casefold()


def matches_base_filters(base, filters):
    """In-memory filter; applied after grouping so all variants remain available."""
    name_and_location = base_name_and_location_text(base)
    ability = str(base.get("base_ability_search_text") or "").casefold()
    if filters.get("name") and filters["name"].strip().casefold() not in name_and_location:
        return False
    if filters.get("ability") and filters["ability"].casefold() not in ability:
        return False
    if filters.get("exclude_ability") and filters["exclude_ability"].casefold() in ability:
        return False

    hp = base.get("hp")
    low, high = filters.get("min_hp", 0), filters.get("max_hp", 100)
    if hp is None:
        if low > 0 or high < filters.get("absolute_max_hp", high):
            return False
    elif (low > 0 and hp < low) or (high < filters.get("absolute_max_hp", high) and hp > high):
        return False

    mode = filters.get("mode", "Deck Compatibility")
    chosen = set(filters.get("selected_aspects") or [])
    aspects = base_aspect_names(base)
    if mode == "Exclude Selected":
        # Preserve the existing convention: no selected colors means non-neutral.
        if chosen and (chosen & aspects):
            return False
        if not chosen and not aspects:
            return False
    elif mode == "Exact":
        # Match the precise aspect combination, including no aspects.
        # No selected colors means only colorless bases.
        if chosen != aspects:
            return False
    elif mode == "Deck Compatibility":
        if not aspects.issubset(chosen):
            return False
    else:
        raise ValueError(f"Unknown base aspect filter mode: {mode}")

    traits = set(base.get("traits") or [])
    keywords = set(base.get("keywords") or [])
    if filters.get("traits") and not (traits & set(filters["traits"])):
        return False
    if filters.get("keywords") and not (keywords & set(filters["keywords"])):
        return False
    if filters.get("sets") and base.get("set_code") not in filters["sets"]:
        return False
    if filters.get("rarities") and base.get("rarity") not in filters["rarities"]:
        return False
    return True


def first_matching_printing(base, grouped, filters):
    """Honor Set/rarity filters even when a different printing was canonical."""
    group = str(base.get("gameplay_id") or base["uuid"])
    for printing in grouped.get(group, [base]):
        if matches_base_filters(printing, filters):
            return printing
    return None


# --------------------------------------------------
# Unified, ordered Base search results
# --------------------------------------------------

def build_base_search_results(all_bases, all_printings, filters_config):
    """Standard colors first, then colorless, then Force bases and other abilities.

    Each standard-color result retains its location choices, so the user selects
    a *real* printed base card without flooding results with all locations.
    All results participate in the same pagination, and no card group is shown
    twice in this list.
    """
    standard = {aspect: [] for aspect in PRIMARY_BASE_ASPECTS}
    force = {aspect: [] for aspect in PRIMARY_BASE_ASPECTS}
    law = {aspect: [] for aspect in PRIMARY_BASE_ASPECTS}
    neutral = []
    special = []

    for base in all_bases:
        category = classify_base(base)
        if category == "standard":
            aspect = next(iter(base_aspect_names(base)))
            standard[aspect].append(base)
        elif is_force_common_base(base):
            aspect = next(iter(base_aspect_names(base)))
            force[aspect].append(base)
        elif is_law_common_base(base):
            aspect = next(iter(base_aspect_names(base)))
            law[aspect].append(base)
        elif category == "neutral":
            neutral.append(base)
        else:
            special.append(base)

    results = []

    # First: up to four colored standard bases, one choice per color.
    for aspect in PRIMARY_BASE_ASPECTS:
        choices = sorted(
            (
                matched
                for base in standard[aspect]
                if (matched := first_matching_printing(
                    base, all_printings, filters_config
                )) is not None
            ),
            key=standard_location_sort,
        )
        if choices:
            results.append({
                "kind": "standard",
                "aspect": aspect,
                "choices": choices,
            })

    # Second: colorless bases must obey the active aspect filter too.
    # Deck Compatibility includes them. Exact includes them only when no
    # aspects are selected. Exclude Selected excludes them when no colors
    # are selected, as in the Cards tab.
    for base in neutral:
        matching = first_matching_printing(base, all_printings, filters_config)
        if matching is not None:
            results.append({"kind": "neutral", "base": matching})

    # Next: one searchable Force-token common base per aspect, each with its
    # two locations (if both survive the active search filters).
    for aspect in PRIMARY_BASE_ASPECTS:
        choices = sorted(
            (
                matched
                for base in force[aspect]
                if (matched := first_matching_printing(
                    base, all_printings, filters_config
                )) is not None
            ),
            key=force_location_sort,
        )
        if choices:
            results.append({
                "kind": "force",
                "aspect": aspect,
                "choices": choices,
            })

    # Common LAW bases: two real selectable locations per aspect.
    for aspect in PRIMARY_BASE_ASPECTS:
        choices = sorted(
            (
                matched
                for base in law[aspect]
                if (matched := first_matching_printing(
                    base, all_printings, filters_config
                )) is not None
            ),
            key=law_location_sort,
        )
        if choices:
            results.append({
                "kind": "law",
                "aspect": aspect,
                "choices": choices,
            })

    # Last: every other base (ability-bearing or unusually configured).
    for base in special:
        matching = first_matching_printing(base, all_printings, filters_config)
        if matching is not None:
            results.append({"kind": "special", "base": matching})

    return results

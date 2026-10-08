"""SWUDB-style Twin Suns JSON export/import via database-resolved card IDs.

External schema: metadata, leader, secondleader, base, deck, sideboard.
Never trust card properties supplied by an uploaded file; resolve every ID
against the project's existing typed Supabase views before restoring state.
"""
import json
import re
from collections import defaultdict

from swu_grouping import printing_id, printing_sort_key
from swu_twin_suns import card_identity, card_copy_limit, leaders_can_pair

MAX_JSON_BYTES = 2_000_000
MAX_DECK_ENTRIES = 500
MAX_COPIES = 1000
CARD_FIELDS = (
    "uuid,gameplay_id,name,subtitle,set_code,collector_number,card_type,"
    "arena,cost,power,hp,rarity,aspects,traits,keywords,rules_text,"
    "front_image_url,variant_type"
)
LEADER_FIELDS = (
    "uuid,gameplay_id,name,subtitle,set_code,collector_number,card_type,"
    "aspects,front_image_url,back_image_url,variant_type"
)
BASE_FIELDS = (
    "uuid,gameplay_id,name,subtitle,set_code,collector_number,card_type,"
    "aspects,hp,front_image_url,back_image_url,variant_type"
)
CARD_ID_PATTERN = re.compile(r"^([A-Z][A-Z0-9]{1,9})_(\d{1,5})$", re.I)


def normalize_id(text):
    """Match numerical collector IDs with or without zero-padding."""
    if not isinstance(text, str):
        raise ValueError("Every entry must have an ID such as SOR_173 or JTL_095.")
    found = CARD_ID_PATTERN.fullmatch(text.strip().upper())
    if not found:
        raise ValueError(f"Invalid card ID {text!r}. Expected SET_NUMBER, e.g. JTL_095.")
    return (found.group(1).upper(), int(found.group(2)))


def _export_entry(card, quantity=1):
    value = printing_id(card)
    normalize_id(value)
    return {"id": value, "count": quantity}


def export_swudb(snapshot, author=""):
    """Export a native deck snapshot using the exact SWUDB-style shape."""
    leaders = snapshot.get("leaders") or []
    base = snapshot.get("base")
    if len(leaders) != 2 or not base:
        raise ValueError("Select two leaders and a base to export a SWUDB deck.")
    cards = snapshot.get("cards") or []
    deck = [_export_entry(row["card"], row["count"]) for row in cards]
    deck.sort(key=lambda item: normalize_id(item["id"]))
    result = {
        "metadata": {
            "name": str(snapshot.get("name") or "Untitled Twin Suns Deck"),
            "author": str(author or ""),
        },
        "leader": _export_entry(leaders[0]),
        "secondleader": _export_entry(leaders[1]),
        "base": _export_entry(base),
        "deck": deck,
        "sideboard": [],
    }
    return json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8")


def _entry(obj, field, exact_one=False):
    if not isinstance(obj, dict):
        raise ValueError(f"{field} must be an object with 'id' and 'count'.")
    identifier = obj.get("id")
    normalize_id(identifier)
    count = obj.get("count")
    if type(count) is not int or not (1 <= count <= MAX_COPIES):
        raise ValueError(f"Invalid count for {identifier}: expected 1–{MAX_COPIES}.")
    if exact_one and count != 1:
        raise ValueError(f"{field} must have count 1.")
    return identifier.strip().upper(), count


def parse_swudb_json(raw_bytes):
    """Shape validation without any DB access; no session changes."""
    if not isinstance(raw_bytes, bytes) or len(raw_bytes) > MAX_JSON_BYTES:
        raise ValueError("Upload a JSON file smaller than 2 MB.")
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The uploaded file isn't valid UTF-8 JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("The deck JSON must contain an object.")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("SWUDB JSON requires metadata.name and metadata.author.")
    name = metadata.get("name")
    author = metadata.get("author", "")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 80):
        raise ValueError("Deck name must have 1–80 characters.")
    if not isinstance(author, str) or len(author) > 120:
        raise ValueError("Author must be a text value up to 120 characters.")
    leader = _entry(payload.get("leader"), "leader", exact_one=True)
    second = _entry(payload.get("secondleader"), "secondleader", exact_one=True)
    base = _entry(payload.get("base"), "base", exact_one=True)
    deck = payload.get("deck")
    if not isinstance(deck, list) or len(deck) > MAX_DECK_ENTRIES:
        raise ValueError("Deck must be a list of at most 500 entries.")
    entries = [_entry(x, "deck entry") for x in deck]
    sideboard = payload.get("sideboard", [])
    if not isinstance(sideboard, list):
        raise ValueError("sideboard must be a list.")
    if sideboard:
        raise ValueError("Twin Suns has no sideboard here; remove sideboard entries before importing to avoid data loss.")
    return {
        "name": name.strip(), "author": author.strip(),
        "leaders": [leader, second], "base": base, "deck": entries,
    }


def _matching_rows(db, view, fields, requested_ids):
    """Retrieve only requested cards, with a set-scoped fallback for odd IDs.

    Multiple physical printings may share a SET_NUMBER. Prefer Standard.
    """
    grouped = defaultdict(set)
    for identifier in requested_ids:
        code, number = normalize_id(identifier)
        grouped[code].add(number)
    rows = defaultdict(list)
    for set_code, numbers in grouped.items():
        possible = set()
        for n in numbers:
            # Numeric values work whether collector_number is text or numeric.
            # Prefixed collector_number values are handled by the fallback.
            possible.update((str(n), str(n).zfill(3), str(n).zfill(4)))
        for i in range(0, len(possible_list := sorted(possible)), 80):
            part = possible_list[i:i + 80]
            result = (db.table(view).select(fields).eq("set_code", set_code)
                      .in_("collector_number", part).limit(1000).execute())
            for row in result.data or []:
                try:
                    key = normalize_id(printing_id(row))
                except ValueError:
                    continue
                if key[0] == set_code and key[1] in numbers:
                    rows[key].append(row)
        missing = {(set_code, n) for n in numbers} - set(rows)
        if missing:
            # Fallback for rare encoded number formats. Read only the needed set.
            offset = 0
            while missing:
                result = (db.table(view).select(fields).eq("set_code", set_code)
                          .order("uuid").range(offset, offset + 999).execute())
                batch = result.data or []
                for row in batch:
                    try:
                        key = normalize_id(printing_id(row))
                    except ValueError:
                        continue
                    if key in missing:
                        rows[key].append(row)
                if len(batch) < 1000:
                    break
                offset += len(batch)
                if offset > 10000:
                    break
    return {key: min(values, key=printing_sort_key) for key, values in rows.items() if values}


def resolve_swudb(db, parsed):
    """Resolve IDs to the same rows the galleries use, then validate whole deck."""
    requested_leaders = [x[0] for x in parsed["leaders"]]
    leader_rows = _matching_rows(db, "swu_grouped_leaders", LEADER_FIELDS, requested_leaders)
    base_rows = _matching_rows(db, "swu_grouped_bases", BASE_FIELDS, [parsed["base"][0]])
    card_rows = _matching_rows(db, "swu_grouped_cards", CARD_FIELDS,
                               [x[0] for x in parsed["deck"]])

    def lookup(rows, identifier):
        return rows.get(normalize_id(identifier))

    missing = []
    for value in requested_leaders:
        if not lookup(leader_rows, value):
            missing.append(value)
    if not lookup(base_rows, parsed["base"][0]):
        missing.append(parsed["base"][0])
    for value, _ in parsed["deck"]:
        if not lookup(card_rows, value):
            missing.append(value)
    if missing:
        sample = ", ".join(sorted(set(missing))[:20])
        more = "..." if len(set(missing)) > 20 else ""
        raise ValueError(f"These IDs were not found in your SWU database ({len(set(missing))}): {sample}{more}. No changes made.")

    leaders = [lookup(leader_rows, value) for value in requested_leaders]
    allowed, reason = leaders_can_pair([leaders[0]], leaders[1])
    if not allowed:
        raise ValueError("Invalid Twin Suns leader pair: " + reason)
    base = lookup(base_rows, parsed["base"][0])
    entries = {}
    for identifier, count in parsed["deck"]:
        card = lookup(card_rows, identifier)
        gid = card_identity(card)
        if gid in entries:
            entries[gid]["count"] += count
        else:
            entries[gid] = {"card": card, "count": count}
    for entry in entries.values():
        limit = card_copy_limit(entry["card"])
        if limit is not None and entry["count"] > limit:
            raise ValueError(
                f"Copy limit exceeded for {entry['card'].get('name')}: "
                f"{entry['count']} copies; allowed {limit}. No changes made."
            )
    return {
        "version": 1, "format": "Twin Suns", "name": parsed["name"],
        "author": parsed["author"],
        "leaders": leaders, "base": base,
        "cards": list(entries.values()),
    }

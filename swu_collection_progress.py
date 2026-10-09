"""Physical Twin Suns deck progress, independent of SWUDB card printings.

Session/native-save data is keyed by gameplay_id. SWUDB JSON extension uses
SET_NUMBER printing IDs so the plain deck and its progress travel together.
"""

PROGRESS_SESSION_KEY = "swu_card_progress"
EXTENSION_NAME = "swuCardLibrary"
EXTENSION_VERSION = 1
MAX_PROGRESS_COUNT = 1000


def game_id(card):
    return str(card.get("gameplay_id") or card.get("uuid") or "")


def required_cards(leaders, base, entries):
    """Mapping game identity -> (card, needed count), including leaders/base."""
    result = {}
    for leader in leaders or []:
        result[game_id(leader)] = (leader, 1)
    if base:
        result[game_id(base)] = (base, 1)
    for entry in entries or []:
        card = entry["card"]
        gid = game_id(card)
        if gid in result:
            existing, qty = result[gid]
            result[gid] = (existing, qty + int(entry["count"]))
        else:
            result[gid] = (card, int(entry["count"]))
    return result


def _number(n, field):
    if type(n) is not int or not (0 <= n <= MAX_PROGRESS_COUNT):
        raise ValueError(f"{field} must be a nonnegative whole number (maximum 1000).")
    return n


def normalize_record(value, required, strict=False):
    if not isinstance(value, dict):
        raise ValueError("Each card's progress must be an object.")
    physical = _number(value.get("inDeck", 0), "inDeck")
    elsewhere = _number(value.get("ownedElsewhere", 0), "ownedElsewhere")
    if strict and physical + elsewhere > required:
        raise ValueError("Card progress exceeds the quantity required by the deck.")
    physical = min(physical, required)
    elsewhere = min(elsewhere, required - physical)
    return {"inDeck": physical, "ownedElsewhere": elsewhere}


def normalize_progress_map(raw, required, strict=False):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("card_progress must be an object.")
    result = {}
    for gid, (_, qty) in required.items():
        if gid in raw:
            result[gid] = normalize_record(raw[gid], qty, strict=strict)
    return result


def get_progress(session, gid, required):
    raw = session.get(PROGRESS_SESSION_KEY) or {}
    return normalize_record(raw.get(gid, {}), required)


def set_progress(session, gid, required, in_deck, owned_elsewhere, changed="inDeck"):
    in_deck = _number(in_deck, "inDeck")
    owned_elsewhere = _number(owned_elsewhere, "ownedElsewhere")
    if changed == "ownedElsewhere":
        owned_elsewhere = min(owned_elsewhere, required)
        in_deck = min(in_deck, required - owned_elsewhere)
    else:
        in_deck = min(in_deck, required)
        owned_elsewhere = min(owned_elsewhere, required - in_deck)
    record = {"inDeck": in_deck, "ownedElsewhere": owned_elsewhere}
    session.setdefault(PROGRESS_SESSION_KEY, {})[gid] = record
    return record


def needs_to_buy(required, record):
    return max(0, required - record["inDeck"] - record["ownedElsewhere"])


def progress_totals(required, progress):
    total = physical = elsewhere = missing = 0
    for gid, (_, qty) in required.items():
        record = normalize_record(progress.get(gid, {}), qty)
        total += qty
        physical += record["inDeck"]
        elsewhere += record["ownedElsewhere"]
        missing += needs_to_buy(qty, record)
    return {"required": total, "inDeck": physical,
            "ownedElsewhere": elsewhere, "needToBuy": missing}

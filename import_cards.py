
import os
import uuid
import requests
from collections import defaultdict
from supabase import create_client

API_URL = "https://api.swuapi.com/export/all"
SWU_DB_URL = "https://api.swu-db.com/cards"
BATCH_SIZE = 100

DOUBLE_COLORS = {
    "Vigilance", "Command", "Aggression", "Cunning"
}


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def optional_int(value):
    if value is None or value == "":
        return None
    return int(value)


def text_list(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Expected an array")
    return [str(item) for item in value]


def normalize(value):
    return " ".join(str(value or "").split()).casefold()


def convert_set(item):
    return {
        "code": item["code"],
        "name": item["name"],
        "release_date": item.get("release_date"),
        "total_cards": optional_int(item.get("total_cards")),
        "raw_data": item
    }


def convert_card(card):
    card_uuid = str(uuid.UUID(str(card["uuid"])))

    collector = (
        card.get("collector_number")
        or card.get("id")
    )

    set_code = (
        card.get("setCode")
        or card.get("set_code")
    )

    if not set_code and collector:
        set_code = collector.split("_")[0]

    number = (
        card.get("cardNumber")
        or card.get("card_number")
    )

    return {
        "uuid": card_uuid,
        "external_id": optional_int(card.get("external_id")),
        "collector_number": collector,
        "name": card["name"],
        "subtitle": card.get("subtitle"),
        "set_code": set_code,
        "card_number": (
            str(number) if number is not None else None
        ),
        "card_type": card.get("type"),
        "card_type2": card.get("type2"),
        "rarity": card.get("rarity"),
        "arena": card.get("arena"),
        "cost": optional_int(card.get("cost")),
        "power": optional_int(card.get("power")),
        "hp": optional_int(card.get("hp")),
        "aspects": text_list(card.get("aspects")),
        "traits": text_list(card.get("traits")),
        "keywords": text_list(card.get("keywords")),
        "rules_text": card.get("text"),
        "variant_type": card.get("variantType"),
        "front_image_url": card.get("frontImageUrl"),
        "back_image_url": card.get("backImageUrl"),
        "source_updated_at": (
            card.get("updated_at")
            or card.get("updatedAt")
        ),
        "raw_data": card
    }


# ---------------------------------------------
# SWU-DB: DETECT DOUBLE ASPECTS ONLY
# ---------------------------------------------

def get_double_aspect_overrides(sets, card_rows):
    # Match all printings of the same gameplay card.
    card_index = defaultdict(list)

    for card in card_rows:
        key = (
            str(card["set_code"] or "").upper(),
            normalize(card["name"]),
            normalize(card["subtitle"])
        )
        card_index[key].append(card)

    overrides = {}

    with requests.Session() as session:
        for set_info in sets:
            code = str(set_info["code"]).upper()

            response = session.get(
                f"{SWU_DB_URL}/{code.lower()}",
                params={"format": "json"},
                timeout=(15, 90)
            )

            if response.status_code in (400, 404):
                print(f"SWU-DB set unavailable: {code}")
                continue

            response.raise_for_status()
            payload = response.json()

            if isinstance(payload, list):
                cards = payload
            elif isinstance(payload, dict):
                cards = payload.get("data")
                if cards is None:
                    cards = payload.get("cards")
            else:
                cards = None

            if not isinstance(cards, list):
                raise ValueError(
                    f"Unexpected SWU-DB response for {code}"
                )

            for card in cards:
                aspects = []

                for icon in card.get("Aspects") or []:
                    if isinstance(icon, dict):
                        icon = icon.get("S")

                    if isinstance(icon, str):
                        aspects.append(icon.strip())

                # We only care about EXACTLY two
                # identical colored aspect icons.
                if len(aspects) != 2:
                    continue

                color = aspects[0]

                if (
                    color not in DOUBLE_COLORS
                    or aspects[1] != color
                ):
                    continue

                key = (
                    code,
                    normalize(card.get("Name")),
                    normalize(card.get("Subtitle"))
                )

                for original in card_index.get(key, []):
                    source_aspects = original["aspects"]

                    # Only supplement one matching icon.
                    # Never replace other aspect data.
                    if source_aspects != [color]:
                        continue

                    card_uuid = original["uuid"]

                    overrides[card_uuid] = {
                        "card_uuid": card_uuid,
                        "aspect_color": color,
                        "source": "SWU-DB"
                    }

            print(f"Checked SWU-DB set: {code}")

    return list(overrides.values())


# ---------------------------------------------
# DATABASE IMPORT
# ---------------------------------------------

def import_batches(database, table, rows, conflict_column):
    if not rows:
        return

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]

        database.table(table).upsert(
            batch,
            on_conflict=conflict_column
        ).execute()

        print(
            f"{table}: "
            f"{start + len(batch)}/{len(rows)}"
        )


def main():
    url = required_env("SUPABASE_URL")
    secret = required_env("SUPABASE_SECRET_KEY")

    print("Downloading SWU API export...")

    response = requests.get(
        API_URL, timeout=(15, 180)
    )
    response.raise_for_status()

    payload = response.json()

    cards = payload.get("cards")
    sets = payload.get("sets")

    if not isinstance(cards, list) or not cards:
        raise ValueError("Invalid card export")

    if not isinstance(sets, list) or not sets:
        raise ValueError("Invalid set export")

    set_rows = [convert_set(s) for s in sets]
    card_rows = [convert_card(c) for c in cards]

    if len({c["uuid"] for c in card_rows}) != len(card_rows):
        raise ValueError("Duplicate UUIDs in export")

    print(f"Downloaded {len(card_rows)} printings")

    # Supplemental source: double-aspect flags ONLY.
    overrides = get_double_aspect_overrides(
        sets, card_rows
    )

    print(
        f"Confirmed {len(overrides)} printings "
        "with double aspects"
    )

    # Known card verification before writing.
    aggression_cards = [
        c for c in card_rows
        if c["collector_number"] == "SOR_155"
        and c["name"] == "Aggression"
        and c["aspects"] == ["Aggression"]
    ]

    override_ids = {
        o["card_uuid"] for o in overrides
    }

    if not aggression_cards or any(
        c["uuid"] not in override_ids
        for c in aggression_cards
    ):
        raise ValueError(
            "SWU-DB double-aspect validation failed "
            "for SOR_155. No database changes made."
        )

    database = create_client(url, secret)

    # Primary data stays unchanged.
    import_batches(
        database, "card_sets", set_rows, "code"
    )

    import_batches(
        database, "card_printings", card_rows, "uuid"
    )

    # Corrections are in a separate table.
    import_batches(
        database,
        "card_double_aspect_overrides",
        overrides,
        "card_uuid"
    )

    print("Import completed successfully!")


if __name__ == "__main__":
    main()

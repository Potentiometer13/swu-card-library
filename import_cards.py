
import os
import uuid
import requests
from supabase import create_client

API_URL = "https://api.swuapi.com/export/all"
BATCH_SIZE = 100


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
        raise ValueError("Expected an array of text values")
    return [str(item) for item in value]


def convert_set(item):
    code = item.get("code")
    name = item.get("name")

    if not code or not name:
        raise ValueError(f"Set missing code or name: {item}")

    return {
        "code": code,
        "name": name,
        "release_date": item.get("release_date"),
        "total_cards": optional_int(item.get("total_cards")),
        "raw_data": item,
    }


def convert_card(card):
    card_uuid = card.get("uuid")
    if not card_uuid:
        raise ValueError(f"Card has no UUID: {card.get('name')}")

    # Verify the identifier is a valid UUID
    card_uuid = str(uuid.UUID(str(card_uuid)))

    name = card.get("name")
    if not name:
        raise ValueError(f"Card has no name: {card_uuid}")

    collector = card.get("collector_number") or card.get("id")
    set_code = card.get("setCode") or card.get("set_code")
    number = card.get("cardNumber") or card.get("card_number")

    if not set_code and isinstance(collector, str):
        set_code = collector.split("_")[0]

    return {
        "uuid": card_uuid,
        "external_id": optional_int(card.get("external_id")),
        "collector_number": collector,
        "name": name,
        "subtitle": card.get("subtitle"),
        "set_code": set_code,
        "card_number": str(number) if number is not None else None,
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
            card.get("updated_at") or card.get("updatedAt")
        ),
        "raw_data": card,
    }


def import_batches(database, table, rows, conflict_column):
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]

        database.table(table).upsert(
            batch,
            on_conflict=conflict_column,
        ).execute()

        print(f"{table}: {start + len(batch)}/{len(rows)} imported")


def main():
    url = required_env("SUPABASE_URL")
    secret_key = required_env("SUPABASE_SECRET_KEY")

    print("Downloading Star Wars Unlimited card data...")

    response = requests.get(API_URL, timeout=(15, 180))
    response.raise_for_status()
    payload = response.json()

    cards = payload.get("cards")
    sets = payload.get("sets")

    if not isinstance(cards, list) or not cards:
        raise ValueError("API did not return a valid card list")

    if not isinstance(sets, list) or not sets:
        raise ValueError("API did not return a valid set list")

    print(f"Downloaded {len(cards)} card printings")
    print(f"Downloaded {len(sets)} sets")

    # Validate all records before writing anything
    set_rows = [convert_set(item) for item in sets]
    card_rows = [convert_card(card) for card in cards]

    unique_uuids = {card["uuid"] for card in card_rows}
    if len(unique_uuids) != len(card_rows):
        raise ValueError("Duplicate UUIDs found in API export")

    database = create_client(url, secret_key)

    # Import sets first, then individual printings
    import_batches(database, "card_sets", set_rows, "code")
    import_batches(database, "card_printings", card_rows, "uuid")

    print("Import completed successfully!")


if __name__ == "__main__":
    main()

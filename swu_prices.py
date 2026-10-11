"""Daily, server-side TCGCSV cache and conservative printing-to-product matching."""
from collections import defaultdict
import json
import math
from pathlib import Path
import re
import threading
import time

import requests

BASE_URL = 'https://tcgcsv.com'
CATEGORY_ID = 79  # Star Wars Unlimited, verified against the categories endpoint.
CACHE_PATH = Path(__file__).parent / '.cache' / 'tcgcsv-prices.json'
_LOCK = threading.Lock()
_MEMORY = None
_CHECKED = 0
_ERROR = None


def _get(session, path, json_response=True):
    time.sleep(0.1)  # TCGCSV usage guidelines require spacing requests.
    response = session.get(BASE_URL + path, timeout=(10, 30))
    response.raise_for_status()
    if not json_response:
        return response.text.strip()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(payload.get('results'), list):
        raise ValueError('Unexpected TCGCSV response')
    return payload['results']


def build_records(group, products, prices):
    """Join TCGCSV products and market prices; exclude sealed/unidentified products."""
    by_id = {p['productId']: p for p in products}
    records = []
    for price in prices:
        product = by_id.get(price.get('productId'))
        amount = price.get('marketPrice')
        if not product or isinstance(amount, bool) or not isinstance(amount, (int, float)):
            continue
        if not math.isfinite(amount) or amount < 0:
            continue
        extended = {e['name']: e.get('value') for e in product.get('extendedData', [])}
        number = str(extended.get('Number') or '').split('/')[0].strip()
        if not number or not extended.get('CardType'):
            continue
        records.append(dict(set_code=str(group.get('abbreviation') or '').upper(),
                            number=number, name=product['name'],
                            subtype=price.get('subTypeName'), market_price=amount,
                            product_id=product['productId']))
    return records


def sync_catalog():
    """Pull at most daily, check build timestamp first, and atomically retain good data."""
    existing = None
    if CACHE_PATH.exists():
        existing = json.loads(CACHE_PATH.read_text())
        if time.time() - existing.get('checked_at', 0) < 86400:
            return existing
    with requests.Session() as session:
        session.headers['User-Agent'] = 'SWU-Card-Library/1.0'
        updated = _get(session, '/last-updated.txt', False)
        if existing and existing.get('source_updated') == updated:
            catalog = {**existing, 'checked_at': time.time()}
        else:
            groups = _get(session, f'/tcgplayer/{CATEGORY_ID}/groups')
            records = []
            for group in groups:
                gid = int(group['groupId'])
                products = _get(session, f'/tcgplayer/{CATEGORY_ID}/{gid}/products')
                prices = _get(session, f'/tcgplayer/{CATEGORY_ID}/{gid}/prices')
                records.extend(build_records(group, products, prices))
            if not records:
                raise ValueError('TCGCSV returned no usable SWU prices')
            catalog = dict(source_updated=updated, checked_at=time.time(), records=records)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(catalog))
    temporary.replace(CACHE_PATH)
    return catalog


def get_catalog():
    global _MEMORY, _CHECKED, _ERROR
    with _LOCK:
        if _MEMORY is not None and time.monotonic() - _CHECKED < 300:
            return _MEMORY
        try:
            _MEMORY = sync_catalog()
            _ERROR = None
        except (requests.RequestException, ValueError, OSError, KeyError, TypeError) as error:
            _ERROR = str(error)
            if _MEMORY is None:
                try:
                    _MEMORY = json.loads(CACHE_PATH.read_text())
                except (ValueError, OSError):
                    _MEMORY = {'records': [], 'source_updated': None}
        _CHECKED = time.monotonic()
        return _MEMORY


def price_status():
    catalog = get_catalog()
    return catalog.get('source_updated'), _ERROR


def _name(value):
    value = re.sub(r'\s*\([^)]*\)\s*$', '', str(value or ''))
    return re.sub(r'[^a-z0-9]', '', value.casefold())


def _index(records):
    index = defaultdict(list)
    for row in records:
        number = row['number'].lstrip('0') or '0'
        index[(row['set_code'], number, _name(row['name']))].append(row)
    return index


def price_for_card(card, catalog=None):
    catalog = get_catalog() if catalog is None else catalog
    number = str(card.get('collector_number') or '').split('_')[-1].split('/')[0]
    title = str(card.get('name') or '')
    if card.get('subtitle'):
        title += ' - ' + str(card['subtitle'])
    key = (str(card.get('set_code') or '').upper(), number.lstrip('0') or '0', _name(title))
    if '_price_index' not in catalog:
        catalog['_price_index'] = _index(catalog.get('records', []))
    candidates = catalog['_price_index'].get(key, [])
    variant = str(card.get('variant_type') or '').casefold()
    # A foil quote must never be substituted for a nonfoil printing or vice versa.
    subtype = 'Foil' if 'foil' in variant else 'Normal'
    candidates = [row for row in candidates if row['subtype'] == subtype]
    return min((row['market_price'] for row in candidates), default=None)


def cheapest_printing(printings, catalog=None):
    catalog = get_catalog() if catalog is None else catalog
    priced = [(price_for_card(p, catalog), p) for p in printings]
    priced = [(price, p) for price, p in priced if price is not None]
    return min(priced, key=lambda item: (item[0], str(item[1]['uuid'])))[1] if priced else None


def price_text(card, catalog=None):
    price = price_for_card(card, catalog)
    return f'${price:.2f}' if price is not None else '—'


if __name__ == '__main__':
    catalog = sync_catalog()
    print(f"Cached {len(catalog['records'])} USD market prices; source updated {catalog['source_updated']}")

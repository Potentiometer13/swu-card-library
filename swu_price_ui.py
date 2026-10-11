"""Printing selection for priced deck cards; no changes to quantities or ownership."""
import streamlit as st
from swu_prices import cheapest_printing, get_catalog, price_status

PRINTING_FIELDS = ('uuid', 'set_code', 'collector_number', 'variant_type', 'front_image_url')


@st.cache_data(ttl=86400)
def load_deck_printings(_db, gameplay_ids):
    grouped = {}
    for offset in range(0, len(gameplay_ids), 25):
        subset = gameplay_ids[offset:offset + 25]
        start = 0
        while True:
            rows = (_db.table('swu_grouped_cards').select('*')
                    .in_('gameplay_id', list(subset)).order('uuid')
                    .range(start, start + 999).execute().data or [])
            for row in rows:
                grouped.setdefault(str(row['gameplay_id']), []).append(row)
            if len(rows) < 1000:
                break
            start += len(rows)
    return grouped


def apply_printing(session, gid, printing, mode):
    for key in ('swu_twin_suns_cards', 'swu_twin_suns_sideboard'):
        entry = (session.get(key) or {}).get(gid)
        if entry and not (mode == 'auto' and entry['card'].get('price_selection') == 'manual'):
            entry['card'] = {**entry['card'],
                             **{k: printing.get(k) for k in PRINTING_FIELDS},
                             'price_selection': mode}


def prepare_deck_prices(session, db):
    entries = {**(session.get('swu_twin_suns_cards') or {}),
               **(session.get('swu_twin_suns_sideboard') or {})}
    if not entries:
        return
    catalog = get_catalog()
    try:
        options = load_deck_printings(db, tuple(sorted(entries)))
    except Exception:
        st.warning('Printing options are unavailable; existing card selections are preserved.')
        return
    session['swu_deck_printing_options'] = options
    for gid, entry in entries.items():
        manual_entry = next((zone[gid] for zone in
                             (session.get('swu_twin_suns_cards') or {},
                              session.get('swu_twin_suns_sideboard') or {})
                             if gid in zone and zone[gid]['card'].get('price_selection') == 'manual'), None)
        if manual_entry:
            continue
        if session.get(f'swu_printing_manual_{gid}'):
            chosen = next((p for p in options.get(gid, [])
                           if str(p['uuid']) == session.get(f'swu_printing_choice_{gid}')), None)
            if chosen:
                apply_printing(session, gid, chosen, 'manual')
            continue
        chosen = cheapest_printing(options.get(gid, []), catalog)
        if chosen:
            apply_printing(session, gid, chosen, 'auto')
            session[f'swu_printing_choice_{gid}'] = str(chosen['uuid'])
    updated, error = price_status()
    if error:
        st.warning('TCGCSV refresh failed. Showing cached prices where available; — means no quote.')
    st.caption(f'TCGplayer market price per copy (USD), via TCGCSV · {updated or "No price data"}. '
               'Cheapest priced printing by default; click a card image to choose another.')

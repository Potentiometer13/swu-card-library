"""Offline tests for price matching, default printing selection and saved choices."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import swu_price_ui
from swu_prices import build_records, cheapest_printing, price_for_card, price_text
from swu_deck_storage import normalize_snapshot, restore_snapshot, snapshot_from_session
from swu_twin_suns import add_card, add_sideboard_card, move_to_sideboard


def card(uid, number='087', variant='Standard'):
    return dict(uuid=uid, gameplay_id='vader', name='Darth Vader',
                subtitle='Commanding the First Legion', set_code='SOR',
                collector_number=number, variant_type=variant,
                card_type='Unit', rules_text='A deck can have up to 15 copies of this card.',
                front_image_url='https://example.org/card.jpg')


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.normal = card('normal')
        self.foil = card('foil', variant='Standard Foil')
        self.hyper = card('hyper', '351', 'Hyperspace')
        self.catalog = {'records': [
            dict(set_code='SOR', number='087', name='Darth Vader - Commanding the First Legion',
                 subtype='Normal', market_price=6.35),
            dict(set_code='SOR', number='087', name='Darth Vader - Commanding the First Legion',
                 subtype='Foil', market_price=13.75),
            dict(set_code='SOR', number='351', name='Darth Vader - Commanding the First Legion (Hyperspace)',
                 subtype='Normal', market_price=14.88),
        ]}

    def test_exact_printing_and_finish(self):
        self.assertEqual(price_for_card(self.normal, self.catalog), 6.35)
        self.assertEqual(price_text(self.foil, self.catalog), '$13.75')
        self.assertEqual(price_for_card(self.hyper, self.catalog), 14.88)
        self.assertEqual(price_text({**self.normal, 'set_code': 'SHD'}, self.catalog), '—')
        self.assertEqual(price_text({**self.normal, 'name': 'Different card'}, self.catalog), '—')
        self.assertIsNone(price_for_card({**self.hyper, 'variant_type': 'Hyperspace Foil'}, self.catalog))
        self.assertEqual(price_for_card({**self.normal, 'collector_number': 'SOR_087'}, self.catalog), 6.35)

    def test_cheapest_and_missing_quotes(self):
        self.assertEqual(cheapest_printing([self.hyper, self.foil, self.normal], self.catalog), self.normal)
        self.assertIsNone(cheapest_printing([self.normal], {'records': []}))
        zero = deepcopy(self.catalog)
        zero['records'][1]['market_price'] = 0
        self.assertEqual(cheapest_printing([self.normal, self.foil], zero), self.foil)

    def test_product_price_join(self):
        products = [dict(productId=1, name='Example', extendedData=[
            dict(name='Number', value='007/252'), dict(name='CardType', value='Unit')]),
            dict(productId=2, name='Booster box', extendedData=[])]
        prices = [dict(productId=1, marketPrice=0, subTypeName='Normal'),
                  dict(productId=1, marketPrice=None, subTypeName='Foil'),
                  dict(productId=1, marketPrice=float('nan'), subTypeName='Normal'),
                  dict(productId=2, marketPrice=100, subTypeName='Normal')]
        result = build_records({'abbreviation': 'SOR'}, products, prices)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['market_price'], 0)
        self.assertEqual(result[0]['number'], '007')

    def test_auto_manual_and_saved_choice(self):
        session = {'swu_twin_suns_cards': {'vader': {'card': self.hyper, 'count': 2}},
                   'swu_card_progress': {'vader': {'inDeck': 1, 'ownedElsewhere': 1}}}
        fake_st = SimpleNamespace(caption=lambda *a: None, warning=lambda *a: None)
        with patch.object(swu_price_ui, 'st', fake_st), \
             patch.object(swu_price_ui, 'get_catalog', return_value=self.catalog), \
             patch.object(swu_price_ui, 'price_status', return_value=('test build', None)), \
             patch.object(swu_price_ui, 'load_deck_printings', return_value={
                 'vader': [self.hyper, self.foil, self.normal]}):
            swu_price_ui.prepare_deck_prices(session, object())
            self.assertEqual(session['swu_twin_suns_cards']['vader']['card']['uuid'], 'normal')
            swu_price_ui.apply_printing(session, 'vader', self.foil, 'manual')
            swu_price_ui.prepare_deck_prices(session, object())
            chosen = session['swu_twin_suns_cards']['vader']['card']
            self.assertEqual(chosen['uuid'], 'foil')
            self.assertEqual(session['swu_card_progress']['vader']['inDeck'], 1)
            self.assertEqual(session['swu_twin_suns_cards']['vader']['count'], 2)
            add_card(session, {**self.normal, 'price_selection': 'auto'})
            self.assertEqual(session['swu_twin_suns_cards']['vader']['card']['uuid'], 'foil')
            move_to_sideboard(session, 'vader')
            add_sideboard_card(session, {**self.normal, 'price_selection': 'auto'})
            self.assertEqual(session['swu_twin_suns_sideboard']['vader']['card']['uuid'], 'foil')
            snapshot = snapshot_from_session(session, 'Saved choice')
            restored = {'swu_printing_manual_vader': True, 'swu_printing_choice_vader': 'normal'}
            restore_snapshot(restored, snapshot)
            self.assertNotIn('swu_printing_manual_vader', restored)
            swu_price_ui.prepare_deck_prices(restored, object())
            self.assertEqual(restored['swu_twin_suns_cards']['vader']['card']['uuid'], 'foil')
            self.assertEqual(restored['swu_twin_suns_sideboard']['vader']['card']['uuid'], 'foil')

    def test_daily_cache_avoids_network_requests(self):
        import json
        import tempfile
        import time
        from pathlib import Path
        import swu_prices
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / 'prices.json'
            cached = dict(checked_at=time.time(), source_updated='test build', records=[])
            cache_path.write_text(json.dumps(cached))
            with patch.object(swu_prices, 'CACHE_PATH', cache_path), \
                 patch.object(swu_prices.requests, 'Session') as request_session:
                self.assertEqual(swu_prices.sync_catalog(), cached)
                request_session.assert_not_called()

    def test_streamlit_printing_selection(self):
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        script = """
import sys
sys.path.insert(0, ROOT)
import streamlit as st
from types import SimpleNamespace
from test_prices import card
from swu_twin_suns import render_deck_builder
rows = [card('normal'), card('foil', variant='Standard Foil'),
        card('hyper', '351', 'Hyperspace')]
class Query:
    def select(self, *a): return self
    def in_(self, *a): return self
    def order(self, *a): return self
    def range(self, *a): return self
    def execute(self): return SimpleNamespace(data=rows)
class DB:
    def table(self, *a): return Query()
if 'swu_twin_suns_cards' not in st.session_state:
    st.session_state['swu_twin_suns_cards'] = {'vader': {'card': rows[2], 'count': 1}}
render_deck_builder(st, DB())
""".replace('ROOT', repr(str(Path(__file__).parent)))
        with patch('swu_prices.get_catalog', return_value=self.catalog), \
             patch.object(swu_price_ui, 'get_catalog', return_value=self.catalog), \
             patch.object(swu_price_ui, 'price_status', return_value=('test build', None)):
            app = AppTest.from_string(script).run(timeout=15)
            self.assertFalse(app.exception)
            self.assertTrue(any(m.value == '$6.35' for m in app.markdown))
            app.button(key='swu_deck_art_card_normal').click().run()
            self.assertFalse(app.exception)
            app.selectbox(key='swu_printing_choice_vader').select('foil').run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['swu_twin_suns_cards']['vader']['card']['uuid'], 'foil')
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any(m.value == '$13.75' for m in app.markdown))
            app.button(key='swu_move_sb_vader').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['swu_twin_suns_sideboard']['vader']['card']['uuid'], 'foil')
            self.assertTrue(any(m.value == '$13.75' for m in app.markdown))

    def test_older_and_imported_printings_preserved(self):
        snapshot = dict(version=1, format='Twin Suns', name='Imported', leaders=[], base=None,
                        cards=[dict(card=self.hyper, count=1)])
        normalized = normalize_snapshot(snapshot)
        self.assertEqual(normalized['cards'][0]['card']['price_selection'], 'manual')


if __name__ == '__main__':
    unittest.main()

"""Offline tests for the Twin Suns deck-list text export."""
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent))
sys.modules.setdefault('streamlit', SimpleNamespace(dialog=lambda *a, **kw: (lambda f: f)))
from swu_twin_suns import official_deck_list_text

def card(name, typ='Unit', arena='Ground', cost=None, subtitle=''):
    return {'name':name, 'subtitle':subtitle, 'card_type':typ, 'arena':arena, 'cost':cost, 'gameplay_id':name}

def test_deck_list_order_count_and_subtitles():
    snapshot = {
        'name':'Two Men on a Moonbase',
        'leaders': [card('Leia Organa', 'Leader', subtitle='Alliance General'), card('Han Solo', 'Leader', subtitle='Audacious Smuggler')],
        'base':card('Lake Country','Base'),
        'cards': [
            {'card':card('Z-95', 'Unit','Space',2), 'count':1},
            {'card':card('Ground Five', 'Unit','Ground',5), 'count':1},
            {'card':card('Upgrade', 'Upgrade',cost=2), 'count':1},
            {'card':card('Ground One', 'Unit','Ground',1), 'count':2},
            {'card':card('First Event', 'Event',cost=1,subtitle='Subtitle'), 'count':1},
            {'card':card('Alpha Space', 'Unit','Space',2), 'count':1},
        ],
    }
    expected = '''Deck Name: Two Men on a Moonbase
Leader 1: Leia Organa - Alliance General
Leader 2: Han Solo - Audacious Smuggler
Base: Lake Country

Main Deck Total: 7
2 Ground One
1 Ground Five
1 Alpha Space
1 Z-95
1 First Event - Subtitle
1 Upgrade

Sideboard Total: 0
'''
    assert official_deck_list_text(snapshot) == expected
    print('PASS: headers, total, category order, numeric cost sorting and subtitle formatting')

def test_empty_and_sideboard():
    blank = official_deck_list_text({'name':'Empty','leaders':[], 'base':None,'cards':[]})
    assert 'Leader 1: Not selected' in blank
    assert 'Main Deck Total: 0\n\nSideboard Total: 0\n' in blank
    sideboard = official_deck_list_text({'name':'SB','leaders':[], 'base':None,'cards':[],
       'sideboard':[{'card':card('A Side', 'Event', cost=2), 'count':2}]})
    assert sideboard.endswith('Sideboard Total: 2\n2 A Side\n')
    print('PASS: empty deck and forward-compatible sideboard section')

def test_exclude_sideboard():
    snapshot = {'name': 'Main only', 'leaders': [], 'base': None,
                'cards': [{'card': card('Main Card'), 'count': 1}],
                'sideboard': [{'card': card('Side Card'), 'count': 2}]}
    text = official_deck_list_text(snapshot, include_sideboard=False)
    assert '1 Main Card\n' in text
    assert 'Sideboard' not in text and 'Side Card' not in text
    assert '2 Side Card\n' in official_deck_list_text(snapshot)
    print('PASS: sideboard inclusion option preserves main-deck export')

if __name__ == '__main__':
    test_deck_list_order_count_and_subtitles()
    test_empty_and_sideboard()
    test_exclude_sideboard()

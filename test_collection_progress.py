"""Offline integration checks for the unified SWUDB JSON + physical deck progress."""
import sys, json
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
sys.modules.setdefault('streamlit', SimpleNamespace(dialog=lambda *a, **kw: (lambda f: f)))

from swu_collection_progress import (
    required_cards, progress_totals, needs_to_buy, get_progress, set_progress,
    PROGRESS_SESSION_KEY,
)
from swu_deck_storage import normalize_snapshot, snapshot_from_session, restore_snapshot
from swu_swudb_json import export_swudb, parse_swudb_json, resolve_swudb
from swu_twin_suns import _on_progress_widget_change, tcgplayer_missing_text, remove_card


def fake(typ, code, number, name, aspects=None, gid=None, rules=''):
    return dict(uuid=f'{typ}{code}{number}', gameplay_id=gid or f'g-{name}',
                name=name, subtitle='', set_code=code, collector_number=number,
                card_type=typ, aspects=aspects or [], rules_text=rules,
                variant_type='Standard', front_image_url='https://example.org/img.png')

l1 = fake('Leader','JTL','008','Leader1',['Command'])
l2 = fake('Leader','JTL','012','Leader2',['Heroism'])
base = fake('Base','SHD','026','Base1',['Vigilance'])
c1 = fake('Unit','SOR','173','Unit1',['Command'])
c2 = fake('Event','JTL','095','Event1')
vulture = fake('Unit','JTL','077','Swarming Vulture Droid',rules='A deck can have up to 15 copies of this card.')
rows = {'swu_grouped_leaders':[l1,l2], 'swu_grouped_bases':[base], 'swu_grouped_cards':[c1,c2,vulture]}
class Query:
    def __init__(self, items): self.items=list(items)
    def select(self,*args): return self
    def eq(self,key,val): self.items=[x for x in self.items if x.get(key)==val]; return self
    def in_(self,key,values): self.items=[x for x in self.items if str(x.get(key)) in values]; return self
    def limit(self,n): self.items=self.items[:n]; return self
    def order(self,*args): return self
    def range(self,a,b): self.items=self.items[a:b+1]; return self
    def execute(self): return SimpleNamespace(data=self.items)
class DB:
    def table(self,name): return Query(rows[name])


def test_export_import_round_trip():
    snap = {
        'version':1,'format':'Twin Suns','name':'Example','author':'Potentiometer13',
        'leaders':[l1,l2], 'base':base,
        'cards':[{'card':c1,'count':1},{'card':c2,'count':1},{'card':vulture,'count':4}],
        'card_progress':{
            'g-Leader1': {'inDeck':1},
            'g-Base1': {'ownedElsewhere':1},
            'g-Unit1': {'inDeck':1},
            'g-Swarming Vulture Droid': {'inDeck':2,'ownedElsewhere':1},
        }
    }
    original = export_swudb(normalize_snapshot(snap),snap['author'])
    document = json.loads(original)
    assert list(document) == ['metadata','leader','secondleader','base','deck','sideboard','swuCardLibrary']
    assert document['swuCardLibrary']['version'] == 1
    assert document['swuCardLibrary']['cardProgress']['JTL_077'] == {'inDeck':2,'ownedElsewhere':1}
    assert document['swuCardLibrary']['cardProgress']['JTL_008'] == {'inDeck':1}
    parsed = parse_swudb_json(original)
    resolved = resolve_swudb(DB(),parsed)
    assert resolved['card_progress']['g-Swarming Vulture Droid'] == {'inDeck':2,'ownedElsewhere':1}
    session = {'swu_progress_in_g-Unit1':False, 'swu_progress_owned_g-Unit1':True}
    restore_snapshot(session,resolved)
    assert 'swu_progress_in_g-Unit1' not in session
    roundtrip = export_swudb(snapshot_from_session(session,'Example'),session['swu_deck_author'])
    assert json.loads(roundtrip) == document
    print('PASS: SWUDB + custom progress round trip (incl leaders, base, multi-copy exceptions)')


def test_pure_swudb_import():
    json_doc = json.loads(export_swudb({'name':'Vanilla','leaders':[l1,l2],'base':base,'cards':[{'card':c1,'count':1}]},'me'))
    json_doc.pop('swuCardLibrary')
    snap = resolve_swudb(DB(),parse_swudb_json(json.dumps(json_doc).encode()))
    assert snap['card_progress'] == {}
    print('PASS: imports plain SWUDB JSON without extension')


def test_invalid_progress_fails_safely():
    doc = json.loads(export_swudb({'name':'Invalid','leaders':[l1,l2],'base':base,'cards':[{'card':c1,'count':1}]},'me'))
    p = doc['swuCardLibrary']['cardProgress']
    p['SOR_173']={'inDeck':2}
    try:
        resolve_swudb(DB(),parse_swudb_json(json.dumps(doc).encode()))
    except ValueError as error:
        assert 'exceeds' in str(error)
    else:
        raise AssertionError('Excess physical card count accepted')
    p['SOR_173']={'inDeck':True}
    try:
        parse_swudb_json(json.dumps(doc).encode())
    except ValueError as error:
        assert 'whole number' in str(error)
    else:
        raise AssertionError('Boolean physical count accepted')
    p['SOR_173']={'inDeck':0}
    p['SOR_999']={'inDeck':1}
    try:
        parse_swudb_json(json.dumps(doc).encode())
    except ValueError as error:
        assert 'not in the deck' in str(error)
    else:
        raise AssertionError('Orphan progress accepted')
    print('PASS: corrupted/unknown progress is rejected before altering a deck')


def test_counts_and_widgets():
    required = required_cards([l1,l2],base,[{'card':c1,'count':1},{'card':vulture,'count':4}])
    session={}
    set_progress(session,'g-Unit1',1,1,0)
    set_progress(session,'g-Swarming Vulture Droid',4,2,1)
    totals=progress_totals(required,session[PROGRESS_SESSION_KEY])
    assert totals == {'required':8,'inDeck':3,'ownedElsewhere':1,'needToBuy':4},totals
    assert tcgplayer_missing_text(required,session[PROGRESS_SESSION_KEY]) == '1 Base1\n1 Leader1\n1 Leader2\n1 Swarming Vulture Droid\n'
    session['swu_progress_in_g-Unit1']=True
    session['swu_progress_owned_g-Unit1']=True
    _on_progress_widget_change(session,'g-Unit1',1,'ownedElsewhere')
    assert get_progress(session,'g-Unit1',1)=={'inDeck':0,'ownedElsewhere':1}
    assert session['swu_progress_in_g-Unit1'] is False
    print('PASS: totals, need-to-buy text and conflicting checkbox selection')


def test_cloud_snapshot_compatibility():
    original={'version':1,'format':'Twin Suns','name':'Old online save','author':'me',
              'leaders':[l1,l2],'base':base,'cards':[{'card':c1,'count':1}]}
    normalized=normalize_snapshot(original)
    assert normalized['card_progress']=={}
    session={}
    restore_snapshot(session,normalized)
    assert session[PROGRESS_SESSION_KEY]=={}
    print('PASS: old online saves load without progress loss/error')


def test_remove_multi_copy_progress():
    q={'swu_twin_suns_cards':{'g-Swarming Vulture Droid':{'card':vulture,'count':3}}}
    set_progress(q,'g-Swarming Vulture Droid',3,2,1)
    q['swu_progress_in_g-Swarming Vulture Droid']=2
    q['swu_progress_owned_g-Swarming Vulture Droid']=1
    remove_card(q,'g-Swarming Vulture Droid')
    assert q['swu_twin_suns_cards']['g-Swarming Vulture Droid']['count']==2
    assert get_progress(q,'g-Swarming Vulture Droid',2)=={'inDeck':2,'ownedElsewhere':0}
    assert q['swu_progress_owned_g-Swarming Vulture Droid']==0
    remove_card(q,'g-Swarming Vulture Droid')
    remove_card(q,'g-Swarming Vulture Droid')
    assert 'g-Swarming Vulture Droid' not in q['swu_card_progress']
    assert 'swu_progress_in_g-Swarming Vulture Droid' not in q
    print('PASS: reducing and removing multi-copy cards keeps progress in bounds')


def test_sideboard_progress_round_trip():
    from swu_twin_suns import (move_to_sideboard, move_to_main_deck,
                               add_sideboard_card, SIDEBOARD_PROGRESS_KEY)
    session = {'swu_selected_leaders': [l1, l2], 'swu_selected_base': base,
               'swu_twin_suns_cards': {'g-Unit1': {'card': c1, 'count': 1}}}
    set_progress(session, 'g-Unit1', 1, 1, 0)
    assert move_to_sideboard(session, 'g-Unit1')[0]
    assert not session['swu_twin_suns_cards']
    assert session[SIDEBOARD_PROGRESS_KEY]['g-Unit1']['inDeck'] == 1
    assert not add_sideboard_card(session, c1)[0]
    snapshot = snapshot_from_session(session, 'Sideboard round trip')
    document = export_swudb(snapshot)
    resolved = resolve_swudb(DB(), parse_swudb_json(document))
    restored = {}
    restore_snapshot(restored, resolved)
    assert export_swudb(snapshot_from_session(restored, 'Sideboard round trip')) == document
    assert move_to_main_deck(restored, 'g-Unit1')[0]
    assert get_progress(restored, 'g-Unit1', 1)['inDeck'] == 1
    assert not restored['swu_twin_suns_sideboard']
    print('PASS: sideboard moves preserve progress, copy limits and JSON round trips')


if __name__ == '__main__':
    tests = [test_export_import_round_trip, test_pure_swudb_import,
             test_invalid_progress_fails_safely, test_counts_and_widgets,
             test_cloud_snapshot_compatibility, test_remove_multi_copy_progress,
             test_sideboard_progress_round_trip]
    for test in tests:
        test()
    print(f'ALL {len(tests)} TEST GROUPS PASSED')

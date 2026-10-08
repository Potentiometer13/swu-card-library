"""Offline contract tests: no external API or Supabase credentials required."""
import sys, types, ast
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).parent))
streamlit=types.ModuleType('streamlit')
streamlit.session_state={}
streamlit.dialog=lambda *a,**kw:(lambda fn:fn)
streamlit.cache_data=lambda *a,**kw:(lambda fn:fn)
sys.modules['streamlit']=streamlit
grouping=types.ModuleType('swu_grouping')
grouping.printing_id=lambda b:f"{b.get('set_code')}_{b.get('collector_number')}"
grouping.printing_sort_key=lambda b:(0 if b.get('variant_type')=='Standard' else 1,b.get('uuid',''))
sys.modules['swu_grouping']=grouping
import swu_bases as m

def make(aspect=None, name='Test Base', hp=30, text='',set_code='HMW',traits=None):
    return dict(uuid=name+'-'+str(aspect),gameplay_id=name+'-'+str(aspect),name=name,
     subtitle='',card_type='Base',set_code=set_code,variant_type='Standard',
     collector_number='022',front_image_url='https://example.com/a.jpg',
     aspects=[aspect] if aspect else [],hp=hp,rarity='Common',
     traits=traits or [],keywords=[],base_ability_search_text=text)

k=make('Command',name='Theed Palace',traits=['NABOO'])
assert m.base_planet(k)=='Naboo'
assert m.classify_base(k)=='standard'
assert m.location_label(k).startswith('Naboo —')
assert m.classify_base(make(name='Neutral'))=='neutral'
assert m.classify_base(make('Cunning',text='Action: draw a card'))=='special'
assert m.classify_base(make('Vigilance',hp=25))=='special'
print('PASS: standard, colorless and ability-bearing bases classified')

sel={'swu_selected_leader':{'gameplay_id':'A','aspects':['Villainy','Command']}}
assert m.leader_primary_aspects(sel)=={'Command'}
sel['swu_selected_leaders']=[{'aspects':['Heroism','Vigilance','Vigilance']}]
assert m.leader_primary_aspects(sel)=={'Command','Vigilance'}
print('PASS: default exclusion derives leader primary colors, ignoring alignments')

f=dict(name='',ability='',exclude_ability='',min_hp=0,max_hp=30,
       absolute_max_hp=30,mode='Exclude Selected',selected_aspects={'Command'},
       traits=[],keywords=[],sets=[],rarities=[])
assert not m.matches_base_filters(k,f)
assert m.matches_base_filters(make('Cunning'),f)
assert not m.matches_base_filters(make(name='Neutral'),dict(f,selected_aspects=set()))
assert m.matches_base_filters(make('Cunning'),dict(f,selected_aspects=set()))
print('PASS: Exclude Selected matches agreed aspect behavior')

versions=[make('Command',set_code='SOR'), make('Command',set_code='HMW')]
versions[1]['uuid']='variant'
versions[1]['gameplay_id']=versions[0]['gameplay_id']
f=dict(f,mode='All Selected',selected_aspects=set(),sets=['HMW'])
assert m.first_matching_printing(versions[0],{versions[0]['gameplay_id']:versions},f)['uuid']=='variant'
print('PASS: set filtering honors alternate printings')

app=Path(__file__).with_name('app.py').read_text()
ast.parse(app)
assert 'sync_base_filters_from_leader()' in app
assert '"swu_base_aspect_mode": "Exclude Selected"' in app
assert '"Bases per page", [100, 40, 20]' in app
assert '#### Standard bases' in app and '#### Colorless bases' in app
assert '#### Bases with abilities' in app
assert app.count('with leader_tab:')==1 and app.count('with card_tab:')==1
print('PASS: code compiles and original three tabs remain present')
# Validate synchronization semantics independently of Streamlit page execution.
script=ast.parse(app)
node=next(n for n in script.body if isinstance(n,ast.FunctionDef)
          and n.name=='sync_base_filters_from_leader')
namespace={'st':streamlit,'leader_primary_aspects':m.leader_primary_aspects,
           'BASE_ASPECTS':list(m.PRIMARY_BASE_ASPECTS),'reset_base_page':lambda:None}
exec(compile(ast.Module(body=[node],type_ignores=[]),'<function>','exec'),namespace)
sync=namespace['sync_base_filters_from_leader']
streamlit.session_state.clear()
streamlit.session_state['swu_selected_leader']={'gameplay_id':'g1','aspects':['Villainy','Command']}
sync()
assert streamlit.session_state['swu_base_aspect_command'] is True
assert all(not streamlit.session_state['swu_base_aspect_'+x.lower()] for x in
           ('Vigilance','Aggression','Cunning'))
assert streamlit.session_state['swu_base_aspect_mode']=='Exclude Selected'
streamlit.session_state['swu_base_aspect_command']=False
sync()
assert streamlit.session_state['swu_base_aspect_command'] is False
streamlit.session_state['swu_selected_leader']={'gameplay_id':'g2','aspects':['Heroism','Aggression']}
sync()
assert streamlit.session_state['swu_base_aspect_aggression'] is True
assert streamlit.session_state['swu_base_aspect_command'] is False
print('PASS: leader change reinitializes aspect exclusions; manual changes persist')

"""Explicit synthetic contract data. No live research or customer fixtures."""
from app.research_contracts import TYPES, TASKS

def reference(row):
    return {'kind':row['kind'],'id':row['id'],'version':row['version']}

def synthetic_fields(kind, **overrides):
    values={}
    for key,spec in TYPES[kind].items():
        if spec['type']=='number':value=0 if spec['required'] else None
        elif spec['type']=='checkbox':value=False
        elif spec['type']=='rows':value=[]
        elif spec['type']=='lines':value=['RS-00']
        elif spec['type']=='select':value=spec['options'][0]
        else:value='SYNTHETIC contract fixture; not observed research' if spec['required'] else ''
        values[key]=value
    if kind in ('metric','measurement'):values['unknown_reason']='No actual measurements in this fixture.'
    if kind=='evidence':values['evidence_type']='ASSUMPTION'
    if kind=='alternative':values['compared_options']=['Synthetic option A','Synthetic option B']
    values.update(overrides)
    return values

def item(service,user,kind,research_id='',refs=(),links=(),fields=None,review=True,title=None,task_id=None,**extra):
    task_id=task_id if task_id is not None else next((key for key,t in TASKS.items() if t[1]==kind),'')
    row=service.post(user,'/api/research-workspace/items',{'title':title or 'Synthetic '+kind,
        'output_type':kind,'task_id':task_id,'research_id':research_id,'fields':fields or synthetic_fields(kind),
        'input_refs':[reference(r) for r in refs], 'links':list(links), **extra})
    if review:
        row=service.post(user,'/api/research-workspace/items/review',{'item_id':row['id'],
            'expected_version':row['version'],'reason':'Synthetic contract review; not user validation.'})
    return row

def pack(service,user,brief,rows,select=True):
    value=service.post(user,'/api/research-workspace/packs',{'title':'Synthetic selected research',
        'research_id':brief['id'],'item_refs':[reference(r) for r in rows]})
    if select:service.post(user,'/api/research-workspace/select',{'pack_id':value['id'],'expected_version':value['version']})
    return value

def scope_with_source(service,user,source):
    brief=item(service,user,'brief')
    manifest=item(service,user,'source_manifest',brief['id'],refs=[source])
    return pack(service,user,brief,[manifest])

def graph(service,user,brief):
    rows=[]
    for kind in ('evidence','problem','story_candidate','requirement','acceptance','uat','metric'):
        links=[{**reference(rows[-1]),'relation':'derived_from','reason':'Synthetic trace relation for contract tests'}] if rows else []
        rows.append(item(service,user,kind,brief['id'],links=links))
    return rows

"""Validate a complete project before exposing any of it to the editor."""
from i18n import tr as ui_text
import copy,math
from pathlib import Path
import core

def validated_project(path):
    p=copy.deepcopy(core.read_json(path))
    if not isinstance(p,dict) or p.get('schema')!=1:raise ValueError(ui_text('不是支持的星谱工程（schema 1）'))
    def number(v,label,lo,hi):
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(ui_text('工程字段无效：')+label)
    for key in ('title','audio','model','source_sha256'):
        if not isinstance(p.get(key),str) or not p[key]:raise ValueError(ui_text('工程缺少有效字段：')+key)
    if not isinstance(p.get('revision'),int) or p['revision']<0:raise ValueError(ui_text('工程修订号无效'))
    number(p.get('duration'),'duration',.001,86400)
    if not isinstance(p.get('tracks'),list) or not isinstance(p.get('candidates'),list):raise ValueError(ui_text('工程声部/候选列表无效'))
    if not isinstance(p.get('tempo_info'),dict):raise ValueError(ui_text('工程速度信息无效'))
    p['tempo_info'].setdefault('warning','')
    tempos=p.get('tempos')
    if not isinstance(tempos,list) or not tempos:raise ValueError(ui_text('工程没有速度图'))
    last=-1
    for m in tempos:
        number(m.get('time'),'tempo time',0,p['duration']);number(m.get('tempo'),'tempo',1,16777215)
        if m['time']<=last:raise ValueError(ui_text('速度图必须按时间递增'))
        m['tempo']=int(m['tempo']);last=m['time']
    if tempos[0]['time']!=0:raise ValueError(ui_text('速度图必须从零开始'))
    ids=set();notes=set()
    for t in p['tracks']:
        if not isinstance(t,dict) or not isinstance(t.get('id'),str) or t['id'] in ids:raise ValueError(ui_text('声部 ID 缺失或重复'))
        ids.add(t['id'])
        if t.get('role') not in core.ROLES or not isinstance(t.get('name'),str):raise ValueError(ui_text('声部角色或名称无效'))
        number(t.get('program'),'program',0,128)
        if int(t['program'])!=t['program']:raise ValueError(ui_text('音源程序必须为整数'))
        t['program']=int(t['program'])
        if not isinstance(t.get('notes'),list):raise ValueError(ui_text('声部音符列表无效'))
        t.setdefault('instrument','unknown')
        for n in t['notes']:
            if not isinstance(n,dict) or not isinstance(n.get('id'),str) or n['id'] in notes:raise ValueError(ui_text('音符 ID 缺失或重复'))
            notes.add(n['id'])
            if n.get('track')!=t['id']:raise ValueError(ui_text('音符所属声部不一致'))
            number(n.get('pitch'),'pitch',0,127)
            if int(n['pitch'])!=n['pitch']:raise ValueError(ui_text('MIDI 音高必须为整数'))
            n['pitch']=int(n['pitch'])
            number(n.get('start'),'note start',0,p['duration']);number(n.get('end'),'note end',0,p['duration']+.1)
            if n['end']<=n['start']:raise ValueError(ui_text('音符结束必须晚于开始'))
            number(n.get('velocity',80),'velocity',1,127)
            if int(n.get('velocity',80))!=n.get('velocity',80):raise ValueError(ui_text('力度必须为整数'))
    for c in p['candidates']:
        if not isinstance(c,dict) or not isinstance(c.get('id'),str) or c.get('track') not in ids:raise ValueError(ui_text('碎音建议引用无效'))
        number(c.get('time'),'candidate time',0,p['duration'])
        if not isinstance(c.get('ids'),list) or len(c['ids'])!=2:raise ValueError(ui_text('碎音建议必须引用两个音符'))
    return p

def fingerprint(project):
    import json,hashlib
    return hashlib.sha256(json.dumps(project,sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()

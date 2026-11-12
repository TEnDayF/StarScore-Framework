"""Song metadata and export timing, independent of UI and model adapters."""
from __future__ import annotations
import copy, math
from pathlib import Path
import numpy as np


def defaults(title=''):
    return dict(title=title, artist='', notes='', key='', mode='auto', bpm=120.,
                numerator=4, denominator=4, first_beat=0., confirmed=False)


def validate(value):
    v=dict(defaults(), **value)
    for k in ('title','artist','notes','key'):
        if not isinstance(v[k],str):raise ValueError('Invalid song text: '+k)
        v[k]=v[k].strip()
    if v['mode'] not in ('auto','fixed','adaptive'):raise ValueError('Invalid tempo mode')
    for k,low,high in [('bpm',20,400),('first_beat',0,86400)]:
        v[k]=float(v[k])
        if not math.isfinite(v[k]) or not low<=v[k]<=high:raise ValueError('Invalid '+k)
    if int(v['numerator'])!=v['numerator'] or not 1<=v['numerator']<=32:raise ValueError('Invalid time signature')
    if v['denominator'] not in (1,2,4,8,16,32):raise ValueError('Invalid time signature')
    v['numerator']=int(v['numerator']);v['denominator']=int(v['denominator'])
    return v


def measure_array(x):
    """Pulse candidates, not a claim of musical meter or downbeat certainty."""
    import core
    from scipy import signal
    tempos,info,(tt,flux)=core.tempo_map(x,True)
    if len(tt)<250 or np.max(flux,initial=0)<.05:raise ValueError('Insufficient rhythmic evidence / 节奏证据不足')
    hop=float(np.median(np.diff(tt)))
    # Combine independent windows so silence/intro does not dominate the result.
    score=np.zeros(int(60/40/hop)+2);count=0
    for a in range(0,len(flux),int(12/hop)):
        y=flux[a:a+int(24/hop)];y=y-y.mean()
        if len(y)<int(60/40/hop)*2:continue
        ac=signal.correlate(y,y,mode='full',method='fft')[len(y)-1:]
        if ac[0]>1e-8:
            n=min(len(score),len(ac));score[:n]+=ac[:n]/ac[0];count+=1
    if not count:raise ValueError('Insufficient rhythmic evidence / 节奏证据不足')
    score/=count;lo=int(60/240/hop);hi=len(score)-1
    ids=signal.find_peaks(score[lo:hi])[0]+lo
    if not len(ids):raise ValueError('No stable pulse / 未找到稳定拍点')
    ranked=sorted(ids,key=lambda k:score[k],reverse=True)[:8];candidates=[]
    for k in ranked:
        denom=score[k-1]-2*score[k]+score[k+1]
        delta=.5*(score[k-1]-score[k+1])/denom if abs(denom)>1e-10 else 0
        bpm=60/((k+float(np.clip(delta,-.5,.5)))*hop)
        candidates.append({'bpm':round(bpm,3),'strength':round(float(max(0,score[k])),3)})
    chosen=next((c for c in candidates if 70<=c['bpm']<=200),candidates[0])
    bpm=chosen['bpm'];period=60/bpm
    phases=np.linspace(0,period,120,endpoint=False)
    scores=[np.mean(np.interp(np.arange(p,min(tt[-1],60),period),tt,flux)) for p in phases]
    phase=float(phases[int(np.argmax(scores))])
    return dict(bpm=bpm,candidates=candidates,confidence=chosen['strength'],first_beat=round(phase,3),
                meter='unknown',tempos=tempos,warning='候选拍点不保证是小节第一拍；拍号和半速/倍速需确认。 / Confirm meter, downbeat and half/double tempo.')


def measure_file(path):
    import core,subprocess
    result=core.checked_run([core.ffmpeg(),'-v','error','-i',str(path),'-f','f32le','-ac','1','-ar','16000','pipe:1'])
    return measure_array(np.frombuffer(result,dtype='<f4'))


def apply(project,value):
    v=validate(value);project['song_info']=v
    if v['title']:project['title']=v['title']
    project.setdefault('detected_tempos',copy.deepcopy(project['tempos']))
    if v['mode']=='fixed':project['tempos']=[{'time':0.,'tempo':round(60e6/v['bpm'])}]
    elif v.get('measurement',{}).get('tempos'):project['tempos']=copy.deepcopy(v['measurement']['tempos'])
    else:project['tempos']=copy.deepcopy(project['detected_tempos'])
    project['tempo_info']=dict(project.get('tempo_info',{}),mode=v['mode'],bpm=60e6/project['tempos'][0]['tempo'])
    return project


def export_view(project):
    """Add leading silence, never discard pickup notes or quantize performances."""
    import core
    p=copy.deepcopy(project)
    if not p.get('song_info'):return p
    apply(p,p['song_info']);v=p['song_info'];bar=960*v['numerator']*4/v['denominator']
    beat_tick=core.seconds_to_tick(v['first_beat'],p['tempos'])
    shift_tick=(math.ceil(beat_tick/bar-1e-9)*bar-beat_tick) if beat_tick else 0
    shift=max(0,shift_tick/960*p['tempos'][0]['tempo']/1e6)
    p['export_lead_in']=shift
    if shift:
        p['tempos']=[dict(p['tempos'][0],time=0.)]+[dict(m,time=m['time']+shift) for m in p['tempos'][1:]]
        for t in p['tracks']:
            for n in t['notes']:n['start']+=shift;n['end']+=shift
        p['duration']+=shift
    return p


def midi_metadata(meta,project):
    import mido
    v=validate(project.get('song_info',{}))
    meta.append(mido.MetaMessage('time_signature',numerator=v['numerator'],denominator=v['denominator'],time=0))
    for key in ('title','artist','key','notes'):
        text=v.get(key) or (project.get('title','') if key=='title' else '')
        if text:meta.append(mido.MetaMessage('text',text=key+': '+text,time=0))

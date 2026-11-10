"""Optional separation-first transcription; explicit stem provenance and sample assets.

UMXHQ estimates broad stems, not individual synthesizers. Do not treat these
labels or the following acoustic heuristics as calibrated ground truth.
"""
from __future__ import annotations
from i18n import tr as ui_text
import copy,json,math,os,subprocess,time,uuid,sys
from pathlib import Path
import numpy as np,soundfile as sf
from scipy import signal,ndimage
import core

REV='complex-2-umxhq'

def separate(audio,job,progress,cancel):
    from adapter_registry import separate as dispatch
    return dispatch(audio,job,progress,cancel)


def rekey_tracks(tracks,stem):
    for t in tracks:
        t['id']=stem+'_'+t['id'];t['source_stem']=stem;t['reference_audio']='stems/'+stem+'.wav'
        t['role_origin']='separated_source_unverified'
        if stem=='bass':t['role']='bass'
        t['name']=core.ROLES[t['role']]+' · '+t.get('instrument',t.get('name',ui_text('来源')))+' ['+stem+']'
        for n in t['notes']:n['id']=stem+'_'+n['id'];n['track']=t['id']
    return tracks

def estimate_velocity(tracks,x,sr):
    """Relative attack energy, not recovered original MIDI velocity."""
    for t in tracks:
        levels=[]
        for n in t['notes']:
            a=max(0,round(n['start']*sr));b=min(len(x),a+max(1,round(min(.075,n['end']-n['start'])*sr)))
            levels.append(float(np.sqrt(np.mean(x[a:b]**2))) if b>a else 0.)
        ref=max(float(np.percentile(levels,90)) if levels else 0.,1e-7)
        for n,v in zip(t['notes'],levels):
            n['velocity']=int(np.clip(round(105*(v/ref)**.45),25,120));n['velocity_origin']='stem_relative_energy'

def refine_drum_onsets(tracks,audio):
    """Bounded local attack refinement on estimated drum stem, never quantize."""
    x,sr=sf.read(audio,dtype='float32',always_2d=True);x=x.mean(axis=1);curves={}
    bands={'kick':(30,220),'snare':(200,7000),'hihat':(4500,15000),'cymbals':(2500,15000),'toms':(70,1800),'percussion':(150,12000)}
    for t in tracks:
        if t['role']!='drums':continue
        for n in t['notes']:
            family=core.drum_family(n['pitch'])[0]
            if family not in curves:
                lo,hi=bands[family];hi=min(hi,sr*.45)
                y=signal.sosfiltfilt(signal.butter(3,(lo,hi),btype='bandpass',fs=sr,output='sos'),x)
                width=max(1,round(.003*sr));power=ndimage.uniform_filter1d(y*y,width)
                curves[family]=np.maximum(0,power-np.r_[np.repeat(power[0],width),power[:-width]])
            curve=curves[family];a=max(0,round((n['start']-.06)*sr));b=min(len(x),round((n['start']+.025)*sr))
            if b<=a:continue
            v=curve[a:b];k=int(np.argmax(v))
            if v[k]<max(1e-9,float(np.median(v))*5):continue
            new=max(0,(a+k)/sr-.0015);delta=new-n['start']
            if abs(delta)>.0615:continue
            n['model_start']=n.get('model_start',n['start']);n['start']=round(new,6);n['end']=min(len(x)/sr,max(n['start']+.01,n['end']+delta))
            n['onset_origin']='local_stem_attack';n['onset_adjust_ms']=round(delta*1000,2)

def drum_assets(tracks,stem,job,cancel=lambda:False):
    """Pick isolated exemplars per GM class, band-limit only when all hits overlap.

    Does not pretend to unmix layered snares into their original sample layers.
    """
    x,sr=sf.read(stem,dtype='float32',always_2d=True);mono=x.mean(axis=1)
    allnotes=[n for t in tracks if t.get('drum') or t['role']=='drums' for n in core.active_notes(t)];folder=job/'drum_samples';folder.mkdir(exist_ok=True)
    rows=[]
    for t in tracks:
        if not (t.get('drum') or t['role']=='drums'):continue
        t['drum_samples']={}
        for pitch in sorted({n['pitch'] for n in core.active_notes(t)}):
            if cancel():raise core.Cancelled()
            notes=[n for n in core.active_notes(t) if n['pitch']==pitch]
            family,_=core.drum_family(pitch)
            def quality(n):
                neighbours=[o for o in allnotes if o['id']!=n['id']]
                isolated=not any(abs(o['start']-n['start'])<.055 for o in neighbours)
                gap=min([o['start']-n['start'] for o in neighbours if o['start']>n['start']+.055]+[1.])
                return (isolated,min(gap,.4),n['velocity'])
            n=max(notes,key=quality);isolated,gap,_=quality(n)
            length=min(.9 if family=='cymbals' or pitch==46 else .45,gap)
            if length<.07:continue
            start=max(0,round(n['start']*sr));end=min(len(x),start+round(length*sr));y=x[start:end].copy()
            if len(y)<256:continue
            if not isolated:
                band={'kick':(25,220),'snare':(180,10000),'hihat':(5000,18000),'toms':(65,1600),'cymbals':(2500,18000),'percussion':(150,16000)}[family]
                y=signal.sosfiltfilt(signal.butter(3,band,fs=sr,btype='bandpass',output='sos'),y,axis=0).astype(np.float32)
            peak=float(np.max(abs(y)))
            if peak<1e-5:continue
            # Preserve the event start. Fade only sub-millisecond attack and tail.
            edge=min(len(y)//4,round(.0007*sr));y[:edge]*=np.linspace(0,1,edge)[:,None]
            tail=min(len(y)//3,round(.015*sr));y[-tail:]*=np.linspace(1,0,tail)[:,None]
            y*=.7/max(float(np.max(abs(y))),1e-8)
            name=f'{t['id']}_GM{pitch}.wav';path=folder/name;sf.write(path,y,sr,subtype='PCM_24')
            rec={'file':'drum_samples/'+name,'sha256':core.sha256(path),'source_time':n['start'],
                 'kind':'isolated_estimated_stem' if isolated else 'band_limited_composite',
                 'warning':ui_text('估计鼓分离轨采样，可能含串音；不是原始鼓样')}
            t['drum_samples'][str(pitch)]=rec;rows.append(dict(track=t['id'],pitch=pitch,**rec))
    core.save_json(job/'鼓采样来源.json',rows)
    return rows

def transcribe_stems(job,model,duration,progress,cancel):
    tracks=[];allraw=[];devices={};rejected=[]
    for i,stem in enumerate(('bass','drums','other','vocals')):
        if cancel():raise core.Cancelled()
        x=core.decode(job/'stems'/(stem+'.wav'),job/(stem+'.f32'),cancel)
        if float(np.sqrt(np.mean(x*x)))<1e-5:continue
        raw,device=core.infer(job/(stem+'.f32'),job/(stem+'_raw.json'),model,
            lambda s,p:progress(ui_text('独立转写 ')+stem+' · '+s,(i+p)/4),cancel)
        devices[stem]=device
        accepted=[n for n in raw if bool(n.get('drum'))==(stem=='drums')]
        rejected.extend([dict(n,source_stem=stem,reason=ui_text('与分离来源类型不符，保留候选')) for n in raw if n not in accepted])
        allraw.extend([dict(n,source_stem=stem) for n in accepted])
        tt,_=core.classify_tracks(accepted,duration,core.audio_features(x),split_voices=stem not in ('bass','drums'))
        rekey_tracks(tt,stem);estimate_velocity(tt,x,16000);tracks.extend(tt)
    core.save_json(job/'raw_notes.json',allraw);core.save_json(job/'来源不一致候选.json',rejected)
    return tracks,allraw,devices

def fuse_mixture(tracks,mix_raw,job,duration,cancel=lambda:False):
    """Keep mix-only pitched events when stem AMT abstains; never replace with silence.

    Deduplicate only near-identical pitch/onset/duration events. Fundamental and
    harmonic evidence select a broad reference source, not an exact instrument.
    """
    waves={}
    for stem in ('bass','other','vocals'):
        x,sr=sf.read(job/'stems'/(stem+'.wav'),dtype='float32',always_2d=True);waves[stem]=(x.mean(axis=1),sr)
    existing=[n for t in tracks if t['role']!='drums' for n in t['notes']];groups={'bass':[],'other':[],'vocals':[]};duplicates=0
    for raw in mix_raw:
        if cancel():raise core.Cancelled()
        if raw.get('drum'):continue
        if any(n['pitch']==raw['pitch'] and abs(n['start']-raw['start'])<=.08 and
               min(n['end'],raw['end'])-max(n['start'],raw['start'])>=.5*min(n['end']-n['start'],raw['end']-raw['start']) for n in existing):
            duplicates+=1;continue
        hz=440*2**((raw['pitch']-69)/12);scores={}
        for stem,(x,sr) in waves.items():
            a=max(0,round(raw['start']*sr));z=min(len(x),a+round(min(.18,max(.06,raw['end']-raw['start']))*sr));v=x[a:z]
            if len(v)<128:scores[stem]=0.;continue
            size=2**int(math.ceil(math.log2(len(v)*2)));spec=np.abs(np.fft.rfft(v*np.hanning(len(v)),size));score=0.
            for h in range(1,7):
                c=round(hz*h*size/sr);width=max(1,round(hz*.025*size/sr))
                if c+width<len(spec):score+=float(np.max(spec[max(0,c-width):c+width+1]))/h
            scores[stem]=score
        source=max(scores,key=scores.get)
        # Avoid calling high pitched growls plain sub-bass solely from stem energy.
        if source=='bass' and raw['pitch']>65:source='other'
        n=copy.deepcopy(raw);n['reference_evidence']=scores;groups[source].append(n)
    added=0
    for stem,raw in groups.items():
        if not raw:continue
        x=core.decode(job/'stems'/(stem+'.wav'),job/(stem+'_fusion.f32'),cancel)
        tt,_=core.classify_tracks(raw,duration,core.audio_features(x))
        rekey_tracks(tt,'mix_'+stem)
        for t in tt:
            t.update(source_stem=stem,reference_audio='stems/'+stem+'.wav',role_origin='mixture_fallback_stem_evidence',
                     name=t['name'].replace('[mix_'+stem+']',ui_text('[混音补充 → ')+stem+']'))
            if stem=='bass':t['role']='bass';t['name']='Bass · '+t['instrument']+ui_text(' [混音补充 → bass]')
            elif t['role']=='bass':t['role']='unknown';t['name']=ui_text('待确认 · ')+t['instrument']+ui_text(' [混音补充 → ')+stem+']'
        estimate_velocity(tt,x,16000);added+=sum(len(t['notes']) for t in tt);tracks.extend(tt)
    # Dense hats/cymbals are often missed by stem AMT. Preserve independent mix
    # detections instead of shipping a recall regression as cleaner separation.
    percussion=[];drums=[n for t in tracks if t['role']=='drums' for n in t['notes']]
    for n in mix_raw:
        if not n.get('drum') or core.drum_family(n['pitch'])[0] not in ('hihat','cymbals','percussion'):continue
        family=core.drum_family(n['pitch'])[0]
        if any(core.drum_family(o['pitch'])[0]==family and abs(o['start']-n['start'])<.045 for o in drums):continue
        percussion.append(n)
    if percussion:
        x=core.decode(job/'stems/drums.wav',job/'drum_fusion.f32',cancel)
        tt,_=core.classify_tracks(percussion,duration,core.audio_features(x));rekey_tracks(tt,'mix_drums')
        for t in tt:t.update(source_stem='drums',reference_audio='stems/drums.wav',role_origin='mixture_percussion_supplement',name=t['name'].replace('[mix_drums]',ui_text('[混音补充 → drums]')))
        estimate_velocity(tt,x,16000);tracks.extend(tt)
    if any(t['role']=='drums' for t in tracks):
        refine_drum_onsets(tracks,job/'stems/drums.wav')
        x=core.decode(job/'stems/drums.wav',job/'drum_final.f32',cancel)
        estimate_velocity([t for t in tracks if t['role']=='drums'],x,16000)
        drum_assets(tracks,job/'stems/drums.wav',job,cancel)
    report={'mixture_pitched':sum(not n.get('drum') for n in mix_raw),'near_duplicate_skipped':duplicates,'mixture_supplement':added,'percussion_supplement':len(percussion),
            'policy':'Retain mixture-only pitches; reference-source energy is heuristic, not calibrated confidence.'}
    core.save_json(job/'混合转写说明.json',report);return report

def sample_for(track,pitch,folder):
    rec=track.get('drum_samples',{}).get(str(pitch))
    if not rec:return None
    path=Path(folder)/rec['file']
    if not path.is_file():raise FileNotFoundError(ui_text('工程鼓采样缺失，请恢复整个工程目录：')+str(path))
    if core.sha256(path)!=rec['sha256']:raise ValueError(ui_text('工程鼓采样已变化，请重新分析：')+str(path))
    return path

def modulation_profile(track,audio):
    """Conservative periodic-envelope candidate on long, isolated bass notes.

    At least three sustained windows must agree. Does not infer every wobble or
    sidechain pulse, and never merges MIDI attacks based on this estimate.
    """
    if track.get('role')!='bass':return None
    x,sr=sf.read(audio,dtype='float32',always_2d=True);x=x.mean(axis=1)
    ns=core.active_notes(track);rates=[];depths=[]
    for n in ns:
        if n['end']-n['start']<.8:continue
        if any(o['id']!=n['id'] and o['start']<n['end'] and o['end']>n['start'] for o in ns):continue
        a=round((n['start']+.04)*sr);b=min(len(x),round(n['end']*sr));v=x[a:b]
        hop=max(1,sr//100);count=len(v)//hop
        if count<70:continue
        env=np.sqrt(np.mean(v[:count*hop].reshape(count,hop)**2,axis=1));spread=np.percentile(env,90)-np.percentile(env,10)
        if spread<max(env.mean()*.25,1e-5):continue
        y=signal.detrend(env);ac=signal.correlate(y,y,mode='full')[len(y)-1:];ac/=max(ac[0],1e-9)
        peaks,_=signal.find_peaks(ac[8:min(51,len(ac)//2)]);peaks+=8
        if not len(peaks):continue
        lag=int(max(peaks,key=lambda k:ac[k]))
        if ac[lag]<.55 or count<lag*3:continue
        rates.append(100/lag);depths.append(float(spread/max(np.percentile(env,90),1e-8)))
        if len(rates)>=24:break
    if len(rates)<3:return None
    rate=float(np.median(rates));agree=[i for i,r in enumerate(rates) if abs(r-rate)/rate<.15]
    if len(agree)<3 or len(agree)/len(rates)<.7:return None
    return dict(rate_hz=rate,depth=min(.35,float(np.median([depths[i] for i in agree]))*.4),windows=len(agree),
                method='periodic_stem_envelope_candidate',warning=ui_text('周期包络候选；可能包含侧链，非原始 LFO 参数'))

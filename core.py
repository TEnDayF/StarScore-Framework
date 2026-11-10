"""Local transcription pipeline. No network calls. Audio seconds remain authoritative."""
from __future__ import annotations
from i18n import tr as ui_text
import copy, hashlib, html, json, math, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
import numpy as np
from scipy import signal, ndimage
import mido
import app_settings

VERSION = '0.10.1-framework'
ROLES = {'lead':'主旋律', 'counter':'副旋律', 'accompaniment':'和弦伴奏', 'bass':'Bass', 'drums':'鼓组', 'unknown':'未确定'}
from voice_tracking import melodic_path as anchored_path, register_anchor, bass_partition, drum_family
MODEL_HASHES = {'small':'925f55af65a20ebc4f8b45ceaf095a12b72493d436cb112623cd0041a1af23d4',
 'medium':'3850cc9e5b436b17a09bd25b8f2615cb3366ab96a71e7b50f73a793a917fdf03',
 'large':'35a750fb1ab1e77195cdc2c0b9b4aeea2f4d59f11f729f02af9920c4854ef72e'}
CREATE_NO_WINDOW = 0x08000000 if os.name=='nt' else 0

def root():
    return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent

def resource(name):
    settings=app_settings.load()
    for size,path in settings['model_paths'].items():
        if name==f'models/muscriptor-{size}-f16.gguf' and path:return Path(path)
    if settings["model_dir"] and (name=="models" or name.startswith("models/")):
        return Path(settings["model_dir"]) / name.removeprefix("models").lstrip("/")
    if name.startswith('models/') and not settings['model_dir']:
        return root()/'unconfigured-resources'/name
    p=root()/name
    if p.exists(): return p
    if not getattr(sys,'frozen',False) and os.environ.get('STARSCORE_RESOURCE_ROOT'):
        alternate=Path(os.environ['STARSCORE_RESOURCE_ROOT'])/name
        if alternate.exists():return alternate
    return Path(getattr(sys,'_MEIPASS',root()))/name

def sha256(p):
    with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def save_json(p, data):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.writing')
    with open(tmp,'w',encoding='utf-8') as f:
        json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,p)

def read_json(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def safe_name(s):return re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',s).strip('. ')[:80] or 'song'

def ffmpeg():
    from adapter_registry import require_file
    return str(require_file(app_settings.load()['ffmpeg_path'], 'FFmpeg'))

class Cancelled(Exception):pass

def checked_run(args,cancel=lambda:False):
    try:p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=CREATE_NO_WINDOW)
    except OSError as e:raise RuntimeError(ui_text('外部工具无法启动。请检查设置中的路径、文件权限及 Windows 版本兼容性。')+'\n'+str(args[0])+'\n'+str(e)) from e
    while True:
        try:
            out,err=p.communicate(timeout=.2);break
        except subprocess.TimeoutExpired:
            if cancel():p.kill();p.communicate();raise Cancelled()
    if p.returncode:raise RuntimeError(ui_text('音频工具执行失败。请先在设置中测试 FFmpeg，并检查音频是否损坏或格式不受支持。')+'\n'+err.decode('utf-8',errors='replace')[-2000:])
    return out

def decode(source,dest,cancel=lambda:False,start=None,duration=None):
    args=[ffmpeg(),'-v','error','-nostdin','-i',str(source)]
    if start is not None:args+=['-ss',str(start)]
    if duration is not None:args+=['-t',str(duration)]
    args+=['-ac','1','-ar','16000','-f','f32le','-y',str(dest)]
    checked_run(args,cancel)
    x=np.fromfile(dest,dtype='<f4')
    if not len(x) or not np.all(np.isfinite(x)):raise ValueError(ui_text('音频为空或包含无效采样'))
    return x

def infer(pcm,output,model,progress=lambda *a:None,cancel=lambda:False):
    from adapter_registry import transcribe
    return transcribe(pcm,output,model,progress,cancel)


def audio_features(x):
    # 10 ms time base, short-time spectral flux; not a confidence probability.
    _,tt,z=signal.stft(x,fs=16000,nperseg=1024,noverlap=864,boundary='zeros')
    mag=np.abs(z).astype(np.float32)
    flux=np.maximum(0,np.diff(np.log1p(30*mag),axis=1)).mean(axis=0)
    flux=np.r_[0,flux];flux=ndimage.gaussian_filter1d(flux,1)
    peak=float(np.percentile(flux,95)) if len(flux) else 0
    flux=flux/max(peak,1e-8)
    return tt,flux

def tempo_map(x,adaptive=True):
    tt,flux=audio_features(x);duration=len(x)/16000
    fallback=[{'time':0.,'tempo':500000}]
    if len(flux)<250 or np.max(flux)<.05:
        return fallback,{'mode':'reference','bpm':120.,'warning':ui_text('节奏证据不足：参考 120 BPM，保留原始秒时间')},(tt,flux)
    hop=float(np.median(np.diff(tt)));lo=int(60/200/hop);hi=int(60/65/hop)
    # Estimate local pulse using autocorrelation. Ambiguous half/double tempo remains a review item.
    windows=[]
    for sec in np.arange(0,duration,4.):
        a=max(0,int((sec-6)/hop));b=min(len(flux),int((sec+6)/hop));v=flux[a:b]
        if len(v)<hi+5:continue
        v=v-v.mean();ac=signal.correlate(v,v,mode='full',method='fft')[len(v)-1:]
        if ac[0]<=1e-9:continue
        ids,_=signal.find_peaks(ac[lo:hi]);ids=ids+lo
        if not len(ids):continue
        best=int(max(ids,key=lambda k:ac[k]/ac[0]));strength=float(ac[best]/ac[0])
        windows.append([float(sec),float(60/(best*hop)),strength])
    good=[w for w in windows if w[2]>.12]
    if not good:return fallback,{'mode':'reference','bpm':120.,'warning':ui_text('拍点模糊：采用参考速度，未量化')},(tt,flux)
    base=float(np.median([w[1] for w in good]));norm=[]
    for sec,bpm,strength in windows:
        while bpm>base*1.45:bpm/=2
        while bpm<base*.69:bpm*=2
        norm.append([sec,bpm if strength>.12 else base,strength])
    bpms=ndimage.median_filter(np.array([w[1] for w in norm]),size=min(5,len(norm)//2*2+1),mode='nearest')
    result=[];prev=None
    for w,bpm in zip(norm,bpms):
        bpm=float(np.clip(bpm,base*.8,base*1.2)) if adaptive else base
        if prev is not None and abs(bpm-prev)/prev<.012:continue
        result.append({'time':w[0],'tempo':int(round(60_000_000/bpm))});prev=bpm
    if not result:result=fallback
    result[0]['time']=0.
    info={'mode':'adaptive_estimate' if adaptive else 'reference','bpm':base,
          'segments':len(result),'warning':ui_text('速度图为音频估计，可能存在半速/倍速或相位歧义；音符未量化，实际秒时间保留。')}
    return result,info,(tt,flux)

def melodic_path(notes):
    """Weighted interval DAG, not 'highest pitch = melody'. Retains unused events elsewhere."""
    if not notes:return []
    ns=sorted(notes,key=lambda n:(n['start'],n['pitch']))
    scores=np.zeros(len(ns));parent=np.full(len(ns),-1,dtype=int)
    for i,n in enumerate(ns):
        dur=n['end']-n['start']
        weight=.48+.55*math.sqrt(min(dur,1.2))+.08*min(n.get('onset_evidence',0),2)
        weight+=.12 if 53<=n['pitch']<=88 else -.18
        if dur>2.0:weight-=.35  # sustained pads must not monopolise a melody path
        best=0.;pred=-1
        for j in range(i-1,-1,-1):
            p=ns[j];gap=n['start']-p['end']
            if n['start']-p['start']>8:break
            if p['start']>=n['start']-.018 or gap<-.055:continue
            cost=.035*abs(n['pitch']-p['pitch'])+.10*max(0,gap)+max(0,-gap)*3
            score=scores[j]-cost
            if score>best:best=score;pred=j
        scores[i]=weight+best;parent[i]=pred
    end=int(np.argmax(scores));path=[]
    while end>=0:path.append(ns[end]['id']);end=int(parent[end])
    return path[::-1]

def classify_tracks(raw,duration,features,split_voices=True):
    tt,flux=features;groups={};candidates=[];drums={}
    for i,n in enumerate(raw):
        if not (0<=int(n['pitch'])<=127):continue
        n=copy.deepcopy(n);n['id']=f'n{i:06}';n['start']=max(0.,float(n['start']));n['end']=min(duration,float(n['end']))
        if n.get('drum') and n['end']<=n['start']:n['end']=min(duration,n['start']+.06)
        if n['end']<=n['start']:continue
        n.update(velocity=80,velocity_origin='defaulted',locked=False,deleted=False,origin='predicted')
        if n.get('drum'):
            family,label=drum_family(n['pitch']);n.update(track='drum_'+family,candidate=False)
            drums.setdefault((family,label),[]).append(n);continue
        n['track']=f'source_{n['program']:03}'
        near=(tt>=n['start']-.04)&(tt<=n['start']+.05)
        n['onset_evidence']=round(float(np.max(flux[near])) if np.any(near) else 0.,3)
        # Only extremely short weak events are withheld. Never call this calibrated confidence.
        n['candidate']=(n['end']-n['start']<.045 and n['onset_evidence']<.2)
        groups.setdefault(n['program'],[]).append(n)
    tracks=[]
    for program,notes in sorted(groups.items()):
        notes.sort(key=lambda n:(n['start'],n['pitch']))
        med=float(np.median([n['pitch'] for n in notes]));active=[];poly=0
        for n in notes:
            active=[end for end in active if end>n['start']+.035]
            if active:poly+=1
            active.append(n['end'])
        overlap=poly/max(1,len(notes))
        if overlap>.35:role='accompaniment'
        elif (32<=program<=39 or med<49):role='bass'
        else:role='counter'
        tracks.append({'id':f'source_{program:03}','program':program,'instrument':notes[0].get('instrument',str(program)),
                       'role':role,'notes':notes,'overlap':round(overlap,3),'role_origin':'heuristic_unverified'})
        # Conservative *suggestions*, never destructive automatic merging.
        bypitch={}
        for n in notes:bypitch.setdefault(n['pitch'],[]).append(n)
        for same in bypitch.values():
            for a,b in zip(same,same[1:]):
                if 0<=b['start']-a['end']<=.09 and b['onset_evidence']<.35:
                    candidates.append({'id':uuid.uuid4().hex[:10],'kind':'merge','track':a['track'],
                        'ids':[a['id'],b['id']],'time':a['start'],'events':[[n['id'],n['pitch'],n['start'],n['end']] for n in (a,b)],'reason':ui_text('同音间隙短、后音起音较弱：可能是碎裂，也可能是真实重复'),'accepted':False})
    if split_voices:
        expanded=[]
        for t in tracks:
            notes=t['notes'];voice_floor=0
            if t['role']!='bass':
                bass,notes,profile=bass_partition(notes)
                if bass:
                    voice_floor=profile['upper']
                    bt={k:copy.deepcopy(v) for k,v in t.items() if k!='notes'}
                    bt.update(id=t['id']+'_bass',role='bass',notes=bass,source_group=t['id'],register_profile=profile,role_origin='register_unverified')
                    for n in bass:n['track']=bt['id']
                    expanded.append(bt);t['notes']=notes
            if t['role']=='accompaniment' and len(notes)>=24:
                remaining=list(notes)
                for v in range(2):
                    eligible=[n for n in remaining if not n.get('candidate') and n['pitch']>voice_floor]
                    anchor=register_anchor(eligible)
                    ids=set(anchored_path(eligible,anchor=anchor))
                    lane=[n for n in remaining if n['id'] in ids]
                    if len(lane)<10 or len(lane)<len(notes)*.035:break
                    nt={k:copy.deepcopy(val) for k,val in t.items() if k!='notes'}
                    nt.update(id=t['id']+f'_voice{v + 1}',role='counter',notes=lane,overlap=0.,
                              role_origin='anchored_path_unverified',source_group=t['id'],register_profile={'center':anchor,'span':12})
                    for n in lane:n['track']=nt['id']
                    expanded.append(nt);remaining=[n for n in remaining if n['id'] not in ids]
                t['notes']=remaining
            if t['notes']:expanded.append(t)
        tracks=expanded
        ownership={n['id']:t['id'] for t in tracks for n in t['notes']}
        candidates=[c for c in candidates if ownership.get(c['ids'][0])==ownership.get(c['ids'][1])]
        for c in candidates:c['track']=ownership[c['ids'][0]]
    possible=[t for t in tracks if t['role']=='counter']
    if possible:
        best=max(possible,key=lambda t:sum(n['end']-n['start'] for n in t['notes'])/(1+t['overlap']*5))
        best['role']='lead'
    for (family,label),notes in drums.items():
        tracks.append({'id':'drum_'+family,'program':128,'instrument':label,'role':'drums','drum':True,'notes':notes,'role_origin':'model_percussion','overlap':0.})
    for t in tracks:
        suffix=ui_text(' · 演奏路径 ')+t['id'][-1] if '_voice' in t['id'] else ''
        t['name']=f'{ROLES[t['role']]} · {t['instrument']}'+suffix
    order={'lead':0,'counter':1,'accompaniment':2,'bass':3,'drums':4,'unknown':5}
    tracks.sort(key=lambda t:order[t['role']])
    candidates=merge_candidates(tracks)
    return tracks,candidates

def merge_candidates(tracks):
    """Scan final voice ownership, never pairs from the original mixed source."""
    result=[]
    for t in tracks:
        if t['role']=='drums':continue
        bypitch={}
        for n in t['notes']:
            if n.get('deleted') or n.get('locked'):continue
            bypitch.setdefault(n['pitch'],[]).append(n)
        for ns in bypitch.values():
            ns.sort(key=lambda n:(n['start'],n['end']))
            for a,b in zip(ns,ns[1:]):
                check=b.get('repeat_attack_check',{})
                if check.get('protected') and check.get('start')==b['start'] and check.get('pitch')==b['pitch']:continue
                if 0<=b['start']-a['end']<=.09 and b.get('onset_evidence',1)<.35:
                    result.append({'id':'merge_'+a['id']+'_'+b['id'],'kind':'merge','track':t['id'],'ids':[a['id'],b['id']],
                                   'events':[[n['id'],n['pitch'],n['start'],n['end']] for n in (a,b)],'time':a['start'],
                                   'reason':ui_text('同音短间隙、后音起音弱；仍可能是真实重复击奏'),'accepted':False})
    return sorted(result,key=lambda c:(c['time'],c['id']))

def seconds_to_tick(sec,tempos,ppq=960):
    ticks=0.;prev=0.;tempo=tempos[0]['tempo']
    for m in tempos[1:]:
        if m['time']>sec:break
        ticks+=(m['time']-prev)*1e6/tempo*ppq;prev=m['time'];tempo=m['tempo']
    return int(round(ticks+max(0,sec-prev)*1e6/tempo*ppq))

def active_notes(track):return [n for n in track['notes'] if not n.get('deleted') and not n.get('candidate')]

def export_project(project,directory,fixed_bpm=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    tempos=project['tempos'];receipts=[]
    if fixed_bpm:
        outtempos=[{'time':0.,'tempo':int(round(60e6/fixed_bpm))}]
    else:outtempos=tempos
    for t in project['tracks']:
        notes=active_notes(t)
        if not notes:continue
        mid=mido.MidiFile(type=1,ticks_per_beat=960,charset='utf-8');meta=mido.MidiTrack();mid.tracks.append(meta)
        meta.append(mido.MetaMessage('track_name',name='Shared tempo / unquantized'))
        last=0
        for m in outtempos:
            tick=seconds_to_tick(m['time'],outtempos);meta.append(mido.MetaMessage('set_tempo',tempo=m['tempo'],time=tick-last));last=tick
        tr=mido.MidiTrack();mid.tracks.append(tr)
        is_drum=t.get('drum',False) or t['role']=='drums' or t['program']==128
        channel=9 if is_drum else 0
        tr.append(mido.MetaMessage('track_name',name=t['name']))
        if not is_drum:tr.append(mido.Message('program_change',program=min(127,t['program']),channel=channel,time=0))
        events=[]
        for n in notes:
            on=seconds_to_tick(n['start'],tempos);off=max(on+1,seconds_to_tick(n['end'],tempos))
            events.extend([(on,1,n['pitch'],n.get('velocity',80)),(off,0,n['pitch'],0)])
        last=0
        for tick,on,pitch,vel in sorted(events):
            tr.append(mido.Message('note_on' if on else 'note_off',note=pitch,velocity=vel,channel=channel,time=tick-last));last=tick
        p=directory/f'{t['role']}_{t['id']}.mid';mid.save(p)
        # Re-read exact absolute times and validate every event, not just file existence.
        got=[];got_all=[];sec=0.
        for msg in mido.MidiFile(p,charset='utf-8'):
            sec+=msg.time
            if msg.type=='note_on' and msg.velocity:got.append((msg.note,sec))
            if msg.type in ('note_on','note_off'):got_all.append((msg.note,1 if msg.type=='note_on' and msg.velocity else 0,sec))
        expected=sorted(notes,key=lambda n:(seconds_to_tick(n['start'],tempos),n['pitch']))
        if len(got)!=len(expected):raise ValueError(ui_text('MIDI 回读事件数不一致'))
        maxerr=0.
        for (pitch,sec),n in zip(got,expected):
            if pitch!=n['pitch']:raise ValueError(ui_text('MIDI 回读音高不一致'))
            target=(seconds_to_tick(n['start'],tempos)/960*60/fixed_bpm) if fixed_bpm else n['start']
            maxerr=max(maxerr,abs(sec-target))
        if maxerr>.002:raise ValueError(f'{ui_text('MIDI 时间误差超过 2 ms：')}{maxerr}')
        expected_all=[]
        for n in notes:
            expected_all.extend([(seconds_to_tick(n['start'],tempos),1,n['pitch'],n['start']),
                                 (max(seconds_to_tick(n['start'],tempos)+1,seconds_to_tick(n['end'],tempos)),0,n['pitch'],n['end'])])
        maxall=0.
        for (pitch,kind,sec),(tick,ek,ep,original) in zip(got_all,sorted(expected_all)):
            if pitch!=ep or kind!=ek:raise ValueError(ui_text('MIDI 起止事件顺序不一致'))
            target=tick/960*60/fixed_bpm if fixed_bpm else original
            maxall=max(maxall,abs(sec-target))
        if len(got_all)!=len(expected_all) or maxall>.002:raise ValueError(ui_text('MIDI 起止时间回读不通过'))
        receipts.append({'file':p.name,'notes':len(notes),'sha256':sha256(p),'max_onset_error_ms':maxerr*1000,'max_event_error_ms':maxall*1000,'track_id':t['id']})
    receipt={'version':VERSION,'project_revision':project['revision'],'fixed_bpm':fixed_bpm,'files':receipts,
                'limitations':[ui_text('力度为默认值 80；模型不预测力度'),ui_text('未导出连续弯音；当前模型仅离散事件'),ui_text('同组演奏路径为启发式拆分，不等于识别出真实音色来源'),ui_text('音乐角色为启发式标签，可人工修改')]}
    if project.get('analysis_mode')=='complex':receipt['limitations'][0]=ui_text('力度按分离来源的相对起音能量估计，不是原始 MIDI 力度')
    save_json(directory/'export_receipt.json',receipt)
    return receipt

def render_preview(project,output,track_ids=None,start=0.,end=None):
    import soundfile as sf
    end=min(project['duration'],end if end is not None else project['duration']);sr=22050
    y=np.zeros(max(1,int((end-start+.4)*sr)),np.float32)
    for t in project['tracks']:
        if track_ids is not None and t['id'] not in track_ids:continue
        for n in active_notes(t):
            if n['end']<=start or n['start']>=end:continue
            a=max(start,n['start']);b=min(end,n['end']);length=max(1,int((b-a+.07)*sr))
            if t.get('drum') or t['role']=='drums':
                decay=.10 if n['pitch'] in (42,44) else .65 if n['pitch'] in (49,51,52,53,55,57,59) else .28
                length=max(length,int(decay*sr))
            tt=np.arange(length)/sr;freq=440*2**((n['pitch']-69)/12)
            if t.get('drum') or t['role']=='drums':
                rng=np.random.default_rng(n['pitch']+int(n['start']*1000));noise=rng.standard_normal(length)
                if n['pitch'] in (35,36):wave=np.sin(2*np.pi*(48*tt+3*(1-np.exp(-tt*30))));env=np.exp(-tt*18)
                elif n['pitch'] in (37,38,39,40):wave=.75*noise+.25*np.sin(2*np.pi*180*tt);env=np.exp(-tt*24)
                else:wave=np.concatenate(([0],np.diff(noise)))*.5;env=np.exp(-tt*(38 if n['pitch'] in (42,44) else 12))
            else:
                wave=np.sin(2*np.pi*freq*tt)+.22*np.sin(4*np.pi*freq*tt)+.08*np.sin(6*np.pi*freq*tt)
                env=np.minimum(1,tt/.008)*np.minimum(1,np.maximum(0,(b-a+.07-tt)/.07))
            pos=int((a-start)*sr);k=min(length,len(y)-pos)
            if k>0:y[pos:pos+k]+=(wave[:k]*env[:k]*.12*n.get('velocity',80)/80).astype(np.float32)
    peak=np.max(np.abs(y));y/=max(1.,peak/0.85);sf.write(output,y,sr)

def candidate_notes(project,cid):
    c=next(c for c in project['candidates'] if c['id']==cid)
    if c.get('accepted') or c.get('dismissed'):raise ValueError(ui_text('该候选已处理'))
    t=next(t for t in project['tracks'] if t['id']==c['track'])
    ns=[n for n in t['notes'] if n['id'] in c['ids']]
    if len(ns)!=2 or any(n.get('locked') or n.get('deleted') for n in ns):raise ValueError(ui_text('事件已锁定、删除或变化，请保留当前编辑'))
    a,b=sorted(ns,key=lambda n:n['start'])
    if a['pitch']!=b['pitch'] or not 0<=b['start']-a['end']<=.09:raise ValueError(ui_text('候选已失效：音高不同、重叠或间隔已改变，请重新检查。'))
    if c.get('events') and c['events']!=[[n['id'],n['pitch'],n['start'],n['end']] for n in (a,b)]:raise ValueError(ui_text('候选涉及的音符已编辑，原建议失效。'))
    return c,t,a,b

def apply_candidate(project,cid):
    c,t,a,b=candidate_notes(project,cid)
    a['end']=max(a['end'],b['end']);a['origin']='user';b['deleted']=True;b['origin']='user'
    c['accepted']=True;project['revision']+=1

def process(source,output_root,model='medium',adaptive=True,progress=lambda *a:None,cancel=lambda:False,complex_mode=False):
    from adapter_registry import preflight
    preflight(model,complex_mode)
    source=Path(source);out=Path(output_root)
    if not source.is_file():raise FileNotFoundError(ui_text('原曲文件不存在或已移动，请重新选择歌曲。')+'\n'+str(source))
    out.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(out).free<500*1024**2:raise OSError(ui_text('输出磁盘可用空间不足 500 MB'))
    job=out/(safe_name(source.stem)+'_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);job.mkdir()
    save_json(job/'task.json',{'status':'running','source':str(source),'model':model})
    started=time.monotonic()
    try:
        progress(ui_text('解码音频'),0.);x=decode(source,job/'audio.f32',cancel)
        if cancel():raise Cancelled()
        progress(ui_text('建立速度图'),.04);tempos,ti,features=tempo_map(x,adaptive)
        separation=None
        if complex_mode:
            import complex_audio
            separation=complex_audio.separate(source,job,lambda s,p:progress(s,.06+.22*p),cancel)
            mix_raw,mix_device=infer(job/'audio.f32',job/'mixture_raw.json',model,lambda s,p:progress(ui_text('混音保底 · ')+s,.28+.2*p),cancel)
            tracks,raw,device=complex_audio.transcribe_stems(job,model,len(x)/16000,lambda s,p:progress(s,.48+.38*p),cancel)
            complex_audio.fuse_mixture(tracks,mix_raw,job,len(x)/16000,cancel);device['mixture']=mix_device
        else:
            raw,device=infer(job/'audio.f32',job/'raw_notes.json',model,lambda s,p:progress(s,.08+.8*p),cancel)
        if cancel():raise Cancelled()
        progress(ui_text('整理来源与候选'),.9)
        if complex_mode:candidates=merge_candidates(tracks)
        else:tracks,candidates=classify_tracks(raw,len(x)/16000,features)
        # Keep a portable, playable source copy. No external path is required to re-open.
        import soundfile as sf
        if not complex_mode:sf.write(job/'source.wav',x,16000,subtype='PCM_16')
        from note_evidence import protect_repetitions
        repeat_report=protect_repetitions(tracks,job,cancel=cancel)
        candidates=merge_candidates(tracks)
        project={'schema':1,'version':VERSION,'revision':1,'title':source.stem,'original_source':str(source),
                 'source_sha256':sha256(source),'duration':len(x)/16000,'audio':'source.wav','model':model,
                 'model_sha256':MODEL_HASHES[model],'device':device,'tempos':tempos,'tempo_info':ti,
                 'tracks':tracks,'candidates':candidates,'history':[],'repeat_attack_report':repeat_report,
                 'limitations':[ui_text('同组采用连续演奏路径拆分，来源与角色仍需试听确认'),ui_text('力度是默认值，连续滑音未恢复'),ui_text('速度图是估计，音符保留原始时间且不量化')]}
        if complex_mode:
            project.update(analysis_mode='complex',separation=separation)
            project['limitations']=[ui_text('分离来源为估计，可能残留串音；其他来源可能仍含中高频 Bass'),ui_text('力度为分离轨相对能量估计，连续滑音未恢复'),ui_text('鼓样为估计分离轨片段，可能含复合击打'),ui_text('速度图是估计，音符保留原始时间且不量化')]
        save_json(job/'project.json',project)
        progress(ui_text('导出并回读 MIDI'),.95);receipt=export_project(project,job/'midi')
        found={t['role'] for t in tracks if active_notes(t)};missing=[ROLES[r] for r in ('lead','counter','accompaniment','bass') if r not in found]
        status='partial' if missing or not receipt['files'] else 'complete'
        report={'status':status,'elapsed_seconds':time.monotonic()-started,'source':str(source),'duration':project['duration'],
                'model':model,'device':device,'raw_events':len(raw),'pitched_events':sum(len(t['notes']) for t in tracks if t['role']!='drums'),
                'drum_events':sum(len(t['notes']) for t in tracks if t['role']=='drums'),
                'files':receipt['files'],'unconfirmed_roles':missing,'candidate_count':len(candidates),
                'tempo':ti,'quality_status':ui_text('真实音乐质量尚需人工试听；文件验证不等于高精度验收'),
                'limitations':project['limitations']+receipt['limitations']}
        save_json(job/'report.json',report);save_json(job/'task.json',{'status':status,'source':str(source)})
        text=json.dumps(report,ensure_ascii=False,indent=2)
        (job/'report.html').write_text(ui_text('<!doctype html><meta charset="utf-8"><title>转写报告</title><h1>')+html.escape(source.stem)+ui_text('</h1><p>处理状态：')+status+'</p><pre>'+html.escape(text)+'</pre>',encoding='utf-8')
        progress(ui_text('完成；请试听确认角色与音符'),1.)
        return job,project,report
    except Exception as e:
        save_json(job/'task.json',{'status':'cancelled' if isinstance(e,Cancelled) else 'failed','error':str(e),'source':str(source)})
        raise

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('audio');p.add_argument('output');p.add_argument('--model',default='medium');args=p.parse_args()
    last=[-1]
    def report(s,v):
        k=int(v*20)
        if k!=last[0]:print(s,round(v*100),flush=True);last[0]=k
    job,_,r=process(args.audio,args.output,args.model,progress=report)
    print(json.dumps({'job':str(job),'report':r},ensure_ascii=False),flush=True)


def external_template(name):
    from adapter_registry import require_file
    key={'vital_init.json':'vital_template_path','flp_scaffold.json':'fl_template_path'}[name]
    value=app_settings.load()[key]
    if not value:
        raise FileNotFoundError(ui_text('框架源码不附带此模板。请在设置 → 模型与引擎关联自己有权使用的模板；详见 README。')+' '+name)
    return require_file(value,name)

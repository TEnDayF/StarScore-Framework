"""Streaming full-song Vital rendering, content-addressed stems, fail-closed mix."""
from __future__ import annotations
from i18n import tr as ui_text
import contextlib,hashlib,json,math,os,subprocess,sys,time,uuid
from pathlib import Path
import mido,numpy as np,soundfile as sf
import core,vital_presets as vp

REV='full-song-2'
SR=24000
TAIL=6.
BLOCK=SR*8
GAINS={'lead':1.,'counter':.75,'accompaniment':.5,'bass':.8,'drums':.8,'unknown':.65}

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def is_drum(t):return t.get('drum') or t.get('role')=='drums' or t.get('program')==128
def events(t):
    out=[]
    for n in core.active_notes(t):
        item={'pitch':int(n['pitch']),'start':float(n['start']),'end':float(n['end']),'velocity':int(n.get('velocity',80))}
        if not all(math.isfinite(item[k]) for k in ('start','end')) or not 0<=item['pitch']<=127 or not 1<=item['velocity']<=127 or item['start']<0 or item['end']<item['start']:
            raise ValueError(ui_text('音符时间、音高或力度无效：')+str(t['id']))
        if item['end']==item['start']:item['end']+=.01
        out.append(item)
    return sorted(out,key=lambda n:(n['start'],n['pitch'],n['end']))

def valid_audio(path,frames,sr=SR):
    try:
        info=sf.info(path);return info.frames==frames and info.samplerate==sr and info.channels==2
    except (OSError,RuntimeError):return False

def cached_record(path,frames):
    try:
        row=core.read_json(path);audio=Path(row['wav'])
        if row['status']=='complete' and valid_audio(audio,frames) and core.sha256(audio)==row['sha256']:return row
    except (OSError,ValueError,KeyError,TypeError):pass
    return None

def drum_hit(pitch,velocity):
    """Original basic synthetic GM percussion, deterministic, no external samples."""
    length=.6 if pitch in (35,36) else .25 if pitch in (42,44) else 1.6 if pitch in (46,49,51,52,53,55,57,59) else .45
    t=np.arange(int(length*SR),dtype=np.float64)/SR;rng=np.random.default_rng(pitch);noise=rng.standard_normal(len(t))
    if pitch in (35,36):y=np.sin(2*np.pi*(46*t+3.2*(1-np.exp(-t*35))))*np.exp(-t*14)
    elif pitch in (37,38,39,40):
        hp=np.r_[0,np.diff(noise)]*.45;y=(hp*.7+np.sin(2*np.pi*185*t)*.3)*np.exp(-t*20)
        if pitch==39:y*=1+.5*np.sin(2*np.pi*65*t)
    elif pitch in (41,43,45,47,48,50):y=np.sin(2*np.pi*((75+(pitch-41)*12)*t+1.4*(1-np.exp(-t*25))))*np.exp(-t*13)
    else:
        hp=np.r_[0,np.diff(noise)]*.45;y=hp*np.exp(-t*(32 if pitch in (42,44) else 4 if pitch in (49,51,52,53,55,57,59) else 8))
    y*=np.minimum(1,t/.001);y[-min(240,len(y)):]*=np.linspace(1,0,min(240,len(y)))
    return (y*.32*(velocity/127)).astype(np.float32)

def render_drums(notes,path,frames,progress=lambda p:None,cancel=lambda:False,reference_samples=None):
    hits=[];samples={}
    for n in notes:
        start=round(n['start']*SR);key=(n['pitch'],n['velocity'])
        if key not in samples:
            if reference_samples and n['pitch'] in reference_samples:
                samples[key]=reference_samples[n['pitch']]*(n['velocity']/127)
            else:samples[key]=drum_hit(*key)
        hit=samples[key]
        # Close hats choke preceding open hats, instead of letting all tails accumulate.
        if n['pitch']==46:
            next_close=next((x['start'] for x in notes if x['start']>n['start'] and x['pitch'] in (42,44)),None)
            if next_close is not None:hit=hit[:max(1,round((next_close-n['start'])*SR))]
        hits.append((start,hit))
    peak=0.
    with sf.SoundFile(path,'w',samplerate=SR,channels=2,subtype='FLOAT') as out:
        for pos in range(0,frames,BLOCK):
            if cancel():raise core.Cancelled()
            end=min(frames,pos+BLOCK);y=np.zeros((end-pos,2),np.float32)
            for start,hit in hits:
                a=max(start,pos);z=min(start+len(hit),end)
                if z>a:
                    part=hit[a-start:z-start]
                    y[a-pos:z-pos]+=part[:,None] if part.ndim==1 else part
            peak=max(peak,float(np.max(np.abs(y))));out.write(y);progress(end/frames)
    return {'peak':peak,'engine':ui_text('估计分离轨鼓样') if reference_samples else ui_text('基础合成鼓（非原曲鼓样）')}

def render_vital_full(request_path):
    """Child process: streaming calls preserve Vital state across block boundaries."""
    req=core.read_json(request_path);plugin,loaded=vp.load_vital(req['preset'],req['plugin']);frames=req['frames']
    midi=[]
    for n in req['notes']:
        midi.extend([(round(n['start']*SR),1,n['pitch'],n['velocity']),
                     (max(round(n['start']*SR)+1,round(n['end']*SR)),0,n['pitch'],0)])
    midi.sort();index=0;peak=0.;energy=0.
    with sf.SoundFile(req['wav'],'w',samplerate=SR,channels=2,subtype='FLOAT') as out:
        for pos in range(0,frames,BLOCK):
            count=min(BLOCK,frames-pos);messages=[]
            while index<len(midi) and midi[index][0]<pos+count:
                tick,on,pitch,vel=midi[index];index+=1
                messages.append(mido.Message('note_on' if on else 'note_off',note=pitch,velocity=vel,time=(tick-pos)/SR))
            y=plugin(messages,duration=count/SR,sample_rate=SR,num_channels=2,buffer_size=512,reset=pos==0)
            if y.shape!=(2,count) or not np.isfinite(y).all():raise ValueError(ui_text('Vital 输出长度或采样值无效'))
            peak=max(peak,float(np.max(np.abs(y))));energy+=float(np.sum(y.astype(np.float64)**2));out.write(y.T)
            core.save_json(req['progress'],{'fraction':(pos+count)/frames})
    if peak<1e-7:raise ValueError(ui_text('Vital 全轨渲染静音'))
    if index!=len(midi):raise ValueError(ui_text('有 MIDI 事件超出渲染范围'))
    core.save_json(req['receipt'],{'preset_name':loaded['preset_name'],'parameters_readback':'passed','peak':peak,'rms':math.sqrt(energy/(frames*2)),
        'frames':frames,'notes':len(req['notes']),'midi_events':index,'engine':'Vital'})

def child_render(req,path,progress,cancel):
    core.save_json(path,req)
    cmd=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('main.py'))]
    # Logs go to a file rather than a pipe, so a noisy native plugin cannot deadlock.
    with open(path.with_suffix('.log'),'wb') as log:
        proc=subprocess.Popen(cmd+['--render-full-vital',str(path)],stdout=log,stderr=log,creationflags=core.CREATE_NO_WINDOW)
        last=time.monotonic();fraction=-1.
        try:
            while proc.poll() is None:
                if cancel():raise core.Cancelled()
                try:
                    new=core.read_json(req['progress'])['fraction']
                    if new!=fraction:fraction=new;last=time.monotonic();progress(new)
                except (OSError,ValueError,KeyError):pass
                if time.monotonic()-last>120:raise RuntimeError(ui_text('Vital 连续 120 秒未推进，已停止该轨'))
                time.sleep(.1)
            if proc.returncode:
                error=path.with_suffix('.error.json');detail=core.read_json(error)['error'] if error.exists() else f'{ui_text('退出码 ')}{proc.returncode}'
                raise RuntimeError(ui_text('Vital 渲染失败：')+detail[-400:])
        finally:
            if proc.poll() is None:proc.kill()
            proc.wait()
    return core.read_json(req['receipt'])

def mix_stems(rows,path,frames,progress=lambda p:None,cancel=lambda:False):
    """Two streaming passes: measure fixed balance then attenuate to -1.5 dBFS sample peak."""
    peak=0.;gain=1.
    for phase in (0,1):
        with contextlib.ExitStack() as stack:
            readers=[(stack.enter_context(sf.SoundFile(row['wav'])),row['mix_gain']) for row in rows]
            out=stack.enter_context(sf.SoundFile(path,'w',samplerate=SR,channels=2,subtype='PCM_24')) if phase else None
            for pos in range(0,frames,BLOCK):
                if cancel():raise core.Cancelled()
                count=min(BLOCK,frames-pos);y=np.zeros((count,2),np.float64)
                for f,g in readers:
                    part=f.read(count,dtype='float32',always_2d=True)
                    if part.shape!=(count,2) or not np.isfinite(part).all():raise ValueError(ui_text('分轨长度不完整或包含无效采样；未完成混音'))
                    y+=part*g
                if phase==0:peak=max(peak,float(np.max(np.abs(y))))
                else:out.write(y*gain)
                progress((phase+(pos+count)/frames)/2)
        if phase==0:
            if peak<1e-7:raise ValueError(ui_text('混音无有效声音'))
            gain=min(1.,10**(-1.5/20)/peak)
    return {'peak_before_gain':peak,'gain':gain,'sample_peak':peak*gain,'sample_peak_dbfs':20*math.log10(peak*gain),
            'note':ui_text('仅采样峰值保护，不是母带或 true-peak 验收')}

def render_song(project,folder,output,options,progress=lambda s,p:None,cancel=lambda:False,plugin=None,renderer=child_render):
    plugin=vp.PLUGIN if plugin is None else plugin
    """Always includes all active pitched and drum tracks, independent of export checkboxes."""
    active=[(t,events(t)) for t in project['tracks'] if core.active_notes(t)]
    if not active:raise ValueError(ui_text('工程没有可试听的有效音符'))
    duration=max(float(project['duration']),max(n['end'] for _,ns in active for n in ns));frames=math.ceil((duration+TAIL)*SR)
    if duration<=0 or duration>86400:raise ValueError(ui_text('歌曲长度无效或超过 24 小时'))
    pitched=any(not is_drum(t) for t,_ in active)
    if pitched and not Path(plugin).is_file():raise ValueError(ui_text('找不到本机 Vital 插件，未开始全曲渲染'))
    plugin_hash=core.sha256(plugin) if pitched else None
    output=Path(output).resolve();cache=output/'全曲缓存';cache.mkdir(parents=True,exist_ok=True)
    run=output/('全曲试听_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);run.mkdir()
    manifest={'version':core.VERSION,'renderer':REV,'status':'working','title':project.get('title'),'project_revision':project['revision'],
        'source_sha256':project.get('source_sha256'),'duration':duration,'tail_seconds':TAIL,'frames':frames,'sample_rate':SR,
        'expected_tracks':len(active),'tracks':[],'all_active_tracks':True,'reused_tracks':0,'rendered_tracks':0}
    try:
        for i,(track,ns) in enumerate(active):
            if cancel():raise core.Cancelled()
            name=track['name'];progress(f'{ui_text('准备 ')}{i + 1}/{len(active)} · {name}',i/len(active)*.85)
            analysis=None;patch=None;reference_samples={};sample_hashes={}
            if not is_drum(track):
                opt=options.get(track['id'],vp.default_options(project)[track['id']]);h=None
                patch,analysis=vp.reference_patch(track,opt,folder,project,cancel)
            elif track.get('drum_samples'):
                from complex_audio import sample_for
                from scipy.signal import resample_poly
                for pitch in {n['pitch'] for n in ns}:
                    sample=sample_for(track,pitch,folder)
                    if sample:
                        y,sr=sf.read(sample,dtype='float32',always_2d=True);g=math.gcd(SR,sr)
                        reference_samples[pitch]=resample_poly(y,SR//g,sr//g,axis=0).astype(np.float32)
                        sample_hashes[pitch]=core.sha256(sample)
            key=digest({'renderer':REV,'notes':ns,'patch':patch,'frames':frames,'sr':SR,'plugin':plugin_hash if patch else None,'drums':bool(is_drum(track)),'sample_hashes':sample_hashes})
            stem_dir=cache/key;stem_dir.mkdir(exist_ok=True);record_path=stem_dir/'complete.json';record=cached_record(record_path,frames)
            reused=record is not None
            if not reused:
                token=uuid.uuid4().hex[:8];wav=stem_dir/(token+'.wav')
                def track_progress(p):progress(f'{ui_text('渲染 ')}{i + 1}/{len(active)} · {name} · {int(p * 100)}%',(i+p)/len(active)*.85)
                if patch:
                    preset=stem_dir/(token+'.vital');core.save_json(preset,patch)
                    req={'preset':str(preset),'plugin':str(plugin),'notes':ns,'frames':frames,'wav':str(wav),
                         'progress':str(stem_dir/(token+'.progress.json')),'receipt':str(stem_dir/(token+'.receipt.json'))}
                    details=renderer(req,stem_dir/(token+'.request.json'),track_progress,cancel)
                else:details=render_drums(ns,wav,frames,track_progress,cancel,reference_samples)
                if not valid_audio(wav,frames):raise ValueError(ui_text('分轨文件不完整：')+name)
                record={'status':'complete','key':key,'wav':str(wav),'sha256':core.sha256(wav),'details':details}
                core.save_json(record_path,record)
            manifest['reused_tracks' if reused else 'rendered_tracks']+=1
            row=dict(record,track_id=track['id'],track_name=name,role=track['role'],notes=len(ns),analysis=analysis,
                     reused=reused,mix_gain=GAINS.get(track['role'],.65))
            manifest['tracks'].append(row);core.save_json(run/'试听报告.json',manifest)
        if cancel():raise core.Cancelled()
        mixkey=digest({'renderer':REV,'title':project.get('title'),'frames':frames,'tracks':[(r['track_id'],r['key'],r['mix_gain']) for r in manifest['tracks']]})
        mix_record=cache/(mixkey+'.mix.json');existing=cached_record(mix_record,frames)
        if existing:
            manifest.update(status='complete',mix=existing['wav'],mix_sha256=existing['sha256'],mix_measurement=existing['details'],mix_reused=True)
        else:
            final=run/(core.safe_name(project.get('title',ui_text('歌曲')))+'_Vital全曲.wav')
            staging=run/('_未完成_'+final.name)
            measurement=mix_stems(manifest['tracks'],staging,frames,lambda p:progress(ui_text('混合与峰值检查'),.85+.15*p),cancel)
            if not valid_audio(staging,frames):raise ValueError(ui_text('完整混音校验失败'))
            os.replace(staging,final)
            sha=core.sha256(final);core.save_json(mix_record,{'status':'complete','wav':str(final),'sha256':sha,'details':measurement})
            manifest.update(status='complete',mix=str(final),mix_sha256=sha,mix_measurement=measurement,mix_reused=False)
        progress(f'{ui_text('全曲完成 · ')}{len(active)}{ui_text(' 轨 · 复用 ')}{manifest['reused_tracks']}{ui_text(' 轨')}',1.)
        return run,manifest
    except core.Cancelled:manifest['status']='cancelled';raise
    except Exception as e:manifest.update(status='failed',error=str(e));raise
    finally:
        core.save_json(run/'试听报告.json',manifest)

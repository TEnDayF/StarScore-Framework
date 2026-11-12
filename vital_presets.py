"""Offline, traceable Vital candidates. Never claims recovered original patches."""
from __future__ import annotations
from i18n import tr as ui_text
import copy, csv, hashlib, json, math, re, subprocess, sys, time, uuid
from pathlib import Path
import mido
import numpy as np
import soundfile as sf
import core
import app_settings

FAMILIES = {'bright':ui_text('明亮 Lead'),'pluck':ui_text('清脆 Pluck'),'bass':ui_text('厚实 Bass'),'sub':ui_text('纯净 Sub'),'pad':ui_text('柔和 Pad'),'keys':ui_text('柔和 Keys')}
DEFAULTS = {'lead':'bright','counter':'pluck','bass':'bass','accompaniment':'pad','unknown':'keys'}
PLUGIN = Path(app_settings.load()['vital_path'])

def eligible(track):
    return not (track.get('drum') or track.get('role')=='drums' or track.get('program')==128) and bool(core.active_notes(track))

def default_options(project):
    counts={};out={}
    for t in project['tracks']:
        role=t['role'];counts[role]=counts.get(role,0)+1
        family=DEFAULTS.get(role,'keys')
        out[t['id']]={'selected':eligible(t),'family':family,'brightness':65 if role!='bass' else 42,
                      'attack':15 if family=='pad' else 2,'space':18 if role!='bass' else 0,'reference':True,'motion':False,'reference_library':False,
                      'name':f'{project.get('title', ui_text('歌曲'))[:24]}_{ui_text(core.ROLES.get(role, role))}{counts[role]:02d}'}
    return out

def file_stem(track,options):
    name=core.safe_name(options['name'])[:65]
    # Stable identity suffix also prevents collisions when user chooses identical names.
    suffix=hashlib.sha256(track['id'].encode()).hexdigest()[:8]
    family=ui_text('参考库候选') if options.get('reference_library') else FAMILIES[options['family']].replace(' ','')
    return f'{name}_{family}_{suffix}'

def envelope(options):
    family=options['family'];a=max(.0005,float(options['attack'])/1000)
    d,s,r={'bright':(.22,.76,.12),'pluck':(.23,.12,.13),'bass':(.16,.8,.045),
           'sub':(.12,.9,.035),'pad':(.5,.83,.8),'keys':(.45,.32,.22)}[family]
    return a,d,s,r

def wave(kind,harmonics=None):
    if harmonics is not None:
        x=np.linspace(0,1,65);y=sum(float(a)*np.sin(2*np.pi*(i+1)*x) for i,a in enumerate(harmonics[:24]))
        y/=max(.001,float(np.max(np.abs(y))));points=[v for xx,yy in zip(x,y) for v in (float(xx),float((1-yy)/2))]
    elif kind=='saw':points=[0.,1.,1.,0.]
    elif kind=='triangle':points=[0.,.5,.25,0.,.75,1.,1.,.5]
    elif kind=='pulse':points=[0.,0.,.4,0.,.4,1.,1.,1.]
    else:points=[v for i in range(65) for v in (i/64,(1-math.sin(2*math.pi*i/64))/2)]
    n=len(points)//2;line=dict(name='StarScore '+kind,num_points=n,points=points,powers=[0.]*n,smooth=False)
    return dict(author='StarScore',full_normalize=False,groups=[dict(components=[dict(interpolation_style=1,
        keyframes=[dict(line=line,position=0,pull_power=0.)],num_points=n,type='Line Source')])],
        name='StarScore '+kind,remove_all_dc=True,version='1.5.5')

def analyse_reference(track,audio,cancel=lambda:False):
    """Conservative harmonic estimate from up to 12 isolated-in-track note windows.

    The source remains a mix: this is explicitly NOT a separated timbre measurement.
    """
    notes=sorted(core.active_notes(track),key=lambda n:n['start']);windows=[];spectra=[]
    if not Path(audio).is_file():return None,{'status':'fallback','reason':ui_text('找不到工程音频，使用音色家族模板'),'windows':0}
    try:
        with sf.SoundFile(audio) as f:
            sr=f.samplerate
            candidates=[n for n in notes if n['end']-n['start']>=.10]
            if len(candidates)>48:candidates=[candidates[i] for i in np.linspace(0,len(candidates)-1,48).astype(int)]
            for n in candidates:
                if cancel():raise core.Cancelled()
                if len(spectra)>=12:break
                t=n['start']+min(.04,(n['end']-n['start'])*.15);dur=min(.18,n['end']-t)
                if dur<.06 or sum(o['start']<t+dur and o['end']>t for o in notes)>1:continue
                f.seek(min(len(f),max(0,int(t*sr))));x=f.read(int(dur*sr),dtype='float32',always_2d=True).mean(axis=1)
                if len(x)<128 or np.sqrt(np.mean(x*x))<1e-5:continue
                size=2**int(math.ceil(math.log2(len(x)*4)));spec=np.abs(np.fft.rfft(x*np.hanning(len(x)),size))
                hz=440*2**((n['pitch']-69)/12);amps=[]
                for h in range(1,25):
                    c=int(round(hz*h*size/sr));width=max(1,int(12*size/sr))
                    amps.append(float(np.max(spec[max(0,c-width):min(len(spec),c+width+1)])) if c<len(spec) else 0.)
                amps=np.array(amps)
                if amps[0]<max(1e-8,amps.max()*.07):continue
                spectra.append(amps/max(amps.max(),1e-8));windows.append(round(t,3))
    except core.Cancelled:raise
    except (OSError,RuntimeError,ValueError) as e:
        return None,{'status':'fallback','reason':ui_text('音频分析不可用：')+str(e)[:180],'windows':0}
    if len(spectra)<3:return None,{'status':'fallback','reason':ui_text('可用单音窗口不足，使用音色家族模板'),'windows':len(spectra)}
    h=np.median(spectra,axis=0);h/=max(h.max(),1e-8)
    return h,{'status':'mixed_reference','reason':ui_text('混音谐波辅助，不是独立声部或原始音色'),'windows':len(spectra),'times':windows}

def make_preset(track,options,harmonics=None):
    p=core.read_json(core.external_template('vital_init.json'));s=p['settings'];family=options['family']
    p.update(author='StarScore MIDI Studio',preset_name=file_stem(track,options),preset_style=FAMILIES[family],
             comments='Generated editable candidate, not recovered original timbre. Track: '+track['id'])
    for fx in ('chorus','compressor','delay','distortion','reverb','phaser','flanger','eq'):s[fx+'_on']=0.
    s['modulations']=[{'source':'','destination':''} for _ in s['modulations']]
    for k in s:
        if k.startswith('modulation_') and k.endswith('_amount'):s[k]=0.
    s.update(volume=4500.,velocity_track=.5,voice_transpose=0.,voice_amplitude=1.,legato=0.,
             sample_on=0.,sample_level=0.,polyphony=1. if family in ('bass','sub') else 12.,
             filter_1_on=0. if family=='sub' else 1.,filter_2_on=0.,filter_1_model=0.,filter_1_style=0.,
             filter_1_blend=0.,filter_1_mix=1.,filter_1_drive=0.,filter_1_keytrack=0.,filter_1_resonance=.08,
             filter_1_cutoff=55.+float(options['brightness'])*.72)
    shapes={'bright':('saw','pulse'),'pluck':('triangle','saw'),'bass':('saw','sine'),
            'sub':('sine','sine'),'pad':('saw','triangle'),'keys':('triangle','sine')}
    for i in (1,2,3):
        s.update({f'osc_{i}_on':float(i<3 and not (family=='sub' and i==2)),f'osc_{i}_destination':0.,
            f'osc_{i}_level':.65 if i==1 else .20 if i==2 else 0.,f'osc_{i}_transpose':0.,f'osc_{i}_tune':0.,
            f'osc_{i}_wave_frame':0.,f'osc_{i}_unison_voices':1.,f'osc_{i}_unison_detune':1.,
            f'osc_{i}_random_phase':0.,f'osc_{i}_phase':0.,f'osc_{i}_pan':0.,f'osc_{i}_stereo_spread':0.,
            f'osc_{i}_distortion_type':0.,f'osc_{i}_distortion_amount':0.,f'osc_{i}_spectral_morph_type':0.,f'osc_{i}_spectral_morph_amount':0.})
    s['wavetables']=[wave(shapes[family][0]),wave(shapes[family][1]),wave('sine')]
    if harmonics is not None and family!='sub':
        base=np.array([1/(i+1) for i in range(24)])
        s['wavetables'][0]=wave('Reference blend',.65*base+.35*np.asarray(harmonics))
    if family in ('bright','pad'):
        s.update(osc_1_unison_voices=3.,osc_1_unison_detune=.8,osc_1_stereo_spread=.5)
    a,d,sustain,r=envelope(options)
    s.update(env_1_attack=a**.25,env_1_decay=d**.25,env_1_sustain=sustain,env_1_release=r**.25,
             env_1_delay=0.,env_1_hold=0.,env_1_attack_power=0.,env_1_decay_power=-2.,env_1_release_power=-2.)
    space=max(0,min(100,float(options['space'])))/100
    s.update(reverb_on=float(space>0),reverb_dry_wet=space*.35,reverb_size=.3+space*.35,
             reverb_decay_time=-.5+space*1.5,reverb_pre_low_cutoff=55.)
    return p

def write_midi(track,tempos,path,project=None):
    """Full song, absolute original times; no program changes to replace Vital."""
    notes=core.active_notes(track);mid=mido.MidiFile(type=1,ticks_per_beat=960,charset='utf-8')
    meta=mido.MidiTrack();mid.tracks.append(meta);last=0
    if project is not None:__import__('song_info').midi_metadata(meta,project)
    for tempo in tempos:
        tick=core.seconds_to_tick(tempo['time'],tempos);meta.append(mido.MetaMessage('set_tempo',tempo=int(tempo['tempo']),time=tick-last));last=tick
    tr=mido.MidiTrack();mid.tracks.append(tr);tr.append(mido.MetaMessage('track_name',name=track['name']));events=[]
    for n in notes:
        a=core.seconds_to_tick(n['start'],tempos);z=max(a+1,core.seconds_to_tick(n['end'],tempos))
        events.extend([(a,1,n['pitch'],n.get('velocity',80)),(z,0,n['pitch'],0)])
    last=0
    for tick,on,pitch,vel in sorted(events):
        tr.append(mido.Message('note_on' if on else 'note_off',note=pitch,velocity=vel,channel=0,time=tick-last));last=tick
    mid.save(path);found=[];sec=0.
    for msg in mido.MidiFile(path,charset='utf-8'):
        sec+=msg.time
        if msg.type in ('note_on','note_off'):found.append((sec,msg.note,int(msg.type=='note_on' and msg.velocity>0)))
    expected=sorted([(n['start'],n['pitch'],1) for n in notes]+[(n['end'],n['pitch'],0) for n in notes])
    # Simultaneous notes may be sorted differently; compare by pitch/kind/time.
    found.sort(key=lambda x:(x[1],x[2],x[0]));expected.sort(key=lambda x:(x[1],x[2],x[0]))
    if len(found)!=len(expected):raise ValueError(ui_text('Vital 配套 MIDI 事件数量不一致'))
    error=max((abs(a[0]-b[0]) for a,b in zip(found,expected)),default=0.)
    if error>.002 or any(a[1:]!=b[1:] for a,b in zip(found,expected)):raise ValueError(ui_text('Vital 配套 MIDI 时序校验失败'))
    return error*1000

def _vst3_chunks(data):
    """Validate the VST3 directory and find Comp by tag, never by entry order."""
    import struct
    error = 'Vital 状态格式不兼容：VST3 目录或组件边界无效'
    if len(data) < 48 or data[:4] != b'VST3':
        raise ValueError(error)
    offset = struct.unpack_from('<q', data, 40)[0]
    if offset < 48 or offset + 8 > len(data) or data[offset:offset+4] != b'List':
        raise ValueError(error)
    count = struct.unpack_from('<i', data, offset+4)[0]
    if count < 1 or count > (len(data)-offset-8)//20:
        raise ValueError(error)
    entries = []
    for i in range(count):
        tag, pos, size = struct.unpack_from('<4sqq', data, offset+8+i*20)
        if pos < 48 or size < 0 or pos+size > offset:
            raise ValueError(error)
        entries.append((tag, data[pos:pos+size]))
    if sum(tag == b'Comp' for tag, _ in entries) != 1:
        raise ValueError(error)
    return entries


def _vital_component(chunk):
    """Read the supported VstW/CcnK wrapper and preserve byte-exact JSON suffix."""
    import struct
    error = 'Vital 状态格式不兼容：预设正文或长度无效'
    if len(chunk) < 176 or chunk[:4] != b'VstW' or chunk[16:20] != b'CcnK':
        raise ValueError(error)
    size = struct.unpack_from('>I', chunk, 172)[0]
    if size < 2 or 176+size != len(chunk) or struct.unpack_from('>I', chunk, 20)[0] != len(chunk)-24:
        raise ValueError(error)
    try:
        text = chunk[176:].decode('utf-8')
        loaded, used = json.JSONDecoder().raw_decode(text)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(error) from exc
    if not isinstance(loaded, dict) or not isinstance(loaded.get('settings'), dict):
        raise ValueError(error)
    # raw_decode counts Unicode characters, while the wrapper stores byte lengths.
    return loaded, chunk[176+len(text[:used].encode('utf-8')):]


def load_vital(preset_path,plugin_path):
    """Load and verify a preset; caller must use an isolated process."""
    import struct
    try:import pedalboard
    except ImportError as e:raise RuntimeError(ui_text('Vital 试听适配器缺少 Pedalboard。请按适配器说明配置运行环境；预设制作和 MIDI 编辑仍可使用。')) from e
    p=core.read_json(preset_path);plugin=pedalboard.load_plugin(str(plugin_path))
    if plugin.name!='Vital':raise ValueError(ui_text('所选插件不是 Vital'))
    data=plugin.preset_data
    entries=[]
    for tag,chunk in _vst3_chunks(data):
        if tag==b'Comp':
            _,tail=_vital_component(chunk);start=176
            header=bytearray(chunk[:start])
            raw=json.dumps(p,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')+tail
            if header[:4]!=b'VstW' or header[16:20]!=b'CcnK' or start!=176:raise ValueError(ui_text('Vital 状态格式不兼容'))
            struct.pack_into('>I',header,20,len(header)+len(raw)-24);struct.pack_into('>I',header,start-4,len(raw))
            chunk=bytes(header)+raw
        entries.append((tag,chunk))
    header=bytearray(data[:48]);payload=b''.join(v for _,v in entries);struct.pack_into('<q',header,40,48+len(payload))
    table=b'List'+struct.pack('<i',len(entries));pos=48
    for tag,v in entries:table+=struct.pack('<4sqq',tag,pos,len(v));pos+=len(v)
    plugin.preset_data=bytes(header)+payload+table
    # Confirm the host accepted this exact patch, not a fallback/default sound.
    component=next(chunk for tag,chunk in _vst3_chunks(plugin.preset_data) if tag==b'Comp')
    loaded,_=_vital_component(component)
    if loaded.get('preset_name')!=p['preset_name']:raise ValueError(ui_text('Vital 预设名称回读不一致'))
    for key in ('filter_1_cutoff','env_1_attack','env_1_sustain','reverb_on','osc_1_on'):
        if abs(loaded['settings'][key]-p['settings'][key])>1e-4:raise ValueError(ui_text('Vital 参数回读不一致：')+key)
    if p['settings']['modulations'][0].get('source')=='lfo_1':
        if loaded['settings']['modulations'][0]!=p['settings']['modulations'][0]:raise ValueError(ui_text('Vital 调制连接回读不一致'))
        for key in ('lfo_1_sync','lfo_1_frequency','modulation_1_amount'):
            if abs(loaded['settings'][key]-p['settings'][key])>1e-4:raise ValueError(ui_text('Vital 调制参数回读不一致：')+key)
    return plugin,loaded

def render_preview(request_path):
    req=core.read_json(request_path);plugin,loaded=load_vital(req['preset'],req['plugin'])
    msgs=[]
    for n in req['notes']:
        msgs.extend([mido.Message('note_on',note=n['pitch'],velocity=n.get('velocity',80),time=n['start']),
                     mido.Message('note_off',note=n['pitch'],velocity=0,time=n['end'])])
    msgs.sort(key=lambda m:(m.time,m.type=='note_on'))
    audio=plugin(msgs,duration=req['duration'],sample_rate=24000,num_channels=2,buffer_size=512)
    if not np.isfinite(audio).all() or np.max(np.abs(audio))<1e-6:raise ValueError(ui_text('Vital 试听无有效音频'))
    peak=float(np.max(np.abs(audio)));gain=min(1.,.7/peak);sf.write(req['wav'],audio.T*gain,24000,subtype='PCM_16')
    core.save_json(Path(req['wav']).with_suffix('.render.json'),{'plugin':plugin.name,'preset_name':loaded['preset_name'],
        'parameters_readback':'passed','peak_before_gain':peak,'rms':float(np.sqrt(np.mean(audio*audio))),
        'gain':gain,'sample_rate':24000,'status':'rendered'})

def reference_patch(track,options,folder,project,cancel=lambda:False):
    """Shared by preset, full-song and FL export: one source and one patch policy."""
    if options.get('reference_library',False):
        from timbre_match import candidate
        return candidate(track,options,folder,project,cancel)
    h=None;analysis={'status':'template','reason':ui_text('使用音色家族模板'),'windows':0};profile=None
    if options.get('reference'):
        source=Path(folder)/track.get('reference_audio',project['audio'])
        if track.get('reference_audio') and not source.is_file():raise FileNotFoundError(ui_text('缺少音色参考分离轨：')+str(source))
        h,analysis=analyse_reference(track,source,cancel)
        if track.get('reference_audio'):
            analysis.update(source=str(source),status='estimated_stem_reference' if h is not None else 'fallback',reason=ui_text('估计分离轨谐波辅助；仍可能串音'))
            if options.get('motion',False):
                from complex_audio import modulation_profile
                profile=modulation_profile(track,source)
                analysis['modulation_candidate']=profile
    patch=make_preset(track,options,h)
    if h is not None and track.get('reference_audio') and options['family']!='sub':
        # Separated reference: stronger harmonic match, retain a small stable template component.
        base=np.array([1/(i+1) for i in range(24)])
        patch['settings']['wavetables'][0]=wave('Separated reference',.15*base+.85*np.asarray(h))
    if profile:
        s=patch['settings'];s['modulations'][0]={'source':'lfo_1','destination':'osc_1_level'}
        s.update(lfo_1_sync=0.,lfo_1_sync_type=0.,lfo_1_frequency=math.log2(profile['rate_hz']),
                 modulation_1_amount=profile['depth'],modulation_1_bipolar=0.)
        patch['comments']+='; periodic amplitude candidate, not recovered original modulation'
    return patch,analysis

def export_bundle(project,folder,output,options,render=True,plugin=None,progress=lambda s,p:None,cancel=lambda:False):
    plugin=PLUGIN if plugin is None else plugin
    tracks=[t for t in project['tracks'] if eligible(t) and options.get(t['id'],{}).get('selected')]
    if not tracks:raise ValueError(ui_text('请至少勾选一个含有效音符的非鼓组声部'))
    directory=Path(output)/('Vital_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);directory.mkdir(parents=True,exist_ok=False)
    manifest={'version':core.VERSION,'status':'working','project_title':project.get('title'),'project_revision':project['revision'],
              'source_sha256':project.get('source_sha256'),'limitation':ui_text('音色候选近似；谐波来自估计分离来源或混音；不是原始预设恢复'),'tracks':[]}
    try:
        for i,t in enumerate(tracks):
            if cancel():raise core.Cancelled()
            opt=copy.deepcopy(options[t['id']]);stem=file_stem(t,opt);progress(ui_text('制作 ')+opt['name'],i/len(tracks));h=None
            analysis={'status':'template','reason':ui_text('使用音色家族模板'),'windows':0}
            patch,analysis=reference_patch(t,opt,folder,project,cancel);preset=directory/(stem+'.vital');core.save_json(preset,patch)
            midi=directory/(stem+'.mid');view=__import__('song_info').export_view(project);mt=next(v for v in view['tracks'] if v['id']==t['id']);error=write_midi(mt,view['tempos'],midi,view)
            row={'track_id':t['id'],'source_group':t.get('source_group'),'role':t['role'],'track_name':t['name'],
                 'preset':preset.name,'midi':midi.name,'notes':len(core.active_notes(t)),'max_time_error_ms':error,
                 'options':opt,'analysis':analysis,'preview_status':ui_text('未请求试听'),'preview':None}
            manifest['tracks'].append(row)
            if render:
                if not Path(plugin).is_file():row['preview_status']=ui_text('未找到 Vital 插件；预设与 MIDI 已生成')
                else:
                    ns=sorted(core.active_notes(t),key=lambda n:n['start']);start=ns[0]['start']
                    chosen=[dict(pitch=n['pitch'],velocity=n.get('velocity',80),start=max(0,n['start']-start+.15),end=min(6.15,n['end']-start+.15)) for n in ns if n['start']<start+6]
                    wav=directory/(stem+'.wav');request=directory/(stem+'.render-request.json')
                    core.save_json(request,{'preset':str(preset),'plugin':str(plugin),'wav':str(wav),'notes':chosen,'duration':7.5})
                    cmd=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('main.py'))]
                    proc=subprocess.Popen(cmd+['--render-vital',str(request)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=core.CREATE_NO_WINDOW)
                    started=time.monotonic()
                    while proc.poll() is None:
                        if cancel() or time.monotonic()-started>60:
                            proc.kill();proc.communicate()
                            if cancel():raise core.Cancelled()
                            break
                        time.sleep(.1)
                    _,err=proc.communicate()
                    if proc.returncode==0 and wav.exists():row.update(preview=wav.name,preview_status=ui_text('Vital 实际渲染'),preview_start_seconds=start)
                    else:
                        error_file=request.with_suffix('.error.json')
                        detail=core.read_json(error_file)['error'] if error_file.exists() else err.decode('utf-8',errors='replace') or ui_text('渲染超时或插件异常退出')
                        row['preview_status']=ui_text('试听失败，预设已保留：')+detail[-160:]
            row['preset_sha256']=core.sha256(preset);row['midi_sha256']=core.sha256(midi)
            core.save_json(directory/'轨道映射.json',manifest)
        manifest['status']='complete';progress(ui_text('预设与对应 MIDI 已生成'),1.)
    except core.Cancelled:
        manifest['status']='cancelled';raise
    except Exception:
        manifest['status']='failed';raise
    finally:
        core.save_json(directory/'轨道映射.json',manifest)
        with open(directory/'轨道映射.csv','w',encoding='utf-8-sig',newline='') as f:
            w=csv.writer(f);w.writerow([ui_text('声部'),ui_text('来源 ID'),ui_text('Vital 预设'),ui_text('对应 MIDI'),ui_text('试听状态')])
            for t in manifest['tracks']:w.writerow([t['track_name'],t['track_id'],t['preset'],t['midi'],t['preview_status']])
        (directory/'如何使用.md').write_text(ui_text('# Vital 音色与 MIDI\n\n状态：')+manifest['status']+ui_text('\n\n在 FL 添加 Vital，使用 Vital 自身的 Open External Preset 打开 .vital，再向该通道钢琴卷帘导入同名 .mid。每个声部单独实例。MIDI 保留全曲时间与共享速度图，不包含更换插件音色的 Program Change。\n\n同名 WAV 是真实 Vital 渲染的前 6 秒有效乐句加尾音，不是全曲；试听做了防削波衰减。缺少 WAV 时请查轨道映射中的试听状态。音色是家族模板及可选混音谐波辅助生成的候选，不是原曲音色恢复。鼓组继续使用已导出的鼓 MIDI 和采样器。\n\n调参后重新生成会新建目录，不覆盖已有结果。\n'),encoding='utf-8')
    return directory,manifest

"""FL 25 editable project export, using a render-tested serialization scaffold.

No source song events or third-party effects are copied. Vital patches are
embedded; percussion uses original generated one-shots beside the project.
"""
from __future__ import annotations
from i18n import tr as ui_text
import copy,json,math,os,struct,time,uuid,shutil
from pathlib import Path
import numpy as np,soundfile as sf
import core,full_song as fs,vital_presets as vp

PPQ=960
BPM=120.0  # neutral grid: seconds -> ticks, no quantization or drift correction
COLORS={'lead':0xADC88B,'counter':0x82C9EA,'accompaniment':0xDDB183,'bass':0xD5A8BC,'drums':0x829DE0}
u16=lambda n:struct.pack('<H',n)
u32=lambda n:struct.pack('<I',n)
text=lambda s:(str(s)+'\0').encode('utf-16le')

def decode(raw):
    if raw[:4]!=b'FLhd' or raw[14:18]!=b'FLdt' or len(raw)!=22+struct.unpack_from('<I',raw,18)[0]:raise ValueError('FLP header invalid')
    p=22;out=[]
    while p<len(raw):
        i=raw[p];p+=1
        if i<192:n=3 if i==172 else 1 if i<64 else 2 if i<128 else 4
        else:
            n=0;shift=0
            while True:
                b=raw[p];p+=1;n|=(b&127)<<shift;shift+=7
                if shift>35:raise ValueError('Invalid event length')
                if b<128:break
        if p+n>len(raw):raise ValueError('Truncated FLP')
        out.append((i,raw[p:p+n]));p+=n
    return out

def encode(items,header,count):
    out=bytearray()
    for i,d in items:
        out.append(i)
        if i>=192:
            n=len(d)
            while n>=128:out.append((n&127)|128);n>>=7
            out.append(n)
        else:
            expected=3 if i==172 else 1 if i<64 else 2 if i<128 else 4
            if len(d)!=expected:raise ValueError('Invalid fixed event')
        out.extend(d)
    h=bytearray(header);struct.pack_into('<HH',h,10,count,PPQ)
    return bytes(h)+u32(len(out))+out

def pairs(s,key):return [(i,bytes.fromhex(d)) for i,d in s[key]]

def vital_state(scaffold,patch):
    state=next(d for i,d in pairs(scaffold,'vital') if i==213)
    a=scaffold['state_json_offset'];cap=scaffold['state_json_capacity']
    raw=json.dumps(patch,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')
    if len(raw)>8*1024*1024:raise ValueError(ui_text('Vital 预设超过 8 MiB 安全上限'))
    delta=max(0,len(raw)-cap)
    state=bytearray(state[:a]+raw+b' '*max(0,cap-len(raw))+state[a+cap:])
    if delta:
        pos=4;found=False
        while pos<len(state):
            tag,length=struct.unpack_from('<IQ',state,pos)
            if tag==53:
                component=state.index(b'VstW',pos+12,a)
                if component+176!=a or state[component+16:component+20]!=b'CcnK':raise ValueError(ui_text('Vital 大预设封装结构不匹配'))
                struct.pack_into('<Q',state,pos+4,length+delta)
                struct.pack_into('<Q',state,component-8,struct.unpack_from('<Q',state,component-8)[0]+delta)
                for offset in (component+20,component+172):struct.pack_into('>I',state,offset,struct.unpack_from('>I',state,offset)[0]+delta)
                found=True;break
            pos+=12+length
        if not found:raise ValueError(ui_text('缺少 Vital 状态块，未导出不完整预设'))
    state=bytes(state)
    restored=json.JSONDecoder().raw_decode(state[a:].decode('utf8',errors='replace'))[0]
    if restored!=patch:raise ValueError('Vital state readback mismatch')
    return state

def build(project,folder,output,options,progress=lambda text,p:None,cancel=lambda:False):
    import song_info
    original_project=project;project=song_info.export_view(project);options=copy.deepcopy(options);folder=Path(folder)
    info=song_info.validate(project.get('song_info',{}))
    if project.get('song_info') and info['mode']!='fixed':
        raise ValueError('FL 导出需要已选择的固定 BPM；变化速度图请导出 MIDI，不能静默转为 120 BPM。 / Choose fixed BPM for FL export; export variable tempo to MIDI.')
    BPM=info['bpm'] if project.get('song_info') else 60e6/project['tempos'][0]['tempo']
    tracks=[t for t in project['tracks'] if core.active_notes(t)]
    if not tracks:raise ValueError(ui_text('工程没有有效音符'))
    if any(not fs.is_drum(t) for t in tracks) and not vp.PLUGIN.exists():raise ValueError(ui_text('找不到 Vital VST3：')+str(vp.PLUGIN))
    s=core.read_json(core.external_template('flp_scaffold.json'))
    out=Path(output)/('FL工程_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);out.mkdir(parents=True)
    (out/'Presets').mkdir();(out/'Samples').mkdir();(out/'MIDI').mkdir()
    report={'status':'working','source_revision':project.get('revision'),'title':project.get('title'),'grid_bpm':BPM,'ppq':PPQ,
            'time_policy':'Selected BPM; unquantized seconds + shared leading silence','song_info':info,'export_lead_in':project.get('export_lead_in',0),'state_json_offset':s['state_json_offset'],'channels':[],
            'required_plugin':str(vp.PLUGIN),'tested_fl_version':'25.2.4.5242','runtime_verified':False}
    core.save_json(out/'导出报告.json',report)
    try:
        channels=[]
        for row,t in enumerate(tracks):
            if cancel():raise core.Cancelled()
            progress(ui_text('准备 FL 声部 ')+str(row+1)+'/'+str(len(tracks))+' · '+t['name'],row/len(tracks)*.75)
            notes=fs.events(t);opt=options.get(t['id'],vp.default_options(project)[t['id']])
            midi=out/'MIDI'/(f'{row + 1:02d}_'+core.safe_name(t['name'])[:65]+'.mid');vp.write_midi(t,project['tempos'],midi,project)
            if fs.is_drum(t):
                for pitch in sorted({n['pitch'] for n in notes}):
                    sample=out/'Samples'/f'{core.safe_name(t['id'])}_GM_{pitch}.wav'
                    from complex_audio import sample_for
                    source_sample=sample_for(t,pitch,folder)
                    if source_sample:shutil.copy2(source_sample,sample)
                    else:sf.write(sample,fs.drum_hit(pitch,127),fs.SR,subtype='PCM_24')
                    subset=[dict(n) for n in notes if n['pitch']==pitch]
                    # Sampler root C5: generated one-shots carry their own drum pitch.
                    for n in subset:n['pitch']=60
                    channels.append(dict(name=t['name']+f' · GM {pitch}',role='drums',notes=subset,sample=sample,source_id=t['id'],gm=pitch,sample_origin='estimated_stem' if source_sample else 'basic_synthetic'))
            else:
                h=None
                patch,analysis=vp.reference_patch(next(v for v in original_project['tracks'] if v['id']==t['id']),opt,folder,original_project,cancel);preset=out/'Presets'/(vp.file_stem(t,opt)+'.vital');core.save_json(preset,patch)
                channels.append(dict(name=opt['name'],role=t['role'],notes=notes,state=vital_state(s,patch),preset=preset,source_id=t['id'],preset_name=patch['preset_name']))
        if len(channels)>125:raise ValueError(ui_text('FL 导出最多支持 125 个音源通道（鼓按音高展开后计数）'))
        cb=[];nb=[];mb=[];clips=[]
        song_ticks=round(max(float(project['duration']),max(n['end'] for c in channels for n in c['notes']))*PPQ*BPM/60)
        for cid,c in enumerate(channels):
            if cancel():raise core.Cancelled()
            color=COLORS.get(c['role'],0xBBBBBB);gain=fs.GAINS.get(c['role'],.65)
            for i,d in pairs(s,'sampler' if 'sample' in c else 'vital'):
                if i==64:d=u16(cid)
                elif i==0:d=b'\1'
                elif i==203:d=text(c['name'])
                elif i==213:d=c['state']
                elif i==196:d=text(str(c['sample']))
                elif i==104:d=u16(cid+1)
                elif i==145:d=u32(0)
                elif i==219:d=struct.pack('<II',6400,12800)+d[8:]
                elif i==229:d=struct.pack('<iI',0,round(12800*gain))+d[8:]
                elif i==128:d=u32(color)
                elif i==155:d=u32(0)
                elif i==212:
                    z=bytearray(d);struct.pack_into('<H',z,16,struct.unpack_from('<H',z,16)[0]&~1);d=bytes(z)
                cb.append((i,d))
            pid=cid+1;packed=[];max_tick_error=0.
            for n in c['notes']:
                start=round(n['start']*PPQ*BPM/60);end=max(start+1,round(n['end']*PPQ*BPM/60));length=end-start
                max_tick_error=max(max_tick_error,abs(start/(PPQ*BPM/60)-n['start']))
                packed.append(struct.pack('<IHHIHH8B',start,0x4000,cid,length,n['pitch'],0,120,0,64,0,64,round(n['velocity']*100/127),128,128))
            length=max(song_ticks,max(struct.unpack_from('<I',n)[0]+struct.unpack_from('<I',n,8)[0] for n in packed))
            nb.extend([(65,u16(pid)),(224,b''.join(packed))]);mb.extend([(65,u16(pid)),(193,text(c['name'])),(150,u32(color)),(164,u32(length)),(52,b'\0'),(171,u32(0)),(252,b'\0\0'),(253,text('0,0,0,0,0,0,0,0,0,0,0,0'))])
            clip=bytearray.fromhex(s['clip']);struct.pack_into('<IHHI',clip,0,0,20480,20480+pid,length);struct.pack_into('<H',clip,12,499-cid);struct.pack_into('<H',clip,18,64);struct.pack_into('<I',clip,32,pid);clips.append(bytes(clip))
            report['channels'].append(dict(index=cid,name=c['name'],source_id=c['source_id'],role=c['role'],mixer=cid+1,pattern=pid,notes=len(packed),
                instrument='Sampler' if 'sample' in c else 'Vital',preset=str(c.get('preset','')),sample=str(c.get('sample','')),sample_origin=c.get('sample_origin'),preset_name=c.get('preset_name'),gm=c.get('gm'),max_onset_error_seconds=max_tick_error))
        prefix=[(i,u32(round(BPM*1000)) if i==156 else bytes([info['numerator']]) if i==17 else bytes([info['denominator']]) if i==18 else u16(1) if i==67 else b'\1' if i==9 else d) for i,d in pairs(s,'prefix') if i not in (231,195,207)]
        prefix.extend([(202,text(str(out)+'\\')),(194,text(project.get('title','StarScore'))),(231,text('StarScore')),(207,text(info['artist'])),(195,text(info['notes']))])
        second=next((j for j,(i,d) in enumerate(cb) if i==64 and d==u16(1)),len(cb))
        result=prefix+nb+cb[:second]+mb+cb[second:]+[(99,u16(0)),(241,text(ui_text('StarScore · 全曲'))),(36,b'\0'),(233,b''.join(clips))]
        for cid,c in enumerate(channels):
            d=bytearray.fromhex(s['playlist_track']);struct.pack_into('<II',d,0,cid+1,COLORS.get(c['role'],0xBBBBBB));d[12]=1
            result.extend([(238,bytes(d)),(239,text(c['name'])),(43,b'\0')])
        result.append((103,u16(126)))
        for ins in range(126):
            result.append((204,text('MASTER' if ins==0 else channels[ins-1]['name'] if ins<=len(channels) else f'Insert {ins}')))
            for i,d in pairs(s,'mixer'):
                if i==235:d=b'\0' if ins==0 else b'\1'
                if i==147 and ins==0:d=u32(0)
                result.append((i,d))
        params=bytearray();base=bytes.fromhex(s['tail'][0][1])
        defaults=[struct.unpack_from('<IBBHi',base,p) for p in range(0,len(base),12)]
        params.extend(struct.pack('<IBBHi',0,0,0,16384,12800))
        for ins in range(126):
            for a,param,flag,addr,value in defaults:
                if not 0x7000<=addr<0x7040:continue
                if param==192:value=9000 if ins==0 else 12800
                params.extend(struct.pack('<IBBHi',a,param,flag,addr+ins*64,value))
        result.extend([(225,bytes(params))]+pairs(s,'tail')[1:])
        raw=encode(result,bytes.fromhex(s['header']),len(channels));verify(raw,report)
        if cancel():raise core.Cancelled()
        progress(ui_text('校验音符、Vital 状态与编曲位置'),.95)
        target=out/(core.safe_name(project.get('title','StarScore'))[:65]+'.flp');pending=out/'工程生成中.partial';pending.write_bytes(raw);os.replace(pending,target)
        report.update(status='complete',flp=str(target),sha256=core.sha256(target),source_tracks=len(tracks),channel_count=len(channels),note_count=sum(len(c['notes']) for c in channels),duration=project['duration'])
        core.save_json(out/'导出报告.json',report)
        (out/'打开说明.txt').write_text(ui_text('打开同目录 FLP，切到 SONG 模式播放。旋律已内嵌 Vital；增强工程鼓优先采用估计分离轨采样，其余使用基础合成鼓。\nBPM 与拍号来自歌曲信息；音符不量化。对齐小节时会按报告统一添加前置静音。\n请保留整个输出文件夹。移动文件夹后如 FL 提示缺少采样，选择此处 Samples 文件夹重新定位。\n需要本机安装 Vital VST3。验证目标 FL Studio 25.2.4；其他版本兼容性未验证。\n'),encoding='utf8')
        progress(ui_text('FL 工程已生成'),1.);return out,report
    except Exception as e:
        report.update(status='cancelled' if isinstance(e,core.Cancelled) else 'failed',error=str(e));core.save_json(out/'导出报告.json',report);raise

def verify(raw,report):
    e=decode(raw);rows=report['channels'];count=len(rows)
    assert abs(next(int.from_bytes(d,'little')/1000 for i,d in e if i==156)-report['grid_bpm'])<=.001
    if 'song_info' in report:
        assert next(d[0] for i,d in e if i==17)==report['song_info']['numerator']
        assert next(d[0] for i,d in e if i==18)==report['song_info']['denominator']
    if struct.unpack_from('<H',raw,10)[0]!=count:raise ValueError('Channel count mismatch')
    if [int.from_bytes(d,'little') for i,d in e if i==64]!=list(range(count)):raise ValueError('Channel identity mismatch')
    notes=[d for i,d in e if i==224]
    if len(notes)!=count or sum(len(d)//24 for d in notes)!=sum(r['notes'] for r in rows):raise ValueError('Note count mismatch')
    for cid,d in enumerate(notes):
        if len(d)%24:raise ValueError('Invalid note size')
        for p in range(0,len(d),24):
            n=struct.unpack_from('<IHHIHH8B',d,p)
            if n[2]!=cid or n[3]<1 or n[4]>127:raise ValueError('Invalid note routing')
    if len(next(d for i,d in e if i==233))!=80*count:raise ValueError('Playlist count mismatch')
    for r in rows:
        if r['sample'] and not Path(r['sample']).is_file():raise ValueError('Missing sample')
    states=[d for i,d in e if i==213];expected=[r for r in rows if r['instrument']=='Vital']
    if len(states)!=len(expected):raise ValueError('Missing Vital')
    for d,r in zip(states,expected):
        a=report.get('state_json_offset',d.index(b'VstW')+176);start=a-176;size=struct.unpack_from('>I',d,start+20)[0]+24
        p,_=vp._vital_component(d[start:start+size])
        if p['preset_name']!=r['preset_name']:raise ValueError('Wrong Vital preset')
    return True

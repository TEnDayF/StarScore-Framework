"""Local, provenance-aware audio descriptors; scores are not confidence probabilities."""
from i18n import tr as ui_text
import copy,hashlib,json,math
from pathlib import Path
import numpy as np,soundfile as sf
from scipy import signal
import core

def descriptor(x,sr,pitch):
    x=np.asarray(x,dtype=np.float64)
    if x.ndim==2:x=x.mean(axis=1)
    if len(x)<256 or not np.isfinite(x).all() or np.sqrt(np.mean(x*x))<1e-6:return None
    nfft=2**int(math.ceil(math.log2(min(len(x),8192)*2)))
    frame=min(len(x),4096);_,_,z=signal.stft(x,sr,nperseg=frame,noverlap=frame//2,nfft=nfft,boundary=None)
    mag=np.abs(z);spec=np.median(mag,axis=1);hz=440*2**((pitch-69)/12)
    harmonics=[]
    for h in range(1,25):
        c=round(hz*h*nfft/sr);w=max(1,round(max(9,hz*.025)*nfft/sr))
        harmonics.append(float(np.max(spec[max(0,c-w):min(len(spec),c+w+1)])) if c<len(spec) else 0.)
    h=np.sqrt(np.maximum(harmonics,0));h/=max(np.linalg.norm(h),1e-10)
    edges=np.linspace(0,len(x),9).astype(int);env=np.array([np.sqrt(np.mean(x[a:b]**2)) for a,b in zip(edges,edges[1:])]);env/=max(float(np.max(env)),1e-9)
    variation=float(np.std(np.sum(mag,axis=0))/max(np.mean(np.sum(mag,axis=0)),1e-9))
    return dict(harmonics=h.tolist(),envelope=env.tolist(),variation=min(2.,variation),duration=len(x)/sr)

def distance(a,b):
    if not a or not b:return float('inf')
    ha,hb=np.array(a['harmonics']),np.array(b['harmonics'])
    spectral=max(0.,1-float(np.dot(ha,hb)/max(np.linalg.norm(ha)*np.linalg.norm(hb),1e-9)))
    envelope=float(np.mean(abs(np.array(a['envelope'])-np.array(b['envelope']))))
    return .7*spectral+.25*envelope+.05*min(1.,abs(a['variation']-b['variation']))

def patch_signature(patch):
    s=copy.deepcopy(patch['settings'])
    return hashlib.sha256(json.dumps(s,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def track_descriptor(track,audio,cancel=lambda:False):
    ns=core.active_notes(track);eligible=[n for n in ns if .15<=n['end']-n['start']<=3 and
        not any(o['id']!=n['id'] and o['start']<n['end']-.03 and o['end']>n['start']+.03 for o in ns)]
    if len(eligible)<3:return None
    indices=np.linspace(0,len(eligible)-1,min(12,len(eligible))).astype(int);features=[]
    with sf.SoundFile(audio) as f:
        sr=f.samplerate
        for i in indices:
            if cancel():raise core.Cancelled()
            n=eligible[i];f.seek(min(len(f),max(0,round(n['start']*sr))))
            x=f.read(round(min(1.4,n['end']-n['start'])*sr),dtype='float32',always_2d=True)
            d=descriptor(x,sr,n['pitch'])
            if d:features.append(d)
    if len(features)<3:return None
    result={k:np.median([f[k] for f in features],axis=0).tolist() for k in ('harmonics','envelope')}
    result.update(variation=float(np.median([f['variation'] for f in features])),duration=float(np.median([f['duration'] for f in features])),windows=len(features))
    return result

def choose(query,index,exclude_songs=(),exclude_signatures=()):
    if not query:return None
    ranked=[]
    for entry in index.get('entries',[]):
        if set(entry['songs'])&set(exclude_songs) or entry['signature'] in exclude_signatures:continue
        score=min(distance(query,d) for d in entry['descriptors'])
        ranked.append((score,entry))
    if not ranked:return None
    score,entry=min(ranked,key=lambda pair:pair[0])
    return dict(entry=entry,score=score)

def candidate(track,options,folder,project,cancel=lambda:False):
    index_path=core.resource('timbre_bank/index.json')
    if not index_path.is_file():raise ValueError(ui_text('未安装个人音色参考库，请关闭参考库匹配后重试。'))
    audio=Path(folder)/track.get('reference_audio',project['audio']);query=track_descriptor(track,audio,cancel)
    result=choose(query,core.read_json(index_path))
    # Conservative acceptance; polyphonic/ambiguous windows fall back to the existing generator.
    if not result or result['score']>.12:raise ValueError(ui_text('参考库未找到足够接近的候选，或独奏音符窗口不足。请关闭参考库匹配，改用谐波辅助制作。'))
    entry=result['entry'];path=index_path.parent/entry['file']
    if core.sha256(path)!=entry['sha256']:raise ValueError(ui_text('本地音色候选库校验失败：')+path.name)
    patch=core.read_json(path);patch['preset_name']=options['name'];patch['comments']=patch.get('comments','')+'; StarScore local library candidate, not recovered source preset'
    return patch,dict(status='local_library_candidate',source_songs=entry['songs'],signature=entry['signature'],score=result['score'],windows=query['windows'],limitation='Descriptor similarity is not perceptual equivalence; source automation and mix effects not recovered.')

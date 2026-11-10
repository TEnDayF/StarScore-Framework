"""Conservative repeated-attack protection. Never edits or removes MIDI notes."""
from pathlib import Path
import numpy as np
import soundfile as sf
from scipy import signal

def repeated_attack(x, sr, boundary, pitch):
    x=np.asarray(x,dtype=float)
    if x.ndim==2:x=x.mean(axis=1)
    if len(x)<sr*.12 or not np.isfinite(x).all():return False
    frame=max(128,round(sr*.024));hop=max(1,round(sr*.004))
    f,t,z=signal.stft(x,sr,nperseg=frame,noverlap=frame-hop,nfft=frame*4,boundary=None)
    hz=440*2**((pitch-69)/12)
    mask=np.zeros(len(f),bool)
    for h in range(1,5):mask|=abs(f-h*hz)<max(sr/frame*.65,h*hz*.025)
    env=np.sqrt(np.sum(abs(z[mask])**2,axis=0))
    def level(a,b):
        v=env[(t>=boundary+a)&(t<boundary+b)]
        return float(np.median(v)) if len(v) else 0.
    early=level(-.12,-.065);valley=level(-.035,-.008);after=level(.015,.06)
    # A reset AND a new attack must be present; continuous sustains remain reviewable.
    return bool(after>1e-5 and early>1e-5 and valley<early*.48 and after>valley*2.4)

def protect_repetitions(tracks,folder,audio='source.wav',cancel=lambda:False):
    import core
    examined=protected=0
    for track in tracks:
        if track['role']=='drums':continue
        pairs=core.merge_candidates([track])
        if not pairs:continue
        path=Path(folder)/track.get('reference_audio',audio)
        lookup={n['id']:n for n in track['notes']}
        with sf.SoundFile(path) as f:
            for pair in pairs:
                if cancel():raise core.Cancelled()
                n=lookup[pair['ids'][1]];start=max(0,n['start']-.18)
                f.seek(min(len(f),round(start*f.samplerate)))
                x=f.read(round(.32*f.samplerate),dtype='float32',always_2d=True)
                yes=repeated_attack(x,f.samplerate,n['start']-start,n['pitch']);examined+=1
                n['repeat_attack_check']={'start':n['start'],'pitch':n['pitch'],'protected':yes}
                protected+=int(yes)
    return {'examined':examined,'protected':protected,'method':'harmonic_envelope_reset_v1',
            'limitation':'Conservative merge-suggestion filter; not a transcription accuracy score.'}

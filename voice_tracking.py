"""Register anchored interval decoding with explicit abstention and source conservation.

These are uncalibrated musical priors, not a trained source separation model.
The decoder can leave rests; it never creates notes to fill them.
"""
from i18n import tr as ui_text
import numpy as np

def register_anchor(notes):
    if not notes:return 60.
    # Saturate repeated attacks in each one-second bin so a rapid ostinato cannot
    # win merely by having more tokens than a sustained melody.
    hist=np.zeros(128)
    for sec in sorted({int(n['start']) for n in notes}):
        local=np.zeros(128)
        for n in notes:
            if int(n['start'])==sec:local[n['pitch']]+=min(.65,max(.08,n['end']-n['start']))
        hist+=np.minimum(local,.8)
    smooth=np.convolve(hist,np.array([1,2,3,4,5,4,3,2,1])/25,'same')
    center=int(np.argmax(smooth))
    nearby=[n['pitch'] for n in notes if abs(n['pitch']-center)<=9]
    return float(np.median(nearby)) if nearby else float(center)

def melodic_path(notes,anchor=None,span=12):
    if not notes:return []
    anchor=register_anchor(notes) if anchor is None else float(anchor)
    ns=sorted(notes,key=lambda n:(n['start'],n['pitch'],n['end']))
    starts=np.array([n['start'] for n in ns]);ends=np.array([n['end'] for n in ns]);pitches=np.array([n['pitch'] for n in ns])
    scores=np.zeros(len(ns));parents=np.full(len(ns),-1,dtype=int)
    for i,n in enumerate(ns):
        distance=abs(n['pitch']-anchor)
        # Out-of-register evidence is withheld from this voice, not reassigned
        # to a different pitch, deleted, or forced into a monophonic chain.
        if distance>span:scores[i]=-1e8;continue
        dur=n['end']-n['start']
        reward=.6+.6*np.sqrt(min(dur,.8))-.045*distance
        if dur>2:reward-=.4
        if n.get('candidate'):reward-=.8
        best=0.;parent=-1
        js=np.flatnonzero((starts[:i]<n['start']-.018)&(ends[:i]<=n['start']+.045))
        if len(js):
            gaps=np.maximum(0,n['start']-ends[js]);jumps=np.abs(pitches[js]-n['pitch'])
            # Silence has bounded cost; switching to bass during a rest does not
            # receive an unconditional reward as it did in 0.1.
            costs=.065*jumps+.22*np.minimum(gaps,1.5)+np.maximum(0,ends[js]-n['start'])*5
            costs+=np.where(jumps>9,(jumps-9)*.14,0)
            values=scores[js]-costs
            k=int(np.argmax(values))
            if values[k]>0:best=float(values[k]);parent=int(js[k])
        scores[i]=reward+best;parents[i]=parent
    k=int(np.argmax(scores));path=[]
    while k>=0:
        if scores[k]>0:path.append(ns[k]['id'])
        k=int(parents[k])
    return path[::-1]

def bass_partition(notes):
    """Extract a distinct low register only when simultaneous voices support it.

    A descending solo line is not split just because it crosses a pitch cutoff.
    """
    if len(notes)<16:return [],list(notes),None
    pitches=np.array([n['pitch'] for n in notes],float)
    lo,hi=np.percentile(pitches,[20,80])
    for _ in range(12):
        split=(lo+hi)/2;a=pitches[pitches<=split];b=pitches[pitches>split]
        if not len(a) or not len(b):return [],list(notes),None
        lo,hi=float(np.median(a)),float(np.median(b))
    split=min(55.,(lo+hi)/2)
    low=[n for n in notes if n['pitch']<=split];high=[n for n in notes if n['pitch']>split]
    if hi-lo<14 or lo>57 or len(low)<8 or len(high)<8:return [],list(notes),None
    simultaneous=sum(any(h['start']<n['end']-.025 and h['end']>n['start']+.025 for h in high) for n in low)
    if simultaneous/len(low)<.2:return [],list(notes),None
    center=register_anchor(low)
    ids=set(melodic_path(low,anchor=center,span=12))
    line=[n for n in low if n['id'] in ids]
    # Permit synchronous octave reinforcement, not arbitrary low-register chords.
    for n in low:
        if any(abs(n['pitch']-p['pitch']) in (12,24) and abs(n['start']-p['start'])<=.035 and abs(n['end']-p['end'])<=.08 for p in line):ids.add(n['id'])
    return [n for n in low if n['id'] in ids],[n for n in notes if n['id'] not in ids],{'center':center,'upper':split,'evidence':'low_voice_path_with_octave_layers'}

DRUM_NAMES={35:'Acoustic Kick',36:'Kick',37:'Side Stick',38:'Snare',39:'Clap',40:'Electric Snare',41:'Low Floor Tom',42:'Closed Hi-Hat',43:'High Floor Tom',44:'Pedal Hi-Hat',45:'Low Tom',46:'Open Hi-Hat',47:'Low Mid Tom',48:'High Mid Tom',49:'Crash',50:'High Tom',51:'Ride',52:'China',53:'Ride Bell',54:'Tambourine',55:'Splash',56:'Cowbell',57:'Crash 2',58:'Vibraslap',59:'Ride 2'}
def drum_family(pitch):
    if pitch in (35,36):return 'kick',ui_text('底鼓')
    if pitch in (37,38,39,40):return 'snare',ui_text('军鼓／拍手')
    if pitch in (42,44,46):return 'hihat',ui_text('踩镲')
    if pitch in (41,43,45,47,48,50):return 'toms',ui_text('通鼓')
    if pitch in (49,51,52,53,55,57,59):return 'cymbals',ui_text('吊镲／叮叮镲')
    return 'percussion',ui_text('其他打击乐')

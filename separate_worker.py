"""Isolated UMXHQ CPU runner. Verified local MIT weights; no model downloads."""
import json, sys, time
from pathlib import Path

def run(request):
    import numpy as np
    import soundfile as sf
    import torch
    import openunmix
    from separation_models import verify
    r=json.loads(Path(request).read_text(encoding='utf8'))
    torch.set_num_threads(8);torch.manual_seed(42)
    paths=verify(r['model_directory'])
    # pretrained=False prevents torch.hub network access; weights_only avoids
    # arbitrary pickle objects. Require an exact state dictionary match.
    model=openunmix.umxhq(targets=list(paths),pretrained=False,device='cpu',niter=1)
    for stem,path in paths.items():
        state=torch.load(path,map_location='cpu',weights_only=True)
        # Official 2019 checkpoints include transform buffers outside today's
        # spectrogram network. Accept only these known legacy extras, retaining
        # strict validation of every learned parameter.
        if 'sample_rate' in state:
            if float(state.pop('sample_rate'))!=44100:raise ValueError('Unexpected UMXHQ sample rate')
        for key in ('stft.window','transform.0.window'):
            if key in state:
                window=state.pop(key)
                if window.shape!=(4096,) or not torch.allclose(window,torch.hann_window(4096),atol=1e-6):
                    raise ValueError('Unexpected UMXHQ transform window')
        model.target_models[stem].load_state_dict(state,strict=True)
    model.freeze()
    sources=list(paths)
    x,sr=sf.read(r['audio'],dtype='float32',always_2d=True)
    if sr!=44100 or x.shape[1]!=2 or not len(x) or not np.isfinite(x).all():raise ValueError('Expected finite nonempty stereo 44100 Hz input')
    x=torch.from_numpy(x.T.copy())
    # Outer chunks bound memory and supply honest progress. Crossfade overlapping predictions.
    length=x.shape[-1];chunk=sr*28;overlap=sr*2;step=chunk-overlap
    output=np.zeros((len(sources),2,length),dtype=np.float32);weight=np.zeros(length,np.float32)
    starts=list(range(0,length,step));tic=time.time()
    for k,a in enumerate(starts):
        b=min(length,a+chunk)
        with torch.inference_mode():
            block=x[:,a:b]
            # Reflect-padding STFT requires at least 2049 samples.
            if block.shape[-1]<4096:block=torch.nn.functional.pad(block,(0,4096-block.shape[-1]))
            pred=model(block[None])[0,:,:,:b-a]
        y=pred.numpy();w=np.ones(b-a,np.float32)
        if not np.isfinite(y).all():raise ValueError('Non-finite separator output')
        if a:w[:min(overlap,len(w))]=np.linspace(0,1,min(overlap,len(w)))
        if b<length:w[-overlap:]=np.linspace(1,0,overlap)
        output[:,:,a:b]+=y*w;weight[a:b]+=w
        print(json.dumps({'progress':b/length,'elapsed':time.time()-tic}),flush=True)
        if b==length:break
    output/=np.maximum(weight,1e-8)
    folder=Path(r['output']);folder.mkdir(parents=True,exist_ok=True)
    rows={}
    for name,y in zip(sources,output):
        path=folder/(name+'.wav');sf.write(path,y.T,sr,subtype='FLOAT')
        rows[name]={'file':path.name,'rms':float(np.sqrt(np.mean(y*y))),'peak':float(np.max(abs(y)))}
    residual=x.numpy()-output.sum(axis=0)
    report={'model':'Open-Unmix UMXHQ / Zenodo 3370489','license':'MIT','sample_rate':sr,'frames':length,'elapsed':time.time()-tic,'stems':rows,
            'reconstruction_residual_rms':float(np.sqrt(np.mean(residual**2))),
            'limitation':'Estimated stems; residual consistency is not separation accuracy.'}
    (folder/'separation.json').write_text(json.dumps(report,indent=2),encoding='utf8')

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    run(sys.argv[1])

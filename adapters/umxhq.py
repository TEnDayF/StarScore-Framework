from pathlib import Path
import json,subprocess,time
import soundfile as sf
import core,app_settings
from adapter_registry import require_file,require_directory
from i18n import tr as ui_text

def separate(audio,job,progress,cancel):
    from separation_models import verify,WEIGHTS
    settings=app_settings.load()
    model=require_directory(settings['umxhq_dir'],'UMXHQ')
    try:verify(model)
    except (ValueError,FileNotFoundError) as e:raise ValueError(ui_text('UMXHQ 权重缺失或校验不符。请在设置中关联完整的四份官方权重，不会回退到其他模型。')+'\n'+str(e)) from e
    worker=core.root()/'separate_worker.py'
    python=require_file(settings['separation_python'],'UMXHQ Python')
    if not worker.is_file() or not python.is_file():
        raise FileNotFoundError('UMXHQ worker/runtime missing; legacy HTDemucs fallback is disabled.')
    source=job/'source.wav'
    core.checked_run([core.ffmpeg(),'-v','error','-nostdin','-i',str(audio),'-ac','2','-ar','44100','-c:a','pcm_f32le','-y',str(source)],cancel)
    dest=job/'stems';request=job/'separate-request.json'
    core.save_json(request,dict(audio=str(source),model_directory=str(model),output=str(dest)))
    with open(job/'separation.log','w',encoding='utf8') as log:
        proc=subprocess.Popen([str(python),str(worker),str(request)],stdout=log,stderr=log,creationflags=core.CREATE_NO_WINDOW)
        try:
            while proc.poll() is None:
                if cancel():raise core.Cancelled()
                # Flush-safe JSON lines; do not block cancellation waiting on stdout.
                lines=(job/'separation.log').read_text(encoding='utf8',errors='replace').splitlines()
                for line in reversed(lines):
                    try:status=json.loads(line);progress(ui_text('分离 Bass / 鼓组 / 合成器来源'),status['progress']);break
                    except (ValueError,KeyError,TypeError):pass
                time.sleep(.2)
            if proc.returncode:raise RuntimeError(ui_text('分离运行失败。请在设置中检查适配器，确认所选 Python 已安装 requirements-separation.txt 中的依赖。')+'\n'+(job/'separation.log').read_text(encoding='utf8',errors='replace')[-1200:])
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
    report=core.read_json(dest/'separation.json');expected=sf.info(source)
    for name in ('bass','drums','other','vocals'):
        info=sf.info(dest/(name+'.wav'))
        if info.frames!=expected.frames or info.samplerate!=44100 or info.channels!=2:raise ValueError(ui_text('分离轨不完整：')+name)
    report['model_sha256']={stem:entry[1] for stem,entry in WEIGHTS.items()};core.save_json(dest/'separation.json',report)
    return report

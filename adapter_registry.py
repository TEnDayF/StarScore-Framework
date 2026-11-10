"""Built-in adapter registry. Selection imports only that adapter; never downloads."""
from pathlib import Path
import subprocess
import app_settings
from i18n import tr as ui_text

CATALOG = (
    dict(id='muscriptor',name='MuScriptor',purpose='MIDI transcription',license='CC BY-NC 4.0',
         author='Mirelo / Kyutai; GGUF: Damien Ronssin',url='https://huggingface.co/DamRsn/muscriptor-gguf',
         code='https://github.com/DamRsn/muscriptor.cpp',supported=True),
    dict(id='umxhq',name='Open-Unmix UMXHQ',purpose='Bass / drums / vocals / other',license='MIT (UMXHQ weights)',
         author='Fabian-Robert Stöter / Antoine Liutkus',url='https://zenodo.org/records/3370489',
         code='https://github.com/sigsep/open-unmix-pytorch',supported=True),
    dict(id='htdemucs',name='HTDemucs',purpose='Upstream reference only',license='Weight license unclear',
         author='Demucs contributors',url='https://github.com/adefossez/demucs#license',
         code='https://github.com/adefossez/demucs',supported=False),
)

def require_file(value,label):
    if not value or not Path(value).is_file():
        reason=ui_text('未配置。请打开设置 → 模型与引擎，导入本地文件并保存。') if not value else ui_text('文件不存在或已移动。请重新关联本地文件并保存。')+'\n'+value
        raise FileNotFoundError('[RESOURCE_FILE] '+label+': '+reason)
    return Path(value)

def require_directory(value,label):
    if not value or not Path(value).is_dir():
        raise FileNotFoundError(label+': '+ui_text('尚未配置或文件不存在，请打开设置 → 模型与引擎。'))
    return Path(value)

def transcribe(*args,**kwargs):
    if app_settings.load()['transcription_adapter']!='muscriptor':
        raise FileNotFoundError(ui_text('转写适配器未启用；可以继续编辑 MIDI。请到设置中配置。'))
    from adapters.muscriptor import transcribe as run
    return run(*args,**kwargs)

def separate(*args,**kwargs):
    if app_settings.load()['separation_adapter']!='umxhq':
        raise FileNotFoundError(ui_text('分离适配器未启用，请配置 UMXHQ 或使用标准转写。'))
    from adapters.umxhq import separate as run
    return run(*args,**kwargs)

def selected_engine(settings):
    import os
    cpu=settings['backend']=='cpu' or (os.name=='nt' and not (Path(os.environ.get('WINDIR','C:/Windows'))/'System32/vulkan-1.dll').exists())
    return require_file(settings['engine_cpu_path'] if cpu else settings['engine_path'] or settings['engine_cpu_path'],
                        'MuScriptor CPU' if cpu else 'MuScriptor')

def preflight(model,complex_mode=False,settings=None):
    s=settings or app_settings.load()
    if s['transcription_adapter']!='muscriptor':
        raise FileNotFoundError(ui_text('转写适配器未启用；可以继续编辑 MIDI。请到设置中配置。'))
    if model not in ('small','medium','large'):raise ValueError('Unknown model: '+str(model))
    weight=s['model_paths'].get(model) or (str(Path(s['model_dir'])/f'muscriptor-{model}-f16.gguf') if s['model_dir'] else '')
    require_file(weight,'MuScriptor '+model)
    selected_engine(s);require_file(s['ffmpeg_path'],'FFmpeg')
    if complex_mode:
        if s['separation_adapter']!='umxhq':raise FileNotFoundError(ui_text('分离适配器未启用，请配置 UMXHQ 或使用标准转写。'))
        folder=require_directory(s['umxhq_dir'],'UMXHQ')
        from separation_models import WEIGHTS
        for name,_ in WEIGHTS.values():require_file(str(folder/name),'UMXHQ '+name)
        require_file(s['separation_python'],'UMXHQ Python')

def probe_ffmpeg(value):
    path=require_file(value,'FFmpeg')
    flags=0x08000000 if __import__('os').name=='nt' else 0
    r=subprocess.run([str(path),'-version'],capture_output=True,timeout=12,creationflags=flags)
    line=r.stdout.decode('utf8',errors='replace').splitlines()
    if r.returncode or not line or not line[0].startswith('ffmpeg version '):
        raise ValueError(ui_text('所选程序未返回有效 FFmpeg 版本。'))
    # Exercise the exact decode/resample primitives used by the application.
    r=subprocess.run([str(path),'-v','error','-f','lavfi','-i','anullsrc=r=44100:cl=stereo',
                      '-t','0.02','-ac','1','-ar','16000','-f','f32le','pipe:1'],
                     capture_output=True,timeout=12,creationflags=flags)
    if r.returncode or len(r.stdout)!=320*4:
        raise ValueError(ui_text('FFmpeg 音频转换测试失败。'))
    return line[0]

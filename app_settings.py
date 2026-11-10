"""Validated application preferences, separate from per-project edits."""
import json,os,sys
from pathlib import Path

def app_root():return Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
def config_dir():return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'StarScoreMidi'
def defaults():
    return dict(output=str(Path.home()/'Music'/'星谱 MIDI 输出'),language='zh_CN',model='medium',model_dir='',adaptive=True,
                complex_mode=False,backend='auto',threads=min(6,os.cpu_count() or 4),
                vital_template_path='',fl_template_path='',vital_path='C:/Program Files/Common Files/VST3/Vital.vst3',ffmpeg_path='',engine_path='',engine_cpu_path='',
                umxhq_dir='',separation_python='',model_paths={},transcription_adapter='none',separation_adapter='none',resource_schema=1)
def validate(data):
    out=dict(defaults());out.update(data)
    if out.get('language','zh_CN') not in ('zh_CN','en'):raise ValueError('请选择简体中文或 English')
    if out.get('model') not in ('small','medium','large'):raise ValueError('请选择 Small、Medium 或 Large 模型')
    if out.get('backend') not in ('auto','cpu'):raise ValueError('请选择自动或 CPU 后端')
    if isinstance(out.get('threads'),bool) or not isinstance(out.get('threads'),int) or not 1<=out['threads']<=64:raise ValueError('线程数必须在 1 到 64 之间')
    for k in ('output','model_dir','vital_template_path','fl_template_path','vital_path','ffmpeg_path','engine_path','engine_cpu_path','umxhq_dir','separation_python'):
        if not isinstance(out.get(k),str):raise ValueError('路径必须是文本')
        out[k]=out[k].strip()
        if out[k] and not Path(out[k]).is_absolute():raise ValueError('请选择完整的绝对路径：'+k)
    if not out['output']:raise ValueError('请选择默认输出目录')
    for k in ('adaptive','complex_mode'):
        if not isinstance(out.get(k),bool):raise ValueError('开关设置无效：'+k)
    if out['transcription_adapter'] not in ('none','muscriptor') or out['separation_adapter'] not in ('none','umxhq'):raise ValueError('未知适配器 / Unknown adapter')
    if not isinstance(out['model_paths'],dict):raise ValueError('模型路径映射无效 / Invalid model paths')
    for k,v in out['model_paths'].items():
        if k not in ('small','medium','large') or not isinstance(v,str) or (v and not Path(v).is_absolute()):raise ValueError('模型文件路径无效 / Invalid model path')
    return out
def load(folder=None):
    result=defaults();p=Path(folder or config_dir())/'settings.json'
    try:
        saved=json.loads(p.read_text(encoding='utf-8-sig'))
        if isinstance(saved,dict):result.update(saved)
        return validate(result)
    except (OSError,ValueError,TypeError):return defaults()
def save(data,folder=None):
    data=validate(data);p=Path(folder or config_dir())/'settings.json';p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix('.writing');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temp,p)
    return data

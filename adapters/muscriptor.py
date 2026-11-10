from pathlib import Path
import os,subprocess,json
import core,app_settings
from adapter_registry import require_file,selected_engine
from i18n import tr as ui_text

def transcribe(pcm,output,model,progress=lambda *a:None,cancel=lambda:False):
    settings=app_settings.load()
    weights=core.resource(f'models/muscriptor-{model}-f16.gguf')
    engine=selected_engine(settings)
    require_file(str(weights),'MuScriptor '+model)
    if core.sha256(weights)!=core.MODEL_HASHES[model]:raise ValueError('[MODEL_HASH] '+ui_text('模型与适配器要求的版本不匹配或文件不完整。请从官方页面取得对应版本，重新导入；不会继续加载。')+'\n'+str(weights))
    log=Path(output).with_suffix('.engine.log')
    device={}
    with open(log,'w',encoding='utf-8') as err:
        p=subprocess.Popen([str(engine),str(weights),str(pcm),str(output),str(app_settings.load()['threads'])],stdout=subprocess.PIPE,stderr=err,
                           creationflags=core.CREATE_NO_WINDOW,text=True,encoding='utf-8',errors='replace')
        # Read in a helper so cancellation also works while a long chunk is decoding.
        import threading,queue
        q=queue.Queue()
        def reader():
            for line in p.stdout:q.put(line)
            q.put(None)
        threading.Thread(target=reader,daemon=True).start()
        try:
            while True:
                if cancel():raise core.Cancelled()
                try:line=q.get(timeout=.1)
                except queue.Empty:continue
                if line is None:break
                try:msg=json.loads(line)
                except ValueError:continue
                if msg['stage']=='device':device=msg
                elif msg['stage']=='transcribe':progress(ui_text('分析音符'),float(msg['progress']))
                elif msg['stage']=='load':progress(ui_text('加载本地模型'),float(msg['progress'])*.03)
            p.wait()
            if p.returncode:raise RuntimeError(f'{ui_text('推理进程失败 (')}{p.returncode})：'+log.read_text(encoding='utf-8')[-1600:])
        finally:
            if p.poll() is None:p.kill();p.wait()
    return core.read_json(output),device

"""Explicit local resource configuration. Links open upstream pages, never download."""
from pathlib import Path
import subprocess
from PySide6.QtCore import QThread,Signal,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QComboBox,QFileDialog,QScrollArea,QTabWidget
from i18n import tr as ui_text
import adapter_registry as registry
import core

class ResourceCheck(QThread):
    result=Signal(str)
    def __init__(self,action,parent):super().__init__(parent);self.action=action
    def run(self):
        try:self.result.emit(self.action())
        except subprocess.TimeoutExpired:self.result.emit(ui_text('检查超时。请确认所选程序可独立运行，再重试；设置尚未保存。'))
        except OSError as e:self.result.emit(ui_text('无法启动或读取所选资源。请检查路径、访问权限及程序兼容性。')+'\n'+str(e))
        except Exception as e:self.result.emit(ui_text('检查失败：')+str(e))

class ModelManager(QWidget):
    def __init__(self,owner,value):
        super().__init__();self.owner=owner;self.fields={};self.model_fields={}
        layout=QVBoxLayout(self);layout.setContentsMargins(12,12,12,12)
        hint=QLabel(ui_text('框架不附带模型或 FFmpeg。访问官方页面后，在这里关联本机文件；不会复制、下载或删除资源。'))
        hint.setWordWrap(True);layout.addWidget(hint)
        tabs=QTabWidget();layout.addWidget(tabs)
        page=QWidget();form=QFormLayout(page);form.setSpacing(10)
        self.transcription=QComboBox();self.transcription.addItem(ui_text('未启用'),'none');self.transcription.addItem('MuScriptor','muscriptor');self.transcription.setCurrentIndex(self.transcription.findData(value['transcription_adapter']));form.addRow(ui_text('转写适配器'),self.transcription)
        self.separation=QComboBox();self.separation.addItem(ui_text('未启用'),'none');self.separation.addItem('UMXHQ','umxhq');self.separation.setCurrentIndex(self.separation.findData(value['separation_adapter']));form.addRow(ui_text('分离适配器'),self.separation)
        for size in ('small','medium','large'):
            self.model_fields[size]=self.path(form,'MuScriptor '+size.title(),value['model_paths'].get(size,''),'*.gguf')
        for key,label,pattern in [
            ('model_dir',ui_text('兼容旧模型目录'),None),
            ('engine_path',ui_text('MuScriptor 通用引擎'),'*.exe'),
            ('engine_cpu_path',ui_text('MuScriptor CPU 引擎'),'*.exe'),
            ('umxhq_dir',ui_text('UMXHQ 四份权重目录'),None),
            ('separation_python',ui_text('分离环境 Python'),'*.exe'),
            ('vital_template_path',ui_text('Vital 初始化预设'),'*.vital *.json'),
            ('fl_template_path',ui_text('FL 导出结构模板'),'*.json'),
            ('ffmpeg_path','FFmpeg','*.exe')]:
            self.fields[key]=self.path(form,label,value[key],pattern)
        self.status=QLabel(ui_text('未检查；文件存在不代表可以推理。'));self.status.setWordWrap(True);self.status.setTextInteractionFlags(__import__('PySide6.QtCore',fromlist=['Qt']).Qt.TextInteractionFlag.TextSelectableByMouse)
        controls=QHBoxLayout()
        for text,action in [(ui_text('校验模型与适配器'),self.check_adapters),(ui_text('测试 FFmpeg'),self.check_ffmpeg)]:
            button=QPushButton(text);button.clicked.connect(action);controls.addWidget(button)
        form.addRow(controls);form.addRow(self.status)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);tabs.addTab(scroll,ui_text('本机配置'))
        recommendations=QWidget();rl=QVBoxLayout(recommendations)
        self.catalog=QComboBox()
        for entry in registry.CATALOG:self.catalog.addItem(entry['name'],entry['id'])
        rl.addWidget(self.catalog);self.detail=QLabel();self.detail.setWordWrap(True);rl.addWidget(self.detail)
        links=QHBoxLayout();official=QPushButton(ui_text('打开官方模型页面'));official.clicked.connect(lambda:self.open_link('url'));links.addWidget(official)
        code=QPushButton(ui_text('打开官方代码与安装说明'));code.clicked.connect(lambda:self.open_link('code'));links.addWidget(code);rl.addLayout(links)
        f=QPushButton(ui_text('打开 FFmpeg 官方下载页'));f.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://ffmpeg.org/download.html')));rl.addWidget(f)
        warning=QLabel(ui_text('推荐表示适配用途，不代表授权背书。MuScriptor 有非商业限制；HTDemucs 仅展示上游参考，本版没有可用适配器。请阅读原项目条款。'))
        warning.setWordWrap(True);rl.addWidget(warning);rl.addStretch();tabs.addTab(recommendations,ui_text('模型推荐与官方链接'))
        self.catalog.currentIndexChanged.connect(self.show_entry);self.show_entry()
        for field in [*self.fields.values(),*self.model_fields.values()]:
            field.textChanged.connect(self.invalidate_check)
            field.textChanged.connect(field.setToolTip)
            field.setToolTip(field.text())
        self.transcription.currentIndexChanged.connect(self.invalidate_check)
        self.separation.currentIndexChanged.connect(self.invalidate_check)
        owner.backend.currentIndexChanged.connect(self.invalidate_check)

    def invalidate_check(self,*args):
        self.status.setText(ui_text('配置已变更，请重新检查；保存后用于后续任务。'))

    def path(self,form,label,value,pattern):
        row=QWidget();box=QHBoxLayout(row);box.setContentsMargins(0,0,0,0)
        field=QLineEdit(value);field.setPlaceholderText(ui_text('未配置'));field.setAccessibleName(label);box.addWidget(field)
        browse=QPushButton(ui_text('导入本地…'));browse.setAccessibleName(label+' '+ui_text('导入本地…'));box.addWidget(browse)
        clear=QPushButton(ui_text('解除关联'));clear.clicked.connect(field.clear);box.addWidget(clear)
        def choose():
            path=QFileDialog.getExistingDirectory(self,label,field.text()) if pattern is None else QFileDialog.getOpenFileName(self,label,field.text(),pattern)[0]
            if path:field.setText(path)
        browse.clicked.connect(choose);form.addRow(label,row);return field

    def values(self):
        return dict({key:field.text().strip() for key,field in self.fields.items()},
                    model_paths={key:field.text().strip() for key,field in self.model_fields.items()},
                    transcription_adapter=self.transcription.currentData(),separation_adapter=self.separation.currentData())

    def reset(self,defaults):
        for key,field in self.fields.items():field.setText(defaults[key])
        for field in self.model_fields.values():field.clear()
        self.transcription.setCurrentIndex(0);self.separation.setCurrentIndex(0)

    def entry(self):return next(e for e in registry.CATALOG if e['id']==self.catalog.currentData())
    def show_entry(self):
        e=self.entry();status=ui_text('可选适配器') if e['supported'] else ui_text('仅参考，未集成')
        purpose=ui_text('音频转 MIDI；需要 GGUF 权重和 muscriptor.cpp 引擎。') if e['id']=='muscriptor' else ui_text('分离 Bass、鼓、人声及其他声部；需要四份权重和 Python 环境。') if e['id']=='umxhq' else ui_text('权重许可未确认；不提供导入、安装或运行入口。')
        self.detail.setText(e['name']+' · '+status+'\n\n'+purpose+'\n\n'+e['author']+'\n'+e['license']+'\n\n'+e['url'])
    def open_link(self,key):QDesktopServices.openUrl(QUrl(self.entry()[key]))

    def start_check(self,action):
        if self.owner.checker and self.owner.checker.isRunning():return
        worker=ResourceCheck(action,self.owner);self.owner.checker=worker
        self.setEnabled(False);self.owner.save_button.setEnabled(False);self.status.setText(ui_text('正在检查，请稍候…'))
        worker.result.connect(self.status.setText)
        worker.finished.connect(lambda:(self.setEnabled(True),self.owner.save_button.setEnabled(True)))
        worker.start()

    def check_ffmpeg(self):
        path=self.fields['ffmpeg_path'].text().strip()
        self.start_check(lambda:ui_text('FFmpeg 版本及音频转换测试通过：')+registry.probe_ffmpeg(path))

    def check_adapters(self):
        value=dict(self.owner.value,**self.values(),backend=self.owner.backend.currentData())
        def check():
            if value['transcription_adapter']=='none' and value['separation_adapter']=='none':return ui_text('没有启用适配器；仍可编辑已有 MIDI 工程。')
            if value['transcription_adapter']=='muscriptor':
                registry.selected_engine(value)
                found=0
                for size,expected in core.MODEL_HASHES.items():
                    path=value['model_paths'].get(size) or (str(Path(value['model_dir'])/f'muscriptor-{size}-f16.gguf') if value['model_dir'] else '')
                    if path and Path(path).is_file():
                        if core.sha256(path)!=expected:raise ValueError(ui_text('模型文件不完整或版本不匹配，请重新从官方页面取得对应权重。')+' '+size)
                        found+=1
                    elif value['model_paths'].get(size):registry.require_file(path,size)
                if not found:raise FileNotFoundError(ui_text('至少导入一份 MuScriptor 模型。'))
            if value['separation_adapter']=='umxhq':
                from separation_models import verify
                verify(registry.require_directory(value['umxhq_dir'],'UMXHQ'))
                python=registry.require_file(value['separation_python'],'Python')
                result=subprocess.run([str(python),'-c','import torch,openunmix,soundfile; print(torch.__version__)'],capture_output=True,timeout=30,creationflags=core.CREATE_NO_WINDOW)
                if result.returncode:raise ValueError(result.stderr.decode('utf8',errors='replace')[-700:])
            return ui_text('模型校验及环境检查通过；实际推理在任务运行时验证。')
        self.start_check(check)

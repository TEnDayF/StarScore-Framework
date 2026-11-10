from i18n import tr as ui_text
from pathlib import Path
from PySide6.QtCore import Qt,QThread,Signal,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QScrollArea,QVBoxLayout,QHBoxLayout,QLabel,QTabWidget,QWidget,QFormLayout,QLineEdit,
    QPushButton,QComboBox,QCheckBox,QSpinBox,QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QMessageBox)
import app_settings,core
from separation_models import WEIGHTS

MODELS=[('Small','muscriptor-small-f16.gguf',core.MODEL_HASHES['small']),('Medium','muscriptor-medium-f16.gguf',core.MODEL_HASHES['medium']),('Large','muscriptor-large-f16.gguf',core.MODEL_HASHES['large'])]+[('UMXHQ '+stem,'umxhq/'+entry[0],entry[1]) for stem,entry in WEIGHTS.items()]
class CheckModels(QThread):
    row=Signal(int,str)
    def __init__(self,folder,parent):super().__init__(parent);self.folder=Path(folder)
    def run(self):
        for i,(_,name,expected) in enumerate(MODELS):
            if self.isInterruptionRequested():return
            p=self.folder/name
            try:result=ui_text('校验通过') if p.is_file() and core.sha256(p)==expected else ui_text('文件缺失') if not p.is_file() else ui_text('校验不符，请重新安装')
            except OSError as e:result=ui_text('无法读取：')+str(e)
            self.row.emit(i,result)

class SettingsDialog(QDialog):
    def __init__(self,parent,folder):
        super().__init__(parent);self.folder=folder;self.value=app_settings.load(folder);self.checker=None
        self.setWindowTitle(ui_text('星谱 · 设置'));self.resize(860,700);self.setMinimumSize(760,580)
        root=QVBoxLayout(self);root.setContentsMargins(24,20,24,20);root.setSpacing(14)
        title=QLabel(ui_text('设置'));title.setStyleSheet('font-size:24px;font-weight:600;color:#EDF2FF');root.addWidget(title)
        subtitle=QLabel(ui_text('统一管理转写偏好、模型资源和外部音源。保存后用于后续任务，已有工程音符保持原样。'));subtitle.setWordWrap(True);root.addWidget(subtitle)
        tabs=QTabWidget();root.addWidget(tabs,1)
        general=QWidget();form=QFormLayout(general);form.setContentsMargins(18,20,18,18);form.setSpacing(16)
        self.language=QComboBox();self.language.addItem('简体中文','zh_CN');self.language.addItem('English','en');self.language.setCurrentIndex(self.language.findData(self.value.get('language','zh_CN')));form.addRow('语言 / Language',self.language)
        language_hint=QLabel('保存后重启软件生效 / Save and restart to apply');language_hint.setWordWrap(True);form.addRow(language_hint)
        self.output=self.path_row(form,ui_text('默认输出目录'),self.value['output'],False)
        self.model=QComboBox()
        for label,key in [(ui_text('Small · 快速预览'),'small'),(ui_text('Medium · 均衡质量'),'medium'),(ui_text('Large · 深度分析'),'large')]:self.model.addItem(label,key)
        self.model.setCurrentIndex(self.model.findData(self.value['model']));form.addRow(ui_text('默认模型'),self.model)
        self.adaptive=QCheckBox(ui_text('估计变化速度图，保留原始音符秒时间'));self.adaptive.setChecked(self.value['adaptive']);form.addRow(ui_text('节奏处理'),self.adaptive)
        self.complex=QCheckBox(ui_text('先分离 Bass / 鼓组 / 合成器来源（耗时更长）'));self.complex.setChecked(self.value['complex_mode']);form.addRow(ui_text('默认复杂模式'),self.complex)
        self.backend=QComboBox();self.backend.addItem(ui_text('自动 · Vulkan 可用时使用；缺少 loader 时 CPU'),'auto');self.backend.addItem(ui_text('仅 CPU · 排查显卡兼容问题'),'cpu');self.backend.setCurrentIndex(self.backend.findData(self.value['backend']));form.addRow(ui_text('转写后端'),self.backend)
        self.threads=QSpinBox();self.threads.setRange(1,64);self.threads.setValue(self.value['threads']);form.addRow(ui_text('转写 CPU 线程'),self.threads)
        hint=QLabel(ui_text('自动模式不保证所有 GPU 都兼容；如驱动报错可切换 CPU。线程设置作用于音符转写，复杂分离进程仍使用独立配置。'));hint.setWordWrap(True);form.addRow(hint);general.setMinimumHeight(440);general_scroll=QScrollArea();general_scroll.setWidgetResizable(True);general_scroll.setWidget(general);tabs.addTab(general_scroll,ui_text('转写与输出'))
        from model_manager_ui import ModelManager
        self.resources=ModelManager(self,self.value);self.models=self.resources.fields['model_dir'];tabs.addTab(self.resources,ui_text('模型与引擎'))
        plugins=QWidget();pf=QFormLayout(plugins);pf.setContentsMargins(18,20,18,18);pf.setSpacing(16);self.vital=self.path_row(pf,ui_text('Vital VST3 文件'),self.value['vital_path'],True)
        self.plugin_status=QLabel();self.vital.textChanged.connect(self.refresh_plugin);pf.addRow(ui_text('检测结果'),self.plugin_status)
        official=QPushButton(ui_text('前往 Vital 官网安装'));official.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://vital.audio/')));pf.addRow('',official)
        explain=QLabel(ui_text('检测文件存在不代表插件已成功加载。真实音色试听需要兼容的 Vital VST3；FL Studio 本体需独立安装。本软件可以生成候选预设，但不会恢复原曲完整效果链。'));explain.setWordWrap(True);pf.addRow(explain)
        licenses=QPushButton(ui_text('第三方署名与许可证'));licenses.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(core.root()/'THIRD_PARTY.md'))));pf.addRow('',licenses)
        location=QLabel(str(folder));location.setWordWrap(True);location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);pf.addRow(ui_text('设置与日志'),location);tabs.addTab(plugins,ui_text('音源与信息'))
        self.error=QLabel('');self.error.setWordWrap(True);self.error.setStyleSheet('color:#FFB4AB');root.addWidget(self.error)
        buttons=QHBoxLayout();reset=QPushButton(ui_text('恢复默认值'));reset.clicked.connect(self.reset);buttons.addWidget(reset);buttons.addStretch();cancel=QPushButton(ui_text('取消'));cancel.clicked.connect(self.reject);buttons.addWidget(cancel);self.save_button=QPushButton(ui_text('保存设置'));self.save_button.setObjectName('primary');self.save_button.clicked.connect(self.apply);buttons.addWidget(self.save_button);root.addLayout(buttons)
        self.refresh_plugin()
    def path_row(self,form,label,value,file):
        row=QWidget();box=QHBoxLayout(row);box.setContentsMargins(0,0,0,0);field=QLineEdit(value);box.addWidget(field,1);pick=QPushButton(ui_text('浏览…'));box.addWidget(pick)
        def browse():
            value=QFileDialog.getOpenFileName(self,ui_text('选择 Vital VST3'),field.text(),ui_text('VST3 插件 (*.vst3)'))[0] if file else QFileDialog.getExistingDirectory(self,ui_text('选择目录'),field.text())
            if value:field.setText(value)
        pick.clicked.connect(browse);form.addRow(label,row);return field
    def refresh_plugin(self):self.plugin_status.setText(ui_text('文件已存在 · 真实加载在试听时验证') if Path(self.vital.text()).is_file() else ui_text('未检测到插件 · 可先转写和编辑 MIDI'))
    def reject(self):
        if self.checker and self.checker.isRunning():self.error.setText(ui_text('模型校验正在读取文件，请等待完成后关闭。'));return
        super().reject()
    def closeEvent(self,event):
        if self.checker and self.checker.isRunning():self.error.setText(ui_text('模型校验尚未完成，请稍候。'));event.ignore()
        else:super().closeEvent(event)
    def reset(self):
        if self.checker and self.checker.isRunning():self.error.setText(ui_text('请等待模型校验完成后再恢复默认值。'));return
        d=app_settings.defaults();self.output.setText(d['output']);self.model.setCurrentIndex(self.model.findData(d['model']));self.resources.reset(d);self.adaptive.setChecked(True);self.complex.setChecked(False);self.backend.setCurrentIndex(0);self.threads.setValue(d['threads']);self.vital.setText(d['vital_path']);self.error.setText(ui_text('默认值已填入；点击“保存设置”才会应用。'))
        self.language.setCurrentIndex(self.language.findData(d['language']))
    def apply(self):
        if self.checker and self.checker.isRunning():return
        value=dict(self.value,language=self.language.currentData(),output=self.output.text(),model=self.model.currentData(),model_dir=self.models.text(),adaptive=self.adaptive.isChecked(),complex_mode=self.complex.isChecked(),backend=self.backend.currentData(),threads=self.threads.value(),vital_path=self.vital.text())
        value.update(self.resources.values())
        try:
            value=app_settings.validate(value);Path(value['output']).mkdir(parents=True,exist_ok=True)
            self.value=app_settings.save(value,self.folder)
        except (ValueError,OSError) as e:self.error.setText(ui_text(str(e)));return
        self.accept()

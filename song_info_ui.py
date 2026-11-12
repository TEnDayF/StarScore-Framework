"""Native song information editor with non-blocking audio measurement."""
import copy
from pathlib import Path
from PySide6.QtCore import QThread,Signal
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLineEdit,QComboBox,QDoubleSpinBox,
 QSpinBox,QLabel,QPushButton,QDialogButtonBox,QHBoxLayout,QCheckBox)
import song_info, i18n

def text(zh,en):return en if i18n.LANGUAGE=='en' else zh

class Measure(QThread):
    done=Signal(object);error=Signal(str)
    def __init__(self,path,parent):super().__init__(parent);self.path=path
    def run(self):
        try:self.done.emit(song_info.measure_file(self.path))
        except Exception as e:self.error.emit(str(e))

class SongInfoDialog(QDialog):
    def __init__(self,parent,value,source):
        super().__init__(parent);self.setWindowTitle(text('歌曲信息与节奏','Song information and tempo'));self.resize(720,640)
        self.value=song_info.validate(value);self.source=source;self.worker=None;self.result_data=None
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.fields={}
        for key,zh,en in [('title','歌曲名称','Title'),('artist','作者','Artist'),('key','调性（可选，不改音高）','Key (optional, no pitch changes)'),('notes','备注','Notes')]:
            field=QLineEdit(self.value[key]);self.fields[key]=field;form.addRow(text(zh,en),field)
        self.mode=QComboBox()
        for zh,en,key in [('先检测，尚未确认','Detect first / unconfirmed','auto'),('固定 BPM','Fixed BPM','fixed'),('变化速度图（MIDI）','Variable tempo map (MIDI)','adaptive')]:self.mode.addItem(text(zh,en),key)
        self.mode.setCurrentIndex(self.mode.findData(self.value['mode']));form.addRow(text('速度模式','Tempo mode'),self.mode)
        self.bpm=QDoubleSpinBox();self.bpm.setRange(20,400);self.bpm.setDecimals(3);self.bpm.setValue(self.value['bpm']);form.addRow('BPM',self.bpm)
        row=QHBoxLayout();self.num=QSpinBox();self.num.setRange(1,32);self.num.setValue(self.value['numerator']);self.den=QComboBox()
        for d in (1,2,4,8,16,32):self.den.addItem(str(d),d)
        self.den.setCurrentIndex(self.den.findData(self.value['denominator']));row.addWidget(self.num);row.addWidget(QLabel('/'));row.addWidget(self.den);form.addRow(text('拍号（请确认）','Time signature (confirm)'),row)
        self.beat=QDoubleSpinBox();self.beat.setRange(0,86400);self.beat.setDecimals(3);self.beat.setSuffix(' s');self.beat.setValue(self.value['first_beat']);form.addRow(text('原曲第一小节的第一拍','First downbeat in source'),self.beat)
        hint=QLabel(text('不量化、不删除前奏。需要对齐小节时，MIDI/FL 会统一添加前置静音，原曲试听和工程音符秒数不变。变化速度图暂只支持 MIDI；FL 导出前须明确选择固定 BPM。','No quantization or pickup deletion. MIDI/FL add matching leading silence when needed; source playback stays unchanged. Variable tempo is currently MIDI-only; choose a fixed BPM explicitly before FL export.'));hint.setWordWrap(True);layout.addWidget(hint)
        self.measure=QPushButton(text('自动测量 BPM 与拍点','Measure BPM and pulse'));self.measure.clicked.connect(self.start);layout.addWidget(self.measure)
        self.candidates=QComboBox();layout.addWidget(self.candidates)
        self.use=QPushButton(text('采用所选 BPM 候选','Use selected BPM candidate'));self.use.setEnabled(False);self.use.clicked.connect(self.use_result);layout.addWidget(self.use)
        self.status=QLabel(text('拍号无法由节拍强度可靠确定；默认 4/4 不代表检测结论。','Meter cannot be reliably inferred from pulse alone; default 4/4 is not a detection result.'));self.status.setWordWrap(True);layout.addWidget(self.status)
        self.confirm=QCheckBox(text('已确认歌曲信息（自动测量仅为候选）','I have confirmed this information (measurement is a suggestion)'));self.confirm.setChecked(bool(self.value.get('confirmed')));layout.addWidget(self.confirm)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(text('保存','Save'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(text('取消','Cancel'))
    def start(self):
        if self.worker and self.worker.isRunning():return
        self.measure.setEnabled(False);self.status.setText(text('正在分析原曲，请稍候…','Analyzing source audio…'))
        self.worker=Measure(self.source,self);self.worker.done.connect(self.measured);self.worker.error.connect(self.status.setText);self.worker.finished.connect(lambda:self.measure.setEnabled(True));self.worker.start()
    def measured(self,result):
        self.result_data=result;self.candidates.clear()
        for c in result['candidates']:self.candidates.addItem(f"{c['bpm']:.3f} BPM · {c['strength']:.2f}",c['bpm'])
        self.use.setEnabled(True);self.status.setText(result['warning']+f"\n候选拍点 / Pulse phase: {result['first_beat']:.3f} s")
    def use_result(self):
        self.bpm.setValue(self.candidates.currentData());self.mode.setCurrentIndex(self.mode.findData('fixed'));self.confirm.setChecked(False)
        self.status.setText(text('已填入 BPM；拍号与第一小节位置请按原曲确认。','BPM filled. Confirm meter and first downbeat against the source.'))
    def apply(self):
        if self.worker and self.worker.isRunning():self.status.setText(text('请等待自动测量完成。','Wait for measurement to finish.'));return
        try:
            value=dict(self.value,**{k:f.text() for k,f in self.fields.items()},mode=self.mode.currentData(),bpm=self.bpm.value(),numerator=self.num.value(),denominator=self.den.currentData(),first_beat=self.beat.value(),confirmed=self.confirm.isChecked())
            if self.result_data:value['measurement']=self.result_data
            self.value=song_info.validate(value);self.accept()
        except ValueError as e:self.status.setText(str(e))
    def reject(self):
        if self.worker and self.worker.isRunning():self.status.setText(text('请等待自动测量完成。','Wait for measurement to finish.'));return
        super().reject()

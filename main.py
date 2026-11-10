from __future__ import annotations
from i18n import tr as ui_text
import copy,json,os,sys,time,uuid,traceback
# Use the OS media backend; this distribution contains no FFmpeg binaries.
if os.name=='nt':os.environ.setdefault('QT_MEDIA_BACKEND','windows')
from pathlib import Path
from PySide6.QtCore import Qt,QThread,Signal,QUrl,QTimer,QPointF
from PySide6.QtGui import QColor,QBrush,QPen,QFont,QFontDatabase,QPainter,QDesktopServices,QKeySequence,QShortcut
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
 QFileDialog,QLineEdit,QComboBox,QTableWidget,QTableWidgetItem,QHeaderView,QProgressBar,QTabWidget,
 QSplitter,QMessageBox,QSpinBox,QDoubleSpinBox,QDialog,QDialogButtonBox,QFormLayout,QAbstractItemView,
 QGraphicsView,QGraphicsScene,QGraphicsRectItem,QSlider,QCheckBox,QGroupBox,QScrollArea)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
import core
import app_settings

APP_NAME=ui_text('星谱 · Suno MIDI Studio')
CONFIG=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'StarScoreMidi'
CONFIG.mkdir(parents=True,exist_ok=True)

STYLE='''
QMainWindow,QDialog {background:#10151f;color:#e7edf7;}
QWidget {font-family:"Microsoft YaHei UI";font-size:13px;color:#e7edf7;}
QLabel#title {font-size:27px;font-weight:700;color:#fafcff;}
QLabel#sub {color:#9daec3;font-size:12px;}
QLabel#badge {color:#a3edc9;background:#18382f;border-radius:8px;padding:7px;}
QPushButton {background:#243146;border:1px solid #3b4c65;border-radius:7px;padding:9px 14px;}
QPushButton:hover {background:#344860;border-color:#87b9ff;}
QPushButton:disabled {color:#63748a;background:#1b2534;}
QPushButton#primary {background:#416ef0;border:1px solid #7198ff;color:white;font-weight:600;}
QPushButton#primary:hover {background:#5380ff;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox {background:#192332;border:1px solid #34445c;border-radius:5px;padding:7px;selection-background-color:#416ef0;}
QTableWidget {background:#151e2c;alternate-background-color:#1a2636;border:1px solid #2b3a50;gridline-color:#253449;selection-background-color:#304f80;}
QHeaderView::section {background:#202e42;padding:8px;border:0;color:#b7c7dc;font-weight:600;}
QTabWidget::pane {border:1px solid #2a3a50;border-radius:8px;}
QTabBar::tab {padding:12px 24px;background:#182334;color:#9db0c9;}
QTabBar::tab:selected {color:#dceaff;background:#2a3e5a;border-bottom:2px solid #7ba7ff;}
QProgressBar {border:0;background:#243146;border-radius:5px;text-align:center;min-height:20px;}
QProgressBar::chunk {background:#517ffc;border-radius:5px;}
QGroupBox {border:1px solid #2b3c54;border-radius:8px;margin-top:14px;padding-top:16px;}
QGroupBox::title {subcontrol-origin:margin;left:12px;color:#a9c0df;}
QSlider::groove:horizontal {height:5px;background:#31425b;}
QSlider::handle:horizontal {width:13px;margin:-4px 0;background:#87b8ff;border-radius:6px;}
QToolTip {background:#23374f;color:white;border:1px solid #668bb7;}
'''

def button(label,callback,primary=False):
    b=QPushButton(label);b.clicked.connect(callback)
    if primary:b.setObjectName('primary')
    return b

class Job(QThread):
    update=Signal(str,float);done=Signal(object);error=Signal(str)
    def __init__(self,source,out,model,adaptive,complex_mode=False):
        super().__init__();self.args=(source,out,model,adaptive);self.cancelled=False;self.complex_mode=complex_mode
    def run(self):
        try:self.done.emit(core.process(*self.args,progress=self.update.emit,cancel=lambda:self.cancelled,complex_mode=self.complex_mode))
        except core.Cancelled:self.error.emit(ui_text('已取消；已提交的产物保留在任务目录'))
        except Exception:self.error.emit(traceback.format_exc())

class LocalJob(QThread):
    update=Signal(str,float);done=Signal(object);error=Signal(str)
    def __init__(self,project,folder,start,end):
        super().__init__();self.project=copy.deepcopy(project);self.folder=folder;self.start_time=start;self.end_time=end;self.cancelled=False
    def run(self):
        try:
            a=max(0,self.start_time-5);b=min(self.project['duration'],self.end_time+5)
            d=self.folder/'reruns'/uuid.uuid4().hex[:10];d.mkdir(parents=True)
            x=core.decode(self.folder/self.project['audio'],d/'audio.f32',lambda:self.cancelled,a,b-a)
            raw,dev=core.infer(d/'audio.f32',d/'raw.json',self.project['model'],self.update.emit,lambda:self.cancelled)
            for n in raw:n['start']+=a;n['end']+=a
            raw=[n for n in raw if self.start_time<=n['start']<self.end_time]
            self.done.emit({'base_revision':self.project['revision'],'source_sha256':self.project['source_sha256'],'start':self.start_time,'end':self.end_time,'notes':raw,'path':str(d),'project_folder':str(self.folder)})
        except core.Cancelled:self.error.emit(ui_text('局部重跑已取消'))
        except Exception:self.error.emit(traceback.format_exc())

class PreviewJob(QThread):
    done=Signal(str);error=Signal(str)
    def __init__(self,p,out,ids):super().__init__();self.p=copy.deepcopy(p);self.out=out;self.ids=ids
    def run(self):
        try:core.render_preview(self.p,self.out,self.ids);self.done.emit(str(self.out))
        except Exception:self.error.emit(traceback.format_exc())

class Piano(QGraphicsView):
    selected=Signal(str);edit=Signal(str)
    def __init__(self):
        super().__init__();self.setScene(QGraphicsScene(self));self.setMinimumHeight(160);self.setBackgroundBrush(QColor('#111c29'))
        self.setRenderHint(QPainter.RenderHint.Antialiasing);self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.items_by_id={};self.cursor=None;self.duration=0;self.scale_x=48;self.low=36;self.high=96
    def populate(self,notes,duration):
        self.scene().clear();self.items_by_id={};self.duration=duration
        if notes:self.low=max(0,min(n['pitch'] for n in notes)-3);self.high=min(127,max(n['pitch'] for n in notes)+3)
        else:self.low,self.high=36,96
        h=(self.high-self.low+1)*10;w=max(1000,duration*self.scale_x)
        for pitch in range(self.low,self.high+1):
            y=(self.high-pitch)*10
            col='#172435' if pitch%12 in (1,3,6,8,10) else '#1d2c3e'
            self.scene().addRect(0,y,w,10,QPen(QColor('#25374a')),QBrush(QColor(col)))
            if pitch%12==0:
                label=self.scene().addSimpleText(f'C{pitch // 12 - 1}');label.setBrush(QColor('#8a9fb7'));label.setPos(0,y)
        for sec in range(0,int(duration)+2,5):
            self.scene().addLine(sec*self.scale_x,0,sec*self.scale_x,h,QPen(QColor('#41546c')))
            tx=self.scene().addSimpleText(f'{sec // 60:02}:{sec % 60:02}');tx.setBrush(QColor('#a8b8ce'));tx.setPos(sec*self.scale_x,0)
        for n in notes:
            if n.get('deleted'):continue
            col='#dba35f' if n.get('candidate') else ('#98dfb7' if n.get('locked') else '#719bff')
            it=self.scene().addRect(n['start']*self.scale_x,(self.high-n['pitch'])*10+1,max(3,(n['end']-n['start'])*self.scale_x),8,QPen(QColor('#aec8ff')),QBrush(QColor(col)))
            it.setData(0,n['id']);it.setToolTip(f'MIDI {n['pitch']} · {n['start']:.3f}–{n['end']:.3f}s');it.setZValue(2);self.items_by_id[n['id']]=it
        self.cursor=self.scene().addLine(0,0,0,h,QPen(QColor('#ff8f93'),1.5));self.cursor.setZValue(5)
        self.scene().setSceneRect(0,0,w,h)
    def set_time(self,sec):
        if self.cursor:self.cursor.setX(sec*self.scale_x)
    def mousePressEvent(self,e):
        it=self.itemAt(e.position().toPoint())
        if it and it.data(0):self.selected.emit(it.data(0))
        super().mousePressEvent(e)
    def mouseDoubleClickEvent(self,e):
        it=self.itemAt(e.position().toPoint())
        if it and it.data(0):self.edit.emit(it.data(0))
        else:super().mouseDoubleClickEvent(e)

class Waveform(QWidget):
    seeked=Signal(float)
    def __init__(self):
        super().__init__();self.setFixedHeight(58);self.peaks=[];self.position=0.;self.duration=1.
    def load(self,path,duration):
        import soundfile as sf
        import numpy as np
        x,_=sf.read(path,dtype='float32');x=x if x.ndim==1 else x.mean(axis=1)
        step=max(1,len(x)//1800);self.peaks=[float(np.max(np.abs(x[i:i+step]))) for i in range(0,len(x),step)];self.duration=max(duration,.01);self.update()
    def paintEvent(self,e):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#142039'));p.setPen(QPen(QColor('#79A7DD'),1))
        for i,v in enumerate(self.peaks):
            x=i/max(1,len(self.peaks))*self.width();h=min(1,v*2)*22;p.drawLine(QPointF(x,29-h),QPointF(x,29+h))
        p.setPen(QPen(QColor('#C3B4FF'),2));x=self.position/self.duration*self.width();p.drawLine(QPointF(x,0),QPointF(x,58))
    def mousePressEvent(self,e):self.seeked.emit(e.position().x()/max(1,self.width())*self.duration)

class NoteDialog(QDialog):
    def __init__(self,n,tracks,duration,parent=None):
        super().__init__(parent);self.setWindowTitle(ui_text('编辑音符'));self.setMinimumWidth(370);l=QFormLayout(self)
        self.pitch=QSpinBox();self.pitch.setRange(0,127);self.pitch.setValue(n['pitch'])
        self.start=QDoubleSpinBox();self.start.setDecimals(3);self.start.setRange(0,duration);self.start.setValue(n['start'])
        self.end=QDoubleSpinBox();self.end.setDecimals(3);self.end.setRange(0,duration);self.end.setValue(n['end'])
        self.track=QComboBox()
        for t in tracks:self.track.addItem(t['name'],t['id'])
        self.track.setCurrentIndex(max(0,self.track.findData(n['track'])))
        l.addRow(ui_text('音高（MIDI）'),self.pitch);l.addRow(ui_text('开始（秒）'),self.start);l.addRow(ui_text('结束（秒）'),self.end);l.addRow(ui_text('所属声部'),self.track)
        self.lock=QCheckBox(ui_text('锁定，保护这次修正'));self.lock.setChecked(n.get('locked',False));l.addRow(self.lock)
        box=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);box.accepted.connect(self.validate);box.rejected.connect(self.reject);l.addRow(box)
    def validate(self):
        if self.end.value()<=self.start.value():QMessageBox.warning(self,ui_text('时间无效'),ui_text('结束必须晚于开始。'));return
        self.accept()

class Window(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle(APP_NAME+' 0.1');self.resize(1260,860);self.setMinimumSize(1000,700);self.setAcceptDrops(True)
        self.jobs=[];self.worker=None;self.current=-1;self.running=False;self.project=None;self.folder=None;self.undo=[];self.pending_local=None;self.preview_job=None;self.local_job=None
        self.settings={}
        try:self.settings=core.read_json(CONFIG/'settings.json')
        except Exception:pass
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central);layout.setContentsMargins(24,20,24,18);layout.setSpacing(14)
        head=QHBoxLayout();titles=QVBoxLayout();title=QLabel(ui_text('星谱  /  MIDI STUDIO'));title.setObjectName('title');titles.addWidget(title)
        sub=QLabel(ui_text('从整曲开始，留下可编辑的旋律。  ·  Suno 电子乐 / 本地转写'));sub.setObjectName('sub');titles.addWidget(sub);head.addLayout(titles);head.addStretch()
        badge=QLabel(ui_text('●  本地处理 · 音频不上传'));badge.setObjectName('badge');head.addWidget(badge);layout.addLayout(head)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1);self.build_queue();self.build_review()
        foot=QLabel(ui_text('研究预览版 0.1  ·  MuScriptor 权重 CC BY-NC 4.0，仅限非商业用途  ·  自动角色与音符需试听复核'));foot.setObjectName('sub');layout.addWidget(foot)
        self.player=QMediaPlayer();self.audio=QAudioOutput();self.audio.setVolume(.65);self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self.on_position);self.player.errorOccurred.connect(lambda e,s:self.statusBar().showMessage(ui_text('播放：')+s))
        try:
            self.jobs=core.read_json(CONFIG/'queue.json')
            for j in self.jobs:
                if j['status'] in ('运行中','等待中'):j['status']='已中断，可重新排队'
        except Exception:self.jobs=[]
        self.refresh_queue()
        QShortcut(QKeySequence('Ctrl+Z'),self,activated=self.undo_edit)
        QShortcut(QKeySequence('Ctrl+O'),self,activated=self.open_project)
    def build_queue(self):
        page=QWidget();l=QVBoxLayout(page);l.setContentsMargins(18,18,18,18);l.setSpacing(13)
        drop=QLabel(ui_text('把歌曲拖到这里，或点击「添加歌曲」\nWAV / MP3 / FLAC / M4A / OGG / AIFF   ·   支持多首队列，逐首分析'))
        drop.setAlignment(Qt.AlignmentFlag.AlignCenter);drop.setMinimumHeight(92);drop.setObjectName('dropZone');l.addWidget(drop)
        row=QHBoxLayout();row.addWidget(button(ui_text('添加歌曲'),self.add_files));row.addWidget(button(ui_text('重新排队所选'),self.retry_job));row.addWidget(button(ui_text('移除等待项'),self.remove_job));row.addStretch();row.addWidget(button(ui_text('打开历史工程'),self.open_project));l.addLayout(row)
        self.queue=QTableWidget(0,4);self.queue.setHorizontalHeaderLabels([ui_text('歌曲'),ui_text('状态'),ui_text('进度'),ui_text('结果目录')]);self.queue.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.queue.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.queue.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch);self.queue.setColumnWidth(1,160);self.queue.setColumnWidth(2,80);self.queue.doubleClicked.connect(self.open_job);l.addWidget(self.queue,1)
        path=QHBoxLayout();path.addWidget(QLabel(ui_text('输出到')));self.output=QLineEdit(self.settings.get('output',str(root_output())));path.addWidget(self.output,1);path.addWidget(button(ui_text('选择文件夹'),self.choose_output));l.addLayout(path)
        options=QHBoxLayout();options.addWidget(QLabel(ui_text('模型')));self.model=QComboBox()
        self.model.addItem(ui_text('深度分析 · Large'),'large')
        self.model.addItem(ui_text('均衡质量 · Medium'),'medium');self.model.addItem(ui_text('快速预览 · Small'),'small');options.addWidget(self.model)
        self.model.setCurrentIndex(max(0,self.model.findData(self.settings.get('model','medium'))))
        self.adaptive=QCheckBox(ui_text('估计变化速度图（保留原始时间，不量化）'));self.adaptive.setChecked(self.settings.get("adaptive",True));options.addWidget(self.adaptive);options.addStretch();l.addLayout(options)
        modes=QHBoxLayout();modes.addWidget(QLabel(ui_text('分析方式')));self.analysis_mode=QComboBox();self.analysis_mode.addItem(ui_text('标准 · 整曲转写'),False);self.analysis_mode.addItem(ui_text('复杂 Bass / 鼓组 · 先分离再转写'),True);modes.addWidget(self.analysis_mode);self.analysis_mode.setCurrentIndex(1 if self.settings.get("complex_mode",False) else 0)
        mode_hint=QLabel(ui_text('增强模式耗时更长；保存分离音频、鼓采样和来源标记。'));mode_hint.setWordWrap(True);modes.addWidget(mode_hint,1);l.addLayout(modes)
        self.progress=QProgressBar();self.progress.setRange(0,100);l.addWidget(self.progress)
        controls=QHBoxLayout();self.stage=QLabel(ui_text('等待歌曲。所有分析在本机运行。'));controls.addWidget(self.stage,1)
        self.start_btn=button(ui_text('开始处理'),self.start_queue,True);controls.addWidget(self.start_btn)
        self.cancel_btn=button(ui_text('取消当前'),self.cancel_current);self.cancel_btn.setEnabled(False);controls.addWidget(self.cancel_btn)
        self.stop_btn=button(ui_text('停止后续队列'),self.stop_queue);controls.addWidget(self.stop_btn);l.addLayout(controls)
        self.tabs.addTab(page,ui_text('01  转写队列'))
    def build_review(self):
        page=QWidget();page.setMinimumHeight(800);l=QVBoxLayout(page);l.setContentsMargins(16,16,16,16)
        self.summary=QLabel(ui_text('打开一个处理结果，查看来源轨道、编辑音符或比较候选。'));self.summary.setWordWrap(True);l.addWidget(self.summary)
        row=QHBoxLayout();self.tracks=QComboBox();self.tracks.currentIndexChanged.connect(self.refresh_notes);row.addWidget(self.tracks,1)
        self.role=QComboBox()
        for key,name in core.ROLES.items():self.role.addItem(ui_text(name),key)
        row.addWidget(self.role);row.addWidget(button(ui_text('修改角色'),self.change_role));row.addWidget(button(ui_text('新增来源轨'),self.new_track));row.addWidget(button(ui_text('打开结果目录'),self.open_folder));l.addLayout(row)
        self.wave=Waveform();self.wave.seeked.connect(lambda s:self.seek_to(s));l.addWidget(self.wave)
        split=QSplitter(Qt.Orientation.Vertical);self.piano=Piano();self.piano.selected.connect(self.select_note);self.piano.edit.connect(self.edit_note);split.addWidget(self.piano)
        bottom=QWidget();bottom.setMinimumHeight(140);bl=QHBoxLayout(bottom);bl.setContentsMargins(0,0,0,0)
        self.notes=QTableWidget(0,6);self.notes.setHorizontalHeaderLabels([ui_text('音高'),ui_text('开始 s'),ui_text('结束 s'),ui_text('时长 s'),ui_text('状态'),ui_text('事件 ID')]);self.notes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.notes.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.notes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.notes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.notes.doubleClicked.connect(lambda:self.edit_note());bl.addWidget(self.notes,3)
        panel=QVBoxLayout();panel.addWidget(QLabel(ui_text('碎音候选 · 不自动合并真实重复')))
        self.candidates=QTableWidget(0,2);self.candidates.setHorizontalHeaderLabels([ui_text('时间'),ui_text('建议')]);self.candidates.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);self.candidates.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.candidates.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);panel.addWidget(self.candidates)
        panel.addWidget(button(ui_text('接受所选合并候选'),self.accept_candidate));bl.addLayout(panel,2);split.addWidget(bottom);split.setSizes([260,210]);l.addWidget(split,1)
        edit=QHBoxLayout()
        for name,cb in [(ui_text('添加音符'),self.add_note),(ui_text('编辑'),self.edit_note),(ui_text('删除所选'),self.delete_notes),(ui_text('合并所选'),self.merge_notes),(ui_text('按播放点拆分'),self.split_note),(ui_text('锁定/解锁'),self.lock_notes),(ui_text('纳入主结果'),self.accept_notes),(ui_text('撤销'),self.undo_edit)]:edit.addWidget(button(name,cb))
        l.addLayout(edit)
        play=QHBoxLayout();play.addWidget(button(ui_text('播放原音'),self.play_source));play.addWidget(button(ui_text('播放当前声部'),self.play_track));play.addWidget(button(ui_text('播放全部 MIDI'),self.play_all));play.addWidget(button(ui_text('暂停/继续'),self.toggle_play));self.loop=QCheckBox(ui_text('循环选区'));play.addWidget(self.loop);self.clock=QLabel('00:00 / 00:00');play.addWidget(self.clock)
        self.seek=QSlider(Qt.Orientation.Horizontal);self.seek.setRange(0,1000);self.seek.sliderReleased.connect(lambda:self.seek_to(self.seek.value()/1000));play.addWidget(self.seek,1);l.addLayout(play)
        rerun=QHBoxLayout();rerun.addWidget(QLabel(ui_text('局部重跑：')));self.range_start=QDoubleSpinBox();self.range_end=QDoubleSpinBox()
        for w in [self.range_start,self.range_end]:w.setRange(0,7200);w.setDecimals(2);w.setSuffix(' s')
        self.range_end.setValue(20);rerun.addWidget(self.range_start);rerun.addWidget(self.range_end);rerun.addWidget(button(ui_text('分析为候选'),self.rerun));rerun.addWidget(button(ui_text('接受重跑候选'),self.accept_rerun));rerun.addStretch();l.addLayout(rerun)
        out=QHBoxLayout();out.addWidget(button(ui_text('保存工程'),self.save_project));out.addWidget(button(ui_text('导出修正后的各轨 MIDI'),self.export_current,True));self.fixed=QSpinBox();self.fixed.setRange(30,300);self.fixed.setValue(120);out.addWidget(self.fixed);out.addWidget(button(ui_text('导出固定 BPM 整理版'),self.export_fixed));out.addWidget(button('BPM ÷2',lambda:self.scale_tempo(2)));out.addWidget(button('BPM ×2',lambda:self.scale_tempo(.5)));out.addStretch();l.addLayout(out)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QScrollArea.Shape.NoFrame);scroll.setWidget(page)
        self.tabs.addTab(scroll,ui_text('02  试听与修正'))
    def persist_queue(self):core.save_json(CONFIG/'queue.json',self.jobs)
    def refresh_queue(self):
        self.queue.setRowCount(len(self.jobs))
        for i,j in enumerate(self.jobs):
            for col,value in enumerate([Path(j['source']).name,ui_text(j['status']),j.get('progress',''),j.get('folder','')]):self.queue.setItem(i,col,QTableWidgetItem(str(value)))
        self.persist_queue()
    def add_paths(self,paths):
        added=0;issues=[];known={os.path.normcase(str(Path(j['source']).resolve())) for j in self.jobs}
        for raw in paths:
            p=Path(raw);key=os.path.normcase(str(p.resolve()))
            if p.suffix.lower() not in ('.mp3','.wav','.flac','.m4a','.ogg','.aiff','.aif','.wma','.mp4'):issues.append(p.name+ui_text('：不支持的格式'));continue
            if not p.is_file():issues.append(p.name+ui_text('：文件不存在'));continue
            if key in known:issues.append(p.name+ui_text('：重复，可选择原任务重新排队'));continue
            self.jobs.append({'source':str(p),'status':'等待中','progress':'','folder':''});known.add(key);added+=1
        self.refresh_queue()
        message=f'{ui_text('已添加 ')}{added}{ui_text(' 首。')}'+('；'.join(issues) if issues else '')
        self.stage.setText(message);self.stage.setToolTip(message);self.statusBar().showMessage(message)

    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls():e.acceptProposedAction()
    def dropEvent(self,e):self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]);self.tabs.setCurrentIndex(0);e.acceptProposedAction()
    def add_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,ui_text('添加歌曲'),'',ui_text('音频 (*.mp3 *.wav *.flac *.m4a *.ogg *.aiff *.wma *.mp4)'));self.add_paths(paths)
    def choose_output(self):
        p=QFileDialog.getExistingDirectory(self,ui_text('输出根目录'),self.output.text())
        if p:self.output.setText(p)
    def retry_job(self):
        i=self.queue.currentRow()
        if i>=0 and not (self.worker and i==self.current):self.jobs[i]['status']='等待中';self.refresh_queue()
    def remove_job(self):
        i=self.queue.currentRow()
        if i>=0 and not self.worker:self.jobs.pop(i);self.refresh_queue()
        elif self.worker:self.statusBar().showMessage(ui_text('处理时不能移除队列项；可以取消当前或停止后续。'))
    def start_queue(self):
        if self.worker:self.statusBar().showMessage(ui_text('当前正在处理，请等待或取消当前任务。'));return
        if not any(j['status']=='等待中' for j in self.jobs):self.statusBar().showMessage(ui_text('没有等待任务，请导入歌曲或重新排队所选任务。'));self.stage.setText(ui_text('没有等待处理的歌曲。'));return
        if not self.output.text().strip():self.warn(ui_text('请选择输出目录'));return
        try:
            from adapter_registry import preflight
            preflight(self.model.currentData(),self.analysis_mode.currentData())
        except (OSError,ValueError) as e:
            box=QMessageBox(self);box.setWindowTitle(ui_text('资源尚未就绪'));box.setIcon(QMessageBox.Icon.Warning)
            box.setText(str(e));box.setInformativeText(ui_text('任务尚未开始，队列和工程保持原样。已有 MIDI 仍可编辑。'))
            configure=box.addButton(ui_text('打开设置'),QMessageBox.ButtonRole.ActionRole);box.addButton(ui_text('暂不处理'),QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton()==configure and hasattr(self,'open_settings'):self.open_settings()
            return
        try:
            p=Path(self.output.text());p.mkdir(parents=True,exist_ok=True)
            self.settings=app_settings.load(CONFIG);self.settings.update(output=str(p),model=self.model.currentData(),adaptive=self.adaptive.isChecked(),complex_mode=self.analysis_mode.currentData());app_settings.save(self.settings,CONFIG)
        except Exception as e:self.warn(str(e));return
        self.running=True;self.start_btn.setEnabled(False);self.next_job()
    def next_job(self):
        if not self.running:self.start_btn.setEnabled(True);return
        i=next((i for i,j in enumerate(self.jobs) if j['status']=='等待中'),None)
        if i is None:self.running=False;self.start_btn.setEnabled(True);self.stage.setText(ui_text('队列已结束。双击任务打开结果。'));return
        self.current=i;self.jobs[i]['status']='运行中';self.refresh_queue();self.cancel_btn.setEnabled(True)
        w=Job(self.jobs[i]['source'],self.output.text(),self.model.currentData(),self.adaptive.isChecked(),self.analysis_mode.currentData());self.worker=w
        w.update.connect(self.job_update);w.done.connect(self.job_done);w.error.connect(self.job_error);w.finished.connect(self.job_finished);w.start()
    def job_update(self,s,p):
        self.progress.setValue(int(p*100));self.stage.setText(s)
        if self.current>=0:self.jobs[self.current]['progress']=f'{int(p * 100)}%';self.queue.item(self.current,2).setText(f'{int(p * 100)}%')
    def job_done(self,result):
        folder,p,r=result;j=self.jobs[self.current];j['folder']=str(folder);j['status']='部分完成 · 请复核' if r['status']=='partial' else '完成 · 请试听';j['progress']='100%';self.refresh_queue()
        self.statusBar().showMessage(f'{ui_text('已输出 ')}{len(r['files'])}{ui_text(' 个声部，')}{r['elapsed_seconds']:.1f}{ui_text(' 秒')}')
    def job_error(self,s):
        self.jobs[self.current]['status']='已取消' if ('已取消' in s or ui_text('已取消') in s) else '失败';self.jobs[self.current]['error']=s;self.refresh_queue();self.stage.setText(s[-160:])
        with open(CONFIG/'errors.log','a',encoding='utf-8') as f:f.write(time.ctime()+'\n'+s+'\n')
    def job_finished(self):
        self.worker=None;self.cancel_btn.setEnabled(False);QTimer.singleShot(150,self.next_job)
    def cancel_current(self):
        if self.worker:self.worker.cancelled=True;self.stage.setText(ui_text('正在取消当前任务，后续队列保持。'))
    def stop_queue(self):self.running=False;self.stage.setText(ui_text('当前歌曲结束后停止，不再启动后续任务。'))
    def open_job(self):
        i=self.queue.currentRow()
        if i<0:return
        j=self.jobs[i]
        if j.get('folder'):self.load_project(Path(j['folder'])/'project.json')
        elif j.get('error'):self.warn(j['error'][-2500:])
    def open_project(self):
        p,_=QFileDialog.getOpenFileName(self,ui_text('打开 project.json'),self.output.text(),ui_text('工程 (project.json *.json)'))
        if p:self.load_project(p)
    def load_project(self,p):
        from project_state import validated_project,fingerprint
        try:obj=validated_project(p)
        except Exception as e:self.warn(ui_text('打开失败，当前工程未改变：')+str(e));return False
        if self.project and self.save_project(silent=True) is False:return False
        # Prepare audio before committing any editor state. Missing audio keeps MIDI usable.
        audio=Path(p).parent/obj['audio'];wave_data=None;audio_error=None
        try:
            import soundfile as sf
            import numpy as np
            x,sr=sf.read(audio,dtype='float32',always_2d=True)
            x=x.mean(axis=1);step=max(1,len(x)//1800)
            wave_data=[float(np.max(np.abs(x[i:i+step]))) for i in range(0,len(x),step)]
        except Exception as e:audio_error=str(e)
        self.player.stop();self.project=obj;self.folder=Path(p).parent;self.undo=[];self.pending_local=None
        self._saved_identity=(str(self.folder.resolve()),fingerprint(obj))
        self.range_start.setMaximum(obj['duration']);self.range_start.setValue(0);self.range_end.setMaximum(obj['duration']);self.range_end.setValue(min(20,obj['duration']))
        self.seek.setMaximum(int(obj['duration']*1000));self.refresh_tracks();self.tabs.setCurrentIndex(1)
        if audio_error:
            self.wave.peaks=[];self.wave.duration=obj['duration'];self.wave.update()
            self.statusBar().showMessage(ui_text('工程已打开，但原曲音频不可用；MIDI 仍可编辑。')+audio_error)
        else:self.wave.peaks=wave_data;self.wave.duration=obj['duration'];self.wave.update()
        self.on_position(0);return True

    def refresh_tracks(self,selected=None):
        if not self.project:return
        selected=selected or self.tracks.currentData();self.tracks.blockSignals(True);self.tracks.clear()
        for t in self.project['tracks']:self.tracks.addItem(f'{t['name']}  ({len(core.active_notes(t))}{ui_text(' 音)')}',t['id'])
        self.tracks.setCurrentIndex(max(0,self.tracks.findData(selected)));self.tracks.blockSignals(False);self.refresh_notes()
    def track(self):
        if not self.project:return None
        return next((t for t in self.project['tracks'] if t['id']==self.tracks.currentData()),None)
    def refresh_notes(self):
        t=self.track()
        if not t:return
        self.role.setCurrentIndex(max(0,self.role.findData(t['role'])));self.shown=sorted([n for n in t['notes'] if not n.get('deleted')],key=lambda n:(n['start'],n['pitch']))
        self.notes.setRowCount(len(self.shown))
        for i,n in enumerate(self.shown):
            vals=[str(n['pitch']),f'{n['start']:.3f}',f'{n['end']:.3f}',f'{n['end'] - n['start']:.3f}',ui_text('锁定') if n.get('locked') else (ui_text('候选') if n.get('candidate') else ui_text('已采用')),n['id']]
            for j,v in enumerate(vals):self.notes.setItem(i,j,QTableWidgetItem(v))
        self.piano.populate(t['notes'],self.project['duration'])
        self.shown_candidates=[c for c in self.project['candidates'] if c['track']==t['id'] and not c.get('accepted')]
        self.candidates.setRowCount(len(self.shown_candidates))
        for i,c in enumerate(self.shown_candidates):
            self.candidates.setItem(i,0,QTableWidgetItem(f'{c['time']:.2f}s'));self.candidates.setItem(i,1,QTableWidgetItem(c.get('reason',ui_text('同音短间隙，请试听判断是否碎裂'))))
    def select_note(self,nid):
        for i,n in enumerate(self.shown):
            if n['id']==nid:self.notes.selectRow(i);self.notes.scrollToItem(self.notes.item(i,0));break
    def selected_notes(self):
        return [self.shown[i.row()] for i in self.notes.selectionModel().selectedRows()] if self.project else []
    def checkpoint(self):
        if self.project:self.undo.append(copy.deepcopy(self.project));self.undo=self.undo[-30:]
    def changed(self):
        self.project['revision']+=1;self.refresh_tracks();self.save_project(silent=True)
    def edit_note(self,nid=None):
        if not self.project:return
        ns=self.selected_notes();n=next((n for n in getattr(self,'shown',[]) if n['id']==nid),None) if isinstance(nid,str) else (ns[0] if ns else None)
        if not n:return
        d=NoteDialog(n,self.project['tracks'],self.project['duration'],self)
        if d.exec():
            self.checkpoint();old=self.track();target=next(t for t in self.project['tracks'] if t['id']==d.track.currentData())
            n.update(pitch=d.pitch.value(),start=d.start.value(),end=d.end.value(),locked=d.lock.isChecked(),origin='user',candidate=False,track=target['id'])
            if old!=target:old['notes'].remove(n);target['notes'].append(n)
            self.changed()
    def add_note(self):
        t=self.track()
        if not t:return
        sec=min(self.seek.value()/1000,self.project['duration']-.1)
        n={'id':'user_'+uuid.uuid4().hex[:10],'track':t['id'],'pitch':60,'start':max(0,sec),'end':min(self.project['duration'],sec+.3),'velocity':80,'velocity_origin':'defaulted','origin':'user','locked':True,'candidate':False,'deleted':False,'program':t['program']}
        d=NoteDialog(n,self.project['tracks'],self.project['duration'],self)
        if d.exec():
            self.checkpoint();n.update(pitch=d.pitch.value(),start=d.start.value(),end=d.end.value(),track=d.track.currentData(),locked=d.lock.isChecked())
            next(t for t in self.project['tracks'] if t['id']==n['track'])['notes'].append(n);self.changed()
    def delete_notes(self):
        ns=self.selected_notes()
        if not ns:return
        self.checkpoint()
        for n in ns:
            if not n.get('locked'):n['deleted']=True;n['origin']='user'
        self.changed();self.statusBar().showMessage(ui_text('已删除未锁定事件；锁定事件保留。删除记录用于重跑保护。'))
    def lock_notes(self):
        ns=self.selected_notes()
        if not ns:return
        self.checkpoint();value=not all(n.get('locked') for n in ns)
        for n in ns:n['locked']=value
        self.changed()
    def accept_notes(self):
        ns=self.selected_notes()
        if not ns:return
        self.checkpoint()
        for n in ns:n['candidate']=False;n['origin']='user'
        self.changed()
    def merge_notes(self):
        ns=sorted(self.selected_notes(),key=lambda n:n['start'])
        if len(ns)<2:return
        if len({n['pitch'] for n in ns})>1 or any(n.get('locked') for n in ns):self.warn(ui_text('只允许合并同音高且未锁定的事件。'));return
        self.checkpoint();ns[0]['end']=max(n['end'] for n in ns);ns[0]['origin']='user'
        for n in ns[1:]:n['deleted']=True
        self.changed()
    def split_note(self):
        ns=self.selected_notes();sec=self.seek.value()/1000
        if len(ns)!=1:return
        n=ns[0]
        if n.get('locked') or not n['start']<sec<n['end']:self.warn(ui_text('将播放位置移到未锁定音符内部，再拆分。'));return
        self.checkpoint();b=copy.deepcopy(n);b['id']='split_'+uuid.uuid4().hex[:10];b['start']=sec;b['origin']='user';n['end']=sec;n['origin']='user';self.track()['notes'].append(b);self.changed()
    def undo_edit(self):
        if self.undo:
            revision=self.project['revision']+1;self.project=self.undo.pop();self.project['revision']=revision;self.refresh_tracks();self.save_project(silent=True)
    def accept_candidate(self):
        i=self.candidates.currentRow()
        if i<0:return
        try:self.checkpoint();core.apply_candidate(self.project,self.shown_candidates[i]['id']);self.changed()
        except Exception as e:
            if self.undo:self.undo.pop()
            self.warn(str(e))
    def change_role(self):
        t=self.track()
        if not t:return
        role=self.role.currentData()
        if role==t['role']:return
        was_drum=bool(t.get('drum') or t['role']=='drums' or t['program']==128);drum=role=='drums'
        self.checkpoint()
        if was_drum!=drum:
            t['program']=128 if drum else 33 if role=='bass' else 80
            t['drum']=drum
            if not drum:t.pop('drum_samples',None)
            for n in t['notes']:n.update(drum=drum,program=t['program'])
        t['role']=role;t['role_origin']='user';t['name']=core.ROLES[role]+' · '+t.get('instrument','user_source');self.changed()
        self.statusBar().showMessage(ui_text('已更改角色')+(ui_text('及发声类型；保留原 MIDI 音高，请试听确认鼓件/音高映射。') if was_drum!=drum else ui_text('；可撤销。')))

    def new_track(self):
        if not self.project:return
        self.checkpoint();tid='user_'+uuid.uuid4().hex[:8];self.project['tracks'].append({'id':tid,'name':ui_text('副旋律 · 新来源'),'role':'counter','program':80,'instrument':'user_source','notes':[],'role_origin':'user'});self.changed();self.refresh_tracks(tid)
    def save_project(self,checked=False,silent=False):
        if not self.project or not self.folder:return True
        from project_state import fingerprint
        try:
            identity=(str(self.folder.resolve()),fingerprint(self.project));path=self.folder/'project.json'
            if path.exists() and getattr(self,'_saved_identity',None)==identity:
                if not silent:self.statusBar().showMessage(ui_text('工程已保存，没有新的修改。'))
                return True
            core.save_json(path,self.project);self._saved_identity=identity
        except Exception as e:
            self.statusBar().showMessage(ui_text('保存失败，修改仍保留在当前窗口；修复输出目录后重试 Ctrl+S。'))
            self.warn(ui_text('保存失败，已阻止切换或退出。修改仍在当前窗口，请修复输出目录后重试：')+str(e));return False
        try:
            hist=self.folder/'revisions';hist.mkdir(exist_ok=True)
            core.save_json(hist/f'revision_{self.project['revision']:06}_{uuid.uuid4().hex[:6]}.json',self.project)
        except Exception as e:self.statusBar().showMessage(ui_text('当前工程已保存，但历史副本写入失败：')+str(e));return True
        if not silent:self.statusBar().showMessage(ui_text('工程已保存；仅新增有变化的历史修订。'))
        return True

    def export_current(self):self.do_export(None)
    def export_fixed(self):self.do_export(self.fixed.value())
    def scale_tempo(self,factor):
        if not self.project:return
        if any(not 1<=round(m['tempo']*factor)<=16777215 for m in self.project['tempos']):self.warn(ui_text('速度超出 MIDI 范围'));return
        self.checkpoint()
        for m in self.project['tempos']:m['tempo']=int(round(m['tempo']*factor))
        self.project['tempo_info']['bpm']/=factor;self.project['tempo_info']['user_scale']=self.project['tempo_info'].get('user_scale',1)/factor
        self.changed();self.statusBar().showMessage(ui_text('已修改速度解释，音符实际秒时间保持不变。'))
    def do_export(self,bpm):
        if not self.project:self.statusBar().showMessage(ui_text('请先导入歌曲或打开工程，再导出 MIDI。'));return
        try:
            if self.save_project(silent=True) is False:return
            d=self.folder/'exports'/((f'fixed_{bpm}_' if bpm else 'faithful_')+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:4])
            r=core.export_project(self.project,d,bpm);self.statusBar().showMessage(f'{ui_text('已验证并导出 ')}{len(r['files'])}{ui_text(' 个独立 MIDI')}');QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        except Exception as e:self.warn(str(e))
    def open_folder(self):
        if self.folder:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))
    def play_source(self):
        if self.project:self.play_file(str(self.folder/self.project['audio']))
    def play_track(self):self.make_preview([self.tracks.currentData()])
    def play_all(self):self.make_preview(None)
    def make_preview(self,ids):
        if not self.project or (self.preview_job and self.preview_job.isRunning()):return
        self.statusBar().showMessage(ui_text('正在生成基础音源试听（不还原原音色）…'));p=self.folder/f'preview_{uuid.uuid4().hex[:8]}.wav'
        self.preview_job=PreviewJob(self.project,p,ids);self.preview_job.done.connect(self.play_file);self.preview_job.error.connect(self.warn);self.preview_job.start()
    def play_file(self,p):
        position=self.seek.value();self.player.setSource(QUrl.fromLocalFile(p));self.player.play();self.player.setPosition(position);self.statusBar().showMessage(ui_text('播放：')+Path(p).name)
    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        else:self.player.play()
    def on_position(self,pos):
        if self.loop.isChecked() and self.range_end.value()>self.range_start.value() and pos/1000>=self.range_end.value():
            self.player.setPosition(int(self.range_start.value()*1000));return
        if not self.seek.isSliderDown():self.seek.setValue(pos)
        sec=pos//1000;dur=int(self.project['duration']) if self.project else 0;self.clock.setText(f'{sec // 60:02}:{sec % 60:02} / {dur // 60:02}:{dur % 60:02}');self.piano.set_time(pos/1000)
        self.wave.position=pos/1000;self.wave.update()
    def seek_to(self,sec):
        self.seek.setValue(int(sec*1000));self.player.setPosition(int(sec*1000));self.on_position(int(sec*1000))
    def rerun(self):
        if not self.project or self.worker or (self.local_job and self.local_job.isRunning()):self.warn(ui_text('请先等待当前分析结束。'));return
        a,b=self.range_start.value(),self.range_end.value()
        if b<=a or b-a>60:self.warn(ui_text('局部范围必须为 0～60 秒，并且结束晚于开始。'));return
        self.local_job=LocalJob(self.project,self.folder,a,b);self.local_job.update.connect(lambda s,p:self.statusBar().showMessage(f'{ui_text('局部候选：')}{s} {int(p * 100)}%'));self.local_job.done.connect(self.local_done);self.local_job.error.connect(self.warn);self.local_job.start()
    def local_done(self,result):
        origin=Path(result.get('project_folder') or Path(result['path']).parent.parent)
        try:core.save_json(origin/'pending_local.json',result)
        except Exception as e:self.warn(ui_text('局部结果未保存：')+str(e));return
        if not self.project or origin.resolve()!=self.folder.resolve() or result.get('source_sha256')!=self.project.get('source_sha256'):
            self.statusBar().showMessage(ui_text('另一工程的局部候选已保存到原目录：')+str(origin));return
        self.pending_local=result;self.statusBar().showMessage(f'{ui_text('得到 ')}{len(result['notes'])}{ui_text(' 个局部事件。点击接收候选，再选择并采用所需音符。')}')

    def accept_rerun(self):
        r=self.pending_local
        if not r:return
        if r.get('source_sha256')!=self.project['source_sha256'] or r['base_revision']!=self.project['revision']:self.warn(ui_text('工程在分析后已变化。为保护修改，请重新生成局部候选。'));return
        if QMessageBox.question(self,ui_text('接受局部候选'),f'{ui_text('将 ')}{r['start']:.2f}–{r['end']:.2f}{ui_text('s 结果加入独立候选轨？\n现有事件不改变。新事件需选择后纳入主结果，并检查与旧音符是否重复。')}')!=QMessageBox.StandardButton.Yes:return
        self.checkpoint()
        new_tracks={}
        for n in r['notes']:
            targets=[t for t in self.project['tracks'] if t['program']==n['program']]
            if any(o['pitch']==n['pitch'] and abs(o['start']-n['start'])<.12 and (o.get('deleted') or o.get('locked') or o.get('origin')=='user') for t in targets for o in t['notes']):continue
            if n['program'] not in new_tracks:
                new_tracks[n['program']]={'id':'rerun_'+uuid.uuid4().hex[:8],'program':n['program'],'name':ui_text('局部候选 · ')+n.get('instrument',str(n['program'])),'role':'drums' if n.get('drum') else 'unknown','drum':bool(n.get('drum')),'instrument':n.get('instrument','unknown'),'notes':[],'role_origin':'unverified'}
            t=new_tracks[n['program']]
            n=copy.deepcopy(n);n.update(id='rerun_'+uuid.uuid4().hex[:10],track=t['id'],candidate=True,deleted=False,locked=False,origin='predicted',velocity=80,velocity_origin='defaulted');t['notes'].append(n)
        self.project['tracks'].extend(new_tracks.values())
        self.pending_local=None;self.changed()
    def warn(self,s):QMessageBox.warning(self,APP_NAME,str(s)[-3000:])
    def closeEvent(self,e):
        if self.worker or (self.local_job and self.local_job.isRunning()) or (self.preview_job and self.preview_job.isRunning()):
            self.warn(ui_text('请先取消或等待正在运行的任务完成，再关闭软件。'));e.ignore();return
        if self.save_project(silent=True) is False:e.ignore();return
        self.player.stop();self.persist_queue();e.accept()

def root_output():return Path.home()/'Music'/'星谱 MIDI 输出'

def main():
    app=QApplication(sys.argv);app.setApplicationName('StarScoreMidi');app.setStyle('Fusion')
    font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'msyh.ttc'
    if font.exists():QFontDatabase.addApplicationFont(str(font))
    from studio_ui import StudioWindow,STYLE as STUDIO_STYLE
    app.setStyleSheet(STUDIO_STYLE)
    app.setFont(QFont('Microsoft YaHei UI',10));win=StudioWindow();win.show()
    def exception_hook(kind,value,tb):
        text=''.join(traceback.format_exception(kind,value,tb))
        with open(CONFIG/'errors.log','a',encoding='utf-8') as f:f.write(text+'\n')
        QMessageBox.critical(win,ui_text('发生错误'),str(value)+ui_text('\n诊断已保存至 ')+str(CONFIG/'errors.log'))
    sys.excepthook=exception_hook
    if len(sys.argv)>1 and Path(sys.argv[1]).name=='project.json':win.load_project(sys.argv[1])
    if '--settings' in sys.argv:
        from settings_ui import SettingsDialog
        settings_window=SettingsDialog(win,CONFIG);settings_window.show()
    if '--repair' in sys.argv:win.open_repairs()
    if '--vital' in sys.argv:win.open_vital()
    if '--full-song' in sys.argv:win.open_full_song(start=False)
    if '--smoke' in sys.argv:
        index=sys.argv.index('--smoke');dest=Path(sys.argv[index+1])
        def capture():
            (settings_window if '--settings' in sys.argv else win).grab().save(str(dest));core.save_json(dest.with_suffix('.json'),{'status':'ok','version':core.VERSION,'tracks':win.tracks.count(),'engine_exists':core.resource('engine/suno_engine.exe').exists()});win.close();app.quit()
        QTimer.singleShot(1800,capture)
    sys.exit(app.exec())

if __name__=='__main__':
    if '--export-fl' in sys.argv:
        import argparse
        from fl_export import build
        import vital_presets as vp
        parser=argparse.ArgumentParser();parser.add_argument('--export-fl',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
        output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
        try:
            source=Path(args.export_fl);project=core.read_json(source);options=vp.default_options(project)
            saved=source.parent/'vital_settings.json'
            if saved.exists():
                for tid,opt in core.read_json(saved).get('tracks',{}).items():
                    if tid in options and opt.get('family') in vp.FAMILIES:options[tid].update(opt)
            path,report=build(project,source.parent,output,options,progress=lambda s,p:core.save_json(output/'fl_status.json',{'message':s,'progress':p}))
            core.save_json(output/'fl_result.json',{'status':'complete','run':str(path),'report':report})
        except Exception:
            core.save_json(output/'fl_result.json',{'status':'failed','error':traceback.format_exc()});sys.exit(1)
    elif '--render-song' in sys.argv:
        import argparse
        from full_song import render_song
        import vital_presets as vp
        parser=argparse.ArgumentParser();parser.add_argument('--render-song',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
        output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
        try:
            source=Path(args.render_song);project=core.read_json(source);options=vp.default_options(project)
            saved=source.parent/'vital_settings.json'
            if saved.exists():
                for tid,opt in core.read_json(saved).get('tracks',{}).items():
                    if tid in options and opt.get('family') in vp.FAMILIES:options[tid].update(opt)
            path,report=render_song(project,source.parent,output,options,progress=lambda s,p:core.save_json(output/'render_status.json',{'message':s,'progress':p}))
            core.save_json(output/'render_result.json',{'status':'complete','run':str(path),'report':report})
        except Exception:
            core.save_json(output/'render_result.json',{'status':'failed','error':traceback.format_exc()});sys.exit(1)
    elif '--render-full-vital' in sys.argv:
        request=Path(sys.argv[sys.argv.index('--render-full-vital')+1])
        try:
            from full_song import render_vital_full
            render_vital_full(request)
        except Exception:
            core.save_json(request.with_suffix('.error.json'),{'error':traceback.format_exc()});sys.exit(1)
    elif '--render-vital' in sys.argv:
        request=Path(sys.argv[sys.argv.index('--render-vital')+1])
        try:
            from vital_presets import render_preview
            render_preview(request)
        except Exception:
            core.save_json(request.with_suffix('.error.json'),{'error':traceback.format_exc()});sys.exit(1)
    elif '--transcribe' in sys.argv:
        import argparse
        p=argparse.ArgumentParser();p.add_argument('--transcribe',required=True);p.add_argument('--output',required=True);p.add_argument('--model',default='medium');p.add_argument('--complex',action='store_true');args=p.parse_args()
        Path(args.output).mkdir(parents=True,exist_ok=True)
        with open(Path(args.output)/'cli.log','a',encoding='utf-8',buffering=1) as log:
            try:
                job,_,report=core.process(args.transcribe,args.output,args.model,complex_mode=args.complex,progress=lambda s,v:log.write(f'{s} {v:.3f}\n'))
                core.save_json(Path(args.output)/'cli_result.json',{'job':str(job),'report':report})
            except Exception:
                log.write(traceback.format_exc());sys.exit(1)
    else:main()

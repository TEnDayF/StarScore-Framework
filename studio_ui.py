"""Native, keyboard-driven correction workspace. No browser or network dependency."""
from i18n import tr as ui_text
import copy,json,os,time,uuid
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt,Signal,QPointF,QRectF,QTimer,QUrl,QItemSelectionModel,QThread
from PySide6.QtGui import QAction,QKeySequence,QColor,QPainter,QPen,QBrush,QFont,QFontMetrics
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTabWidget,
 QComboBox,QListWidget,QListWidgetItem,QSplitter,QTableWidget,QHeaderView,QAbstractItemView,QGraphicsView,QGraphicsScene,
 QGraphicsItem,QGraphicsRectItem,QFormLayout,QSpinBox,QDoubleSpinBox,QCheckBox,QSlider,QLineEdit,QAbstractSpinBox,QTextEdit,QPlainTextEdit,
 QMenu,QMessageBox,QScrollArea,QFrame,QDialog,QTextBrowser,QDialogButtonBox)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
import main as legacy
import core
import app_settings
from voice_tracking import DRUM_NAMES,melodic_path
from repair_ui import RepairDiagram,RepairAudio
from vital_ui import VitalPage
from full_song_ui import FullSongPage

from ui_theme import COLORS,STYLE,BrandMark,WorkflowTabs

DRUM_SHORT={35:ui_text('底鼓'),36:ui_text('底鼓'),37:ui_text('边击'),38:ui_text('军鼓'),39:ui_text('拍手'),40:ui_text('电军鼓'),41:ui_text('低通鼓'),42:ui_text('闭镲'),43:ui_text('高通鼓'),44:ui_text('踏镲'),45:ui_text('低通鼓'),46:ui_text('开镲'),47:ui_text('中通鼓'),48:ui_text('中通鼓'),49:ui_text('吊镲'),50:ui_text('高通鼓'),51:ui_text('叮叮镲'),52:ui_text('中国镲'),53:ui_text('镲帽'),54:ui_text('铃鼓'),55:ui_text('水镲'),56:ui_text('牛铃'),57:ui_text('吊镲'),58:ui_text('颤音器'),59:ui_text('叮叮镲')}


def b(text,callback,tip=None,primary=False):
    w=legacy.button(text,callback,primary);w.setToolTip(tip or text);return w

class UpgradeJob(QThread):
    done=Signal(str);error=Signal(str)
    def __init__(self,p,folder):super().__init__();self.p=copy.deepcopy(p);self.folder=folder
    def run(self):
        try:
            import soundfile as sf,shutil
            raw=core.read_json(self.folder/'raw_notes.json');x,sr=sf.read(self.folder/self.p['audio'],dtype='float32')
            if sr!=16000:raise ValueError(ui_text('历史工程复核音频应为 16 kHz'))
            tracks,candidates=core.classify_tracks(raw,self.p['duration'],core.audio_features(x))
            # Retain every user/locked/deleted event in its original source track.
            protected={n['id']:(t,n) for t in self.p['tracks'] for n in t['notes'] if n.get('locked') or n.get('deleted') or n.get('origin')=='user'}
            for t in tracks:t['notes']=[n for n in t['notes'] if n['id'] not in protected]
            for old,n in protected.values():
                target=next((t for t in tracks if t['id']==old['id']),None)
                if target is None:target={k:copy.deepcopy(v) for k,v in old.items() if k!='notes'};target['notes']=[];tracks.append(target)
                if old.get('role_origin')=='user':target.update(role=old['role'],name=old['name'],role_origin='user')
                target['notes'].append(n)
            candidates=[c for c in candidates if not any(i in protected for i in c['ids'])]
            dest=self.folder.parent/(self.folder.name+'_v03_'+uuid.uuid4().hex[:6]);dest.mkdir()
            for file in ('raw_notes.json',self.p['audio']):shutil.copy2(self.folder/file,dest/file)
            self.p.update(tracks=tracks,candidates=candidates,revision=self.p['revision']+1,version=core.VERSION)
            core.save_json(dest/'project.json',self.p);core.export_project(self.p,dest/'midi');core.save_json(dest/'upgrade.json',{'parent':str(self.folder),'protected_events':len(protected),'quality':'Role inference needs listening verification'})
            self.done.emit(str(dest/'project.json'))
        except Exception as e:self.error.emit(str(e))

class NoteItem(QGraphicsRectItem):
    def paint(self,painter,option,widget=None):
        painter.setBrush(self.brush());painter.setPen(QPen(QColor('#D7C9FF'),2) if self.isSelected() else self.pen());painter.drawRect(self.rect())

class Roll(QGraphicsView):
    selected=Signal(str);edit=Signal(str);selection_ids=Signal(object);seeked=Signal(float);moved=Signal(object,float,int)
    def __init__(self):
        super().__init__();self.setScene(QGraphicsScene(self));self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag);self.setMinimumHeight(180)
        self.items_by_id={};self.scale_x=90.;self.row_h=15.;self.low=36;self.high=96;self.duration=0;self.cursor=None
        self.role='lead';self.ghosts=[];self.drums=False;self.filling=False;self.drag=None;self.notes=[]
        self.setAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignTop)
        self.scene().selectionChanged.connect(self.on_selection)
    def on_selection(self):
        if not self.filling:self.selection_ids.emit([it.data(0) for it in self.scene().selectedItems() if it.data(0)])
    def populate(self,notes,duration):
        scroll=self.horizontalScrollBar().value();vertical=self.verticalScrollBar().value()
        self.filling=True;self.scene().clear();self.items_by_id={};self.notes=notes;self.duration=duration
        visible=[n for n in notes if not n.get('deleted')]
        alln=visible
        if alln:self.low=max(0,min(n['pitch'] for n in alln)-2);self.high=min(127,max(n['pitch'] for n in alln)+2)
        else:self.low,self.high=48,84
        h=(self.high-self.low+1)*self.row_h+25;w=max(900,duration*self.scale_x+64)
        for pitch in range(self.low,self.high+1):
            y=25+(self.high-pitch)*self.row_h
            col='#10192B' if pitch%12 in (1,3,6,8,10) else '#142039'
            self.scene().addRect(0,y,w,self.row_h,QPen(QColor('#23334D')),QBrush(QColor(col)))
        step=1 if self.scale_x>=60 else 5
        for sec in range(0,int(duration)+2,step):
            x=64+sec*self.scale_x
            self.scene().addLine(x,25,x,h,QPen(QColor('#3B5277' if sec%5==0 else '#243751')))
            tx=self.scene().addSimpleText(f'{sec // 60:02}:{sec % 60:02}');tx.setBrush(QColor('#B9CAE6'));tx.setPos(x+3,0)
        for n in self.ghosts:
            it=self.scene().addRect(64+n['start']*self.scale_x,26+(self.high-n['pitch'])*self.row_h,max(2,(n['end']-n['start'])*self.scale_x),self.row_h-3,QPen(Qt.PenStyle.NoPen),QBrush(QColor('#596A8C')));it.setOpacity(.45)
        for n in visible:
            color='#AA98FF' if n.get('candidate') else COLORS.get(self.role,COLORS['unknown'])
            pen=QPen(QColor('#ffffff' if n.get('locked') else color),1.5 if n.get('locked') else .6)
            it=NoteItem(QRectF(64+n['start']*self.scale_x,26+(self.high-n['pitch'])*self.row_h,max(3,(n['end']-n['start'])*self.scale_x),self.row_h-3));it.setPen(pen);it.setBrush(QBrush(QColor(color)));self.scene().addItem(it)
            it.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable);it.setData(0,n['id']);it.setZValue(2)
            label=DRUM_NAMES.get(n['pitch'],str(n['pitch'])) if self.drums else self.pitch_name(n['pitch'])
            it.setToolTip(f'{label} · {n['start']:.3f}—{n['end']:.3f}s'+(ui_text(' · 锁定') if n.get('locked') else ''))
            self.items_by_id[n['id']]=it
        self.cursor=self.scene().addLine(64,25,64,h,QPen(QColor('#EDF2FF'),1));self.cursor.setZValue(5)
        self.scene().setSceneRect(0,0,w,h);self.horizontalScrollBar().setValue(scroll);self.verticalScrollBar().setValue(vertical)
        self.filling=False;self.viewport().update()
    @staticmethod
    def pitch_name(p):return ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][p%12]+str(p//12-1)
    def set_time(self,sec):
        if self.cursor:self.cursor.setX(sec*self.scale_x)
    def paintEvent(self,event):
        super().paintEvent(event);p=QPainter(self.viewport());p.fillRect(0,0,63,self.viewport().height(),QColor('#18243C'))
        for pitch in range(self.low,self.high+1):
            y=self.mapFromScene(QPointF(0,25+(self.high-pitch)*self.row_h)).y()
            if y<0 or y>self.viewport().height():continue
            black=pitch%12 in (1,3,6,8,10);p.fillRect(0,y,62,int(self.row_h)-1,QColor('#19263F' if black else '#BFCDE4'))
            if self.drums or pitch%12==0:
                p.setPen(QColor('#EDF2FF' if black else '#10192B'));label=f'{pitch} {ui_text(DRUM_SHORT.get(pitch, ''))}' if self.drums else self.pitch_name(pitch)
                p.drawText(QRectF(3,y,58,self.row_h),Qt.AlignmentFlag.AlignVCenter,label)
        p.fillRect(0,0,self.viewport().width(),24,QColor('#18243C'));p.setPen(QColor('#B9CAE6'))
        first=max(0,(self.mapToScene(self.viewport().rect().topLeft()).x()-64)/self.scale_x)
        step=next((v for v in (.25,.5,1,2,5,10,15,30,60,120) if v*self.scale_x>=72),120)
        for sec in np.arange(np.floor(first/step)*step,min(self.duration+step,first+self.viewport().width()/self.scale_x+step),step):
            x=self.mapFromScene(QPointF(64+sec*self.scale_x,0)).x()
            if x>=64:p.drawText(x+3,17,f'{int(sec) // 60:02}:{sec % 60:04.1f}' if step<1 else f'{int(sec) // 60:02}:{int(sec) % 60:02}')
        p.fillRect(0,0,63,24,QColor('#18243C'));p.drawText(6,17,ui_text('鼓件') if self.drums else ui_text('音高'))
    def mousePressEvent(self,e):
        point=self.mapToScene(e.position().toPoint());it=self.itemAt(e.position().toPoint())
        if e.position().y()<25:self.seeked.emit(max(0,(point.x()-64)/self.scale_x));return
        super().mousePressEvent(e)
        if it and it.data(0) and e.button()==Qt.MouseButton.LeftButton:
            self.drag=(point,[x.data(0) for x in self.scene().selectedItems() if x.data(0)])
    def focusOutEvent(self,e):
        self.drag=None;super().focusOutEvent(e)
    def mouseReleaseEvent(self,e):
        super().mouseReleaseEvent(e)
        if self.drag:
            p,ids=self.drag;end=self.mapToScene(e.position().toPoint());dt=(end.x()-p.x())/self.scale_x;dp=round((p.y()-end.y())/self.row_h)
            self.drag=None
            if abs(end.x()-p.x())>5 or abs(end.y()-p.y())>5:self.moved.emit(ids,round(dt,3),dp)
    def mouseDoubleClickEvent(self,e):
        it=self.itemAt(e.position().toPoint())
        if it and it.data(0):self.edit.emit(it.data(0))
    def wheelEvent(self,e):
        if e.modifiers()&Qt.KeyboardModifier.ControlModifier:self.zoom(1.2 if e.angleDelta().y()>0 else 1/1.2);e.accept()
        else:super().wheelEvent(e)
    def focus_phrase(self):
        ns=[n for n in self.notes if not n.get('deleted')]
        if not ns:return
        n=min(ns,key=lambda n:n['start'])
        self.centerOn(64+n['start']*self.scale_x+self.viewport().width()*.3,25+(self.high-n['pitch']+.5)*self.row_h)
    def fit_vertical(self):
        self.row_h=max(5,min(22,(self.viewport().height()-26)/max(1,self.high-self.low+1)))
    def zoom(self,factor):
        center=self.mapToScene(self.viewport().rect().center()).x();old=self.scale_x;self.scale_x=max(8,min(700,self.scale_x*factor));self.populate(self.notes,self.duration);self.horizontalScrollBar().setValue(int((center-64)/old*self.scale_x-self.viewport().width()/2+64))

class StudioWindow(legacy.Window):
    def __init__(self):
        QMainWindow.__init__(self);self.setWindowTitle('StarScore MIDI Studio '+core.VERSION);self.resize(1380,850);self.setMinimumSize(1040,680);self.setAcceptDrops(True)
        self.jobs=[];self.worker=None;self.current=-1;self.running=False;self.project=None;self.folder=None;self.undo=[];self.redo=[];self.clipboard=[]
        self.preview_request=0;self._stage_index=0;self.pending_seek=None;self.pending_local=None;self.preview_job=None;self.local_job=None;self.upgrade_job=None;self.settings={};self.syncing=False;self.muted=set();self.solo=set();self.preview_cache={};self.last_track=None
        try:self.settings=app_settings.load(legacy.CONFIG)
        except Exception:pass
        self.player=QMediaPlayer();self.audio=QAudioOutput();self.audio.setVolume(.65);self.player.setAudioOutput(self.audio)
        self.repair_job=None;self.repair_request=0;self.repair_player=QMediaPlayer();self.repair_audio=QAudioOutput();self.repair_audio.setVolume(.65);self.repair_player.setAudioOutput(self.repair_audio)
        central=QWidget();central.setObjectName('appShell');self.setCentralWidget(central);layout=QVBoxLayout(central);layout.setContentsMargins(12,8,12,5);layout.setSpacing(10)
        top=QHBoxLayout();top.setSpacing(10);top.addWidget(BrandMark());brand=QLabel(ui_text('星谱 MIDI Studio'));brand.setObjectName('brand');top.addWidget(brand);top.addStretch()
        top.addWidget(b(ui_text('设置'),self.open_settings,'Ctrl+,'));top.addWidget(b('歌曲信息 / Song info',self.edit_song_info,'Ctrl+Shift+I'));top.addWidget(b(ui_text('导入歌曲'),self.add_files,'Ctrl+I'));top.addWidget(b(ui_text('开始转写'),self.start_queue,primary=True));self.export_button=b(ui_text('导出 MIDI'),self.export_current,'Ctrl+E');self.export_button.setEnabled(False);top.addWidget(self.export_button);self.vital_next=b(ui_text('下一步：制作 Vital 音色'),self.open_vital,primary=True);self.vital_next.setEnabled(False);top.addWidget(self.vital_next);top.addStretch();self.title_label=QLabel(ui_text('本地处理 · 研究预览 ')+core.VERSION);self.title_label.setMaximumWidth(170);top.addWidget(self.title_label);layout.addLayout(top)
        self.tabs=QTabWidget();self.tabs.setTabBar(WorkflowTabs(self.tabs));layout.addWidget(self.tabs,1);self.build_queue();self.build_review();self.tabs.setTabText(0,ui_text('01 提取歌曲'));self.tabs.setTabText(1,ui_text('02 检查 MIDI'));self.vital_page=VitalPage(self);self.tabs.addTab(self.vital_page,ui_text('03 制作 Vital 音色'));self.full_page=FullSongPage(self);self.tabs.addTab(self.full_page,ui_text('04 全曲试听 / FL 导出'))
        self.build_transport(layout);self.build_menus();self.tabs.currentChanged.connect(self.update_shortcuts);self.tabs.currentChanged.connect(self.stage_changed)
        self.player.positionChanged.connect(self.on_position);self.player.errorOccurred.connect(lambda e,s:self.statusBar().showMessage(ui_text('播放：')+s))
        self.player.mediaStatusChanged.connect(self.media_status)
        try:
            self.jobs=core.read_json(legacy.CONFIG/'queue.json')
            for j in self.jobs:
                if j['status'] in ('运行中','等待中'):j['status']='已中断，可重新排队'
        except Exception:self.jobs=[]
        self.refresh_queue();self.statusBar().showMessage(ui_text('拖入歌曲开始；Ctrl+O 打开历史工程。模型仅限非商业用途。F1 查看快捷键。'))
        QApplication.instance().focusChanged.connect(lambda *_:self.update_shortcuts())
    def job_done(self,result):
        super().job_done(result)
        self.last_completed_project=Path(result[0])/'project.json';self.vital_next.setEnabled(True)
        self.stage.setText(ui_text('提取完成。双击任务检查 MIDI，或点击“下一步：制作 Vital 音色”。'))
    def open_vital(self):
        if self.full_page.busy:self.statusBar().showMessage(ui_text('请先完成或取消全曲渲染。'));return
        if self.tabs.currentIndex()==2:return
        if self.tabs.currentIndex()==0:
            row=self.queue.currentRow();job=self.jobs[row] if 0<=row<len(self.jobs) else {}
            target=Path(job['folder'])/'project.json' if job.get('folder') else getattr(self,'last_completed_project',None)
            if target and self.load_project(target) is False:return
        if not self.project:self.statusBar().showMessage(ui_text('请先提取歌曲或打开已有工程。'));return
        if self.vital_page.busy:self.tabs.setCurrentIndex(2);return
        self.vital_page.load_project(self.project,self.folder);self.tabs.setCurrentIndex(2)
    def open_full_song(self,start=True):
        if not self.project:self.statusBar().showMessage(ui_text('请先提取歌曲或打开工程。'));return
        if self.full_page.busy:self.tabs.setCurrentIndex(3);return
        if self.vital_page.busy:self.warn(ui_text('请先完成或取消音色预设制作。'));return
        if self.vital_page.folder!=self.folder:self.vital_page.load_project(self.project,self.folder)
        try:self.vital_page.save_options()
        except OSError as e:self.warn(str(e));return
        self.full_page.load_project(self.project,self.folder,self.vital_page.output.text() or str(self.folder/'Vital音色'),self.vital_page.options)
        self.tabs.setCurrentIndex(3)
        if start:self.full_page.start()
    def stage_changed(self,index):
        self.preview_request+=1;self.repair_request+=1
        previous=self._stage_index
        if previous==2:
            try:self.vital_page.save_options()
            except OSError as e:
                self.tabs.blockSignals(True);self.tabs.setCurrentIndex(2);self.tabs.blockSignals(False)
                self.warn(ui_text('音色设置保存失败，已保留当前编辑：')+str(e));return
        self._stage_index=index;self.transport_widget.setVisible(index<2)
        self.player.pause();self.repair_player.stop();self.vital_page.stop_audio()
        if index==2 and self.project and not self.vital_page.busy:self.vital_page.load_project(self.project,self.folder)
        elif index==3 and self.project and not self.full_page.busy:
            if self.vital_page.folder!=self.folder:self.vital_page.load_project(self.project,self.folder)
            output=self.vital_page.output.text().strip() or str(self.folder/'Vital音色')
            if self.full_page.project is None or self.full_page.folder!=self.folder:
                self.full_page.load_project(self.project,self.folder,output,self.vital_page.options)
            else:
                self.full_page.output=Path(output);self.full_page.options=copy.deepcopy(self.vital_page.options)
                if self.full_page.project!=self.project:self.full_page.snapshot.setText(ui_text('MIDI 已修改；请重新生成以试听最新修订'))
            self.full_page.output_label.setText(ui_text('本次输出：')+output)
        if index!=3:self.full_page.stop_audio()
        self.update_shortcuts()

    def build_review(self):
        page=QWidget();outer=QVBoxLayout(page);outer.setContentsMargins(0,0,0,0);outer.setSpacing(0)
        body=QSplitter(Qt.Orientation.Horizontal);outer.addWidget(body)
        left=QWidget();left.setObjectName('surface');left.setMinimumWidth(190);ll=QVBoxLayout(left);ll.setContentsMargins(8,10,8,8)
        title=QLabel(ui_text('声部 / 来源'));title.setObjectName('section');ll.addWidget(title)
        self.track_list=QListWidget();self.track_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.track_list.setTextElideMode(Qt.TextElideMode.ElideRight);self.track_list.currentRowChanged.connect(self.list_changed);ll.addWidget(self.track_list,1)
        ms=QHBoxLayout();self.mute_button=b(ui_text('静音 M'),lambda:self.toggle_track_flag('mute'));self.mute_button.setCheckable(True);self.solo_button=b(ui_text('独奏 S'),lambda:self.toggle_track_flag('solo'));self.solo_button.setCheckable(True);ms.addWidget(self.mute_button);ms.addWidget(self.solo_button);ll.addLayout(ms)
        ll.addWidget(b(ui_text('新增来源轨'),self.new_track));ll.addWidget(b(ui_text('打开结果目录'),self.open_folder));body.addWidget(left)
        center=QWidget();center.setObjectName('surface');cl=QVBoxLayout(center);cl.setContentsMargins(7,8,7,8);cl.setSpacing(6)
        self.summary=QLabel(ui_text('打开工程后选择声部。拖框多选，拖动音符调整时间与音高。'));self.summary.setObjectName('muted');self.summary.setWordWrap(True);cl.addWidget(self.summary)
        self.wave=legacy.Waveform();self.wave.setFixedHeight(52);self.wave.seeked.connect(self.seek_to);cl.addWidget(self.wave)
        editbar=QHBoxLayout();editbar.addWidget(b(ui_text('添加'),self.add_note,'Insert'));editbar.addWidget(b(ui_text('删除'),self.delete_notes,'Delete'));editbar.addWidget(b(ui_text('合并'),self.merge_notes,'Ctrl+J'));editbar.addWidget(b(ui_text('拆分'),self.split_note,ui_text('B，按播放光标拆分')));editbar.addStretch()
        self.adopt_button=b(ui_text('采用所选候选'),self.accept_notes,ui_text('Ctrl+Shift+A · 候选采用后才参与试听和导出'))
        editbar.addWidget(b(ui_text('修复碎音'),self.open_repairs,ui_text('定位、高亮并试听同音碎裂建议')))
        self.ghost=QCheckBox(ui_text('其他轨参考'));self.ghost.setChecked(False);self.ghost.setToolTip(ui_text('灰色是其他声部参考，不会导出到当前 MIDI'));self.ghost.toggled.connect(self.refresh_notes);editbar.addWidget(self.ghost);editbar.addWidget(b(ui_text('适配'),self.fit_roll,'F'));cl.addLayout(editbar)
        vertical=QSplitter(Qt.Orientation.Vertical);self.editor_split=vertical;self.piano=Roll();self.piano.selection_ids.connect(self.roll_selected);self.piano.edit.connect(self.edit_note);self.piano.seeked.connect(self.seek_to);self.piano.moved.connect(self.move_ids);vertical.addWidget(self.piano)
        self.detail_tabs=QTabWidget();self.notes=QTableWidget(0,6);self.notes.setHorizontalHeaderLabels([ui_text('音高'),ui_text('开始 s'),ui_text('结束 s'),ui_text('时长 s'),ui_text('状态'),ui_text('事件 ID')]);self.notes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.notes.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);self.notes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.notes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.notes.doubleClicked.connect(lambda:self.edit_note());self.notes.itemSelectionChanged.connect(self.table_selected)
        note_panel=QWidget();note_layout=QVBoxLayout(note_panel);note_layout.setContentsMargins(0,0,0,0);note_layout.setSpacing(4);note_layout.addWidget(self.notes,1);note_layout.addWidget(self.adopt_button);self.detail_tabs.addTab(note_panel,ui_text('音符列表'));panel=QWidget();pl=QVBoxLayout(panel);pl.setContentsMargins(4,4,4,4);pl.setSpacing(4)
        self.repair_hint=QLabel(ui_text('碎音：本来一颗持续音被切成同音高短段。真实重复敲击应保留。'));self.repair_hint.setWordWrap(True);pl.addWidget(self.repair_hint)
        repair_body=QSplitter(Qt.Orientation.Horizontal);self.candidates=QTableWidget(0,3);self.candidates.setHorizontalHeaderLabels([ui_text('时间(s)'),ui_text('音高'),ui_text('间隙')]);self.candidates.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.candidates.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.candidates.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.candidates.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.candidates.itemSelectionChanged.connect(self.focus_candidate);repair_body.addWidget(self.candidates)
        self.candidates.setMinimumWidth(250);self.candidates.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column,width in enumerate((80,70,55)):self.candidates.setColumnWidth(column,width)
        detail=QWidget();dl=QVBoxLayout(detail);dl.setContentsMargins(4,0,0,0);dl.setSpacing(3);self.repair_diagram=RepairDiagram();dl.addWidget(self.repair_diagram);self.repair_description=QLabel(ui_text('选择建议后，紫色边框标记待合并的两颗音符。'));self.repair_description.setWordWrap(True);dl.addWidget(self.repair_description)
        actions=QHBoxLayout();self.repair_original=b(ui_text('原曲对照'),self.audition_original);self.repair_before=b(ui_text('原2音试听'),lambda:self.audition_repair(False));self.repair_after=b(ui_text('合并试听'),lambda:self.audition_repair(True));actions.addWidget(self.repair_original);actions.addWidget(self.repair_before);actions.addWidget(self.repair_after);repair_audio_actions=actions
        actions=QHBoxLayout();self.repair_apply=b(ui_text('合并这一处'),self.accept_candidate,primary=True);self.repair_keep=b(ui_text('保留重复'),self.dismiss_candidate);actions.addWidget(self.repair_apply);actions.addWidget(self.repair_keep);actions.addWidget(b(ui_text('撤销'),self.undo_edit,'Ctrl+Z'));repair_edit_actions=actions;detail.setMinimumHeight(150);repair_scroll=QScrollArea();repair_scroll.setWidgetResizable(True);repair_scroll.setFrameShape(QFrame.Shape.NoFrame);repair_scroll.setWidget(detail);repair_body.addWidget(repair_scroll);repair_body.setSizes([250,480]);pl.addWidget(repair_body,1);pl.addLayout(repair_audio_actions);pl.addLayout(repair_edit_actions);panel.setMinimumHeight(270);self.detail_tabs.addTab(panel,ui_text('碎音修复'));self.repair_buttons_enabled(False)
        vertical.addWidget(self.detail_tabs);vertical.setSizes([400,145]);cl.addWidget(vertical,1);body.addWidget(center)
        self.detail_tabs.currentChanged.connect(self.review_detail_changed)
        right=QWidget();right.setObjectName('surface');rl=QVBoxLayout(right);rl.setContentsMargins(10,10,10,8);rl.setSpacing(8)
        label=QLabel(ui_text('音符属性'));label.setObjectName('section');rl.addWidget(label);self.selection_label=QLabel(ui_text('未选中音符'));self.selection_label.setWordWrap(True);rl.addWidget(self.selection_label)
        form=QFormLayout();form.setSpacing(7);self.in_pitch=QSpinBox();self.in_pitch.setRange(0,127);self.in_start=QDoubleSpinBox();self.in_end=QDoubleSpinBox()
        for w in (self.in_start,self.in_end):w.setRange(0,86400);w.setDecimals(3);w.setSingleStep(.01)
        self.in_velocity=QSpinBox();self.in_velocity.setRange(1,127);self.in_velocity.setValue(80);self.target=QComboBox();self.in_lock=QCheckBox(ui_text('锁定人工修正'))
        form.addRow(ui_text('音高 MIDI'),self.in_pitch);form.addRow(ui_text('开始 / 秒'),self.in_start);form.addRow(ui_text('结束 / 秒'),self.in_end);form.addRow(ui_text('力度'),self.in_velocity);form.addRow(ui_text('所属来源'),self.target);form.addRow(self.in_lock);rl.addLayout(form)
        rl.addWidget(b(ui_text('应用音符属性'),self.apply_inspector));rl.addWidget(b(ui_text('移动所选到此来源'),self.move_to_target,ui_text('支持整句移动，保留锁定音符')))
        self.role=QComboBox()
        for key,name in core.ROLES.items():self.role.addItem(ui_text(name),key)
        rr=QHBoxLayout();rr.addWidget(self.role);rr.addWidget(b(ui_text('设置角色'),self.change_role));rl.addLayout(rr)
        sep=QFrame();sep.setFrameShape(QFrame.Shape.HLine);rl.addWidget(sep)
        label=QLabel(ui_text('选区 / 声部约束'));label.setObjectName('section');rl.addWidget(label)
        ranges=QFormLayout();self.range_start=QDoubleSpinBox();self.range_end=QDoubleSpinBox()
        for w in (self.range_start,self.range_end):w.setDecimals(2);w.setRange(0,86400)
        self.range_end.setValue(20);ranges.addRow(ui_text('起点 / 秒'),self.range_start);ranges.addRow(ui_text('终点 / 秒'),self.range_end);rl.addLayout(ranges)
        self.anchor_label=QLabel(ui_text('选择正确旋律片段作为身份参考'));self.anchor_label.setWordWrap(True);self.anchor_label.setObjectName('muted');rl.addWidget(self.anchor_label)
        rl.addWidget(b(ui_text('用所选乐句寻找同声部'),self.anchor_reassign,ui_text('在指定时间范围内按所选音域重新组织同来源事件；保留锁定和人工修改')))
        rerun=QHBoxLayout();rerun.addWidget(b(ui_text('局部重跑'),self.rerun));rerun.addWidget(b(ui_text('接收候选'),self.accept_rerun));rl.addLayout(rerun)
        rl.addStretch();self.fixed=QSpinBox();self.fixed.setRange(30,300);self.fixed.setValue(120)
        foot=QHBoxLayout();foot.addWidget(QLabel(ui_text('固定 BPM')));foot.addWidget(self.fixed);foot.addWidget(b(ui_text('导出'),self.export_fixed));rl.addLayout(foot)
        right.setMinimumWidth(230);right.setMinimumHeight(625)
        inspector=QScrollArea();inspector.setWidgetResizable(True);inspector.setFrameShape(QFrame.Shape.NoFrame);inspector.setWidget(right);inspector.setMinimumWidth(250);inspector.setMaximumWidth(320)
        body.addWidget(inspector);body.setSizes([205,900,265]);body.setCollapsible(0,False);body.setCollapsible(1,False);body.setCollapsible(2,False)
        self.tracks=QComboBox();self.tracks.hide();self.tracks.currentIndexChanged.connect(self.refresh_notes)
        self.tabs.addTab(page,ui_text('音符编辑'))
    def review_detail_changed(self,index):
        repair=index==1
        self.wave.setVisible(not repair);self.summary.setVisible(not repair)
        self.piano.setMinimumHeight(80 if repair else 180)
        self.detail_tabs.setMinimumHeight(310 if repair else 0)
        self.editor_split.setSizes([120,340] if repair else [400,145])
    def build_transport(self,layout):
        row=QHBoxLayout();row.setSpacing(7)
        row.addWidget(b(ui_text('播放 / 暂停'),self.toggle_play,'Space'));row.addWidget(b(ui_text('停止'),self.stop_play,'Shift+Space'));self.clock=QLabel('00:00.000 / 00:00');self.clock.setMinimumWidth(170);row.addWidget(self.clock)
        self.listen=QComboBox();self.listen.addItems([ui_text('原音'),ui_text('当前 MIDI'),ui_text('全部 MIDI'),ui_text('当前来源分离音频')]);self.listen.currentIndexChanged.connect(self.listen_changed);row.addWidget(self.listen)
        self.loop=QCheckBox(ui_text('循环 L'));row.addWidget(self.loop);self.seek=QSlider(Qt.Orientation.Horizontal);self.seek.setRange(0,1000);self.seek.setSingleStep(100);self.seek.setPageStep(1000);self.seek.sliderReleased.connect(lambda:self.seek_to(self.seek.value()/1000));self.seek.actionTriggered.connect(lambda _:QTimer.singleShot(0,lambda:self.seek_to(self.seek.sliderPosition()/1000)));row.addWidget(self.seek,1)
        row.addWidget(b(ui_text('缩小'),lambda:self.piano.zoom(1/1.25),'Ctrl+-'));row.addWidget(b(ui_text('放大'),lambda:self.piano.zoom(1.25),'Ctrl+='));row.addWidget(b(ui_text('快捷键'),self.help_keys,'F1'));self.transport_widget=QWidget();self.transport_widget.setLayout(row);layout.addWidget(self.transport_widget)
    def open_settings(self):
        objects=[self,getattr(self,'vital_page',None),getattr(self,'full_page',None)]
        for obj in objects:
            for name in ('worker','job','preview_job','local_job','repair_job','upgrade_job'):
                worker=getattr(obj,name,None)
                if worker and hasattr(worker,'isRunning') and worker.isRunning():
                    self.statusBar().showMessage(ui_text('任务正在运行，请等待完成或取消后再修改设置。'));return
        from settings_ui import SettingsDialog
        dialog=SettingsDialog(self,legacy.CONFIG)
        if dialog.exec():
            self.settings=dialog.value;self.output.setText(self.settings['output'])
            self.model.setCurrentIndex(max(0,self.model.findData(self.settings['model'])))
            self.adaptive.setChecked(self.settings['adaptive']);self.analysis_mode.setCurrentIndex(1 if self.settings['complex_mode'] else 0)
            import vital_presets as vp
            vp.PLUGIN=Path(self.settings['vital_path'])
            self.statusBar().showMessage(ui_text('设置已保存，将用于后续任务。'))

    def build_menus(self):
        self.key_actions=[];self.shortcut_rows=[]
        file=self.menuBar().addMenu(ui_text('文件'));edit=self.menuBar().addMenu(ui_text('编辑'));view=self.menuBar().addMenu(ui_text('视图'));play=self.menuBar().addMenu(ui_text('播放'));help_menu=self.menuBar().addMenu(ui_text('帮助'))
        def action(menu,label,key,cb,editing=False):
            a=QAction(label,self);a.setShortcut(QKeySequence(key));a.triggered.connect(lambda checked=False:cb());menu.addAction(a);self.shortcut_rows.append((key,label))
            if editing:a.setProperty('editorOnly',menu is edit and key not in ('Ctrl+Z','Ctrl+Y','Ctrl+Shift+Z','Ctrl+Shift+A'));self.key_actions.append(a)
            return a
        for label,key,cb in [(ui_text('导入歌曲'),'Ctrl+I',self.add_files),(ui_text('打开工程'),'Ctrl+O',self.open_project),(ui_text('保存工程'),'Ctrl+S',self.save_project),(ui_text('导出各轨 MIDI'),'Ctrl+E',self.export_current)]:action(file,label,key,cb)
        action(file,ui_text('设置'),'Ctrl+,',self.open_settings)
        action(file,'歌曲信息 / Song info','Ctrl+Shift+I',self.edit_song_info)
        for label,key,cb in [(ui_text('撤销'),'Ctrl+Z',self.undo_edit),(ui_text('重做'),'Ctrl+Shift+Z',self.redo_edit),(ui_text('重做'),'Ctrl+Y',self.redo_edit),(ui_text('采用所选候选'),'Ctrl+Shift+A',self.accept_notes),(ui_text('全选当前声部'),'Ctrl+A',self.select_all),(ui_text('复制音符'),'Ctrl+C',self.copy_notes),(ui_text('粘贴到播放位置'),'Ctrl+V',self.paste_notes),(ui_text('紧接复制一份'),'Ctrl+D',self.duplicate_notes),(ui_text('删除未锁定音符'),'Delete',self.delete_notes),(ui_text('添加音符'),'Insert',self.add_note),(ui_text('合并同音'),'Ctrl+J',self.merge_notes),(ui_text('按光标拆分'),'B',self.split_note),(ui_text('锁定 / 解锁'),'Ctrl+L',self.lock_notes),(ui_text('选中范围作为循环区'),'R',self.range_from_selection),(ui_text('音高 +1'),'Up',lambda:self.nudge(0,1)),(ui_text('音高 -1'),'Down',lambda:self.nudge(0,-1)),(ui_text('八度 +1'),'Shift+Up',lambda:self.nudge(0,12)),(ui_text('八度 -1'),'Shift+Down',lambda:self.nudge(0,-12)),(ui_text('向前 10 ms'),'Left',lambda:self.nudge(-.01,0)),(ui_text('向后 10 ms'),'Right',lambda:self.nudge(.01,0)),(ui_text('向前 100 ms'),'Shift+Left',lambda:self.nudge(-.1,0)),(ui_text('向后 100 ms'),'Shift+Right',lambda:self.nudge(.1,0))]:action(edit,label,key,cb,True)
        for label,key,cb in [(ui_text('播放 / 暂停'),'Space',self.toggle_play),(ui_text('停止并返回选区起点'),'Shift+Space',self.stop_play),(ui_text('回到歌曲开头'),'Home',lambda:self.seek_to(0)),(ui_text('到歌曲末尾'),'End',lambda:self.seek_to(self.project['duration'] if self.project else 0)),(ui_text('循环选区'),'L',lambda:self.loop.setChecked(not self.loop.isChecked())),(ui_text('静音当前轨'),'M',lambda:self.toggle_track_flag('mute')),(ui_text('独奏当前轨'),'S',lambda:self.toggle_track_flag('solo')),(ui_text('试听原音'),'1',lambda:self.set_listen(0)),(ui_text('试听当前 MIDI'),'2',lambda:self.set_listen(1)),(ui_text('试听全部 MIDI'),'3',lambda:self.set_listen(2))]:action(play,label,key,cb,True)
        action(file,ui_text('从原始事件重建分轨（另存）'),'Ctrl+Shift+R',self.upgrade_project)
        action(view,ui_text('适配全曲'),'F',self.fit_roll,True);action(view,ui_text('放大'),'Ctrl+=',lambda:self.piano.zoom(1.25),True);action(view,ui_text('缩小'),'Ctrl+-',lambda:self.piano.zoom(1/1.25),True)
        action(view,ui_text('显示 / 隐藏事件列表'),'Ctrl+Shift+L',lambda:self.detail_tabs.setVisible(not self.detail_tabs.isVisible()),True)
        action(file,ui_text('下一步：制作 Vital 音色'),'Ctrl+Shift+V',self.open_vital)
        self.shortcut_rows.extend([('Ctrl+Enter',ui_text('音色页：生成所选预设')),('Alt+Left',ui_text('音色页：返回 MIDI 检查'))])
        action(help_menu,ui_text('快捷键与操作'),'F1',self.help_keys)
    def update_shortcuts(self,*args):
        w=QApplication.focusWidget();typing=isinstance(w,(QLineEdit,QAbstractSpinBox,QTextEdit,QPlainTextEdit,QComboBox))
        editor=w is self.piano or self.piano.isAncestorOf(w) if w else False
        editor=editor or (w is self.notes or self.notes.isAncestorOf(w) if w else False)
        for a in self.key_actions:a.setEnabled(not typing and self.tabs.currentIndex()==1 and self.project is not None and (not a.property('editorOnly') or editor))
    def help_keys(self):
        text=ui_text('<h3>星谱快捷键</h3><table>')+''.join(f'<tr><td style="padding-right:25px">{key}</td><td>{label}</td></tr>' for key,label in self.shortcut_rows)+ui_text('</table><p>拖框多选；Ctrl 点击增选；拖动音符移动。Ctrl+滚轮缩放。锁定音符不会被移动或删除。文本输入时编辑快捷键暂停。</p>')
        box=QDialog(self);box.setWindowTitle(ui_text('快捷键'));box.resize(620,580);layout=QVBoxLayout(box);browser=QTextBrowser();browser.setHtml(text);layout.addWidget(browser);buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);buttons.rejected.connect(box.reject);layout.addWidget(buttons);box.exec()
    def list_changed(self,index):
        if index>=0:self.tracks.setCurrentIndex(index)
    def refresh_tracks(self,selected=None):
        if not self.project:return
        self.syncing=True;selected=selected or self.tracks.currentData();self.tracks.blockSignals(True);self.target.blockSignals(True);self.track_list.blockSignals(True)
        self.tracks.clear();self.target.clear();self.track_list.clear()
        for t in self.project['tracks']:
            self.tracks.addItem(t['name'],t['id']);self.target.addItem(t['name'],t['id'])
            item=QListWidgetItem(f'{ui_text(core.ROLES[t['role']])}  ·  {len(core.active_notes(t))}{ui_text(' 音\n')}{t.get('instrument', ui_text('来源'))}');item.setForeground(QColor(COLORS.get(t['role'],COLORS['unknown'])));item.setToolTip(t['name']);self.track_list.addItem(item)
        index=max(0,self.tracks.findData(selected));self.tracks.setCurrentIndex(index);self.target.setCurrentIndex(index);self.track_list.setCurrentRow(index)
        self.tracks.blockSignals(False);self.target.blockSignals(False);self.track_list.blockSignals(False);self.syncing=False;self.refresh_track_labels();self.refresh_notes();self.update_shortcuts()
    def refresh_track_labels(self):
        if not self.project:return
        fm=QFontMetrics(self.track_list.font());width=max(70,self.track_list.viewport().width()-18)
        for i,t in enumerate(self.project['tracks']):
            flags=(' [M]' if t['id'] in self.muted else '')+(' [S]' if t['id'] in self.solo else '')
            first=f'{ui_text(core.ROLES[t['role']])}{flags} · {len(core.active_notes(t))}{ui_text(' 音')}';second=t.get('instrument',ui_text('来源'))
            self.track_list.item(i).setText(fm.elidedText(first,Qt.TextElideMode.ElideRight,width)+'\n'+fm.elidedText(second,Qt.TextElideMode.ElideRight,width));self.track_list.item(i).setToolTip(t['name']+flags+f'\n{len(core.active_notes(t))}{ui_text(' 个音符')}')
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'track_list'):QTimer.singleShot(0,self.refresh_track_labels)
    def refresh_notes(self,*args):
        if self.syncing:return
        t=self.track()
        if not t:
            self.shown=[];self.shown_candidates=[];self.notes.setRowCount(0);self.candidates.setRowCount(0)
            self.piano.ghosts=[];self.piano.populate([],self.project['duration'] if self.project else 0)
            self.repair_diagram.clear(empty=True);self.repair_buttons_enabled(False);self.adopt_button.setEnabled(False)
            self.selection_label.setText(ui_text('当前工程没有声部，可新增来源轨'));return
        row=self.candidates.currentRow();old=getattr(self,'shown_candidates',[]);selected=old[row]['id'] if 0<=row<len(old) else None
        self.syncing=True;self.piano.role=t['role'];self.piano.drums=t['role']=='drums';self.piano.ghosts=[]
        if self.ghost.isChecked() and not self.piano.drums:self.piano.ghosts=[n for ot in self.project['tracks'] if ot['id']!=t['id'] and ot['role']!='drums' for n in core.active_notes(ot)]
        super().refresh_notes();self.refresh_repair_candidates(selected);self.track_list.blockSignals(True);self.track_list.setCurrentRow(self.tracks.currentIndex());self.track_list.blockSignals(False)
        self.mute_button.setChecked(t['id'] in self.muted);self.solo_button.setChecked(t['id'] in self.solo);self.target.setCurrentIndex(max(0,self.target.findData(t['id'])));self.syncing=False;self.table_selected()
        if self.last_track!=t['id'] and hasattr(self,'listen') and self.listen.currentIndex() in (1,3):self.invalidate_preview()
        if self.last_track!=t['id']:self.piano.focus_phrase()
        self.last_track=t['id']
        if self.detail_tabs.currentIndex()==1 and self.candidates.currentRow()>=0:self.focus_candidate()
    def repair_buttons_enabled(self,enabled):
        for button in (self.repair_original,self.repair_before,self.repair_after,self.repair_apply,self.repair_keep):button.setEnabled(enabled)
    def refresh_repair_candidates(self,selected=None):
        t=self.track();self.shown_candidates=sorted([c for c in self.project['candidates'] if c['track']==t['id'] and not c.get('accepted') and not c.get('dismissed')],key=lambda c:(c['time'],c['id']))
        self.candidates.blockSignals(True);self.candidates.setRowCount(len(self.shown_candidates));target=-1
        from PySide6.QtWidgets import QTableWidgetItem
        for row,c in enumerate(self.shown_candidates):
            try:
                _,_,a,z=core.candidate_notes(self.project,c['id']);values=[f'{a['start']:.2f}',f'{Roll.pitch_name(a['pitch'])}/{a['pitch']}',f'{(z['start'] - a['end']) * 1000:.0f} ms']
            except (ValueError,StopIteration):values=[f'{c['time']:.3f}s',ui_text('已变化'),ui_text('需重检')]
            for col,value in enumerate(values):self.candidates.setItem(row,col,QTableWidgetItem(value))
            if c['id']==selected:target=row
        if target>=0:self.candidates.selectRow(target)
        else:self.candidates.clearSelection();self.candidates.setCurrentCell(-1,-1)
        self.candidates.blockSignals(False);self.repair_diagram.clear(empty=not self.shown_candidates);self.repair_buttons_enabled(False)
        self.detail_tabs.setTabText(1,f'{ui_text('碎音修复 · ')}{len(self.shown_candidates)}')
        self.repair_description.setText(ui_text('点击一条建议，查看两颗音符和合并效果。') if self.shown_candidates else ui_text('当前声部没有可用建议。没有建议不代表音符已经准确；可多选同音符后用“合并”。'))
    def open_repairs(self):
        self.detail_tabs.show();self.detail_tabs.setCurrentIndex(1);self.editor_split.setSizes([180,340])
        if self.shown_candidates:self.candidates.selectRow(max(0,self.candidates.currentRow()));self.focus_candidate()
    def candidate_selection(self):
        i=self.candidates.currentRow()
        if not self.project or not 0<=i<len(getattr(self,'shown_candidates',[])):raise ValueError(ui_text('先在左侧选择一条碎音建议。'))
        return core.candidate_notes(self.project,self.shown_candidates[i]['id'])
    def focus_candidate(self):
        if self.syncing:return
        if self.candidates.currentRow()<0:return
        self.repair_request+=1;self.repair_player.stop();self.player.pause();self.loop.setChecked(False)
        try:c,t,a,z=self.candidate_selection()
        except (ValueError,StopIteration) as e:self.repair_diagram.clear();self.repair_buttons_enabled(False);self.repair_description.setText(str(e));return
        self.repair_diagram.set_notes(a,z);self.repair_buttons_enabled(True)
        self.repair_description.setText(f'{Roll.pitch_name(a['pitch'])} / MIDI {a['pitch']} · {a['start']:.3f}–{z['end']:.3f}{ui_text('s\n前后为合成音；请用原曲判断重复起音。')}')
        span=max(1.6,z['end']-a['start']+1);self.piano.scale_x=min(700,max(60,(self.piano.viewport().width()-90)/span));self.piano.populate(t['notes'],self.project['duration']);self.restore_selection([a['id'],z['id']]);self.seek_to(a['start'])
        y=25+(self.piano.high-a['pitch'])*self.piano.row_h
        self.piano.centerOn(64+(a['start']+z['end'])/2*self.piano.scale_x,y)
        for n in (a,z):
            item=self.piano.items_by_id[n['id']];item.setPen(QPen(QColor('#D7C9FF'),3));item.setZValue(7)
    def accept_candidate(self):
        try:c,t,a,z=self.candidate_selection()
        except (ValueError,StopIteration) as e:self.statusBar().showMessage(str(e));return
        row=self.candidates.currentRow();self.checkpoint();core.apply_candidate(self.project,c['id']);self.changed();self.statusBar().showMessage(ui_text('已把这两颗同音合为一颗；Ctrl+Z 或修复面板“撤销”可恢复。'))
        if self.shown_candidates:self.candidates.selectRow(min(row,len(self.shown_candidates)-1))
    def dismiss_candidate(self):
        try:c,t,a,z=self.candidate_selection()
        except (ValueError,StopIteration):return
        row=self.candidates.currentRow();self.checkpoint();c['dismissed']=True;self.changed();self.statusBar().showMessage(ui_text('已保留两次击奏，仅忽略此建议；可撤销。'))
        if self.shown_candidates:self.candidates.selectRow(min(row,len(self.shown_candidates)-1))
    def audition_repair(self,merged):
        if self.repair_job and self.repair_job.isRunning():return
        try:c,t,a,z=self.candidate_selection()
        except (ValueError,StopIteration) as e:self.statusBar().showMessage(str(e));return
        self.player.pause();self.repair_player.stop();self.repair_request+=1;request=self.repair_request;rev=self.project['revision'];folder=self.folder;cid=c['id']
        dest=folder/'repair_previews';dest.mkdir(exist_ok=True);path=dest/(('after_' if merged else 'before_')+uuid.uuid4().hex[:8]+'.wav')
        self.repair_job=RepairAudio(self.project,cid,path,merged)
        def ready(p):
            if self.folder!=folder or self.project['revision']!=rev or self.repair_request!=request:return
            try:current=self.candidate_selection()[0]['id']
            except (ValueError,StopIteration):return
            if current!=cid:return
            self.repair_player.setSource(QUrl.fromLocalFile(p));self.repair_player.play();self.statusBar().showMessage(ui_text('正在试听：')+(ui_text('合并后 1 音（尚未应用）') if merged else ui_text('原来 2 音'))+ui_text(' · 基础音源'))
        self.repair_job.done.connect(ready);self.repair_job.error.connect(self.warn);self.repair_job.start()
    def audition_original(self):
        try:c,t,a,z=self.candidate_selection()
        except (ValueError,StopIteration):return
        self.repair_request+=1;self.listen.blockSignals(True);self.listen.setCurrentIndex(0);self.listen.blockSignals(False)
        self.range_start.setValue(max(0,a['start']-.3));self.range_end.setValue(min(self.project['duration'],z['end']+.4));self.loop.setChecked(True);self.seek_to(self.range_start.value());self.play_source();self.statusBar().showMessage(ui_text('循环对听原曲中的这一小段；停止按钮可结束。'))
    def load_project(self,p):
        if self.vital_page.busy or self.full_page.busy:self.warn(ui_text('请等待或取消音色/全曲制作，再切换工程。'));return False
        try:self.vital_page.save_options()
        except OSError as e:self.warn(ui_text('音色设置保存失败，当前工程未改变：')+str(e));return False
        if super().load_project(p) is False:return False
        self.preview_request+=1;self.repair_request+=1;self.repair_player.stop();self.vital_page.stop_audio();self.full_page.stop_audio()
        self.full_page.project=None;self.full_page.mix_path=None;self.full_page.set_available()
        self.redo=[];self.muted=set();self.solo=set();self.preview_cache={};self.last_track=None;self.pending_seek=None;self.player.setSource(QUrl())
        lead=next((t['id'] for t in self.project['tracks'] if t['role']=='lead'),None)
        self.piano.scale_x=90;self.refresh_tracks(lead)
        self.summary.setText(f'{self.project['title']}  ·  {self.project['model']}{ui_text('  ·  未量化 / 原始秒时间  ·  候选需采用后才导出')}')
        self.vital_next.setEnabled(True);self.export_button.setEnabled(True);self.title_label.setText(self.project['title']);self.update_shortcuts()
        return True

    def upgrade_project(self):
        if self.project and self.project.get('analysis_mode')=='complex':self.warn(ui_text('增强工程已按分离来源组织；请重新以复杂模式分析，不使用旧版整曲重分轨。'));return
        if not self.project or self.worker or (self.upgrade_job and self.upgrade_job.isRunning()):return
        if not (self.folder/'raw_notes.json').exists():self.warn(ui_text('此工程缺少 raw_notes.json；请重新转写原音频。'));return
        self.save_project(silent=True);self.upgrade_job=UpgradeJob(self.project,self.folder);self.upgrade_job.done.connect(self.upgrade_done);self.upgrade_job.error.connect(self.warn);self.upgrade_job.start();self.statusBar().showMessage(ui_text('正在从原始事件重建分轨，另存新目录；保留人工修改。'))
    def upgrade_done(self,path):
        result=core.read_json(path)
        if self.folder!=self.upgrade_job.folder or self.project['revision']!=result['revision']-1:
            self.statusBar().showMessage(ui_text('重建已另存，但当前工程已变化，未自动切换：')+path);return
        self.load_project(path)
    def save_project(self,checked=False,silent=False):
        if not silent:
            try:self.vital_page.save_options()
            except OSError as e:self.warn(ui_text('音色设置保存失败，编辑仍保留：')+str(e));return False
        return super().save_project(checked,silent)
    def closeEvent(self,event):
        if self.vital_page.busy or self.full_page.busy:self.warn(ui_text('请等待或取消音色/全曲制作，再关闭软件。'));event.ignore();return
        self.full_page.stop_audio()
        self.vital_page.stop_audio()
        try:self.vital_page.save_options()
        except OSError as e:self.warn(ui_text('音色设置保存失败，已阻止退出：')+str(e));event.ignore();return
        if self.repair_job and self.repair_job.isRunning():self.warn(ui_text('请等待试听生成完成。'));event.ignore();return
        self.repair_player.stop()
        if self.upgrade_job and self.upgrade_job.isRunning():self.warn(ui_text('请等待历史工程重建完成。'));event.ignore();return
        super().closeEvent(event)
    def roll_selected(self,ids):
        if self.syncing:return
        self.syncing=True;sm=self.notes.selectionModel();sm.clearSelection()
        for i,n in enumerate(getattr(self,'shown',[])):
            if n['id'] in ids:sm.select(self.notes.model().index(i,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
        self.syncing=False;self.table_selected()
    def table_selected(self):
        if self.syncing:return
        self.syncing=True;ns=self.selected_notes();ids={n['id'] for n in ns};self.piano.filling=True
        for nid,it in self.piano.items_by_id.items():it.setSelected(nid in ids)
        self.piano.filling=False;self.adopt_button.setEnabled(any(n.get('candidate') for n in ns));self.selection_label.setText(f'{ui_text('已选 ')}{len(ns)}{ui_text(' 个音符')}'+(ui_text(' · 单音可编辑属性') if len(ns)!=1 else ''))
        if ns:
            n=ns[0];self.in_pitch.setValue(n['pitch']);self.in_start.setValue(n['start']);self.in_end.setValue(n['end']);self.in_velocity.setValue(n.get('velocity',80));self.in_lock.setChecked(n.get('locked',False));self.target.setCurrentIndex(max(0,self.target.findData(n['track'])))
        self.syncing=False
    def checkpoint(self):
        super().checkpoint();self.redo=[]
    def invalidate_preview(self):
        self.preview_request+=1;pos=self.seek.value();self.player.stop();self.pending_seek=None;self.player.setSource(QUrl());self.seek_to(pos/1000)
    def changed(self):
        self.repair_player.stop()
        super().changed()
        if self.listen.currentIndex()!=0:self.invalidate_preview()
    def undo_edit(self):
        if self.undo:
            self.redo.append(copy.deepcopy(self.project));super().undo_edit()
            if self.listen.currentIndex()!=0:self.invalidate_preview()
    def redo_edit(self):
        if self.redo:
            revision=self.project['revision']+1;self.undo.append(copy.deepcopy(self.project));self.project=self.redo.pop();self.project['revision']=revision;self.refresh_tracks();self.save_project(silent=True)
            if self.listen.currentIndex()!=0:self.invalidate_preview()
    def select_all(self):self.notes.selectAll()
    def restore_selection(self,ids):self.roll_selected(ids)
    def move_ids(self,ids,dt,dp):
        if not self.project:return
        ns=[n for t in self.project['tracks'] for n in t['notes'] if n['id'] in ids and not n.get('locked') and not n.get('deleted')]
        if not ns:return
        if any(not 0<=n['pitch']+dp<=127 or n['start']+dt<0 or n['end']+dt>self.project['duration'] for n in ns):self.statusBar().showMessage(ui_text('移动超出音高或歌曲范围，未修改。'));return
        self.checkpoint()
        for n in ns:n.update(start=round(n['start']+dt,6),end=round(n['end']+dt,6),pitch=n['pitch']+dp,origin='user')
        self.changed();self.restore_selection(ids)
    def nudge(self,dt,dp):self.move_ids([n['id'] for n in self.selected_notes()],dt,dp)
    def apply_inspector(self):
        ns=self.selected_notes()
        if len(ns)!=1:self.statusBar().showMessage(ui_text('属性编辑请选择一个音符；整句换轨使用下面的移动按钮。'));return
        n=ns[0]
        if self.in_end.value()<=self.in_start.value() or self.in_end.value()>self.project['duration']:self.warn(ui_text('结束时间必须晚于开始，并位于歌曲范围内。'));return
        if n.get('locked') and self.in_lock.isChecked():self.statusBar().showMessage(ui_text('先取消锁定再应用修改。'));return
        target=next(t for t in self.project['tracks'] if t['id']==self.target.currentData());self.checkpoint();old=next(t for t in self.project['tracks'] if t['id']==n['track'])
        if old is not target:old['notes'].remove(n);target['notes'].append(n)
        n.update(pitch=self.in_pitch.value(),start=self.in_start.value(),end=self.in_end.value(),velocity=self.in_velocity.value(),velocity_origin='user',locked=self.in_lock.isChecked(),track=target['id'],program=target['program'],drum=target['role']=='drums',origin='user',candidate=False)
        self.changed();self.refresh_tracks(target['id']);self.restore_selection([n['id']])
    def move_to_target(self):
        ns=[n for n in self.selected_notes() if not n.get('locked')]
        if not ns:return
        target=next(t for t in self.project['tracks'] if t['id']==self.target.currentData());self.checkpoint()
        for n in ns:
            old=next(t for t in self.project['tracks'] if t['id']==n['track'])
            if old is not target:old['notes'].remove(n);target['notes'].append(n)
            n.update(track=target['id'],program=target['program'],drum=target['role']=='drums',origin='user',candidate=False)
        self.changed();self.refresh_tracks(target['id']);self.restore_selection([n['id'] for n in ns])
    def copy_notes(self):self.clipboard=copy.deepcopy(self.selected_notes());self.statusBar().showMessage(f'{ui_text('已复制 ')}{len(self.clipboard)}{ui_text(' 个音符。')}')
    def paste_notes(self,at=None):
        if not self.clipboard or not self.project:return
        at=self.seek.value()/1000 if at is None else at;first=min(n['start'] for n in self.clipboard);t=self.track();new=[]
        for old in self.clipboard:
            n=copy.deepcopy(old);n.update(id='paste_'+uuid.uuid4().hex[:10],track=t['id'],program=t['program'],drum=t['role']=='drums',start=old['start']-first+at,end=old['end']-first+at,origin='user',locked=False,candidate=False,deleted=False)
            if n['end']>self.project['duration']:self.statusBar().showMessage(ui_text('粘贴超出歌曲范围，未修改。'));return
            new.append(n)
        self.checkpoint();t['notes'].extend(new);self.changed();self.restore_selection([n['id'] for n in new])
    def duplicate_notes(self):
        ns=self.selected_notes()
        if ns:self.copy_notes();self.paste_notes(max(n['end'] for n in ns))
    def range_from_selection(self):
        ns=self.selected_notes()
        if ns:self.range_start.setValue(min(n['start'] for n in ns));self.range_end.setValue(max(n['end'] for n in ns));self.seek_to(self.range_start.value())
    def anchor_reassign(self):
        ns=self.selected_notes();target=self.track()
        if not ns or target['role']=='drums':self.statusBar().showMessage(ui_text('先选择当前旋律轨中正确的几个音符。'));return
        a,z=self.range_start.value(),self.range_end.value()
        if z<=a:self.warn(ui_text('选区终点必须晚于起点。'));return
        anchor=float(np.median([n['pitch'] for n in ns]));span=max(7,min(18,max(abs(n['pitch']-anchor) for n in ns)+3))
        pool=[n for t in self.project['tracks'] if t['program']==target['program'] and t['role']!='drums' for n in t['notes'] if a<=n['start']<z and not n.get('deleted') and not n.get('locked') and n.get('origin')!='user']
        chosen=set(melodic_path(pool,anchor,span));self.checkpoint()
        residual=next((t for t in self.project['tracks'] if t['role']=='accompaniment' and t['program']==target['program'] and t is not target),None)
        if residual is None:
            residual={'id':'rest_'+uuid.uuid4().hex[:8],'program':target['program'],'role':'accompaniment','name':ui_text('伴奏 · 锚定后剩余'),'instrument':target.get('instrument',ui_text('来源')),'notes':[],'role_origin':'user'};self.project['tracks'].append(residual)
        for n in pool:
            old=next(t for t in self.project['tracks'] if t['id']==n['track']);dest=target if n['id'] in chosen else residual if old is target else old
            if old is not dest:old['notes'].remove(n);dest['notes'].append(n);n['track']=dest['id']
        target['register_profile']={'center':anchor,'span':span,'origin':'user_anchor'};self.changed();self.refresh_tracks(target['id']);self.anchor_label.setText(f'{ui_text('锚定中心 ')}{anchor:.0f}{ui_text(' / 范围 ±')}{span:.0f}{ui_text('；已保留人工修改')}');self.statusBar().showMessage(ui_text('已按选中乐句重分配当前范围；可撤销。仍需试听确认。'))
    def fit_roll(self):
        if self.project:self.piano.scale_x=max(2,(self.piano.viewport().width()-70)/max(1,self.project['duration']));self.piano.fit_vertical();self.refresh_notes();self.piano.horizontalScrollBar().setValue(0);self.piano.verticalScrollBar().setValue(0)
    def toggle_track_flag(self,kind):
        t=self.track()
        if not t:return
        collection=self.muted if kind=='mute' else self.solo
        if t['id'] in collection:collection.remove(t['id'])
        else:collection.add(t['id'])
        self.mute_button.setChecked(t['id'] in self.muted);self.solo_button.setChecked(t['id'] in self.solo)
        self.refresh_track_labels()
        if self.listen.currentIndex()==2:self.play_all()
    def play_all(self):
        if self.project:self.make_preview([t['id'] for t in self.project['tracks'] if t['id'] not in self.muted and (not self.solo or t['id'] in self.solo)])
    def make_preview(self,ids):
        if not self.project or (self.preview_job and self.preview_job.isRunning()):return
        self.preview_request+=1;request=self.preview_request;revision=self.project['revision'];folder=self.folder;context=self.audition_context();key=(str(folder),revision,tuple(ids) if ids is not None else None)
        if key in self.preview_cache and Path(self.preview_cache[key]).exists():self.play_file(self.preview_cache[key]);return
        self.statusBar().showMessage(ui_text('正在生成 MIDI 试听，鼓组使用基础打击音源。'))
        path=self.folder/f'preview_{uuid.uuid4().hex[:8]}.wav';self.preview_job=legacy.PreviewJob(self.project,path,ids)
        def ready(p):
            if request!=self.preview_request or self.tabs.currentIndex()!=1 or self.folder!=folder or not self.project or self.project['revision']!=revision or self.audition_context()!=context:self.statusBar().showMessage(ui_text('试听生成期间工程或声部已变化，请重新播放以使用最新音符。'));return
            self.preview_cache[key]=p;self.play_file(p)
        self.preview_job.done.connect(ready);self.preview_job.error.connect(self.warn);self.preview_job.start()
    def audition_context(self):return (self.listen.currentIndex(),self.tracks.currentData(),tuple(sorted(self.muted)),tuple(sorted(self.solo)))
    def set_listen(self,index):
        if self.listen.currentIndex()==index:self.listen_changed(index)
        else:self.listen.setCurrentIndex(index)
    def listen_changed(self,index):
        self.preview_request+=1;self.player.stop()
        if self.project:(self.play_source,self.play_track,self.play_all,self.play_stem)[index]()
    def play_stem(self):
        self.player.stop();t=self.track()
        if not t or not t.get('reference_audio'):
            self.player.setSource(QUrl());self.statusBar().showMessage(ui_text('此轨没有分离音频；请使用复杂 Bass / 鼓组模式重新分析。'));return
        path=self.folder/t['reference_audio']
        if not path.is_file():self.player.setSource(QUrl());self.warn(ui_text('来源分离音频缺失：')+str(path));return
        self.play_file(str(path));self.statusBar().showMessage(ui_text('试听估计来源 ')+t.get('source_stem','')+ui_text('；可能含多个音色及串音，不是单乐器原始分轨。'))
    def toggle_play(self):
        if not self.project:return
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        elif self.player.source().isEmpty():self.listen_changed(self.listen.currentIndex())
        else:self.player.play()
    def stop_play(self):self.preview_request+=1;self.pending_seek=None;self.repair_request+=1;self.repair_player.stop();self.player.stop();self.seek_to(self.range_start.value() if self.loop.isChecked() else 0)
    def on_position(self,pos):
        super().on_position(pos);duration=int(self.project['duration']) if self.project else 0;sec=pos//1000;self.clock.setText(f'{sec // 60:02}:{sec % 60:02}.{pos % 1000:03} / {duration // 60:02}:{duration % 60:02}')
    def play_file(self,p):
        self.repair_player.stop()
        self.pending_seek=self.seek.value();self.player.setSource(QUrl.fromLocalFile(p));self.player.play()
    def media_status(self,status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia) and self.pending_seek is not None:
            # Consume before setPosition: Qt can synchronously re-enter this slot.
            position=self.pending_seek;self.pending_seek=None
            self.player.setPosition(position)

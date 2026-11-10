"""Full-song playback page, explicit rendering and snapshot-bound transport."""
from i18n import tr as ui_text
import copy,re,time
from pathlib import Path
from PySide6.QtCore import Qt,QThread,Signal,QUrl,QTimer
from PySide6.QtGui import QDesktopServices,QShortcut,QKeySequence,QColor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QProgressBar,QSlider,QApplication,QLineEdit,QAbstractSpinBox
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
import core,full_song

class SongWorker(QThread):
    progress=Signal(str,float);done=Signal(str,dict);failed=Signal(str)
    def __init__(self,project,folder,output,options):
        super().__init__();self.project=copy.deepcopy(project);self.folder=folder;self.output=output;self.options=copy.deepcopy(options)
    def run(self):
        try:
            path,m=full_song.render_song(self.project,self.folder,self.output,self.options,progress=self.progress.emit,cancel=self.isInterruptionRequested)
            self.done.emit(str(path),m)
        except core.Cancelled:self.failed.emit(ui_text('已取消全曲渲染。已完成分轨保留在缓存中；再次制作可复用。'))
        except Exception as e:self.failed.emit(ui_text('全曲未完成：')+str(e)+ui_text('。没有把漏轨结果标记为成功。'))

def button(text,callback,primary=False):
    b=QPushButton(text);b.clicked.connect(callback)
    if primary:b.setObjectName('primary')
    return b

def clock(ms):
    s=max(0,int(ms))//1000;return f'{s // 60:02d}:{s % 60:02d}'

class ExportWorker(QThread):
    progress=Signal(str,float);done=Signal(str,object);failed=Signal(str)
    def __init__(self,project,folder,output,options):
        super().__init__();self.args=(copy.deepcopy(project),folder,output,copy.deepcopy(options));self.cancelled=False
    def run(self):
        try:
            from fl_export import build
            path,report=build(*self.args,progress=self.progress.emit,cancel=self.isInterruptionRequested)
            self.done.emit(str(path),report)
        except core.Cancelled:self.failed.emit(ui_text('已取消 FL 导出；未发布完整工程。'))
        except Exception as e:self.failed.emit(ui_text('FL 导出失败：')+str(e))

class FullSongPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.project=None;self.folder=None;self.options={};self.output=None
        self.worker=None;self.busy=False;self.manifest=None;self.mix_path=None;self.run_path=None;self.current_row=-1
        self.pending_seek=None;self.mode='mix';self.started=0;self.fl_path=None;self.job_kind='audio'
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.audio.setVolume(.6);self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self.media_status);self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed);self.player.playbackStateChanged.connect(self.play_state)
        self.player.errorOccurred.connect(lambda _,s:self.status.setText(ui_text('播放失败：')+s))
        root=QVBoxLayout(self);root.setContentsMargins(16,14,16,12);root.setSpacing(12)
        line=QHBoxLayout();self.title=QLabel(ui_text('Vital 全曲试听'));self.title.setStyleSheet('font-size:20px;font-weight:600;');line.addWidget(self.title);line.addStretch();self.snapshot=QLabel(ui_text('尚未选择工程'));line.addWidget(self.snapshot);root.addLayout(line)
        self.intro=QLabel(ui_text('所有有效声部按原始时间合奏；旋律使用当前 Vital 设置。增强工程鼓组优先使用估计分离轨采样，其余使用基础合成鼓。'));self.intro.setWordWrap(True);root.addWidget(self.intro)
        self.output_label=QLabel(ui_text('本次输出：打开工程后显示'));self.output_label.setWordWrap(True);self.output_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);root.addWidget(self.output_label)
        actions=QHBoxLayout();self.back=button(ui_text('返回音色设置'),lambda:host.tabs.setCurrentIndex(2));actions.addWidget(self.back)
        self.render=button(ui_text('生成并试听全曲'),self.start,True);actions.addWidget(self.render);self.cancel=button(ui_text('取消渲染'),self.cancel_job);actions.addWidget(self.cancel);actions.addStretch();self.elapsed=QLabel(ui_text('首次需要渲染；未修改的声部自动复用缓存'));actions.addWidget(self.elapsed);root.addLayout(actions)
        export=QHBoxLayout();self.export_fl=button(ui_text('导出 FL 工程'),self.start_export);export.addWidget(self.export_fl)
        self.launch_fl=button(ui_text('在 FL Studio 打开'),self.open_fl);export.addWidget(self.launch_fl)
        self.fl_folder=button(ui_text('打开工程目录'),lambda:self.open_fl(True));export.addWidget(self.fl_folder)
        self.fl_status=QLabel(ui_text('Vital + 可编辑音符 + 编曲已铺好；鼓使用基础采样。'));self.fl_status.setWordWrap(True);export.addWidget(self.fl_status,1);root.addLayout(export)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels([ui_text('声部 / 来源'),ui_text('有效音符'),ui_text('发声音源'),ui_text('WAV 渲染状态')]);self.table.verticalHeader().hide();self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for col in (0,3):self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.Stretch)
        for col in (1,2):self.table.horizontalHeader().setSectionResizeMode(col,QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table,1)
        self.progress=QProgressBar();self.progress.setRange(0,100);root.addWidget(self.progress)
        self.status=QLabel(ui_text('在音色页点击“试听全曲”，或打开工程后在此生成。不会自动修改 MIDI。'));self.status.setWordWrap(True);self.status.setMinimumHeight(36);root.addWidget(self.status)
        self.file_label=QLabel(ui_text('完整 WAV：生成完成后可在软件外播放'));self.file_label.setWordWrap(True);self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);root.addWidget(self.file_label)
        transport=QHBoxLayout();self.play=button(ui_text('播放'),self.toggle_play);self.stop=button(ui_text('停止'),self.stop_audio);self.compare=button(ui_text('原曲对照'),self.compare_source)
        for b in (self.play,self.stop,self.compare):transport.addWidget(b)
        self.seek=QSlider(Qt.Orientation.Horizontal);self.seek.setRange(0,0);self.seek.setSingleStep(1000);self.seek.setPageStep(5000);self.seek.sliderReleased.connect(self.seek_released);self.seek.actionTriggered.connect(lambda _:QTimer.singleShot(0,self.seek_released));transport.addWidget(self.seek,1)
        self.clock=QLabel('00:00 / 00:00');self.clock.setMinimumWidth(100);transport.addWidget(self.clock);transport.addWidget(QLabel(ui_text('音量')))
        self.volume=QSlider(Qt.Orientation.Horizontal);self.volume.setRange(0,100);self.volume.setValue(60);self.volume.setFixedWidth(90);self.volume.valueChanged.connect(lambda v:self.audio.setVolume(v/100));transport.addWidget(self.volume)
        self.open=button(ui_text('打开 WAV 目录'),self.open_output);transport.addWidget(self.open);root.addLayout(transport)
        hint=QLabel(ui_text('Space 播放/暂停 · Shift+Space 停止 · 包含全部声部，与音色页“导出预设”的勾选无关。'));hint.setObjectName('muted');hint.setWordWrap(True);root.addWidget(hint)
        self.shortcuts=[]
        for key,cb in [('Space',self.toggle_play),('Shift+Space',self.stop_audio)]:
            s=QShortcut(QKeySequence(key),self);s.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);s.activated.connect(cb);self.shortcuts.append(s)
        self.timer=QTimer(self);self.timer.timeout.connect(self.tick);self.set_available()

    def set_available(self):
        self.render.setEnabled(self.project is not None and not self.busy);self.cancel.setEnabled(self.busy);self.back.setEnabled(not self.busy)
        self.export_fl.setEnabled(self.project is not None and not self.busy)
        self.launch_fl.setEnabled(self.fl_path is not None and not self.busy);self.fl_folder.setEnabled(self.fl_path is not None and not self.busy)
        for w in (self.play,self.compare,self.seek,self.open):w.setEnabled(self.mix_path is not None and not self.busy)
    def load_project(self,project,folder,output,options):
        if self.busy:return
        self.stop_audio();self.project=copy.deepcopy(project);self.folder=Path(folder);self.output=Path(output);self.options=copy.deepcopy(options);self.output_label.setText(ui_text('本次输出：')+str(self.output))
        self.manifest=None;self.mix_path=None;self.run_path=None;self.mode='mix';self.player.setSource(QUrl())
        self.fl_path=None;self.fl_status.setText(ui_text('导出当前全部声部；Vital 内嵌，鼓为基础采样。固定网格保留原始时序。'))
        self.title.setText(project.get('title',ui_text('歌曲'))[:35]+ui_text(' · 全曲试听'));self.snapshot.setText(f'{ui_text('工程修订 ')}{project['revision']}')
        self.active=[t for t in project['tracks'] if core.active_notes(t)];self.table.setRowCount(len(self.active));self.current_row=-1;self.snapshot.setText(f'{len(self.active)}{ui_text(' 轨 · 工程修订 ')}{project['revision']}')
        for i,t in enumerate(self.active):
            for j,value in enumerate((t['name'],str(len(core.active_notes(t))),(ui_text('分离轨鼓样') if t.get('drum_samples') else ui_text('基础合成鼓')) if full_song.is_drum(t) else 'Vital',ui_text('等待生成'))):
                item=QTableWidgetItem(value);item.setToolTip(value);self.table.setItem(i,j,item)
        self.status.setText(f'{ui_text('将试听全部 ')}{len(self.active)}{ui_text(' 条有效声部，保留原始时序；不自动量化。')}')
        self.file_label.setText(ui_text('完整 WAV：生成后可直接在软件外播放'));self.progress.setValue(0);self.seek.setValue(0);self.clock.setText('00:00 / 00:00');self.set_available()
    def start(self):
        if self.busy or self.project is None:return
        if self.host.worker or self.host.vital_page.busy:self.status.setText(ui_text('请先完成或取消正在运行的提取/预设任务。'));return
        # Refresh settings and notes on every deliberate render, rather than reusing stale snapshots.
        if self.host.folder==self.folder:
            self.output=Path(self.host.vital_page.output.text().strip() or str(self.folder/'Vital音色'))
            self.load_project(self.host.project,self.folder,self.output,self.host.vital_page.options)
        self.job_kind='audio';self.cancel.setText(ui_text('取消渲染'));self.stop_audio();self.mix_path=None;self.manifest=None;self.busy=True;self.started=time.monotonic();self.timer.start(500);self.set_available()
        for index in (0,1,2):self.host.tabs.setTabEnabled(index,False)
        self.worker=SongWorker(self.project,self.folder,self.output,self.options)
        self.worker.progress.connect(self.on_progress);self.worker.done.connect(self.completed);self.worker.failed.connect(self.failed);self.worker.finished.connect(self.finished);self.worker.start()
    def start_export(self):
        if self.busy or self.project is None:return
        if self.host.worker or self.host.vital_page.busy:self.fl_status.setText(ui_text('请先完成当前提取/音色任务。'));return
        if self.host.folder==self.folder:
            self.output=Path(self.host.vital_page.output.text().strip() or str(self.folder/'Vital音色'));self.output_label.setText(ui_text('本次输出：')+str(self.output))
            self.project=copy.deepcopy(self.host.project);self.options=copy.deepcopy(self.host.vital_page.options)
        try:self.host.vital_page.save_options()
        except OSError as e:self.fl_status.setText(ui_text('无法保存音色设置：')+str(e));return
        self.fl_path=None;self.job_kind='fl';self.cancel.setText(ui_text('取消导出'));self.busy=True
        self.status.setText(ui_text('正在导出当前 MIDI 与 Vital 设置；完成后可直接在 FL Studio 打开。'));self.progress.setValue(0)
        self.stop_audio();self.started=time.monotonic();self.timer.start(500);self.set_available()
        for index in (0,1,2):self.host.tabs.setTabEnabled(index,False)
        self.worker=ExportWorker(self.project,self.folder,self.output,self.options)
        self.worker.progress.connect(lambda s,p:(self.fl_status.setText(s),self.progress.setValue(int(p*100))))
        self.worker.done.connect(self.export_complete);self.worker.failed.connect(self.export_failed);self.worker.finished.connect(self.finished);self.worker.start()
    def export_failed(self,message):
        self.fl_path=None;self.fl_status.setText(message);self.status.setText(message+ui_text('；可重新点击“导出 FL 工程”。'));self.progress.setValue(0)
    def export_complete(self,path,report):
        self.fl_path=Path(report['flp']);self.progress.setValue(100)
        self.fl_status.setText(f'{ui_text('已导出 ')}{report['channel_count']}{ui_text(' 个音源 / ')}{report['note_count']}{ui_text(' 个音符。点击“在 FL Studio 打开”。')}')
        self.fl_status.setToolTip(str(self.fl_path));self.status.setText(ui_text('FL 工程已生成并通过结构校验；需要本机 Vital VST3。请保留整个导出目录。'))
    def open_fl(self,folder=False):
        if self.fl_path and not self.busy:
            target=self.fl_path.parent if folder else self.fl_path
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):self.fl_status.setText(ui_text('无法自动打开，请在工程目录中手动打开 FLP。'))
    def tick(self):self.elapsed.setText(f'{ui_text('已用时 ')}{int(time.monotonic() - self.started)}{ui_text(' 秒')}'+(ui_text(' · 正在生成可编辑 FL 工程') if self.job_kind=='fl' else ui_text(' · 可取消，保留已完成缓存')))
    def on_progress(self,text,p):
        self.status.setText(text);self.progress.setValue(int(p*100));m=re.match(ui_text('(?:准备|渲染) (\\d+)/'),text)
        if m:
            row=int(m[1])-1
            if row!=self.current_row and self.current_row>=0:self.table.item(self.current_row,3).setText(ui_text('已处理'))
            self.current_row=row;self.table.item(row,3).setText(ui_text('检查缓存') if text.startswith(ui_text('准备')) else text.rsplit(' · ',1)[-1]);self.table.selectRow(row)
    def completed(self,path,manifest):
        self.run_path=Path(path);self.manifest=manifest;self.mix_path=Path(manifest['mix']);self.progress.setValue(100)
        for i,row in enumerate(manifest['tracks']):self.table.item(i,3).setText(ui_text('完成 · 缓存复用') if row['reused'] else ui_text('完成 · 新渲染'))
        self.status.setText(f'{ui_text('全曲完成：')}{len(manifest['tracks'])}/{manifest['expected_tracks']}{ui_text(' 轨；新渲染 ')}{manifest['rendered_tracks']}{ui_text(' 轨，复用 ')}{manifest['reused_tracks']}{ui_text(' 轨。包含 ')}{manifest['tail_seconds']:.0f}{ui_text(' 秒尾音。')}')
        self.file_label.setText(ui_text('完整 WAV：')+str(self.mix_path));self.mode='mix';self.compare.setText(ui_text('原曲对照'))
        # Auto-play only while this page remains the user's active surface.
        if self.host.tabs.currentWidget() is self:self.player.setSource(QUrl.fromLocalFile(str(self.mix_path)));self.player.play()
    def failed(self,message):
        self.mix_path=None;self.manifest=None;self.status.setText(message)
        if self.current_row>=0:self.table.item(self.current_row,3).setText(ui_text('已取消') if ('已取消' in message or ui_text('已取消') in message) else ui_text('失败；未完成全曲'))
    def finished(self):
        self.busy=False;self.timer.stop();self.elapsed.setText(f'{ui_text('本次用时 ')}{int(time.monotonic() - self.started)}{ui_text(' 秒')}')
        for index in (0,1,2):self.host.tabs.setTabEnabled(index,True)
        self.set_available()
    def cancel_job(self):
        if self.worker and self.worker.isRunning():self.worker.requestInterruption();self.cancel.setEnabled(False);self.status.setText(ui_text('正在取消 FL 导出…') if self.job_kind=='fl' else ui_text('正在取消，已完成分轨会保留…'))
    def toggle_play(self):
        if not self.mix_path or self.busy:return
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        else:
            if self.player.source().isEmpty():self.player.setSource(QUrl.fromLocalFile(str(self.mix_path)))
            self.player.play()
    def play_state(self,state):self.play.setText(ui_text('暂停') if state==QMediaPlayer.PlaybackState.PlayingState else ui_text('播放'))
    def stop_audio(self):self.pending_seek=None;self.player.stop()
    def seek_released(self):self.player.setPosition(self.seek.value())
    def position_changed(self,ms):
        if not self.seek.isSliderDown():self.seek.setValue(ms)
        self.clock.setText(clock(ms)+' / '+clock(self.player.duration()))
    def duration_changed(self,ms):self.seek.setMaximum(ms);self.position_changed(self.player.position())
    def compare_source(self):
        if not self.mix_path or self.busy:return
        source=self.folder/self.project['audio'] if self.mode=='mix' else self.mix_path
        if not source.is_file():self.status.setText(ui_text('找不到对照音频：')+str(source));return
        position=self.player.position();self.player.stop();self.pending_seek=position;self.mode='source' if self.mode=='mix' else 'mix'
        self.compare.setText(ui_text('返回全曲试听') if self.mode=='source' else ui_text('原曲对照'));self.player.setSource(QUrl.fromLocalFile(str(source)));self.player.play()
        self.status.setText(ui_text('当前音源：原曲混音；对照未做感知响度匹配。') if self.mode=='source' else ui_text('当前音源：Vital 全曲混音。'))
    def media_status(self,status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia) and self.pending_seek is not None:
            pos=self.pending_seek;self.pending_seek=None;self.player.setPosition(min(pos,max(0,self.player.duration()-1)))
    def open_output(self):
        if self.mix_path:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.mix_path.parent)))

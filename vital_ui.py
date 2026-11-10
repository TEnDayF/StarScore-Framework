"""Explicit second-stage workflow; importing/transcribing never generates presets."""
from i18n import tr as ui_text
import copy
from pathlib import Path
from PySide6.QtCore import Qt,QThread,Signal,QUrl,QPointF
from PySide6.QtGui import QColor,QPainter,QPen,QPainterPath,QDesktopServices,QShortcut,QKeySequence
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QListWidget,QListWidgetItem,
    QSplitter,QFormLayout,QLineEdit,QComboBox,QSlider,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QAbstractItemView,QFileDialog,QProgressBar,QScrollArea)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
import core,vital_presets as vp

from ui_theme import COLORS

def button(text,callback,primary=False):
    w=QPushButton(text);w.clicked.connect(callback)
    if primary:w.setObjectName('primary')
    return w

class Envelope(QWidget):
    def __init__(self):
        super().__init__();self.options=None;self.setMinimumHeight(120)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor('#142039'))
        if not self.options:return
        a,d,s,r=vp.envelope(self.options);width=self.width()-32;top=28;bottom=self.height()-45
        total=a+d+.35+r;xs=[0,a,a+d,a+d+.35,total];ys=[0,1,s,s,0];path=QPainterPath()
        for i,(x,y) in enumerate(zip(xs,ys)):
            point=QPointF(16+x/total*width,bottom-y*(bottom-top))
            if i==0:path.moveTo(point)
            else:path.lineTo(point)
        p.setPen(QPen(QColor('#AA98FF'),2));p.drawPath(path);p.setPen(QColor('#ADBBD2'))
        p.drawText(12,17,ui_text('音量包络 · 随设置更新'))
        p.drawText(12,self.height()-24,f'A {a * 1000:.0f} ms    D {d * 1000:.0f} ms')
        p.drawText(12,self.height()-8,f'S {s:.2f}       R {r * 1000:.0f} ms')

class BundleWorker(QThread):
    progress=Signal(str,float);done=Signal(str,dict);failed=Signal(str)
    def __init__(self,project,folder,output,options,render):
        super().__init__();self.project=copy.deepcopy(project);self.folder=folder;self.output=output
        self.options=copy.deepcopy(options);self.render=render
    def run(self):
        try:
            path,manifest=vp.export_bundle(self.project,self.folder,self.output,self.options,self.render,
                progress=self.progress.emit,cancel=self.isInterruptionRequested);self.done.emit(str(path),manifest)
        except core.Cancelled:self.failed.emit(ui_text('已取消。已完成的文件保留在输出目录，轨道映射标记为 cancelled。'))
        except Exception as e:self.failed.emit(ui_text('制作失败：')+str(e)+ui_text('。已有文件保留，可修正后重新生成。'))

class VitalPage(QWidget):
    def __init__(self,host):
        super().__init__();self.host=host;self.project=None;self.folder=None;self.options={};self.busy=False
        self.loading=False;self.current_id=None;self.worker=None;self.result=None;self.result_path=None
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.audio.setVolume(.65);self.player.setAudioOutput(self.audio)
        self.pending_seek=None;self.preview_end=None
        self.player.mediaStatusChanged.connect(self.media_status);self.player.positionChanged.connect(self.preview_position)
        self.player.errorOccurred.connect(lambda _,s:self.status.setText(ui_text('试听失败：')+s))
        root=QVBoxLayout(self);root.setContentsMargins(12,12,12,10);root.setSpacing(8)
        heading=QHBoxLayout();self.title=QLabel(ui_text('制作 Vital 音色'));self.title.setStyleSheet('font-size:19px;font-weight:600;');heading.addWidget(self.title);heading.addStretch()
        self.badge=QLabel(ui_text('先提取歌曲或打开已有工程'));self.badge.setObjectName('muted');heading.addWidget(self.badge);root.addLayout(heading)
        self.intro=QLabel(ui_text('每份 Vital 预设配一份同名 MIDI。音色是可编辑候选，并非原始音色恢复。'));self.intro.setWordWrap(True);root.addWidget(self.intro)
        body=QSplitter(Qt.Orientation.Horizontal);root.addWidget(body,1)
        left=QWidget();ll=QVBoxLayout(left);ll.setContentsMargins(0,0,10,0);ll.setSpacing(6)
        self.count=QLabel(ui_text('声部列表'));ll.addWidget(self.count);self.tracks=QListWidget();self.tracks.setMinimumWidth(235);self.tracks.setMaximumWidth(390);ll.addWidget(self.tracks,1)
        row=QHBoxLayout();self.all=button(ui_text('全选可制作'),lambda:self.check_all(True));self.none=button(ui_text('取消全选'),lambda:self.check_all(False));row.addWidget(self.all);row.addWidget(self.none);ll.addLayout(row)
        hint=QLabel(ui_text('鼓组使用采样器，此步骤不生成鼓音色。'));hint.setWordWrap(True);hint.setObjectName('muted');ll.addWidget(hint);body.addWidget(left)
        right=QWidget();rl=QVBoxLayout(right);rl.setContentsMargins(8,0,0,0);rl.setSpacing(5)
        self.editor_title=QLabel(ui_text('选择左侧声部'));self.editor_title.setStyleSheet('font-size:16px;font-weight:600;');rl.addWidget(self.editor_title)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setMinimumHeight(185);form_widget=QWidget();editor_row=QHBoxLayout(form_widget);editor_row.setContentsMargins(0,0,0,0);fields=QWidget();editor_row.addWidget(fields,1);form=QFormLayout(fields);form.setContentsMargins(0,4,10,4);form.setVerticalSpacing(5)
        self.name=QLineEdit();self.name.setMaxLength(65);self.name.setPlaceholderText(ui_text('歌曲名_声部编号'));self.name.setToolTip(ui_text('文件名还会添加音色家族和固定轨道编号，避免重名'))
        form.addRow(ui_text('预设名称'),self.name);self.family=QComboBox()
        for key,label in vp.FAMILIES.items():self.family.addItem(label,key)
        form.addRow(ui_text('音色家族'),self.family);self.sliders={};self.values={}
        for key,label,maximum,unit in [('brightness',ui_text('亮度'),100,'%'),('attack',ui_text('起音时间'),250,' ms'),('space',ui_text('空间感'),100,'%')]:
            box=QWidget();r=QHBoxLayout(box);r.setContentsMargins(0,0,0,0);slider=QSlider(Qt.Orientation.Horizontal);slider.setRange(0,maximum);value=QLabel();value.setFixedWidth(58);value.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter);r.addWidget(slider,1);r.addWidget(value)
            self.sliders[key]=slider;self.values[key]=(value,unit);form.addRow(label,box);slider.valueChanged.connect(self.update_option)
        self.reference=QCheckBox(ui_text('参考来源音频谐波'));self.reference.setToolTip(ui_text('增强工程优先分析分离来源；标准工程分析混音。均为音色候选，窗口不足时使用模板。'));form.addRow(self.reference)
        self.library=QCheckBox(ui_text('从我的音色库匹配（实验）'));self.library.setToolTip(ui_text('用原工程筛选后的 Vital 预设匹配音频特征。使用候选自身的包络与调制；不代表恢复原始预设。未匹配时明确报错，可关闭后重试。'));form.addRow(self.library);self.library.toggled.connect(self.update_option)
        self.library_hint=QLabel(ui_text('使用候选原有参数；模板设置暂不生效。未匹配时请关闭此选项重试。'));self.library_hint.setWordWrap(True);self.library_hint.hide();form.addRow(self.library_hint)
        self.motion=QCheckBox(ui_text('估计 Bass 周期起伏（实验）'));self.motion.setToolTip(ui_text('仅分离来源中多个长音的周期一致时加入音量 LFO 候选；可能包含侧链，不恢复原始调制。'));form.addRow(self.motion);self.motion.toggled.connect(self.update_option)
        self.caution=QLabel(ui_text('音色是可编辑候选，不是原始预设恢复。先听 MIDI 是否正确，再比较音色。'));self.caution.setWordWrap(True);self.caution.setObjectName('muted');self.caution.hide()
        self.env=Envelope();self.env.setMinimumWidth(185);self.env.setMaximumWidth(250);editor_row.addWidget(self.env);scroll.setWidget(form_widget);rl.addWidget(scroll,3);self.editor=scroll
        rl.addWidget(QLabel(ui_text('输出对应关系 · 同一行的文件属于同一个声部')))
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels([ui_text('声部'),ui_text('预设 / 同名 MIDI'),ui_text('状态')]);self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.ResizeToContents);self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch);self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents);self.table.setMinimumHeight(75);self.table.verticalHeader().hide();rl.addWidget(self.table,2)
        row=QHBoxLayout();self.preview=button(ui_text('试听所选结果'),self.play_result);self.original=button(ui_text('原曲对照'),self.play_original);self.stop=button(ui_text('停止试听'),self.stop_audio)
        for w in (self.preview,self.original,self.stop):row.addWidget(w)
        row.addStretch();self.full=button(ui_text('试听全曲（含鼓组）'),lambda:host.open_full_song(),True);row.addWidget(self.full);rl.addLayout(row);body.addWidget(right);body.setSizes([290,900]);body.setCollapsible(0,False);body.setCollapsible(1,False)
        line=QHBoxLayout();line.addWidget(QLabel(ui_text('输出位置')));self.output=QLineEdit();self.output.setPlaceholderText(ui_text('打开工程后默认保存到工程内的 Vital 音色目录'));line.addWidget(self.output,1);self.choose=button(ui_text('选择目录'),self.choose_output);line.addWidget(self.choose);self.open=button(ui_text('打开输出目录'),self.open_output);line.addWidget(self.open);root.addLayout(line)
        footer=QHBoxLayout();self.back=button(ui_text('返回 MIDI 检查'),lambda:host.tabs.setCurrentIndex(1));footer.addWidget(self.back)
        self.render=QCheckBox(ui_text('生成 Vital 实际试听'));self.render.setChecked(vp.PLUGIN.is_file());self.render.setToolTip(ui_text('在独立进程加载本机 Vital；每轨生成开头有效乐句的短试听，失败不影响预设保存'));footer.addWidget(self.render);footer.addStretch()
        self.cancel=button(ui_text('取消制作'),self.cancel_job);self.cancel.setEnabled(False);footer.addWidget(self.cancel);self.generate=button(ui_text('生成所选预设'),self.start,True);self.generate.setToolTip(ui_text('Ctrl+Enter · 每次生成到新目录，不覆盖已有文件'));footer.addWidget(self.generate);root.addLayout(footer)
        self.progress=QProgressBar();self.progress.setMaximum(100);self.progress.setValue(0);self.progress.setMaximumHeight(16);root.addWidget(self.progress)
        self.status=QLabel(ui_text('先完成提取，再点击“下一步：制作 Vital 音色”。'));self.status.setWordWrap(True);root.addWidget(self.status)
        self.tracks.currentItemChanged.connect(self.select_track);self.tracks.itemChanged.connect(self.check_changed)
        self.name.textEdited.connect(self.update_option);self.family.currentIndexChanged.connect(self.family_changed);self.reference.toggled.connect(self.update_option)
        self.table.itemSelectionChanged.connect(self.update_preview_buttons)
        self.shortcuts=[]
        for key,callback in [('Ctrl+Return',self.start),('Ctrl+Enter',self.start),('Alt+Left',lambda:host.tabs.setCurrentIndex(1))]:
            sc=QShortcut(QKeySequence(key),self);sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);sc.activated.connect(callback);self.shortcuts.append(sc)
        self.set_available(False)

    def set_available(self,available):
        for w in (self.tracks,self.editor,self.output,self.choose,self.all,self.none,self.render):w.setEnabled(available and not self.busy)
        self.generate.setEnabled(available and not self.busy and any(o.get('selected') for o in self.options.values()))
        self.cancel.setEnabled(self.busy);self.full.setEnabled(available and not self.busy);self.update_preview_buttons()

    def load_project(self,project,folder):
        if self.busy:return
        same=self.folder is not None and self.folder.resolve()==Path(folder).resolve()
        retained=self.result if same else None;retained_path=self.result_path if same else None;current=self.current_id if same else None
        live={'tracks':copy.deepcopy(self.options),'output':self.output.text()} if same else None
        self.stop_audio();self.loading=True;self.project=project;self.folder=Path(folder);self.current_id=None
        defaults=vp.default_options(project)
        try:saved=live if live is not None else core.read_json(self.folder/'vital_settings.json')
        except Exception:saved={}
        for t in project['tracks']:
            old=saved.get('tracks',{}).get(t['id'],{})
            if old.get('family') in vp.FAMILIES:
                defaults[t['id']].update({k:v for k,v in old.items() if k in defaults[t['id']]})
            if not vp.eligible(t):defaults[t['id']]['selected']=False
        self.options=defaults;self.result=retained;self.result_path=retained_path;self.tracks.clear()
        if not same:
            try:
                record=core.read_json(self.folder/'vital_result.json');self.result=record['manifest'];self.result_path=Path(record['path'])
            except (OSError,ValueError,KeyError,TypeError):pass
        self.title.setText(project.get('title',ui_text('工程'))[:30]+ui_text(' · Vital 音色'));self.title.setToolTip(project.get('title',ui_text('工程')));self.badge.setText(f'{ui_text('已提取 · ')}{len(project['tracks'])}{ui_text(' 轨')}')
        counts={}
        for t in project['tracks']:
            role=t['role'];counts[role]=counts.get(role,0)+1
            item=QListWidgetItem(f'{ui_text(core.ROLES.get(role, role))} {counts[role]:02d}\n{len(core.active_notes(t))}{ui_text(' 音 · ')}'+(ui_text('可制作') if vp.eligible(t) else ui_text('鼓组使用采样器') if role=='drums' else ui_text('无有效音符')))
            item.setToolTip(t['name']+ui_text('\n来源：')+t['id'])
            item.setData(Qt.ItemDataRole.UserRole,t['id']);item.setForeground(QColor(COLORS.get(t['role'],'#ADBBD2')))
            if vp.eligible(t):item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Checked if defaults[t['id']]['selected'] else Qt.CheckState.Unchecked)
            else:item.setFlags(item.flags()&~Qt.ItemFlag.ItemIsEnabled)
            self.tracks.addItem(item)
        self.output.setText(saved.get('output',str(self.folder/'Vital音色')));self.loading=False
        for i,t in enumerate(project['tracks']):
            if vp.eligible(t) and (current is None or t['id']==current):self.tracks.setCurrentRow(i);break
        if self.tracks.currentRow()<0:
            for i,t in enumerate(project['tracks']):
                if vp.eligible(t):self.tracks.setCurrentRow(i);break
        self.status.setText(ui_text('保留上次生成结果；修改 MIDI 或参数后请重新生成。') if self.result else ui_text('勾选声部并调整音色，然后点击生成。Ctrl+Enter 生成；Alt+← 返回。'));self.progress.setValue(100 if self.result else 0);self.refresh_table();self.set_available(True)

    def save_options(self):
        if self.folder and not self.loading:core.save_json(self.folder/'vital_settings.json',{'tracks':self.options,'output':self.output.text()})
    def select_track(self,item,*args):
        if self.loading or not item:return
        tid=item.data(Qt.ItemDataRole.UserRole);opt=self.options[tid];self.current_id=tid;self.loading=True
        self.editor_title.setText(opt['name']+ui_text(' · 音色设置'));self.name.setText(opt['name']);self.family.setCurrentIndex(self.family.findData(opt['family']))
        for k,w in self.sliders.items():w.setValue(int(opt[k]))
        track=next(t for t in self.project['tracks'] if t['id']==tid)
        self.reference.setText(ui_text('参考 ')+track['source_stem']+ui_text(' 估计分离轨谐波') if track.get('reference_audio') else ui_text('参考原曲混音谐波'))
        eligible_motion=bool(track.get('reference_audio')) and track['role']=='bass';self.motion.setEnabled(eligible_motion)
        self.reference.setChecked(opt['reference']);self.library.setChecked(opt.get('reference_library',False));self.motion.setChecked(opt.get('motion',False) and eligible_motion);self.loading=False;self.update_option()
    def family_changed(self,*args):
        if not self.loading:
            self.sliders['attack'].setValue(85 if self.family.currentData()=='pad' else 2);self.update_option()
    def update_option(self,*args):
        if self.loading or self.current_id is None:return
        o=self.options[self.current_id];o.update(name=self.name.text().strip() or ui_text('未命名声部'),family=self.family.currentData(),reference=self.reference.isChecked(),motion=self.motion.isChecked())
        library=self.library.isChecked();o['reference_library']=library
        self.library_hint.setVisible(library);self.family.setEnabled(not library);self.reference.setEnabled(not library);self.env.setVisible(not library)
        track=next(t for t in self.project['tracks'] if t['id']==self.current_id)
        self.motion.setEnabled(not library and bool(track.get('reference_audio')) and track['role']=='bass')
        for slider in self.sliders.values():slider.setEnabled(not library)
        for k,w in self.sliders.items():o[k]=w.value();value,unit=self.values[k];value.setText(str(w.value())+unit)
        self.env.options=o;self.env.update();self.refresh_table()
        if self.result:self.status.setText(ui_text('设置已变更；下方保留上次生成结果。再次生成后才会应用到预设。'))
    def check_changed(self,item):
        if self.loading:return
        self.options[item.data(Qt.ItemDataRole.UserRole)]['selected']=item.checkState()==Qt.CheckState.Checked;self.refresh_table();self.set_available(True)
    def check_all(self,checked):
        for i in range(self.tracks.count()):
            item=self.tracks.item(i)
            if item.flags()&Qt.ItemFlag.ItemIsUserCheckable:item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    def refresh_table(self):
        if not self.project:return
        selected=[t for t in self.project['tracks'] if vp.eligible(t) and self.options[t['id']]['selected']]
        self.count.setText(f'{ui_text('声部列表 · 已选 ')}{len(selected)}{ui_text(' 轨')}')
        rows=self.result['tracks'] if self.result else [{'track_id':t['id'],'track_name':t['name'],'preset':vp.file_stem(t,self.options[t['id']])+'.vital','preview_status':ui_text('待生成')} for t in selected]
        selected_row=self.table.currentRow();self.table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,v in enumerate((r['track_name'],Path(r['preset']).stem,r['preview_status'])):
                item=QTableWidgetItem(v);item.setToolTip(v);item.setData(Qt.ItemDataRole.UserRole,r['track_id']);self.table.setItem(i,j,item)
        if rows:self.table.selectRow(max(0,min(selected_row,len(rows)-1)))
    def choose_output(self):
        p=QFileDialog.getExistingDirectory(self,ui_text('选择 Vital 音色输出目录'),self.output.text())
        if p:self.output.setText(p)
    def open_output(self):
        p=self.result_path or Path(self.output.text())
        if p and p.is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
        else:self.status.setText(ui_text('输出目录尚未创建；点击生成后可打开。'))
    def start(self):
        if self.busy or not self.project:return
        if not any(v['selected'] for v in self.options.values()):self.status.setText(ui_text('请至少勾选一个非鼓组声部。'));return
        if not self.output.text().strip():self.status.setText(ui_text('请先选择输出目录。'));return
        self.stop_audio();self.host.player.pause();self.host.repair_player.stop()
        try:self.save_options()
        except OSError as e:self.status.setText(ui_text('无法保存音色设置：')+str(e));return
        self.busy=True;self.result=None;self.result_path=None;self.set_available(True);self.status.setText(ui_text('正在生成，原 MIDI 工程不会被修改…'));self.progress.setValue(0)
        self.worker=BundleWorker(self.project,self.folder,Path(self.output.text()),self.options,self.render.isChecked())
        self.worker.progress.connect(lambda s,p:(self.status.setText(s),self.progress.setValue(int(p*100))))
        self.worker.done.connect(self.completed);self.worker.failed.connect(self.failed);self.worker.finished.connect(self.job_finished);self.worker.start()
    def completed(self,path,manifest):
        self.result_path=Path(path);self.result=manifest
        try:core.save_json(self.folder/'vital_result.json',{'path':str(self.result_path),'manifest':manifest})
        except OSError as e:self.status.setText(ui_text('结果已生成，但清单保存失败：')+str(e))
        self.refresh_table();self.progress.setValue(100)
        rendered=sum(bool(t.get('preview')) for t in manifest['tracks']);self.status.setText(f'{ui_text('已生成 ')}{len(manifest['tracks'])}{ui_text(' 份预设与同名 MIDI，')}{rendered}{ui_text(' 份 Vital 实际试听。可试听或打开输出目录。')}');self.update_preview_buttons()
    def failed(self,message):self.status.setText(message)
    def job_finished(self):self.busy=False;self.set_available(True)
    def cancel_job(self):
        if self.worker and self.worker.isRunning():self.worker.requestInterruption();self.cancel.setEnabled(False);self.status.setText(ui_text('正在取消，保留已完成文件…'))
    def result_row(self):
        i=self.table.currentRow()
        return self.result['tracks'][i] if self.result and 0<=i<len(self.result['tracks']) else None
    def update_preview_buttons(self):
        row=self.result_row();self.preview.setEnabled(not self.busy and bool(row and row.get('preview')))
        self.original.setEnabled(not self.busy and bool(row and row.get('preview')))
    def play_result(self):
        row=self.result_row()
        if row and row.get('preview'):
            if not (self.result_path/row['preview']).is_file():self.status.setText(ui_text('上次试听文件已移动或缺失，请重新生成。'));return
            self.stop_audio();self.player.setSource(QUrl.fromLocalFile(str(self.result_path/row['preview'])));self.player.play();self.status.setText(ui_text('试听：上次生成的 Vital 实际音色 · ')+row['track_name'])
    def play_original(self):
        row=self.result_row()
        if not row:return
        p=self.folder/self.project['audio']
        if not p.exists():self.status.setText(ui_text('找不到工程原曲音频'));return
        self.stop_audio();start=int(row.get('preview_start_seconds',0)*1000);self.pending_seek=start;self.preview_end=start+6000
        self.player.setSource(QUrl.fromLocalFile(str(p)));self.player.play();self.status.setText(ui_text('对照：原曲混音的对应 6 秒；不是分离声部。'))
    def media_status(self,status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia) and self.pending_seek is not None:
            position=self.pending_seek;self.pending_seek=None;self.player.setPosition(position)
    def preview_position(self,pos):
        if self.preview_end is not None and pos>=self.preview_end:self.stop_audio()
    def stop_audio(self):self.pending_seek=None;self.preview_end=None;self.player.stop()

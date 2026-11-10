"""Concrete, reversible two-note repair presentation and isolated A/B audition."""
from i18n import tr as ui_text
from pathlib import Path
import copy
from PySide6.QtCore import Qt,QThread,Signal,QRectF
from PySide6.QtGui import QPainter,QColor,QPen
from PySide6.QtWidgets import QWidget
import core

class RepairDiagram(QWidget):
    def __init__(self):super().__init__();self.notes=None;self.message=ui_text('点击左侧建议：定位两颗音符，比较合并前后。');self.setFixedHeight(102)
    def set_notes(self,a,b):self.notes=(copy.deepcopy(a),copy.deepcopy(b));self.update()
    def clear(self,empty=False):self.notes=None;self.message=ui_text('当前声部没有碎音建议。可选择其他声部查看。') if empty else ui_text('点击左侧建议：定位两颗音符，比较合并前后。');self.update()
    def paintEvent(self,e):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#142039'));p.setPen(QColor('#EDF2FF'))
        if not self.notes:
            p.drawText(self.rect().adjusted(12,5,-12,-5),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,self.message);return
        a,b=self.notes;start=a['start'];end=b['end'];width=max(80,self.width()-112);scale=width/max(.001,end-start);x=90
        p.drawText(9,27,ui_text('修复前 · 2 音'));p.drawText(9,70,ui_text('修复后 · 1 音'))
        for n in (a,b):
            rect=QRectF(x+(n['start']-start)*scale,12,max(2,(n['end']-n['start'])*scale),21);p.fillRect(rect,QColor('#AA98FF'));p.setPen(QPen(QColor('#DED4FF'),1));p.drawRect(rect)
        p.fillRect(QRectF(x,55,width,21),QColor('#79C4FF'))
        p.setPen(QColor('#B9CAE6'));p.drawText(x,48,f'{ui_text('间隙 ')}{(b['start'] - a['end']) * 1000:.0f} ms')
        p.drawText(x,96,f'{start:.3f} → {end:.3f}{ui_text(' 秒')}')

class RepairAudio(QThread):
    done=Signal(str);error=Signal(str)
    def __init__(self,project,cid,path,merged):super().__init__();self.project=copy.deepcopy(project);self.cid=cid;self.path=Path(path);self.merged=merged
    def run(self):
        try:
            c,t,a,b=core.candidate_notes(self.project,self.cid);start=max(0,a['start']-.25);end=min(self.project['duration'],b['end']+.35)
            # Two selected notes only: changing track mixtures cannot confound A/B.
            t['notes']=[a,b];a['candidate']=False;b['candidate']=False;self.project['tracks']=[t]
            if self.merged:core.apply_candidate(self.project,self.cid)
            core.render_preview(self.project,self.path,[t['id']],start=start,end=end);self.done.emit(str(self.path))
        except Exception as e:self.error.emit(str(e))

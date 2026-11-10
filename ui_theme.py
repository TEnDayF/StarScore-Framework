"""Geometric blue/violet native theme; one shared vocabulary for all stages."""
from i18n import tr as ui_text
from PySide6.QtCore import Qt,QSize,QPointF
from PySide6.QtGui import QColor,QPainter,QPolygonF,QPen,QFont
from PySide6.QtWidgets import QWidget,QTabBar
import core

COLORS={'lead':'#79C4FF','counter':'#B5A4FF','accompaniment':'#80D5CC','bass':'#DBB0F5','drums':'#F1AEAC','unknown':'#ADBBD2'}
TOKENS={'canvas':'#0B1020','surface':'#111A2E','raised':'#18243C','line':'#30415F','text':'#EDF2FF','muted':'#ADBBD2','blue':'#4775F5','purple':'#AA98FF'}
STYLE='''
QWidget {background:#111A2E;color:#EDF2FF;font-family:"Microsoft YaHei UI";font-size:12px;}
QMainWindow,QDialog {background:#0B1020;}
QWidget#appShell {background:#0B1020;}
QWidget#surface,QStackedWidget {background:#111A2E;}
QWidget#transport,QWidget#header {background:#0B1020;}
QLabel {background:transparent;}
QLabel#brand {font-size:19px;font-weight:600;}
QLabel#section {font-size:13px;font-weight:600;color:#D5DFFF;padding:4px 0;}
QLabel#muted {color:#ADBBD2;}
QLabel#dropZone {background:#14213C;color:#DFE8FF;border:1px dashed #6488CF;border-radius:4px;font-size:16px;}
QMenuBar,QMenu {background:#0B1020;color:#CEDAF2;}
QMenuBar::item {padding:5px 12px;}
QMenu::item {padding:8px 28px;}
QMenu::item:selected,QMenuBar::item:selected {background:#283A62;}
QMenu::separator {height:1px;background:#30415F;margin:5px 10px;}
QPushButton {background:#18243C;border:1px solid #405576;border-radius:4px;padding:6px 11px;min-height:20px;}
QPushButton:hover {background:#24365B;border-color:#91B5FF;}
QPushButton:pressed {background:#304775;}
QPushButton:focus {border:1px solid #BBAEFF;}
QPushButton:checked {background:#443B72;border-color:#AA98FF;color:#F5F0FF;}
QPushButton#primary {background:#355DCF;border-color:#688FFB;color:#FFFFFF;font-weight:600;}
QPushButton#primary:hover {background:#426EE8;border-color:#ABC2FF;}
QPushButton:disabled,QPushButton#primary:disabled {background:#141E32;border-color:#2C3A54;color:#8491AC;}
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox {background:#0D1628;color:#EDF2FF;border:1px solid #405576;border-radius:3px;padding:5px;min-height:20px;selection-background-color:#50477C;selection-color:#FFFFFF;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDoubleSpinBox:focus {border:1px solid #AA98FF;}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled,QDoubleSpinBox:disabled {color:#8491AC;border-color:#2C3A54;}
QComboBox QAbstractItemView {background:#18243C;selection-background-color:#304775;color:#EDF2FF;}
QCheckBox {spacing:7px;background:transparent;padding:2px 0;}
QCheckBox:disabled {color:#8491AC;}
QCheckBox::indicator {width:15px;height:15px;}
QTableWidget,QListWidget,QTextBrowser {background:#10192B;alternate-background-color:#142039;border:1px solid #30415F;gridline-color:#24334D;selection-background-color:#34345F;selection-color:#FFFFFF;outline:0;}
QTableWidget::item {padding:3px 6px;}
QTableWidget::item:selected {background:#34345F;color:#FFFFFF;}
QListWidget::item {padding:12px 10px;border-bottom:1px solid #24334D;}
QListWidget::item:selected {background:#253B66;}
QListWidget::item:hover {background:#1E2E4D;}
QHeaderView::section {background:#18243C;color:#BCCCE8;border:0;border-bottom:1px solid #30415F;padding:7px 6px;font-weight:600;}
QTableCornerButton::section {background:#18243C;border:1px solid #30415F;}
QTabWidget::pane {border:1px solid #30415F;background:#111A2E;}
QTabBar::tab {background:#142039;border:1px solid #30415F;padding:8px 16px;color:#B9C8E3;}
QTabBar::tab:selected {background:#36315D;color:#E6DDFF;border-color:#8C7BCD;}
QSplitter::handle {background:#263653;}
QSplitter::handle:horizontal {width:3px;}
QSplitter::handle:vertical {height:4px;}
QProgressBar {background:#18243C;border:1px solid #30415F;border-radius:2px;min-height:17px;text-align:center;color:#E5ECFF;}
QProgressBar::chunk {background:#4D64C2;}
QSlider::groove:horizontal {height:4px;background:#344666;border-radius:2px;}
QSlider::sub-page:horizontal {background:#6988EA;border-radius:2px;}
QSlider::handle:horizontal {width:12px;margin:-4px 0;background:#B5A4FF;border:1px solid #DACFFF;border-radius:2px;}
QSlider::handle:disabled {background:#687692;border-color:#8491AC;}
QScrollArea {border:0;background:#111A2E;}
QScrollBar:vertical {background:#0D1628;width:11px;margin:0;}
QScrollBar::handle:vertical {background:#435777;min-height:24px;border-radius:2px;}
QScrollBar:horizontal {background:#0D1628;height:11px;margin:0;}
QScrollBar::handle:horizontal {background:#435777;min-width:24px;border-radius:2px;}
QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;}
QScrollBar::add-page,QScrollBar::sub-page {background:transparent;}
QToolTip {background:#243354;color:#FFFFFF;border:1px solid #8A9FD4;padding:6px;}
QStatusBar {background:#0B1020;color:#A5B6D3;font-size:11px;}
QFrame[frameShape="4"] {color:#30415F;max-height:1px;}
'''
ASSETS=core.resource('ui_assets').as_posix()
STYLE+='''
QCheckBox::indicator,QAbstractItemView::indicator {width:14px;height:14px;border:1px solid #748CB4;border-radius:2px;background:#0D1628;}
QCheckBox::indicator:checked,QAbstractItemView::indicator:checked {background:#355DCF;border-color:#91B5FF;image:url(ASSETS/check.svg);}
QCheckBox::indicator:disabled {background:#17243A;border-color:#405576;}
QSpinBox::up-button,QDoubleSpinBox::up-button {background:#243653;border:0;width:16px;subcontrol-origin:border;subcontrol-position:top right;}
QSpinBox::down-button,QDoubleSpinBox::down-button {background:#243653;border:0;width:16px;subcontrol-origin:border;subcontrol-position:bottom right;}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow {image:url(ASSETS/up.svg);width:8px;height:6px;}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow {image:url(ASSETS/down.svg);width:8px;height:6px;}
'''.replace('ASSETS',ASSETS)

class BrandMark(QWidget):
    def __init__(self,parent=None):super().__init__(parent);self.setFixedSize(28,30);self.setAccessibleName(ui_text('星谱标识'))
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.setPen(Qt.PenStyle.NoPen)
        for points,color in [([(3,8),(14,2),(14,13),(3,19)],'#4775F5'),([(16,2),(26,8),(26,19),(16,13)],'#AA98FF'),([(4,22),(14,16),(25,22),(14,28)],'#79C4FF')]:
            p.setBrush(QColor(color));p.drawPolygon(QPolygonF([QPointF(x,y) for x,y in points]))

class WorkflowTabs(QTabBar):
    """Native tab semantics, hit testing and keyboard navigation, geometric paint."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setExpanding(True);self.setDrawBase(False);self.setUsesScrollButtons(False);self.setAccessibleName(ui_text('制作步骤'))
    def tabSizeHint(self,index):
        return QSize(180,42)
    def sizeHint(self):
        return QSize(max(720,self.parentWidget().width()-4),42)
    def minimumSizeHint(self):
        return QSize(720,42)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i in range(self.count()):
            r=self.tabRect(i).adjusted(0,1,-1,-1);selected=i==self.currentIndex();cut=12
            points=[QPointF(r.left(),r.top()),QPointF(r.right()-cut,r.top()),QPointF(r.right(),r.center().y()),QPointF(r.right()-cut,r.bottom()),QPointF(r.left(),r.bottom())]
            if i:points.append(QPointF(r.left()+cut,r.center().y()))
            p.setBrush(QColor('#AA98FF' if selected else '#142039'));p.setPen(QPen(QColor('#CDC1FF' if selected else '#405576'),1));p.drawPolygon(QPolygonF(points))
            p.setPen(QColor('#10162B' if selected else '#C2D0EA'));font=QFont(self.font());font.setBold(selected);p.setFont(font)
            p.drawText(r.adjusted(13,0,-13,0),Qt.AlignmentFlag.AlignCenter,self.tabText(i))
            if selected and self.hasFocus():
                p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor('#18243C'),1.5,Qt.PenStyle.DashLine));p.drawRect(r.adjusted(16,6,-24,-6))

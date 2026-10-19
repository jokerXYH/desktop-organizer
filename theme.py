# -*- coding: utf-8 -*-
"""「瓷白纸感」设计 token：格子、控制面板与系统对话框共用一套语言。"""
from PySide6.QtGui import QColor

FONT_FAMILY = "Microsoft YaHei UI"

PALETTE = {
    "ink": "#1F2327",
    "ink_soft": "#3B4145",
    "muted": "#73797D",
    "faint": "#9AA0A3",
    "porcelain": "#F4F3EF",
    "paper": "#FBFAF7",
    "paper_alt": "#EFEEE9",
    "line": "rgba(31,35,39,26)",
    "jade": "#2F6F62",
    "jade_dark": "#24584E",
    "jade_soft": "rgba(47,111,98,18)",
    "rail": "#222629",
    "rail_text": "#F2F1ED",
    "rail_muted": "#A1A7A6",
    "danger": "#B5473F",
}

GRID_BG_TOP = QColor(251, 250, 247, 247)
GRID_BG_BOTTOM = QColor(245, 244, 240, 247)
GRID_BORDER = QColor(31, 35, 39, 38)
GRID_INNER = QColor(255, 255, 255, 120)
GRID_SEPARATOR = QColor(31, 35, 39, 26)
GRID_ACCENT = QColor(47, 111, 98)

COLOR_TEXT = PALETTE["ink"]
COLOR_TEXT_DIM = PALETTE["muted"]
COLOR_TEXT_FAINT = PALETTE["faint"]
COLOR_ACCENT = PALETTE["jade"]

GRID_QSS = (
    "QListWidget{background:transparent;border:none;color:%s;outline:0;padding:2px;}"
    "QListWidget::item{border-radius:10px;padding:3px;}"
    "QListWidget::item:hover{background:rgba(47,111,98,16);}"
    "QListWidget::item:selected{background:rgba(47,111,98,30);color:#172A25;"
    "border:1px solid rgba(47,111,98,92);}"
    "QScrollBar:vertical{background:transparent;width:8px;margin:2px 1px;}"
    "QScrollBar::handle:vertical{background:rgba(31,35,39,46);"
    "border-radius:4px;min-height:28px;}"
    "QScrollBar::handle:vertical:hover{background:%s;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
) % (COLOR_TEXT, COLOR_ACCENT)

COMPACT_LIST_QSS = GRID_QSS + "QListWidget::item{padding:7px 6px;border-radius:9px;}"

SEARCH_EDIT_QSS = (
    "QLineEdit{background:rgba(31,35,39,6);"
    "border:1px solid rgba(31,35,39,28);border-radius:10px;"
    "padding:6px 10px;color:%s;selection-background-color:rgba(47,111,98,70);}"
    "QLineEdit:focus{border-color:%s;background:rgba(255,255,255,190);}"
) % (COLOR_TEXT, COLOR_ACCENT)

MENU_QSS = """
QMenu{background:#FBFAF7;color:#1F2327;border:1px solid rgba(31,35,39,36);
      border-radius:12px;padding:6px;}
QMenu::item{padding:7px 22px;border-radius:8px;}
QMenu::item:selected{background:rgba(47,111,98,24);color:#172A25;}
QMenu::separator{height:1px;background:rgba(31,35,39,20);margin:5px 8px;}
"""

PANEL_QSS = """
QWidget{color:#1F2327;font-family:"Microsoft YaHei UI";font-size:12px;}
QWidget#page{background:#F4F3EF;}
QWidget#rail{background:#222629;}
QLabel{background:transparent;}
QLabel#brand{color:#F2F1ED;font-size:19px;font-weight:600;}
QLabel#brandSub{color:#A1A7A6;font-size:11px;}
QLabel#railVersion{color:#8F9594;font-size:10px;}
QLabel#pageTitle{color:#1F2327;font-size:24px;font-weight:600;}
QLabel#pageSubtitle{color:#73797D;font-size:12px;}
QLabel#sectionTitle{color:#1F2327;font-size:13px;font-weight:600;}
QLabel#sectionHint{color:#73797D;font-size:11px;}
QLabel#statusLabel{color:#73797D;font-size:11px;}
QFrame#card{background:#FBFAF7;border:1px solid rgba(31,35,39,22);
            border-radius:16px;}
QFrame#card QLabel{background:transparent;}
QPushButton{background:#FBFAF7;border:1px solid rgba(31,35,39,28);
            border-radius:10px;padding:7px 14px;color:#1F2327;}
QPushButton:hover{background:#FFFFFF;border-color:rgba(47,111,98,90);}
QPushButton:pressed{background:#EFEEE9;}
QPushButton:disabled{color:#A5AAAD;background:#F0EFEB;
                     border-color:rgba(31,35,39,16);}
QPushButton#primaryBtn{background:#2F6F62;border:none;color:#FFFFFF;
                       font-weight:600;padding:10px 20px;}
QPushButton#primaryBtn:hover{background:#24584E;}
QPushButton#primaryBtn:pressed{background:#1E4A41;}
QPushButton#ghostBtn{background:transparent;border:1px solid rgba(31,35,39,20);}
QPushButton#ghostBtn:hover{background:#FFFFFF;border-color:rgba(47,111,98,80);}
QPushButton#dangerBtn{background:rgba(181,71,63,10);
                      border:1px solid rgba(181,71,63,70);color:#A33D36;
                      font-weight:600;}
QPushButton#dangerBtn:hover{background:rgba(181,71,63,22);
                            border-color:rgba(181,71,63,130);}
QPushButton#navBtn{background:transparent;border:none;color:#A1A7A6;
                   text-align:left;padding:11px 16px;border-radius:10px;}
QPushButton#navBtn:hover{background:rgba(255,255,255,8);color:#F2F1ED;}
QPushButton#navBtn:checked{background:rgba(255,255,255,13);color:#FFFFFF;
                           border-left:3px solid #6FA492;
                           padding-left:13px;}
QLineEdit{background:#FFFFFF;border:1px solid rgba(31,35,39,28);
          border-radius:10px;padding:7px 10px;color:#1F2327;
          selection-background-color:rgba(47,111,98,70);}
QLineEdit:focus{border-color:#2F6F62;}
QComboBox{background:#FFFFFF;border:1px solid rgba(31,35,39,28);
          border-radius:10px;padding:6px 10px;color:#1F2327;}
QComboBox:hover{border-color:rgba(47,111,98,90);}
QComboBox::drop-down{border:none;width:24px;}
QComboBox QAbstractItemView{background:#FBFAF7;color:#1F2327;outline:0;
          border:1px solid rgba(31,35,39,30);border-radius:10px;padding:4px;
          selection-background-color:rgba(47,111,98,26);}
QListWidget{background:#FBFAF7;border:1px solid rgba(31,35,39,22);
            border-radius:12px;outline:0;padding:5px;}
QListWidget::item{padding:8px;border-radius:8px;}
QListWidget::item:hover{background:rgba(47,111,98,12);}
QListWidget::item:selected{background:rgba(47,111,98,28);color:#172A25;}
QScrollBar:vertical{background:transparent;width:8px;margin:2px;}
QScrollBar::handle:vertical{background:rgba(31,35,39,40);border-radius:4px;
                            min-height:28px;}
QScrollBar::handle:vertical:hover{background:#2F6F62;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}
QScrollArea{background:transparent;border:none;}
QScrollArea > QWidget > QWidget{background:transparent;}
QTextBrowser{background:#FBFAF7;border:1px solid rgba(31,35,39,22);
             border-radius:12px;padding:12px;color:#1F2327;}
"""

DIALOG_QSS = """
QMenu{background:#FBFAF7;color:#1F2327;border:1px solid rgba(31,35,39,36);
      border-radius:12px;padding:6px;}
QMenu::item{padding:7px 22px;border-radius:8px;}
QMenu::item:selected{background:rgba(47,111,98,24);color:#172A25;}
QMenu::separator{height:1px;background:rgba(31,35,39,20);margin:5px 8px;}
QToolTip{background:#FBFAF7;color:#1F2327;border:1px solid rgba(31,35,39,36);
         border-radius:8px;padding:5px 8px;}
QDialog,QMessageBox,QInputDialog{background:#F4F3EF;color:#1F2327;}
QDialog QLabel,QMessageBox QLabel,QInputDialog QLabel{
      color:#1F2327;background:transparent;}
QDialog QPushButton,QMessageBox QPushButton{
      background:#FBFAF7;border:1px solid rgba(31,35,39,30);
      border-radius:9px;padding:6px 18px;color:#1F2327;}
QDialog QPushButton:hover,QMessageBox QPushButton:hover{
      background:#FFFFFF;border-color:rgba(47,111,98,90);}
QDialog QLineEdit,QInputDialog QLineEdit{background:#FFFFFF;
      border:1px solid rgba(31,35,39,28);border-radius:9px;
      padding:6px 10px;color:#1F2327;
      selection-background-color:rgba(47,111,98,70);}
QDialog QLineEdit:focus{border-color:#2F6F62;}
QTextBrowser{background:#FBFAF7;border:1px solid rgba(31,35,39,22);
      border-radius:10px;padding:8px;color:#1F2327;}
"""

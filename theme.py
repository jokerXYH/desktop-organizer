# -*- coding: utf-8 -*-
"""「石墨玻璃」设计 token：格子、控制面板与系统对话框共用一套语言。

视觉原则：
- 半透明深色玻璃：让桌面壁纸透出来，但不影响文字可读性；
- 冷灰青作为唯一强调色，低饱和、无发光、无霓虹；
- 只用 1px 细线、轻微渐变和极淡的内高光营造"科技感"，不堆装饰。
"""
from PySide6.QtGui import QColor

FONT_FAMILY = "Microsoft YaHei UI"

PALETTE = {
    "ink": "#E6EDF1",                 # 主文字
    "ink_soft": "#C3CDD3",            # 次级文字
    "muted": "#94A1A9",               # 说明文字
    "faint": "#6E7B84",               # 最弱提示
    "porcelain": "#0B1015",           # 页面底色（近似实色，用于不透明场景）
    "paper": "#141B21",               # 卡片/输入框底色
    "paper_alt": "#1A232A",
    "line": "rgba(255,255,255,28)",
    "jade": "#7FB3A4",                # 冷灰青强调色（非霓虹）
    "jade_dark": "#5F9486",
    "jade_soft": "rgba(127,179,164,26)",
    "rail": "#080D11",                # 托盘图标/深色实体背景
    "rail_text": "#EDF2F4",
    "rail_muted": "#8B979F",
    "danger": "#CE8A82",
}

# 格子玻璃底：保留约 15%~20% 的桌面透出，文字对比度仍足够
GRID_BG_TOP = QColor(20, 27, 34, 200)
GRID_BG_BOTTOM = QColor(11, 16, 21, 210)
GRID_BORDER = QColor(255, 255, 255, 34)
GRID_INNER = QColor(255, 255, 255, 18)
GRID_SEPARATOR = QColor(255, 255, 255, 30)
GRID_ACCENT = QColor(127, 179, 164)

COLOR_TEXT = PALETTE["ink"]
COLOR_TEXT_DIM = PALETTE["muted"]
COLOR_TEXT_FAINT = PALETTE["faint"]
COLOR_ACCENT = PALETTE["jade"]

GRID_QSS = (
    "QListWidget{background:transparent;border:none;color:%s;outline:0;padding:2px;}"
    "QListWidget::item{border-radius:10px;padding:3px;}"
    "QListWidget::item:hover{background:rgba(255,255,255,14);}"
    "QListWidget::item:selected{background:rgba(127,179,164,38);color:#F2F8F5;"
    "border:1px solid rgba(127,179,164,96);}"
    "QScrollBar:vertical{background:transparent;width:8px;margin:2px 1px;}"
    "QScrollBar::handle:vertical{background:rgba(255,255,255,46);"
    "border-radius:4px;min-height:28px;}"
    "QScrollBar::handle:vertical:hover{background:%s;}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
) % (COLOR_TEXT, COLOR_ACCENT)

COMPACT_LIST_QSS = GRID_QSS + "QListWidget::item{padding:7px 6px;border-radius:9px;}"

SEARCH_EDIT_QSS = (
    "QLineEdit{background:rgba(0,0,0,70);"
    "border:1px solid rgba(255,255,255,34);border-radius:10px;"
    "padding:6px 10px;color:%s;selection-background-color:rgba(127,179,164,90);}"
    "QLineEdit:focus{border-color:rgba(127,179,164,150);"
    "background:rgba(255,255,255,14);}"
) % COLOR_TEXT

MENU_QSS = """
QMenu{background:rgba(14,20,26,242);color:#E6EDF1;
      border:1px solid rgba(255,255,255,34);border-radius:12px;padding:6px;}
QMenu::item{padding:7px 22px;border-radius:8px;}
QMenu::item:selected{background:rgba(127,179,164,34);color:#F2F8F5;}
QMenu::separator{height:1px;background:rgba(255,255,255,24);margin:5px 8px;}
"""

PANEL_QSS = """
QWidget{color:#E6EDF1;font-family:"Microsoft YaHei UI";font-size:12px;}
QWidget#page{background:#0D1318;}
QWidget#rail{background:#060A0E;border-right:1px solid rgba(255,255,255,22);}
QStackedWidget{background:transparent;}
QLabel{background:transparent;}
QLabel#brand{color:#EDF2F4;font-size:19px;font-weight:600;}
QLabel#brandSub{color:#8B979F;font-size:11px;}
QLabel#railVersion{color:#6F7C84;font-size:10px;}
QLabel#pageTitle{color:#F0F5F7;font-size:24px;font-weight:600;}
QLabel#pageSubtitle{color:#94A1A9;font-size:12px;}
QLabel#sectionTitle{color:#E6EDF1;font-size:13px;font-weight:600;}
QLabel#sectionHint{color:#94A1A9;font-size:11px;}
QLabel#statusLabel{color:#94A1A9;font-size:11px;}
QFrame#card{background:rgba(255,255,255,12);
            border:1px solid rgba(255,255,255,26);border-radius:16px;}
QFrame#card QLabel{background:transparent;}
QPushButton{background:rgba(255,255,255,12);
            border:1px solid rgba(255,255,255,30);border-radius:10px;
            padding:7px 14px;color:#E6EDF1;}
QPushButton:hover{background:rgba(255,255,255,22);
                  border-color:rgba(127,179,164,130);}
QPushButton:pressed{background:rgba(255,255,255,10);}
QPushButton:disabled{color:#68747C;background:rgba(255,255,255,6);
                     border-color:rgba(255,255,255,14);}
QPushButton#primaryBtn{background:#6FA492;border:1px solid #86B7A8;
                       color:#07110D;font-weight:600;padding:10px 20px;}
QPushButton#primaryBtn:hover{background:#7FB3A4;border-color:#9AC6B7;}
QPushButton#primaryBtn:pressed{background:#5F9486;}
QPushButton#ghostBtn{background:transparent;border:1px solid rgba(255,255,255,24);}
QPushButton#ghostBtn:hover{background:rgba(255,255,255,16);
                           border-color:rgba(127,179,164,120);}
QPushButton#dangerBtn{background:rgba(206,138,130,16);
                      border:1px solid rgba(206,138,130,80);color:#E4A39B;
                      font-weight:600;}
QPushButton#dangerBtn:hover{background:rgba(206,138,130,30);
                            border-color:rgba(206,138,130,140);}
QPushButton#windowBtn{background:transparent;border:none;border-radius:8px;
                      padding:0;color:#9AA7AF;font-size:15px;}
QPushButton#windowBtn:hover{background:rgba(255,255,255,22);color:#F0F5F7;}
QPushButton#closeBtn{background:transparent;border:none;border-radius:8px;
                     padding:0;color:#9AA7AF;font-size:16px;}
QPushButton#closeBtn:hover{background:rgba(206,138,130,70);color:#FFFFFF;}
QPushButton#navBtn{background:transparent;border:none;color:#8B979F;
                   text-align:left;padding:11px 16px;border-radius:10px;}
QPushButton#navBtn:hover{background:rgba(255,255,255,10);color:#EDF2F4;}
QPushButton#navBtn:checked{background:rgba(255,255,255,16);color:#FFFFFF;
                           border-left:3px solid #7FB3A4;padding-left:13px;}
QLineEdit{background:rgba(0,0,0,80);border:1px solid rgba(255,255,255,30);
          border-radius:10px;padding:7px 10px;color:#E6EDF1;
          selection-background-color:rgba(127,179,164,90);}
QLineEdit:focus{border-color:rgba(127,179,164,160);}
QComboBox{background:rgba(0,0,0,70);border:1px solid rgba(255,255,255,30);
          border-radius:10px;padding:6px 10px;color:#E6EDF1;}
QComboBox:hover{border-color:rgba(127,179,164,130);}
QComboBox::drop-down{border:none;width:24px;}
QComboBox QAbstractItemView{background:#141C22;color:#E6EDF1;outline:0;
          border:1px solid rgba(255,255,255,30);border-radius:10px;padding:4px;
          selection-background-color:rgba(127,179,164,40);}
QListWidget{background:rgba(0,0,0,55);border:1px solid rgba(255,255,255,22);
            border-radius:12px;outline:0;padding:5px;color:#E6EDF1;}
QListWidget::item{padding:8px;border-radius:8px;}
QListWidget::item:hover{background:rgba(255,255,255,14);}
QListWidget::item:selected{background:rgba(127,179,164,40);color:#F2F8F5;}
QScrollBar:vertical{background:transparent;width:8px;margin:2px;}
QScrollBar::handle:vertical{background:rgba(255,255,255,46);border-radius:4px;
                            min-height:28px;}
QScrollBar::handle:vertical:hover{background:#7FB3A4;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}
QScrollArea{background:transparent;border:none;}
QScrollArea > QWidget > QWidget{background:transparent;}
QTextBrowser{background:rgba(0,0,0,70);border:1px solid rgba(255,255,255,22);
             border-radius:12px;padding:12px;color:#C9D4DA;}
QCheckBox{color:#E6EDF1;background:transparent;}
"""

DIALOG_QSS = """
QMenu{background:rgba(14,20,26,242);color:#E6EDF1;
      border:1px solid rgba(255,255,255,34);border-radius:12px;padding:6px;}
QMenu::item{padding:7px 22px;border-radius:8px;}
QMenu::item:selected{background:rgba(127,179,164,34);color:#F2F8F5;}
QMenu::separator{height:1px;background:rgba(255,255,255,24);margin:5px 8px;}
QToolTip{background:rgba(14,20,26,245);color:#E6EDF1;
         border:1px solid rgba(255,255,255,34);border-radius:8px;padding:5px 8px;}
QDialog,QMessageBox,QInputDialog{background:#111820;color:#E6EDF1;}
QDialog QLabel,QMessageBox QLabel,QInputDialog QLabel{
      color:#E6EDF1;background:transparent;}
QDialog QPushButton,QMessageBox QPushButton{
      background:rgba(255,255,255,12);border:1px solid rgba(255,255,255,30);
      border-radius:9px;padding:6px 18px;color:#E6EDF1;}
QDialog QPushButton:hover,QMessageBox QPushButton:hover{
      background:rgba(255,255,255,22);border-color:rgba(127,179,164,130);}
QDialog QLineEdit,QInputDialog QLineEdit{background:rgba(0,0,0,80);
      border:1px solid rgba(255,255,255,30);border-radius:9px;
      padding:6px 10px;color:#E6EDF1;
      selection-background-color:rgba(127,179,164,90);}
QDialog QLineEdit:focus{border-color:rgba(127,179,164,160);}
QTextBrowser{background:rgba(0,0,0,70);border:1px solid rgba(255,255,255,22);
      border-radius:10px;padding:8px;color:#C9D4DA;}
QLabel{color:#E6EDF1;}
QCheckBox{color:#E6EDF1;}
"""

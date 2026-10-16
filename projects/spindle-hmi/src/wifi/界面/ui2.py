# -*- coding: utf-8 -*-
# Form generated from reading UI file ui2.ui
# Auto-converted by workbuddy (pyside6-uic not available)

import os
from PySide6.QtCore import (QCoreApplication, QMetaObject, QRect, QSize, Qt)
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QLabel,
                               QLineEdit, QMainWindow, QMenuBar, QPushButton,
                               QPlainTextEdit, QStatusBar, QTextEdit, QWidget)


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName("MainWindow")
        MainWindow.resize(1163, 645)
        MainWindow.setWindowTitle("磁悬浮铣削电主轴上位机程序 V2.0")
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")

        self.label = QLabel(self.centralwidget)
        self.label.setObjectName("label")
        self.label.setGeometry(QRect(150, 50, 54, 16))

        self.lineEdit = QLineEdit(self.centralwidget)
        self.lineEdit.setObjectName("lineEdit")
        self.lineEdit.setGeometry(QRect(290, 420, 40, 40))
        self.lineEdit.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.pushButton_2 = QPushButton(self.centralwidget)
        self.pushButton_2.setObjectName("pushButton_2")
        self.pushButton_2.setGeometry(QRect(250, 420, 40, 40))
        self.pushButton_2.setStyleSheet("color: red;")
        self.pushButton_2.setText("X+")

        self.pushButton_3 = QPushButton(self.centralwidget)
        self.pushButton_3.setObjectName("pushButton_3")
        self.pushButton_3.setGeometry(QRect(290, 380, 40, 40))
        self.pushButton_3.setStyleSheet("color: green;")
        self.pushButton_3.setText("Y-")

        self.pushButton_4 = QPushButton(self.centralwidget)
        self.pushButton_4.setObjectName("pushButton_4")
        self.pushButton_4.setGeometry(QRect(330, 420, 40, 40))
        self.pushButton_4.setStyleSheet("color: red;")
        self.pushButton_4.setText("X-")

        self.pushButton_6 = QPushButton(self.centralwidget)
        self.pushButton_6.setObjectName("pushButton_6")
        self.pushButton_6.setGeometry(QRect(430, 380, 40, 40))
        self.pushButton_6.setStyleSheet("color:blue;")
        self.pushButton_6.setText("Z+")

        self.lineEdit_2 = QLineEdit(self.centralwidget)
        self.lineEdit_2.setObjectName("lineEdit_2")
        self.lineEdit_2.setGeometry(QRect(430, 420, 40, 40))
        self.lineEdit_2.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label_3 = QLabel(self.centralwidget)
        self.label_3.setObjectName("label_3")
        self.label_3.setGeometry(QRect(300, 60, 241, 191))
        _img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "111.png")
        if not os.path.isfile(_img_path):
            _img_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "图", "111.png")
        if os.path.isfile(_img_path):
            self.label_3.setPixmap(QPixmap(_img_path.replace("\\", "/")))
            self.label_3.setScaledContents(True)
        # 图片为装饰用，不存在时静默跳过

        self.pushButton_7 = QPushButton(self.centralwidget)
        self.pushButton_7.setObjectName("pushButton_7")
        self.pushButton_7.setGeometry(QRect(430, 460, 40, 40))
        self.pushButton_7.setStyleSheet("color:blue;")
        self.pushButton_7.setText("Z-")

        self.pushButton_8 = QPushButton(self.centralwidget)
        self.pushButton_8.setObjectName("pushButton_8")
        self.pushButton_8.setGeometry(QRect(290, 460, 40, 40))
        self.pushButton_8.setStyleSheet("color: green;")
        self.pushButton_8.setText("Y+")

        self.label_2 = QLabel(self.centralwidget)
        self.label_2.setObjectName("label_2")
        self.label_2.setGeometry(QRect(30, 560, 81, 31))
        self.label_2.setStyleSheet("border: 1px solid black;")
        self.label_2.setText("电主轴旋转：")

        self.lineEdit_3 = QLineEdit(self.centralwidget)
        self.lineEdit_3.setObjectName("lineEdit_3")
        self.lineEdit_3.setGeometry(QRect(130, 560, 161, 31))

        self.pushButton_9 = QPushButton(self.centralwidget)
        self.pushButton_9.setObjectName("pushButton_9")
        self.pushButton_9.setGeometry(QRect(420, 560, 81, 31))
        self.pushButton_9.setStyleSheet("color: green;")
        self.pushButton_9.setText("Start")

        self.label_6 = QLabel(self.centralwidget)
        self.label_6.setObjectName("label_6")
        self.label_6.setGeometry(QRect(370, 420, 30, 40))
        self.label_6.setStyleSheet("border: 1px solid black;")
        self.label_6.setScaledContents(True)
        self.label_6.setText("→")

        self.label_7 = QLabel(self.centralwidget)
        self.label_7.setObjectName("label_7")
        self.label_7.setGeometry(QRect(220, 420, 30, 40))
        self.label_7.setStyleSheet("border: 1px solid black;")
        self.label_7.setScaledContents(True)
        self.label_7.setText("←")

        self.label_9 = QLabel(self.centralwidget)
        self.label_9.setObjectName("label_9")
        self.label_9.setGeometry(QRect(430, 350, 40, 30))
        self.label_9.setStyleSheet("border: 1px solid black;")
        self.label_9.setScaledContents(True)
        self.label_9.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_9.setText("↑")

        self.label_10 = QLabel(self.centralwidget)
        self.label_10.setObjectName("label_10")
        self.label_10.setGeometry(QRect(430, 500, 40, 30))
        self.label_10.setStyleSheet("border: 1px solid black;")
        self.label_10.setScaledContents(True)
        self.label_10.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_10.setText("↓")

        self.label_11 = QLabel(self.centralwidget)
        self.label_11.setObjectName("label_11")
        self.label_11.setGeometry(QRect(290, 350, 40, 30))
        self.label_11.setStyleSheet("border: 1px solid black;")
        self.label_11.setScaledContents(True)
        self.label_11.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_11.setText("↑")

        self.label_12 = QLabel(self.centralwidget)
        self.label_12.setObjectName("label_12")
        self.label_12.setGeometry(QRect(290, 500, 40, 30))
        self.label_12.setStyleSheet("border: 1px solid black;")
        self.label_12.setScaledContents(True)
        self.label_12.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_12.setText("↓")

        self.label_4 = QLabel(self.centralwidget)
        self.label_4.setObjectName("label_4")
        self.label_4.setGeometry(QRect(470, 480, 31, 51))
        self.label_4.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_4.setText("刀具\n靠近\n工件")
        self.label_5 = QLabel(self.centralwidget)
        self.label_5.setObjectName("label_5")
        self.label_5.setGeometry(QRect(470, 350, 31, 51))
        self.label_5.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_5.setText("刀具\n远离\n工件")
        self.label_13 = QLabel(self.centralwidget)
        self.label_13.setObjectName("label_13")
        self.label_13.setGeometry(QRect(330, 350, 41, 31))
        self.label_13.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_13.setText("加工台\n远离\n操作者")
        self.label_14 = QLabel(self.centralwidget)
        self.label_14.setObjectName("label_14")
        self.label_14.setGeometry(QRect(190, 420, 31, 41))
        self.label_14.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_14.setText("加工台\n向左\n移动")
        self.label_15 = QLabel(self.centralwidget)
        self.label_15.setObjectName("label_15")
        self.label_15.setGeometry(QRect(400, 420, 31, 41))
        self.label_15.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_15.setText("加工台\n向右\n移动")
        self.label_16 = QLabel(self.centralwidget)
        self.label_16.setObjectName("label_16")
        self.label_16.setGeometry(QRect(330, 500, 41, 31))
        self.label_16.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_16.setText("加工台\n靠近\n操作者")
        self.pushButton = QPushButton(self.centralwidget)
        self.pushButton.setObjectName("pushButton")
        self.pushButton.setGeometry(QRect(30, 25, 75, 24))
        self.pushButton.setText("search")

        self.comboBox = QComboBox(self.centralwidget)
        self.comboBox.setObjectName("comboBox")
        self.comboBox.setGeometry(QRect(110, 25, 101, 22))
        self.comboBox.setEditable(True)

        self.pushButton_5 = QPushButton(self.centralwidget)
        self.pushButton_5.setObjectName("pushButton_5")
        self.pushButton_5.setGeometry(QRect(220, 25, 75, 24))
        self.pushButton_5.setText("Connect")

        self.pushButton_10 = QPushButton(self.centralwidget)
        self.pushButton_10.setObjectName("pushButton_10")
        self.pushButton_10.setGeometry(QRect(300, 25, 75, 24))
        self.pushButton_10.setText("InitZMC")

        self.pushButton_12 = QPushButton(self.centralwidget)
        self.pushButton_12.setObjectName("pushButton_12")
        self.pushButton_12.setGeometry(QRect(104, 190, 71, 24))
        self.pushButton_12.setText("手动运行")

        self.pushButton_13 = QPushButton(self.centralwidget)
        self.pushButton_13.setObjectName("pushButton_13")
        self.pushButton_13.setGeometry(QRect(30, 190, 71, 24))
        self.pushButton_13.setText("未使能")

        self.pushButton_14 = QPushButton(self.centralwidget)
        self.pushButton_14.setObjectName("pushButton_14")
        self.pushButton_14.setGeometry(QRect(180, 190, 71, 24))
        self.pushButton_14.setText("相对")

        self.pushButton_15 = QPushButton(self.centralwidget)
        self.pushButton_15.setObjectName("pushButton_15")
        self.pushButton_15.setGeometry(QRect(30, 220, 71, 24))
        self.pushButton_15.setText("回零点")

        self.pushButton_16 = QPushButton(self.centralwidget)
        self.pushButton_16.setObjectName("pushButton_16")
        self.pushButton_16.setGeometry(QRect(104, 220, 71, 24))
        self.pushButton_16.setText("重设坐标系")

        self.pushButton_17 = QPushButton(self.centralwidget)
        self.pushButton_17.setObjectName("pushButton_17")
        self.pushButton_17.setGeometry(QRect(180, 220, 71, 24))
        self.pushButton_17.setText("回原点")

        self.label_8 = QLabel(self.centralwidget)
        self.label_8.setObjectName("label_8")
        self.label_8.setGeometry(QRect(0, 90, 54, 16))
        self.label_8.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_8.setText("X轴")

        self.label_17 = QLabel(self.centralwidget)
        self.label_17.setObjectName("label_17")
        self.label_17.setGeometry(QRect(0, 120, 54, 16))
        self.label_17.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_17.setText("Y轴")

        self.label_18 = QLabel(self.centralwidget)
        self.label_18.setObjectName("label_18")
        self.label_18.setGeometry(QRect(0, 150, 54, 16))
        self.label_18.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_18.setText("Z轴")

        self.label_19 = QLabel(self.centralwidget)
        self.label_19.setObjectName("label_19")
        self.label_19.setGeometry(QRect(60, 70, 54, 16))
        self.label_19.setText("坐标下限")

        self.label_20 = QLabel(self.centralwidget)
        self.label_20.setObjectName("label_20")
        self.label_20.setGeometry(QRect(130, 70, 54, 16))
        self.label_20.setText("当前坐标")

        self.label_21 = QLabel(self.centralwidget)
        self.label_21.setObjectName("label_21")
        self.label_21.setGeometry(QRect(200, 70, 54, 16))
        self.label_21.setText("坐标上限")

        self.lineEdit_4 = QLineEdit(self.centralwidget)
        self.lineEdit_4.setObjectName("lineEdit_4")
        self.lineEdit_4.setGeometry(QRect(60, 90, 61, 20))

        self.lineEdit_5 = QLineEdit(self.centralwidget)
        self.lineEdit_5.setObjectName("lineEdit_5")
        self.lineEdit_5.setGeometry(QRect(130, 90, 61, 20))

        self.lineEdit_6 = QLineEdit(self.centralwidget)
        self.lineEdit_6.setObjectName("lineEdit_6")
        self.lineEdit_6.setGeometry(QRect(200, 90, 61, 20))

        self.lineEdit_7 = QLineEdit(self.centralwidget)
        self.lineEdit_7.setObjectName("lineEdit_7")
        self.lineEdit_7.setGeometry(QRect(200, 120, 61, 20))

        self.lineEdit_8 = QLineEdit(self.centralwidget)
        self.lineEdit_8.setObjectName("lineEdit_8")
        self.lineEdit_8.setGeometry(QRect(130, 120, 61, 20))

        self.lineEdit_9 = QLineEdit(self.centralwidget)
        self.lineEdit_9.setObjectName("lineEdit_9")
        self.lineEdit_9.setGeometry(QRect(60, 120, 61, 20))

        self.lineEdit_10 = QLineEdit(self.centralwidget)
        self.lineEdit_10.setObjectName("lineEdit_10")
        self.lineEdit_10.setGeometry(QRect(200, 150, 61, 20))

        self.lineEdit_11 = QLineEdit(self.centralwidget)
        self.lineEdit_11.setObjectName("lineEdit_11")
        self.lineEdit_11.setGeometry(QRect(130, 150, 61, 20))

        self.lineEdit_12 = QLineEdit(self.centralwidget)
        self.lineEdit_12.setObjectName("lineEdit_12")
        self.lineEdit_12.setGeometry(QRect(60, 150, 61, 20))

        self.pushButton_11 = QPushButton(self.centralwidget)
        self.pushButton_11.setObjectName("pushButton_11")
        self.pushButton_11.setGeometry(QRect(510, 350, 61, 51))
        self.pushButton_11.setStyleSheet("color: red;")
        self.pushButton_11.setText("急停")

        self.pushButton_18 = QPushButton(self.centralwidget)
        self.pushButton_18.setObjectName("pushButton_18")
        self.pushButton_18.setGeometry(QRect(510, 480, 61, 50))
        self.pushButton_18.setStyleSheet("color: green;")
        self.pushButton_18.setText("恢复")

        self.groupBox_7 = QGroupBox(self.centralwidget)
        self.groupBox_7.setObjectName("groupBox_7")
        self.groupBox_7.setGeometry(QRect(580, 20, 331, 581))

        self.label_22 = QLabel(self.centralwidget)
        self.label_22.setObjectName("label_22")
        self.label_22.setGeometry(QRect(320, 560, 81, 31))
        self.label_22.setStyleSheet("border: 1px solid black;")
        self.label_22.setText("单位：r/min")

        self.groupBox_8 = QGroupBox(self.centralwidget)
        self.groupBox_8.setObjectName("groupBox_8")
        self.groupBox_8.setGeometry(QRect(920, 20, 241, 581))

        self.groupBox_2 = QGroupBox(self.centralwidget)
        self.groupBox_2.setObjectName("groupBox_2")
        self.groupBox_2.setGeometry(QRect(10, 550, 521, 51))

        # 手轮右侧的独立手动速度档。它只定义点动/模拟手轮速度，
        # 不显示、更不修改 G 代码正在使用的 F 进给速度。
        self.ManualSpeedBox = QGroupBox(self.centralwidget)
        self.ManualSpeedBox.setObjectName("ManualSpeedBox")
        # 标题置于框外；边框只包住 X/Y/Z 三组手动速度输入。
        self.ManualSpeedTitle = QLabel(self.centralwidget)
        self.ManualSpeedTitle.setObjectName("ManualSpeedTitle")
        self.ManualSpeedTitle.setGeometry(QRect(285, 271, 225, 18))
        self.ManualSpeedTitle.setStyleSheet("font-weight: bold; color: #526d88;")
        self.ManualSpeedTitle.setText("手动点动设定速度（mm/min）")

        self.ManualSpeedBox.setGeometry(QRect(285, 290, 225, 48))
        self.ManualSpeedBox.setTitle("")
        self.ManualSpeedBox.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #a8bdd8; "
            "border-radius: 4px; margin-top: 10px; padding-top: 4px; }"
            "QLineEdit { background: white; }")

        self.ManualSpeedXLabel = QLabel(self.ManualSpeedBox)
        self.ManualSpeedXLabel.setObjectName("ManualSpeedXLabel")
        self.ManualSpeedXLabel.setGeometry(QRect(10, 15, 16, 22))
        self.ManualSpeedXLabel.setText("X")
        self.ManualSpeedX = QLineEdit(self.ManualSpeedBox)
        self.ManualSpeedX.setObjectName("ManualSpeedX")
        self.ManualSpeedX.setGeometry(QRect(27, 12, 44, 25))
        self.ManualSpeedX.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ManualSpeedX.setText("120")

        self.ManualSpeedYLabel = QLabel(self.ManualSpeedBox)
        self.ManualSpeedYLabel.setObjectName("ManualSpeedYLabel")
        self.ManualSpeedYLabel.setGeometry(QRect(82, 15, 16, 22))
        self.ManualSpeedYLabel.setText("Y")
        self.ManualSpeedY = QLineEdit(self.ManualSpeedBox)
        self.ManualSpeedY.setObjectName("ManualSpeedY")
        self.ManualSpeedY.setGeometry(QRect(99, 12, 44, 25))
        self.ManualSpeedY.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ManualSpeedY.setText("120")

        self.ManualSpeedZLabel = QLabel(self.ManualSpeedBox)
        self.ManualSpeedZLabel.setObjectName("ManualSpeedZLabel")
        self.ManualSpeedZLabel.setGeometry(QRect(154, 15, 16, 22))
        self.ManualSpeedZLabel.setText("Z")
        self.ManualSpeedZ = QLineEdit(self.ManualSpeedBox)
        self.ManualSpeedZ.setObjectName("ManualSpeedZ")
        self.ManualSpeedZ.setGeometry(QRect(171, 12, 44, 25))
        self.ManualSpeedZ.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ManualSpeedZ.setText("60")

        self.pushButton_22 = QPushButton(self.centralwidget)
        self.pushButton_22.setObjectName("pushButton_22")
        self.pushButton_22.setGeometry(QRect(10, 280, 41, 41))
        self.pushButton_22.setText("普通\n模式")
        self.label_26 = QLabel(self.centralwidget)
        self.label_26.setObjectName("label_26")
        self.label_26.setGeometry(QRect(200, 280, 16, 16))
        self.label_26.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_26.setText("X")

        self.label_27 = QLabel(self.centralwidget)
        self.label_27.setObjectName("label_27")
        self.label_27.setGeometry(QRect(220, 280, 16, 16))
        self.label_27.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_27.setText("Y")

        self.label_28 = QLabel(self.centralwidget)
        self.label_28.setObjectName("label_28")
        self.label_28.setGeometry(QRect(240, 280, 16, 16))
        self.label_28.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_28.setText("Z")

        self.label_33 = QLabel(self.centralwidget)
        self.label_33.setObjectName("label_33")
        self.label_33.setGeometry(QRect(80, 280, 51, 16))
        self.label_33.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_33.setText("普通模式")

        self.label_35 = QLabel(self.centralwidget)
        self.label_35.setObjectName("label_35")
        self.label_35.setGeometry(QRect(80, 300, 51, 16))
        self.label_35.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_35.setText("手轮模式")

        self.label_36 = QLabel(self.centralwidget)
        self.label_36.setObjectName("label_36")
        self.label_36.setGeometry(QRect(200, 260, 51, 20))
        self.label_36.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_36.setText("MPG轴")

        self.pushButton_23 = QPushButton(self.centralwidget)
        self.pushButton_23.setObjectName("pushButton_23")
        self.pushButton_23.setGeometry(QRect(150, 270, 41, 51))
        self.pushButton_23.setText("模拟\n手轮")
        self.pushButton_24 = QPushButton(self.centralwidget)
        self.pushButton_24.setObjectName("pushButton_24")
        self.pushButton_24.setGeometry(QRect(190, 350, 31, 31))
        self.pushButton_24.setText("0.01")

        self.pushButton_25 = QPushButton(self.centralwidget)
        self.pushButton_25.setObjectName("pushButton_25")
        self.pushButton_25.setGeometry(QRect(220, 350, 31, 31))
        self.pushButton_25.setText("0.05")

        self.pushButton_26 = QPushButton(self.centralwidget)
        self.pushButton_26.setObjectName("pushButton_26")
        self.pushButton_26.setGeometry(QRect(250, 350, 31, 31))
        self.pushButton_26.setText("0.1")

        self.pushButton_27 = QPushButton(self.centralwidget)
        self.pushButton_27.setObjectName("pushButton_27")
        self.pushButton_27.setGeometry(QRect(160, 380, 31, 31))
        self.pushButton_27.setText("0.5")

        self.pushButton_28 = QPushButton(self.centralwidget)
        self.pushButton_28.setObjectName("pushButton_28")
        self.pushButton_28.setGeometry(QRect(190, 380, 31, 31))
        self.pushButton_28.setText("1")

        self.pushButton_29 = QPushButton(self.centralwidget)
        self.pushButton_29.setObjectName("pushButton_29")
        self.pushButton_29.setGeometry(QRect(220, 380, 31, 31))
        self.pushButton_29.setText("5")

        self.pushButton_30 = QPushButton(self.centralwidget)
        self.pushButton_30.setObjectName("pushButton_30")
        self.pushButton_30.setGeometry(QRect(250, 380, 31, 31))
        self.pushButton_30.setText("10")

        self.pushButton_31 = QPushButton(self.centralwidget)
        self.pushButton_31.setObjectName("pushButton_31")
        self.pushButton_31.setGeometry(QRect(160, 350, 31, 31))
        self.pushButton_31.setText("连续")

        self.label_39 = QLabel(self.centralwidget)
        self.label_39.setObjectName("label_39")
        self.label_39.setGeometry(QRect(160, 330, 51, 16))
        self.label_39.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_39.setText("步进选择")

        self.checkBox_4 = QCheckBox(self.centralwidget)
        self.checkBox_4.setObjectName("checkBox_4")
        self.checkBox_4.setGeometry(QRect(200, 300, 16, 21))
        self.checkBox_4.setText("CheckBox")

        self.checkBox_5 = QCheckBox(self.centralwidget)
        self.checkBox_5.setObjectName("checkBox_5")
        self.checkBox_5.setGeometry(QRect(220, 300, 16, 21))
        self.checkBox_5.setText("CheckBox")

        self.checkBox_6 = QCheckBox(self.centralwidget)
        self.checkBox_6.setObjectName("checkBox_6")
        self.checkBox_6.setGeometry(QRect(240, 300, 16, 21))
        self.checkBox_6.setText("CheckBox")

        self.checkBox_7 = QCheckBox(self.centralwidget)
        self.checkBox_7.setObjectName("checkBox_7")
        self.checkBox_7.setGeometry(QRect(60, 280, 16, 16))
        self.checkBox_7.setText("CheckBox")

        self.checkBox_8 = QCheckBox(self.centralwidget)
        self.checkBox_8.setObjectName("checkBox_8")
        self.checkBox_8.setGeometry(QRect(60, 300, 16, 16))
        self.checkBox_8.setText("CheckBox")

        self.pushButton_32 = QPushButton(self.centralwidget)
        self.pushButton_32.setObjectName("pushButton_32")
        self.pushButton_32.setGeometry(QRect(10, 350, 31, 61))
        self.pushButton_32.setText("G54")

        self.label_31 = QLabel(self.centralwidget)
        self.label_31.setObjectName("label_31")
        self.label_31.setGeometry(QRect(13, 520, 21, 20))
        self.label_31.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_31.setText("Z轴")

        self.label_29 = QLabel(self.centralwidget)
        self.label_29.setObjectName("label_29")
        self.label_29.setGeometry(QRect(13, 490, 21, 20))
        self.label_29.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_29.setText("Y轴")

        self.label_32 = QLabel(self.centralwidget)
        self.label_32.setObjectName("label_32")
        self.label_32.setGeometry(QRect(13, 460, 21, 20))
        self.label_32.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_32.setText("X轴")

        self.lineEdit_18 = QLineEdit(self.centralwidget)
        self.lineEdit_18.setObjectName("lineEdit_18")
        self.lineEdit_18.setGeometry(QRect(120, 520, 71, 20))

        self.lineEdit_21 = QLineEdit(self.centralwidget)
        self.lineEdit_21.setObjectName("lineEdit_21")
        self.lineEdit_21.setGeometry(QRect(120, 490, 71, 20))

        self.lineEdit_20 = QLineEdit(self.centralwidget)
        self.lineEdit_20.setObjectName("lineEdit_20")
        self.lineEdit_20.setGeometry(QRect(40, 460, 71, 20))

        self.lineEdit_17 = QLineEdit(self.centralwidget)
        self.lineEdit_17.setObjectName("lineEdit_17")
        self.lineEdit_17.setGeometry(QRect(40, 520, 71, 20))

        self.lineEdit_16 = QLineEdit(self.centralwidget)
        self.lineEdit_16.setObjectName("lineEdit_16")
        self.lineEdit_16.setGeometry(QRect(40, 490, 71, 20))

        self.lineEdit_19 = QLineEdit(self.centralwidget)
        self.lineEdit_19.setObjectName("lineEdit_19")
        self.lineEdit_19.setGeometry(QRect(120, 460, 71, 20))

        self.label_30 = QLabel(self.centralwidget)
        self.label_30.setObjectName("label_30")
        self.label_30.setGeometry(QRect(50, 430, 41, 31))
        self.label_30.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_30.setText("机械坐标\nmachine")
        self.label_34 = QLabel(self.centralwidget)
        self.label_34.setObjectName("label_34")
        self.label_34.setGeometry(QRect(130, 430, 41, 31))
        self.label_34.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_34.setText("工件坐标\nG54")
        self.pushButton_33 = QPushButton(self.centralwidget)
        self.pushButton_33.setObjectName("pushButton_33")
        self.pushButton_33.setGeometry(QRect(40, 350, 101, 21))
        self.pushButton_33.setText("G54 设 X=0")

        self.pushButton_34 = QPushButton(self.centralwidget)
        self.pushButton_34.setObjectName("pushButton_34")
        self.pushButton_34.setGeometry(QRect(40, 370, 101, 21))
        self.pushButton_34.setText("G54 设 Y=0")

        self.pushButton_35 = QPushButton(self.centralwidget)
        self.pushButton_35.setObjectName("pushButton_35")
        self.pushButton_35.setGeometry(QRect(40, 390, 101, 21))
        self.pushButton_35.setText("G54 设 Z=0")

        self.groupBox_3 = QGroupBox(self.centralwidget)
        self.groupBox_3.setObjectName("groupBox_3")
        self.groupBox_3.setGeometry(QRect(10, 60, 261, 121))

        self.label_37 = QLabel(self.centralwidget)
        self.label_37.setObjectName("label_37")
        self.label_37.setGeometry(QRect(10, 330, 61, 16))
        self.label_37.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_37.setText("G54对刀")

        self.label_38 = QLabel(self.centralwidget)
        self.label_38.setObjectName("label_38")
        self.label_38.setGeometry(QRect(10, 260, 51, 20))
        self.label_38.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_38.setText("模式切换")

        self.groupBox_4 = QGroupBox(self.centralwidget)
        self.groupBox_4.setObjectName("groupBox_4")
        self.groupBox_4.setGeometry(QRect(10, 180, 261, 71))

        self.label_40 = QLabel(self.centralwidget)
        self.label_40.setObjectName("label_40")
        self.label_40.setGeometry(QRect(380, 70, 101, 16))
        self.label_40.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_40.setText("Z- 刀具远离工件")

        self.label_41 = QLabel(self.centralwidget)
        self.label_41.setObjectName("label_41")
        self.label_41.setGeometry(QRect(410, 160, 101, 16))
        self.label_41.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_41.setText("x- 加工台右移")

        self.label_42 = QLabel(self.centralwidget)
        self.label_42.setObjectName("label_42")
        self.label_42.setGeometry(QRect(340, 230, 121, 16))
        self.label_42.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_42.setText("y+ 加工台靠近操作者")

        self.label_23 = QLabel(self.groupBox_7)
        self.label_23.setObjectName("label_23")
        self.label_23.setGeometry(QRect(10, 10, 51, 31))
        self.label_23.setStyleSheet("border: 1px solid black;")
        self.label_23.setText("Gcode：")

        self.pushButton_19 = QPushButton(self.groupBox_7)
        self.pushButton_19.setObjectName("pushButton_19")
        self.pushButton_19.setGeometry(QRect(230, 10, 81, 31))
        self.pushButton_19.setStyleSheet("color: green;")
        self.pushButton_19.setText("run")

        self.lineEdit_13 = QPlainTextEdit(self.groupBox_7)
        self.lineEdit_13.setObjectName("lineEdit_13")
        self.lineEdit_13.setGeometry(QRect(0, 42, 331, 539))

        self.label_24 = QLabel(self.groupBox_8)
        self.label_24.setObjectName("label_24")
        self.label_24.setGeometry(QRect(20, 10, 41, 31))
        self.label_24.setStyleSheet("border: 1px solid black;")
        self.label_24.setText("日志：")

        self.lineEdit_14 = QTextEdit(self.groupBox_8)
        self.lineEdit_14.setObjectName("lineEdit_14")
        self.lineEdit_14.setGeometry(QRect(10, 50, 221, 521))
        self.lineEdit_14.setReadOnly(True)

        self.pushButton_21 = QPushButton(self.groupBox_8)
        self.pushButton_21.setObjectName("pushButton_21")
        self.pushButton_21.setGeometry(QRect(140, 10, 81, 31))
        self.pushButton_21.setStyleSheet("color: green;")
        self.pushButton_21.setText("导出")

        menubar = QMenuBar(MainWindow)
        menubar.setObjectName("menubar")
        menubar.setGeometry(QRect(0, 0, 1163, 22))
        self.menu = menubar.addMenu("磁悬浮铣削电主轴上位机程序")
        self.menuV1_0 = menubar.addMenu("V2.0")
        MainWindow.setMenuBar(menubar)

        statusbar = QStatusBar(MainWindow)
        statusbar.setObjectName("statusbar")
        MainWindow.setStatusBar(statusbar)

        # ====== Aliases + 自定义控件（main_ui2.py 引用） ======

        # 装饰性 groupbox 对鼠标事件透明（groupBox_7=Gcode区、groupBox_8=日志区保留交互）
        for _gb_name in ("groupBox_2", "groupBox_3", "groupBox_4"):
            _gb = getattr(self, _gb_name, None)
            if _gb is not None:
                _gb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # 9 个数据展示框：只读 + 居中
        for _i, _n in enumerate(("lineEdit_4","lineEdit_5","lineEdit_6",
                                 "lineEdit_9","lineEdit_8","lineEdit_7",
                                 "lineEdit_12","lineEdit_11","lineEdit_10")):
            _le = getattr(self, _n, None)
            if _le is not None:
                _le.setReadOnly(True)
                _le.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 输入框默认值 0
        for _n in ("lineEdit", "lineEdit_2", "lineEdit_3"):
            _le = getattr(self, _n, None)
            if _le is not None:
                _le.setText("0")

        # 机械坐标/工件坐标 6 个 LineEdit 全部只读（main_ui2.py 周期性写入）
        for _n in ("lineEdit_16","lineEdit_17","lineEdit_18",
                   "lineEdit_19","lineEdit_20","lineEdit_21"):
            _le = getattr(self, _n, None)
            if _le is not None:
                _le.setReadOnly(True)
                _le.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 三色状态灯（急停/恢复之间，简洁版：小圆点+文字+三色图例一行）
        self.StatusLight = QLabel(self.centralwidget)
        self.StatusLight.setObjectName("StatusLight")
        self.StatusLight.setGeometry(QRect(510, 410, 20, 20))
        self.StatusLight.setStyleSheet(
            "background-color: #FFD700; border-radius: 10px; border: 1px solid #888;")

        self.StatusLabel = QLabel("未使能", self.centralwidget)
        self.StatusLabel.setObjectName("StatusLabel")
        self.StatusLabel.setGeometry(QRect(534, 410, 40, 20))
        self.StatusLabel.setStyleSheet(
            "font: 8pt 'Microsoft YaHei'; color: #222;")

        # 三色含义备注（单行 + 三个小圆点）
        self.LegendLabel = QLabel(
            "<span style='color:#e6a800;'>●</span>未使能  "
            "<span style='color:#2ea84f;'>●</span>使能  "
            "<span style='color:#d12f2f;'>●</span>急停",
            self.centralwidget)
        self.LegendLabel.setObjectName("LegendLabel")
        self.LegendLabel.setGeometry(QRect(510, 432, 61, 30))
        self.LegendLabel.setStyleSheet("font: 7pt 'Microsoft YaHei'; color: #222;")
        self.LegendLabel.setWordWrap(True)

        # 默认勾选普通模式 + MPG 轴 X
        self.checkBox_7.setChecked(True)
        self.checkBox_8.setChecked(False)
        self.checkBox_4.setChecked(True)
        self.checkBox_5.setChecked(False)
        self.checkBox_6.setChecked(False)

        # 步进选择互斥：默认 0.01 高亮
        # 连续按钮：默认未选中
        self.pushButton_24.setStyleSheet("background-color: #4a9eff; color: white; font-weight: bold;")  # 0.01 高亮

        # ====== 业务对象名别名（与 main3.py 兼容，main_ui2.py 引用） ======
        # 顶栏
        self.Search = self.pushButton
        self.iplist = self.comboBox
        self.Connect = self.pushButton_5
        self.InitZMC = self.pushButton_10
        # 数据展示
        self.X_N_Limit = self.lineEdit_4
        self.X_Current = self.lineEdit_5
        self.X_P_Limit = self.lineEdit_6
        self.Y_N_Limit = self.lineEdit_9
        self.Y_Current = self.lineEdit_8
        self.Y_P_Limit = self.lineEdit_7
        self.Z_N_Limit = self.lineEdit_12
        self.Z_Current = self.lineEdit_11
        self.Z_P_Limit = self.lineEdit_10
        # 手动操作
        self.Ax_enable = self.pushButton_13
        self.Move_Type = self.pushButton_14
        self.GoHome = self.pushButton_15
        self.Reset_CSys = self.pushButton_16
        self.GoZero = self.pushButton_17
        # 方向键
        self.X_P_B = self.pushButton_2    # X+
        self.Y_N_B = self.pushButton_3    # Y-
        self.X_N_B = self.pushButton_4    # X-
        self.Z_N_B = self.pushButton_7    # Z- (原 pushButton_6)
        self.Z_P_B = self.pushButton_6    # Z+ (原 pushButton_7)
        self.Y_P_B = self.pushButton_8    # Y+
        # 距离 + 主轴 + Gcode + 日志
        self.DMove_dist = self.lineEdit
        self.DZZ_r_data = self.lineEdit_3
        self.DZZ_Run_B = self.pushButton_9
        self.Gcode_run = self.pushButton_19
        self.GCode_text = self.lineEdit_13
        self.Log_text = self.lineEdit_14
        self.Log_export = self.pushButton_21
        # 急停 / 恢复
        self.EStop_Btn = self.pushButton_11
        self.Resume_Btn = self.pushButton_18
        # 备用
        self.by = QLineEdit(self.centralwidget)
        self.by.setObjectName("by")
        self.by.setVisible(False)

        # 电主轴转速显示（隐藏字段，给 Dzz_zs 用）
        self.Disp_Dzz_zs = QLineEdit(self.centralwidget)
        self.Disp_Dzz_zs.setObjectName("Disp_Dzz_zs")
        self.Disp_Dzz_zs.setGeometry(QRect(0, 0, 1, 1))
        self.Disp_Dzz_zs.setReadOnly(True)
        self.Disp_Dzz_zs.setVisible(False)
        # （不隐藏 label_3——它是 3D 坐标图）

        # 机械/工件 6 个 LineEdit 默认显示（值在连上 ZMC 后由 worker 周期性写入）
        for _n in ("lineEdit_16","lineEdit_17","lineEdit_18",
                   "lineEdit_19","lineEdit_20","lineEdit_21"):
            _le = getattr(self, _n, None)
            if _le is not None:
                _le.setText("0.000")
                _le.setVisible(True)
        # 隐藏「G54」主标签和设零提示（保留设零按钮即可）
        for _n in ("label_37",):
            _lbl = getattr(self, _n, None)
            if _lbl is not None:
                _lbl.setVisible(False)

        # 6 个标签的字体大小改为 6（机械坐标/工件坐标 + 4 加工台方向说明）
        _f6 = QFont()
        _f6.setPointSize(6)
        for _n in ("label_13", "label_14", "label_15", "label_16",
                   "label_30", "label_34"):
            _lbl = getattr(self, _n, None)
            if _lbl is not None:
                _lbl.setFont(_f6)

        # 模式复选框（checkBox_7/8）只读：由 pushButton_22 控制，用户不可直接点击
        for _n in ("checkBox_7", "checkBox_8"):
            _cb = getattr(self, _n, None)
            if _cb is not None:
                _cb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # MPG 轴复选框（checkBox_4/5/6）可由用户点击，不做透明拦截

        MainWindow.setCentralWidget(self.centralwidget)

        QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        pass


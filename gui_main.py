#gui_main.py
import sys
import os
import time
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QComboBox, QCheckBox, QFileDialog, 
                             QGroupBox, QSpinBox, QProgressBar, QMessageBox, QListWidget,
                             QTabWidget, QSplitter)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont
# --- 导入你现有项目中的模块 ---
try:
    from docx_processor import translate_docx 
    from md_processor import translate_markdown 
    from latex_processor import translate_latex_project  # 新增
    from translation import llm_translate_concurrent 
    from translation_md import llm_translate_markdown
    from constants import (CUSTOM_SIZE_MAP_ZH_TO_EN, CUSTOM_SIZE_MAP_EN_TO_ZH, 
                          DEFAULT_SIZE_MAP_ZH_TO_EN, DEFAULT_SIZE_MAP_EN_TO_ZH)
except ImportError as e: 
    print(f"Error importing modules: {e}") 
    sys.exit(1)

# --- 日志重定向类 ---
class Stream(QObject):
    newText = pyqtSignal(str)

    def write(self, text):
        self.newText.emit(str(text))

    def flush(self):
        pass
# --- 工作线程 (防止界面卡死) ---
class TranslationWorker(QThread):
    finished = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, params):
        super().__init__()  # 调用 QThread 的 __init__，不传递 params
        self.params = params  # 将 params 作为实例属性保存
    
    def run(self):
        try:
            # 提取参数
            input_file = self.params['input_file']
            output_file = self.params['output_file']
            direction = self.params['direction']
            model = self.params['model']
            api_base = self.params['api_base']
            api_key = self.params['api_key']
            workers = self.params['workers']
            file_type = self.params['file_type']
            
            # 确定文件类型
            file_ext = os.path.splitext(input_file)[1].lower()
            
            # 通用翻译参数
            translate_kwargs = dict(
                source_lang='Chinese' if direction == 'zh-to-en' else 'English',
                target_lang='English' if direction == 'zh-to-en' else 'Chinese',
                model=model,
                api_base=api_base,
                api_key=api_key,
                max_workers=workers,
                timeout=180,
                max_retries=3,
                interval=0.4
            )
            
            print(f"正在处理: {os.path.basename(input_file)}...")
            
            if file_ext == '.docx':
                # 处理 DOCX（需要字体设置）
                font_latin = self.params.get('font_latin', '等线')
                font_ea = self.params.get('font_east_asia', '等线')
                use_modern_font = self.params.get('use_modern_font_table', True)
                font_size_profile = self.params.get('font_size_profile', 'default')
                
                # 构建 size_map
                size_map = None
                if font_size_profile != "none":
                    if font_size_profile == "custom":
                        if direction == 'zh-to-en':
                            size_map = CUSTOM_SIZE_MAP_ZH_TO_EN
                        else:
                            size_map = CUSTOM_SIZE_MAP_EN_TO_ZH
                    else:  # default
                        if direction == 'zh-to-en':
                            size_map = DEFAULT_SIZE_MAP_ZH_TO_EN
                        else:
                            size_map = DEFAULT_SIZE_MAP_EN_TO_ZH
                
                style_profile = {
                    "font_latin": font_latin,
                    "font_east_asia": font_ea,
                    "lang_latin": "en-US" if direction == "zh-to-en" else "zh-CN",
                    "lang_ea": "zh-CN",
                    "size_map": size_map
                }
                
                kwargs_for_process = {
                    "translate_function": llm_translate_concurrent,
                    "translate_kwargs": translate_kwargs,
                    "style_mode": "runs",
                    "style_profile": style_profile,
                    "line_spacing_half": None,
                    "map_math_size": False,
                }
                
                translate_docx(
                    input_docx_path=input_file,
                    output_docx_path=output_file,
                    use_modern_font_table=use_modern_font,
                    custom_styles_path=None,
                    materialize_styles=True,
                    debug_materialize=False,
                    **kwargs_for_process
                )
                
            elif file_ext == '.md':
                # 处理 Markdown（使用专用翻译函数）
                translate_markdown(
                    input_md_path=input_file,
                    output_md_path=output_file,
                    translate_function=llm_translate_markdown,
                    translate_kwargs=translate_kwargs
                )
            
            elif file_ext == '.tex':
                # 🆕 提取样式文件选项
                translate_style = self.params.get('translate_style_files', False)
                
                # 处理 LaTeX 项目
                translate_latex_project(
                    input_main_file=input_file,
                    output_dir=output_file,
                    translate_function=llm_translate_markdown,
                    translate_kwargs=translate_kwargs,
                    translate_style_files=translate_style  # 🆕 传递参数
                )
            
            else:
                raise ValueError(f"不支持的文件格式: {file_ext}")
            
            self.finished.emit(True, f"成功: {output_file}")
            
        except Exception as e:
            import traceback
            error_msg = traceback.format_exc()
            print(error_msg)
            self.finished.emit(False, str(e))

# --- 主界面 ---
class TranslatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LLM Document Translator Pro")
        self.resize(1000, 800)
        self.setAcceptDrops(True)

        # 初始化UI
        self.init_ui()
        
        # 重定向 stdout 到 UI 日志
        sys.stdout = Stream(newText=self.on_update_log)
        sys.stderr = Stream(newText=self.on_update_log)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # 1. API 设置区域（通用）
        api_group = QGroupBox("API Configuration (通用配置)")
        api_layout = QVBoxLayout()
        
        # Base URL
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("API Base:"))
        self.input_api_base = QLineEdit()
        # 尝试从环境变量或配置文件加载，这里留空，避免打包泄露
        self.input_api_base.setText("") 
        url_layout.addWidget(self.input_api_base)
        
        # API Key
        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel("API Key:"))
        self.input_api_key = QLineEdit()
        # 尝试从环境变量或配置文件加载，这里留空，避免打包泄露
        self.input_api_key.setText("")
        self.input_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self.input_api_key)

        # # Base URL
        # url_layout = QHBoxLayout()
        # url_layout.addWidget(QLabel("API Base:"))
        # self.input_api_base = QLineEdit()
        # self.input_api_base.setText("http://8.138.249.222:4001/v1")
        # url_layout.addWidget(self.input_api_base)
        
        # # API Key
        # key_layout = QHBoxLayout()
        # key_layout.addWidget(QLabel("API Key:"))
        # self.input_api_key = QLineEdit()
        # self.input_api_key.setText("sk-h8TnQlFxA7j3Kba2AEI1ZhFPrq7HB7Rnqhf2kmUn2s4xhiIB")
        # self.input_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        # key_layout.addWidget(self.input_api_key)
        # Model
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel("Model:"))
        self.input_model = QLineEdit()
        self.input_model.setText("gpt-4o-mini")
        model_layout.addWidget(self.input_model)

        api_layout.addLayout(url_layout)
        api_layout.addLayout(key_layout)
        api_layout.addLayout(model_layout)
        api_group.setLayout(api_layout)
        main_layout.addWidget(api_group)

        # 2. 翻译参数设置（按类型分标签）
        settings_tab = QTabWidget()
        
        word_settings = self.create_word_settings()
        settings_tab.addTab(word_settings, "Word (.docx) 设置")
        
        md_settings = self.create_markdown_settings()
        settings_tab.addTab(md_settings, "Markdown (.md) 设置")
        
        latex_settings = self.create_latex_settings()  # 新增
        settings_tab.addTab(latex_settings, "LaTeX (.tex) 设置")
        
        main_layout.addWidget(settings_tab)    

        # 3. 文件列表区域
        file_group = QGroupBox("Files (Drag & Drop Supported)")
        file_layout = QVBoxLayout()
        
        self.file_list = QListWidget()
        self.file_list.setAcceptDrops(True)
        self.file_list.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.file_list.setToolTip("请将 .docx 或 .md 文件拖入此处")
        
        btn_layout = QHBoxLayout()
        self.btn_add_file = QPushButton("选择文件...")
        self.btn_add_file.clicked.connect(self.browse_files)
        self.btn_clear_list = QPushButton("清空列表")
        self.btn_clear_list.clicked.connect(self.file_list.clear)
        
        btn_layout.addWidget(self.btn_add_file)
        btn_layout.addWidget(self.btn_clear_list)
        
        file_layout.addWidget(self.file_list)
        file_layout.addLayout(btn_layout)
        file_group.setLayout(file_layout)
        main_layout.addWidget(file_group)

        # 4. 开始按钮
        self.btn_start = QPushButton("开始翻译 (Start Translation)")
        self.btn_start.setFixedHeight(50)
        self.btn_start.setStyleSheet("font-size: 16px; font-weight: bold; background-color: #4CAF50; color: white;")
        self.btn_start.clicked.connect(self.start_translation_queue)
        main_layout.addWidget(self.btn_start)

        # 5. 日志窗口
        log_group = QGroupBox("Console Log")
        log_layout = QVBoxLayout()
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas;")
        log_layout.addWidget(self.text_log)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)
        settings_tab = QTabWidget()

    # ===== Word 设置标签 =====
    def create_word_settings(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 翻译方向
        direction_layout = QHBoxLayout()
        direction_layout.addWidget(QLabel("翻译方向:"))
        self.combo_direction_word = QComboBox()
        self.combo_direction_word.addItems(["中文 -> 英文 (zh-to-en)", "英文 -> 中文 (en-to-zh)"])
        direction_layout.addWidget(self.combo_direction_word)
        layout.addLayout(direction_layout)

        # 字体设置
        font_group = QGroupBox("字体设置")
        font_layout = QVBoxLayout()
        
        font_row1 = QHBoxLayout()
        font_row1.addWidget(QLabel("英文字体:"))
        self.input_font_latin = QLineEdit("等线")
        font_row1.addWidget(self.input_font_latin)
        
        font_row2 = QHBoxLayout()
        font_row2.addWidget(QLabel("中文字体:"))
        self.input_font_ea = QLineEdit("等线")
        font_row2.addWidget(self.input_font_ea)
        
        font_layout.addLayout(font_row1)
        font_layout.addLayout(font_row2)
        font_group.setLayout(font_layout)
        layout.addWidget(font_group)

        # 其他选项
        opts_group = QGroupBox("其他选项")
        opts_layout = QVBoxLayout()
        
        self.check_modern_font = QCheckBox("使用现代字体表 (Inject Modern Font Table)")
        self.check_modern_font.setChecked(True)
        opts_layout.addWidget(self.check_modern_font)

        workers_layout = QHBoxLayout()
        workers_layout.addWidget(QLabel("并发线程数:"))
        self.spin_workers_word = QSpinBox()
        self.spin_workers_word.setRange(1, 20)
        self.spin_workers_word.setValue(1)
        workers_layout.addWidget(self.spin_workers_word)
        opts_layout.addLayout(workers_layout)

        opts_group.setLayout(opts_layout)
        layout.addWidget(opts_group)
        
        layout.addStretch()
        return widget

    # ===== Markdown 设置标签 =====
    def create_markdown_settings(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 翻译方向
        direction_layout = QHBoxLayout()
        direction_layout.addWidget(QLabel("翻译方向:"))
        self.combo_direction_md = QComboBox()
        self.combo_direction_md.addItems(["中文 -> 英文 (zh-to-en)", "英文 -> 中文 (en-to-zh)"])
        direction_layout.addWidget(self.combo_direction_md)
        layout.addLayout(direction_layout)

        # Markdown 专用选项
        md_group = QGroupBox("Markdown 选项")
        md_layout = QVBoxLayout()
        
        info_label = QLabel("💡 Markdown 不需要字体和字体大小设置，这些将被忽略。")
        info_label.setStyleSheet("color: #ff9800; font-weight: bold;")
        md_layout.addWidget(info_label)
        
        workers_layout = QHBoxLayout()
        workers_layout.addWidget(QLabel("并发线程数:"))
        self.spin_workers_md = QSpinBox()
        self.spin_workers_md.setRange(1, 20)
        self.spin_workers_md.setValue(1)
        workers_layout.addWidget(self.spin_workers_md)
        md_layout.addLayout(workers_layout)
        
        md_group.setLayout(md_layout)
        layout.addWidget(md_group)
        
        layout.addStretch()
        return widget
    def on_style_option_toggled(self, checked):
        """当样式文件选项被切换时显示/隐藏警告"""
        self.label_style_warning.setVisible(checked)
        
        if checked:
            # 弹出二次确认对话框
            reply = QMessageBox.question(
                self,
                "确认翻译样式文件",
                "⚠️ 翻译样式文件可能导致：\n\n"
                "• 格式定义被破坏\n"
                "• LaTeX 编译错误\n"
                "• 排版完全混乱\n\n"
                "通常只有当样式文件包含大量中文注释时才需要翻译。\n\n"
                "❓ 确定要启用吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.No:
                # 用户选择"否"，恢复为不翻译
                self.check_translate_style_files.setChecked(False)
    def create_latex_settings(self):
        """创建 LaTeX 设置标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 翻译方向
        direction_layout = QHBoxLayout()
        direction_layout.addWidget(QLabel("翻译方向:"))
        self.combo_direction_latex = QComboBox()
        self.combo_direction_latex.addItems(["中文 -> 英文 (zh-to-en)", "英文 -> 中文 (en-to-zh)"])
        direction_layout.addWidget(self.combo_direction_latex)
        layout.addLayout(direction_layout)
        
        # LaTeX 项目信息
        info_group = QGroupBox("项目信息")
        info_layout = QVBoxLayout()
        
        info_text = QLabel(
            "💡 使用说明：\n"
            "1️⃣ 选择项目的主 .tex 文件（如 scutthesis.tex）\n"
            "2️⃣ 程序将自动发现并翻译所有被 \\include 和 \\input 的子文件\n"
            "3️⃣ 英译中时会自动修改 documentclass 为 ctexart 并添加中文支持\n"
            "4️⃣ 图片、参考文献等资源文件将被自动复制\n\n"
            "⚠️ 注意：\n"
            "• LaTeX 命令（\\cite, \\ref 等）和数学公式将被保护\n"
            "• 翻译后的项目会存放在新目录中"
        )
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #ff9800; background-color: #fff3e0; padding: 10px; border-radius: 5px;")
        info_layout.addWidget(info_text)
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # ========== 🆕 样式文件翻译选项 ==========
        style_group = QGroupBox("样式文件处理")
        style_layout = QVBoxLayout()
        
        # 复选框
        self.check_translate_style_files = QCheckBox("翻译样式文件（.cls/.sty/.bst）")
        self.check_translate_style_files.setChecked(False)  # 默认不翻译
        self.check_translate_style_files.toggled.connect(self.on_style_option_toggled)
        style_layout.addWidget(self.check_translate_style_files)
        
        # 警告提示
        self.label_style_warning = QLabel(
            "⚠️ 警告：样式文件包含格式定义，翻译可能导致：\n"
            "   • 格式定义被破坏\n"
            "   • 编译错误\n"
            "   • 排版异常\n\n"
            "💡 建议：仅当样式文件包含大量中文注释时才启用此选项。\n"
            "   通常情况下，保持默认（不翻译）即可。"
        )
        self.label_style_warning.setWordWrap(True)
        self.label_style_warning.setStyleSheet(
            "color: #ff5722; "
            "background-color: #ffebee; "
            "padding: 10px; "
            "border-left: 4px solid #ff5722; "
            "border-radius: 3px; "
            "font-size: 11px;"
        )
        self.label_style_warning.setVisible(False)  # 默认隐藏
        style_layout.addWidget(self.label_style_warning)
        
        style_group.setLayout(style_layout)
        layout.addWidget(style_group)
        # ==========================================
        
        # 其他翻译选项
        opts_group = QGroupBox("翻译选项")
        opts_layout = QVBoxLayout()
        
        workers_layout = QHBoxLayout()
        workers_layout.addWidget(QLabel("并发线程数:"))
        self.spin_workers_latex = QSpinBox()
        self.spin_workers_latex.setRange(1, 10)
        self.spin_workers_latex.setValue(1)
        workers_layout.addWidget(self.spin_workers_latex)
        opts_layout.addLayout(workers_layout)
        
        opts_group.setLayout(opts_layout)
        layout.addWidget(opts_group)
        
        layout.addStretch()
        return widget

    # --- 拖拽事件处理 ---
       # --- 拖拽事件处理 ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        self.add_files_to_list(files)

    def browse_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, 
            "Select Files to Translate", 
            "", 
            "All Supported (*.docx *.md *.tex);;Word Documents (*.docx);;Markdown Files (*.md);;LaTeX Files (*.tex);;All Files (*.*)"
        )
        if files:
            self.add_files_to_list(files)


    def add_files_to_list(self, file_paths):
        """添加文件到列表，支持 .docx, .md, .tex"""
        for path in file_paths:
            ext = os.path.splitext(path)[1].lower()
            if ext in [".docx", ".md", ".tex"]:
                self.file_list.addItem(path)
            elif ext in [".pptx", ".ppt"]:
                QMessageBox.information(self, "Coming Soon", 
                    f"检测到 {ext} 格式。\n目前仅支持 .docx、.md 和 .tex，未来版本将支持此格式！")
            else:
                self.text_log.append(f"[Warning] 不支持的文件格式: {path}")



    # --- 逻辑处理 ---
    def on_update_log(self, text):
        cursor = self.text_log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.text_log.setTextCursor(cursor)
        self.text_log.ensureCursorVisible()

    def start_translation_queue(self):
        if self.file_list.count() == 0:
            QMessageBox.warning(self, "Warning", "请先添加文件！")
            return

        # 锁定界面
        self.btn_start.setEnabled(False)
        self.files_to_process = [self.file_list.item(i).text() for i in range(self.file_list.count())]
        self.current_file_index = 0
        
        self.process_next_file()

    def process_next_file(self):
        """处理队列中的下一个文件"""
        if self.current_file_index >= len(self.files_to_process):
            self.btn_start.setEnabled(True)
            QMessageBox.information(self, "Finished", "所有文件处理完毕！")
            return
        
        input_path = self.files_to_process[self.current_file_index]
        file_ext = os.path.splitext(input_path)[1].lower()
        
        dirname = os.path.dirname(input_path)
        basename = os.path.splitext(os.path.basename(input_path))[0]
        ext = os.path.splitext(input_path)[1]
        
        
        if file_ext == '.docx':
            direction_str = "zh-to-en" if "zh-to-en" in self.combo_direction_word.currentText() else "en-to-zh"
            workers = self.spin_workers_word.value()
            model = self.input_model.text()
            output_path = os.path.join(dirname, f"{basename}_translated_{direction_str}{ext}")
            
            params = {
                "input_file": input_path,
                "output_file": output_path,
                "direction": direction_str,
                "model": model,
                "api_base": self.input_api_base.text(),
                "api_key": self.input_api_key.text(),
                "workers": workers,
                "file_type": file_ext,
                "font_latin": self.input_font_latin.text(),
                "font_east_asia": self.input_font_ea.text(),
                "use_modern_font_table": self.check_modern_font.isChecked(),
                "font_size_profile": "default"
            }
            
        elif file_ext == '.md':
            direction_str = "zh-to-en" if "zh-to-en" in self.combo_direction_md.currentText() else "en-to-zh"
            workers = self.spin_workers_md.value()
            model = self.input_model.text()
            output_path = os.path.join(dirname, f"{basename}_translated_{direction_str}{ext}")
            
            params = {
                "input_file": input_path,
                "output_file": output_path,
                "direction": direction_str,
                "model": model,
                "api_base": self.input_api_base.text(),
                "api_key": self.input_api_key.text(),
                "workers": workers,
                "file_type": file_ext,
            }
            
        elif file_ext == '.tex':  # 新增 LaTeX 处理
            direction_str = "zh-to-en" if "zh-to-en" in self.combo_direction_latex.currentText() else "en-to-zh"
            workers = self.spin_workers_latex.value()
            model = self.input_model.text()
            # LaTeX 输出是一个目录
            output_path = os.path.join(dirname, f"{basename}_translated_{direction_str}")
            
            params = {
                "input_file": input_path,
                "output_file": output_path,
                "direction": direction_str,
                "model": model,
                "api_base": self.input_api_base.text(),
                "api_key": self.input_api_key.text(),
                "workers": workers,
                "file_type": file_ext,
                "translate_style_files": self.check_translate_style_files.isChecked(),  # 🆕 传递选项

            }
        
        else:
            raise ValueError(f"不支持的文件格式: {file_ext}")
        
        print(f"\n{'='*20} 开始处理第 {self.current_file_index + 1}/{len(self.files_to_process)} 个文件 {'='*20}")
        print(f"文件类型: {file_ext}")
        print(f"翻译方向: {direction_str}")
        
        self.worker = TranslationWorker(params)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()


    def on_worker_finished(self, success, message):
        if success:
            print(f"✅ 完成: {message}")
        else:
            print(f"❌ 失败: {message}")
        
        self.current_file_index += 1
        self.process_next_file()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = TranslatorApp()
    window.show()
    sys.exit(app.exec())

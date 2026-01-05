# constants.py
from typing import Dict

# ======================== 全局常量 ========================

# XML 命名空间
NAMESPACES: Dict[str, str] = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'xml': 'http://www.w3.org/XML/1998/namespace' # <--- 添加这一行

}

# 文本翻译时的占位符
PLACEHOLDER_TAG: str = "<placeholder>"

# 日志文件名
LOG_FILE: str = 'translation_errors.log'
AUDIT_LOG_FILE: str = 'translation_audit.log'

# 现代化的 fontTable.xml 内容
MODERN_FONT_TABLE_XML: str = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:fonts xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" xmlns:w16se="http://schemas.microsoft.com/office/word/2015/wordml/symex" mc:Ignorable="w14 w15 w16se">
<w:font w:name="Times New Roman"><w:panose1 w:val="02020603050405020304"/><w:charset w:val="00"/><w:family w:val="roman"/><w:pitch w:val="variable"/><w:sig w:usb0="E0002EFF" w:usb1="C000785B" w:usb2="00000009" w:usb3="00000000" w:csb0="000001FF" w:csb1="00000000"/></w:font>
<w:font w:name="宋体"><w:altName w:val="SimSun"/><w:panose1 w:val="02010600030101010101"/><w:charset w:val="86"/><w:family w:val="auto"/><w:pitch w:val="variable"/><w:sig w:usb0="00000003" w:usb1="288F0000" w:usb2="00000016" w:usb3="00000000" w:csb0="00040001" w:csb1="00000000"/></w:font>
<w:font w:name="Cambria"><w:panose1 w:val="02040503050406030204"/><w:charset w:val="00"/><w:family w:val="roman"/><w:pitch w:val="variable"/><w:sig w:usb0="E00002FF" w:usb1="400004FF" w:usb2="00000000" w:usb3="00000000" w:csb0="0000019F" w:csb1="00000000"/></w:font>
<w:font w:name="Arial"><w:panose1 w:val="020B0604020202020204"/><w:charset w:val="00"/><w:family w:val="swiss"/><w:pitch w:val="variable"/><w:sig w:usb0="E0002EFF" w:usb1="C0007843" w:usb2="00000009" w:usb3="00000000" w:csb0="000001FF" w:csb1="00000000"/></w:font>
<w:font w:name="Calibri"><w:panose1 w:val="020F0502020204030204"/><w:charset w:val="00"/><w:family w:val="swiss"/><w:pitch w:val="variable"/><w:sig w:usb0="E0002AFF" w:usb1="C000247B" w:usb2="00000009" w:usb3="00000000" w:csb0="000001FF" w:csb1="00000000"/></w:font>
<w:font w:name="Consolas"><w:panose1 w:val="020B0609020204030204"/><w:charset w:val="00"/><w:family w:val="modern"/><w:pitch w:val="default"/><w:sig w:usb0="E00006FF" w:usb1="0000FCFF" w:usb2="00000001" w:usb3="00000000" w:csb0="6000019F" w:csb1="DFD70000"/></w:font>
<w:font w:name="等线"><w:altName w:val="DengXian"/><w:panose1 w:val="02010600030101010101"/><w:charset w:val="86"/><w:family w:val="auto"/><w:pitch w:val="variable"/><w:sig w:usb0="A00002BF" w:usb1="38CF7CFA" w:usb2="00000016" w:usb3="00000000" w:csb0="0004000F" w:csb1="00000000"/></w:font>
<w:font w:name="Cambria Math"><w:panose1 w:val="02040503050406030204"/><w:charset w:val="00"/><w:family w:val="roman"/><w:pitch w:val="variable"/><w:sig w:usb0="E00002FF" w:usb1="420024FF" w:usb2="00000000" w:usb3="00000000" w:csb0="0000019F" w:csb1="00000000"/></w:font>
</w:fonts>
"""

# ======================== 字号映射表 ========================
DEFAULT_SIZE_MAP_ZH_TO_EN: Dict[int, int] = { 32: 36, 28: 32, 24: 24, 21: 22 }
DEFAULT_SIZE_MAP_EN_TO_ZH: Dict[int, int] = {v: k for k, v in DEFAULT_SIZE_MAP_ZH_TO_EN.items()}
CUSTOM_SIZE_MAP_ZH_TO_EN: Dict[int, int] = { 32: 27, 28: 23, 24: 20, 21: 16 }
CUSTOM_SIZE_MAP_EN_TO_ZH: Dict[int, int] = {v: k for k, v in CUSTOM_SIZE_MAP_ZH_TO_EN.items()}

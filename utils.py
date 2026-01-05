# utils.py
import xml.etree.ElementTree as ET
from constants import NAMESPACES
def is_meaningful_text(s: str) -> bool: 
    return s and s.strip() and any(char.isalnum() for char in s)
def clean_llm_output(text: str) -> str:
    if not text: return ""
    s = text.strip(); s = s.strip(' "\'""`')
    return s
def ensure_rpr_first_in_run(r_elem: ET.Element) -> None:
    """
    确保 <w:rPr> 元素始终是 <w:r> 的第一个子元素（Word 格式要求）。
    
    Args:
        r_elem: <w:r> 元素
    """
    rPr = r_elem.find('w:rPr', NAMESPACES)
    if rPr is not None and r_elem[0] != rPr:
        # rPr 不在第一位，需要移动
        r_elem.remove(rPr)
        r_elem.insert(0, rPr)

def save_debug_document(root: ET.Element, debug_filename: str, step_name: str):
    """
    保存调试用的 XML 文件，并格式化输出便于查看。
    
    Args:
        root: XML 根元素
        debug_filename: 输出文件名
        step_name: 步骤名称（用于日志）
    """
    try:
        # 格式化 XML 以便阅读
        xml_str = ET.tostring(root, encoding='utf-8')
        
        # 美化 XML（添加缩进）
        import xml.dom.minidom as minidom
        dom = minidom.parseString(xml_str)
        pretty_xml = dom.toprettyxml(indent="  ")
        
        # 移除空行
        pretty_xml = '\n'.join([line for line in pretty_xml.split('\n') if line.strip()])
        
        with open(debug_filename, 'w', encoding='utf-8') as f:
            f.write(pretty_xml)
        
        print(f"\n[DEBUG] {step_name} saved to: {debug_filename}")
        print(f"[DEBUG] File size: {len(pretty_xml)} bytes")
    except Exception as e:
        print(f"[DEBUG ERROR] Failed to save debug file: {e}")
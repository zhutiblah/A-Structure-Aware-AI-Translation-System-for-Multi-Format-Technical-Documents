# style_manager.py
import xml.etree.ElementTree as ET
from typing import Optional, Dict, List, Any
from constants import NAMESPACES
from config import error_logger

# Helper function to deep copy an ElementTree element.
# This is crucial when merging rPr elements, to avoid modifying original elements.
def _copy_element(element: ET.Element) -> ET.Element:
    new_element = ET.Element(element.tag, element.attrib)
    new_element.text = element.text
    new_element.tail = element.tail
    for child in element:
        new_element.append(_copy_element(child))
    return new_element

# ======================== 升级的 StyleManager 类 ========================
class StyleManager:
    """
    一个用于解析和管理 word/styles.xml 的类。
    支持样式继承链追溯，能够找到最终的字号定义和其他字符属性。
    """
    def __init__(self, styles_xml_bytes: bytes):
        self._styles: Dict[str, Dict[str, Any]] = {} # 存储解析后的样式信息
        self._default_size = None
        self._style_names: Dict[str, str] = {}
        if not styles_xml_bytes:
            return
            
        try:
            root = ET.fromstring(styles_xml_bytes)
            w_styleId = f"{{{NAMESPACES['w']}}}styleId"
            w_val = f"{{{NAMESPACES['w']}}}val"
            
            # 第一遍扫描：收集所有样式的基本信息
            for style in root.findall('w:style', NAMESPACES):
                style_id = style.get(w_styleId)
                if not style_id:
                    continue
                
                # 获取样式名称
                name_elem = style.find('w:name', NAMESPACES)
                style_name = name_elem.get(w_val, "Unknown") if name_elem is not None else "Unknown"
                self._style_names[style_id] = style_name
                
                # 获取样式类型
                w_type = f"{{{NAMESPACES['w']}}}type"
                style_type = style.get(w_type, "unknown")
                
                # 获取父样式 ID
                basedOn = style.find('w:basedOn', NAMESPACES)
                based_on_id = basedOn.get(w_val) if basedOn is not None else None
                
                # 查找并存储 <w:rPr> 元素本身（而不是只存储 size）
                rPr_elem = style.find('w:rPr', NAMESPACES)
                
                # 提取直接定义的字号 (用于快速查找，但最终解析以 get_style_rpr 为准)
                size = None
                if rPr_elem is not None:
                    sz_node = rPr_elem.find('w:sz', NAMESPACES)
                    if sz_node is not None:
                        size_val = sz_node.get(w_val)
                        if size_val and size_val.isdigit():
                            size = int(size_val)
                
                self._styles[style_id] = {
                    'name': style_name,
                    'type': style_type,
                    'direct_size': size, # 存储直接定义的字号
                    'based_on': based_on_id,
                    'rPr_xml': ET.tostring(rPr_elem, encoding='utf-8') if rPr_elem is not None else None,
                    # 'pPr_xml': ET.tostring(pPr_elem, encoding='utf-8') if pPr_elem is not None else None # 可以根据需要添加pPr
                }
                
                # 记录 Normal 样式的字号作为默认值 (这里仍然使用直接定义的字号)
                if style_id == 'Normal' and size is not None:
                    self._default_size = size
                    
        except ET.ParseError as e:
            error_logger.error(f"Failed to parse styles.xml: {e}")
        except Exception as e:
            error_logger.error(f"Error in StyleManager initialization: {e}")

    def get_style_rpr(self, style_id: str) -> Optional[ET.Element]:
        """
        获取给定 style_id 的最终字符属性 (w:rPr) Element。
        会追溯继承链并合并所有祖先样式和当前样式的 rPr 属性。
        
        合并规则：子样式属性覆盖父样式属性。
        """
        current_style_id = style_id
        # 使用一个栈来存储继承链上的 rPr 元素，从基样式到当前样式
        rpr_stack = []
        visited = set() # 防止循环引用

        while current_style_id and current_style_id not in visited:
            visited.add(current_style_id)
            style_info = self._styles.get(current_style_id)
            if not style_info:
                break

            if style_info['rPr_xml']:
                rpr_stack.append(ET.fromstring(style_info['rPr_xml']))
            
            # 向上查找基础样式
            current_style_id = style_info.get('based_on')

        if not rpr_stack:
            return None
        
        # 从栈底部（最基础的样式）开始合并
        final_rpr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")
        for rpr_elem in rpr_stack:
            for child in rpr_elem:
                # 如果 final_rpr 中已经有同名子元素，先移除
                existing_child = final_rpr.find(child.tag)
                if existing_child is not None:
                    final_rpr.remove(existing_child)
                # 添加或覆盖
                final_rpr.append(_copy_element(child)) # 使用深拷贝

        return final_rpr

    def get_size_by_style_id(self, style_id: str) -> Optional[int]:
        """
        根据样式ID获取其字号（半磅值）。
        通过 get_style_rpr 获取最终 rPr 后解析。
        """
        final_rpr_elem = self.get_style_rpr(style_id)
        if final_rpr_elem is not None:
            sz_node = final_rpr_elem.find('w:sz', NAMESPACES)
            if sz_node is not None:
                w_val = f"{{{NAMESPACES['w']}}}val"
                size_val = sz_node.get(w_val)
                if size_val and size_val.isdigit():
                    return int(size_val)
        return None
    
    def get_default_size(self) -> Optional[int]:
        """获取默认字号，现在也通过 get_style_rpr("Normal") 获取."""
        # 最好也通过 get_style_rpr("Normal") 来获取，因为 Normal 也可能有基于别的样式
        return self.get_size_by_style_id("Normal")
    
    def get_all_style_sizes(self) -> Dict[str, int]:
        """获取所有样式的字号映射。"""
        result = {}
        for style_id in self._styles.keys():
            size = self.get_size_by_style_id(style_id)
            if size is not None:
                result[style_id] = size
        return result
    
    def get_style_name(self, style_id: str) -> str:
        """获取样式名称。"""
        return self._style_names.get(style_id, "Unknown")
    
    def get_style_chain(self, style_id: str) -> List[str]:
        """
        获取样式的继承链（用于调试）。
        
        Returns:
            从该样式一直到 Normal 的样式 ID 列表
        """
        chain = [style_id]
        current_id = style_id
        visited = set()
        
        while True:
            if current_id in visited:
                break
            visited.add(current_id)
            
            style_info = self._styles.get(current_id)
            if not style_info or not style_info['based_on']:
                break
            
            parent_id = style_info['based_on']
            chain.append(parent_id)
            current_id = parent_id
        
        return chain
    
    def debug_print_all_styles(self):
        """打印所有样式及其最终解析的 rPr（用于调试）。"""
        print("\n[StyleManager] All styles with resolved properties:")
        for style_id in sorted(self._styles.keys()):
            name = self.get_style_name(style_id)
            chain = self.get_style_chain(style_id)
            chain_str = " -> ".join(chain)
            
            final_rpr = self.get_style_rpr(style_id)
            rpr_str = ET.tostring(final_rpr, encoding='utf-8', method='xml').decode('utf-8').strip() if final_rpr is not None else "None"
            
            size = self.get_size_by_style_id(style_id)
            size_str = f"{size} half-points ({size/2} pt)" if size is not None else "None"
            
            print(f"  ID: '{style_id}' | Name: '{name}'")
            print(f"    Inheritance chain: {chain_str}")
            print(f"    Resolved Size: {size_str}")
            print(f"    Resolved rPr: {rpr_str}")
            print("-" * 20)


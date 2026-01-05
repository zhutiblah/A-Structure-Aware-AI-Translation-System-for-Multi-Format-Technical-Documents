# docx_processor.py
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, List, Optional, Dict
import os
from constants import NAMESPACES, MODERN_FONT_TABLE_XML
from style_manager import StyleManager
from translation import fix_run_structure
from utils import ensure_rpr_first_in_run, save_debug_document
from config import *
from utils import *
from constants import *
from translation import llm_translate_concurrent
strict_placeholder_regex = re.compile(r"(<\s*placeholder\s*>)")

def preserve_paragraph_alignment(root: ET.Element) -> None:
    """
    遍历所有段落，记录并保留对齐方式。
    特别处理包含 <m:oMathPara> 的段落，从 <m:jc> 推断对齐方式。
    """
    for p in root.findall('.//w:p', NAMESPACES):
        pPr = p.find('w:pPr', NAMESPACES)
        if pPr is None:
            pPr = ET.SubElement(p, f"{{{NAMESPACES['w']}}}pPr")
        
        # 检查段落中是否有 <m:oMathPara>
        math_para = p.find('m:oMathPara', NAMESPACES)
        math_para_alignment = None
        
        if math_para is not None:
            # 从 <m:oMathParaPr><m:jc> 中获取对齐方式
            math_para_pr = math_para.find('m:oMathParaPr', NAMESPACES)
            if math_para_pr is not None:
                m_jc = math_para_pr.find('m:jc', NAMESPACES)
                if m_jc is not None:
                    m_val = f"{{{NAMESPACES['m']}}}val"
                    math_alignment = m_jc.get(m_val)
                    
                    # 将数学公式的对齐方式映射到段落对齐
                    alignment_map = {
                        'center': 'center',
                        'centerGroup': 'center',
                        'left': 'left',
                        'right': 'right'
                    }
                    math_para_alignment = alignment_map.get(math_alignment, 'center')
                    audit_logger.info(f"[ParagraphFormat] Found math paragraph alignment from <m:jc>: {math_para_alignment}")
        
        # 检查是否已有对齐设置（来自段落样式或直接设置）
        existing_alignment = None
        jc = pPr.find('w:jc', NAMESPACES)
        if jc is not None:
            w_val = f"{{{NAMESPACES['w']}}}val"
            existing_alignment = jc.get(w_val)
            audit_logger.info(f"[ParagraphFormat] Found existing paragraph alignment: {existing_alignment}")
        
        # 如果有数学公式的对齐但段落没有对齐设置，添加对齐
        if math_para_alignment and not existing_alignment:
            if jc is None:
                jc = ET.SubElement(pPr, f"{{{NAMESPACES['w']}}}jc")
            w_val = f"{{{NAMESPACES['w']}}}val"
            jc.set(w_val, math_para_alignment)
            audit_logger.info(f"[ParagraphFormat] Added paragraph alignment from math formula: {math_para_alignment}")


def apply_style_to_math_elements(
    root: ET.Element,
    *,
    style_manager: StyleManager,
    font_east_asia: Optional[str],
    font_latin: Optional[str],
    lang_ea: Optional[str],
    lang_latin: Optional[str],
    size_map: Optional[Dict[int, int]] = None,
) -> None:
    """
    为公式中的元素应用样式和字号映射，同时保留现有的所有格式。
    
    Args:
        root: XML 根元素
        style_manager: 样式管理器
        font_east_asia: 东亚字体
        font_latin: 拉丁字体
        lang_ea: 东亚语言
        lang_latin: 拉丁语言
        size_map: 字号映射表
    """
    # 查找所有数学公式
    for math_elem in root.findall('.//m:oMath', NAMESPACES):
        # 在公式内查找所有 <m:r>（数学 run）
        for m_r in math_elem.findall('.//m:r', NAMESPACES):
            # 获取或创建 <w:rPr>
            w_rPr = m_r.find('w:rPr', NAMESPACES)
            if w_rPr is None:
                # 如果没有 w:rPr，创建一个
                w_rPr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")
                # 插入到 <m:rPr> 后面（如果存在）或在最前面
                m_rPr = m_r.find('m:rPr', NAMESPACES)
                if m_rPr is not None:
                    # 在 m:rPr 后面
                    insert_index = list(m_r).index(m_rPr) + 1
                    m_r.insert(insert_index, w_rPr)
                else:
                    # 在最前面
                    m_r.insert(0, w_rPr)
            
            # --- 保存现有的格式元素 ---
            # 这些元素在映射后仍然需要保留
            existing_formats = {
                'bold': w_rPr.find('w:b', NAMESPACES),
                'italic': w_rPr.find('w:i', NAMESPACES),
                'underline': w_rPr.find('w:u', NAMESPACES),
                'strike': w_rPr.find('w:strike', NAMESPACES),
                'dstrike': w_rPr.find('w:dstrike', NAMESPACES),
                'shadow': w_rPr.find('w:shadow', NAMESPACES),
                'outline': w_rPr.find('w:outline', NAMESPACES),
                'color': w_rPr.find('w:color', NAMESPACES),
                'highlight': w_rPr.find('w:highlight', NAMESPACES),
                'vertAlign': w_rPr.find('w:vertAlign', NAMESPACES),
            }
            
            # --- 1. 确定原始字号 ---
            original_size = None
            
            # 策略a: 检查 w:rPr 中是否已有字号
            sz_node = w_rPr.find('w:sz', NAMESPACES)
            if sz_node is not None:
                w_val = f"{{{NAMESPACES['w']}}}val"
                val = sz_node.get(w_val, "")
                if val and val.isdigit():
                    original_size = int(val)
            
            # 策略b: 如果没有，用默认字号
            if original_size is None and style_manager:
                original_size = style_manager.get_default_size()
            
            # 如果还是没有，跳过这个元素
            if original_size is None:
                continue
            
            # --- 2. 确定新字号 ---
            new_size = None
            if size_map and original_size in size_map:
                new_size = size_map[original_size]
                m_t = m_r.find('m:t', NAMESPACES)
                text = m_t.text if m_t is not None else "unknown"
                audit_logger.info(f"[MathStyle] Mapping math formula '{text}' size: {original_size} -> {new_size}")
            
            # --- 3. 应用字体 ---
            if font_latin and font_east_asia:
                # 移除旧的 rFonts（保留其他格式）
                for old_rFonts in w_rPr.findall('w:rFonts', NAMESPACES):
                    w_rPr.remove(old_rFonts)
                
                rFonts = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}rFonts")
                w_ascii = f"{{{NAMESPACES['w']}}}ascii"
                w_hAnsi = f"{{{NAMESPACES['w']}}}hAnsi"
                w_eastAsia = f"{{{NAMESPACES['w']}}}eastAsia"
                w_hint = f"{{{NAMESPACES['w']}}}hint"
                
                rFonts.set(w_ascii, font_latin)
                rFonts.set(w_hAnsi, font_latin)
                rFonts.set(w_eastAsia, font_east_asia)
                rFonts.set(w_hint, "eastAsia")
            
            # --- 4. 应用语言 ---
            if lang_latin and lang_ea:
                # 移除旧的 lang
                for old_lang in w_rPr.findall('w:lang', NAMESPACES):
                    w_rPr.remove(old_lang)
                
                lang = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}lang")
                w_val = f"{{{NAMESPACES['w']}}}val"
                w_eastAsia = f"{{{NAMESPACES['w']}}}eastAsia"
                
                lang.set(w_val, lang_latin)
                lang.set(w_eastAsia, lang_ea)
            
            # --- 5. 应用新字号 ---
            if new_size is not None:
                # 移除旧的字号（但保留其他格式）
                for old_sz in list(w_rPr.findall('w:sz', NAMESPACES)):
                    w_rPr.remove(old_sz)
                for old_szCs in list(w_rPr.findall('w:szCs', NAMESPACES)):
                    w_rPr.remove(old_szCs)
                
                # 添加新字号
                w_val = f"{{{NAMESPACES['w']}}}val"
                
                sz_node = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}sz")
                sz_node.set(w_val, str(new_size))
                
                sz_cs_node = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}szCs")
                sz_cs_node.set(w_val, str(new_size))
                
                audit_logger.info(f"[MathStyle] Applied size mapping to math formula: {original_size} -> {new_size}")
            
            # --- 6. 重新添加所有保留的格式元素 ---
            # 这确保格式元素始终在正确的位置
            for format_name, format_elem in existing_formats.items():
                if format_elem is not None:
                    # 检查元素是否还在 rPr 中
                    if format_elem not in w_rPr:
                        # 移除并重新添加（确保顺序正确）
                        try:
                            w_rPr.remove(format_elem)
                        except ValueError:
                            pass
                        w_rPr.append(format_elem)
                        audit_logger.info(f"[MathStyle] Preserved format '{format_name}' in math formula")
    
    # 处理复杂结构中的 <m:r>
    for math_elem in root.findall('.//m:oMath', NAMESPACES):
        for complex_elem in math_elem.findall('.//*', NAMESPACES):
            for m_r in complex_elem.findall('m:r', NAMESPACES):
                w_rPr = m_r.find('w:rPr', NAMESPACES)
                if w_rPr is None:
                    w_rPr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")
                    m_r.insert(0, w_rPr)
                
                # 保存现有格式
                existing_formats = {
                    'bold': w_rPr.find('w:b', NAMESPACES),
                    'italic': w_rPr.find('w:i', NAMESPACES),
                    'underline': w_rPr.find('w:u', NAMESPACES),
                    'strike': w_rPr.find('w:strike', NAMESPACES),
                    'dstrike': w_rPr.find('w:dstrike', NAMESPACES),
                    'color': w_rPr.find('w:color', NAMESPACES),
                    'highlight': w_rPr.find('w:highlight', NAMESPACES),
                }
                
                # 确定原始字号
                original_size = None
                sz_node = w_rPr.find('w:sz', NAMESPACES)
                if sz_node is not None:
                    w_val = f"{{{NAMESPACES['w']}}}val"
                    val = sz_node.get(w_val, "")
                    if val and val.isdigit():
                        original_size = int(val)
                
                if original_size is None and style_manager:
                    original_size = style_manager.get_default_size()
                
                if original_size is None:
                    continue
                
                # 映射字号
                new_size = None
                if size_map and original_size in size_map:
                    new_size = size_map[original_size]
                
                # 应用字体
                if font_latin and font_east_asia:
                    for old_rFonts in w_rPr.findall('w:rFonts', NAMESPACES):
                        w_rPr.remove(old_rFonts)
                    
                    rFonts = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}rFonts")
                    w_ascii = f"{{{NAMESPACES['w']}}}ascii"
                    w_hAnsi = f"{{{NAMESPACES['w']}}}hAnsi"
                    w_eastAsia = f"{{{NAMESPACES['w']}}}eastAsia"
                    _hint = f"{{{NAMESPACES['w']}}}hint"
                    
                    rFonts.set(w_ascii, font_latin)
                    rFonts.set(w_hAnsi, font_latin)
                    rFonts.set(w_eastAsia, font_east_asia)
                    rFonts.set(w_hint, "eastAsia")
                
                # 应用语言
                if lang_latin and lang_ea:
                    for old_lang in w_rPr.findall('w:lang', NAMESPACES):
                        w_rPr.remove(old_lang)
                    
                    lang = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}lang")
                    w_val = f"{{{NAMESPACES['w']}}}val"
                    w_eastAsia = f"{{{NAMESPACES['w']}}}eastAsia"
                    
                    lang.set(w_val, lang_latin)
                    lang.set(w_eastAsia, lang_ea)
                
                # 应用新字号
                if new_size is not None:
                    for old_sz in list(w_rPr.findall('w:sz', NAMESPACES)):
                        w_rPr.remove(old_sz)
                    for old_szCs in list(w_rPr.findall('w:szCs', NAMESPACES)):
                        w_rPr.remove(old_szCs)
                    
                    w_val = f"{{{NAMESPACES['w']}}}val"
                    
                    sz_node = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}sz")
                    sz_node.set(w_val, str(new_size))
                    
                    sz_cs_node = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}szCs")
                    sz_cs_node.set(w_val, str(new_size))
                
                # 重新添加所有保留的格式元素
                for format_name, format_elem in existing_formats.items():
                    if format_elem is not None:
                        if format_elem not in w_rPr:
                            try:
                                w_rPr.remove(format_elem)
                            except ValueError:
                                pass
                            w_rPr.append(format_elem)
def materialize_styles_from_style_defs(
    root: ET.Element, 
    *, 
    style_manager: StyleManager,
    filename: str = "",  # ⚠️ 添加文件名参数
    font_east_asia: Optional[str] = None,
    font_latin: Optional[str] = None,
    lang_ea: Optional[str] = None,
    lang_latin: Optional[str] = None
) -> None:
    """
    将 styles.xml 中定义的样式属性显式地应用到 document.xml 中。
    """
    if not style_manager:
        audit_logger.warning("StyleManager not provided. Materialization skipped.")
        return
    
    # ⚠️ 初始化统计变量
    total_runs_found = 0
    total_runs_processed = 0
    runs_with_rstyle = 0
    runs_materialized_bold = 0
    
    # 遍历所有段落
    for p_elem in root.findall('.//w:p', NAMESPACES):
        # 找出该段落使用的样式ID
        pPr = p_elem.find('w:pPr', NAMESPACES)
        para_style_id = None
        if pPr is not None:
            p_style_node = pPr.find('w:pStyle', NAMESPACES)
            if p_style_node is not None:
                w_val = f"{{{NAMESPACES['w']}}}val"
                para_style_id = p_style_node.get(w_val)
        
        # 获取段落默认的 rPr
        para_default_rpr_elem = None
        if para_style_id:
            para_default_rpr_elem = style_manager.get_style_rpr(para_style_id)
            if para_default_rpr_elem is not None:
                audit_logger.info(f"[Materialize] Paragraph uses style '{para_style_id}'")

        # --- 处理普通的 w:r 元素 ---
        for r_elem in p_elem.findall('w:r', NAMESPACES):
            total_runs_found += 1
            
            t_elem = r_elem.find('w:t', NAMESPACES)
            
            # 打印所有 Run 的文本
            text_preview = t_elem.text[:50] if (t_elem is not None and t_elem.text) else "(no text)"
            audit_logger.debug(f"[Materialize] Found Run #{total_runs_found}: '{text_preview}'")
            
            # 只处理有意义的文本
            if t_elem is None:
                audit_logger.debug(f"[Materialize]   Skipping: no <w:t> element")
                continue
            
            if not is_meaningful_text(t_elem.text or ""):
                audit_logger.debug(f"[Materialize]   Skipping: text not meaningful")
                continue
            
            total_runs_processed += 1
            audit_logger.info(f"--- Materializing Run #{total_runs_processed}: '{text_preview}' ---")

            # 获取或创建 rPr
            rPr = r_elem.find('w:rPr', NAMESPACES)
            if rPr is None:
                rPr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")
                r_elem.insert(0, rPr)
            
            # 打印原始的 rPr
            audit_logger.debug(f"  Original rPr: {ET.tostring(rPr, encoding='utf-8').decode('utf-8').strip()}")
            
            # 检查 run 是否有直接的 rStyle
            run_style_id = None
            rStyle_node = rPr.find('w:rStyle', NAMESPACES)
            if rStyle_node is not None:
                runs_with_rstyle += 1
                run_style_id = rStyle_node.get(f"{{{NAMESPACES['w']}}}val")
                audit_logger.info(f"  ✓ Run has w:rStyle='{run_style_id}'")
            else:
                audit_logger.debug(f"  Run has NO w:rStyle")
            
            # 获取 run 自身样式定义的 rPr
            run_style_rpr_elem = None
            if run_style_id:
                run_style_rpr_elem = style_manager.get_style_rpr(run_style_id)
                if run_style_rpr_elem is not None:
                    rpr_str = ET.tostring(run_style_rpr_elem, encoding='utf-8').decode('utf-8').strip()
                    audit_logger.info(f"  ✓✓ Resolved rPr for rStyle '{run_style_id}': {rpr_str}")
                    
                    if run_style_rpr_elem.find('w:b', NAMESPACES) is not None:
                        runs_materialized_bold += 1
                        audit_logger.info(f"  ✓✓✓ rStyle '{run_style_id}' CONTAINS <w:b/> (BOLD)!")
                else:
                    audit_logger.warning(f"  ✗ Failed to resolve rPr for rStyle '{run_style_id}'")
            
            # 样式合并逻辑
            final_rpr_for_run = ET.Element(f"{{{NAMESPACES['w']}}}rPr")

            # Step 1: 从段落默认样式继承
            if para_default_rpr_elem is not None:
                for child in para_default_rpr_elem:
                    final_rpr_for_run.append(copy_element(child))

            # Step 2: 从 run 的 rStyle 继承并覆盖
            if run_style_rpr_elem is not None:
                for child in run_style_rpr_elem:
                    existing_child = final_rpr_for_run.find(child.tag)
                    if existing_child is not None:
                        final_rpr_for_run.remove(existing_child)
                    final_rpr_for_run.append(copy_element(child))
                audit_logger.info(f"  After merging from rStyle, final_rpr: {ET.tostring(final_rpr_for_run, encoding='utf-8').decode('utf-8').strip()}")
                if final_rpr_for_run.find('w:b', NAMESPACES) is not None:
                    audit_logger.info(f"  !!! final_rpr AFTER rStyle merge CONTAINS w:b !!!")

            # Step 3: 移除 w:rStyle，然后用直接格式覆盖
            if rStyle_node is not None:
                rPr.remove(rStyle_node)
                audit_logger.info(f"  Removed w:rStyle '{run_style_id}' from run's direct rPr")
            
            for child in list(rPr):
                if child.tag == f"{{{NAMESPACES['w']}}}rStyle":
                    audit_logger.warning(f"  Found w:rStyle remaining, removing it")
                    rPr.remove(child)
                    continue

                existing_child = final_rpr_for_run.find(child.tag)
                if existing_child is not None:
                    final_rpr_for_run.remove(existing_child)
                final_rpr_for_run.append(copy_element(child))
            
            # 清空原 rPr，并填充新的 final_rpr_for_run
            for child in list(rPr):
                rPr.remove(child)
            for child in final_rpr_for_run:
                rPr.append(child)

            # 打印最终的 rPr
            final_rpr_str = ET.tostring(rPr, encoding='utf-8').decode('utf-8').strip()
            audit_logger.info(f"  Final rPr: {final_rpr_str}")
            if rPr.find('w:b', NAMESPACES) is not None:
                audit_logger.info(f"  ✓✓✓ Final rPr CONTAINS <w:b/> (BOLD)!")
            
            audit_logger.info(f"--- End Materializing Run #{total_runs_processed} ---\n")

            # 强制补充字体和语言
            if font_latin and font_east_asia:
                for old_rFonts in rPr.findall('w:rFonts', NAMESPACES):
                    rPr.remove(old_rFonts)
                
                rFonts = ET.SubElement(rPr, f"{{{NAMESPACES['w']}}}rFonts")
                rFonts.set(f"{{{NAMESPACES['w']}}}ascii", font_latin)
                rFonts.set(f"{{{NAMESPACES['w']}}}hAnsi", font_latin)
                rFonts.set(f"{{{NAMESPACES['w']}}}eastAsia", font_east_asia)
                rFonts.set(f"{{{NAMESPACES['w']}}}hint", "eastAsia")
            
            if lang_latin and lang_ea:
                for old_lang in rPr.findall('w:lang', NAMESPACES):
                    rPr.remove(old_lang)
                
                lang = ET.SubElement(rPr, f"{{{NAMESPACES['w']}}}lang")
                lang.set(f"{{{NAMESPACES['w']}}}val", lang_latin)
                lang.set(f"{{{NAMESPACES['w']}}}eastAsia", lang_ea)
        
        # --- 处理公式中的 m:r 元素 ---
        for m_oMath in p_elem.findall('.//m:oMath', NAMESPACES):
            for m_r in m_oMath.findall('.//m:r', NAMESPACES):
                m_t = m_r.find('m:t', NAMESPACES)
                
                if m_t is None or not is_meaningful_text(m_t.text or ""):
                    continue
                
                audit_logger.info(f"--- Materializing Math Run: '{m_t.text[:30]}' ---")

                w_rPr = m_r.find('w:rPr', NAMESPACES)
                if w_rPr is None:
                    w_rPr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")
                    m_rPr_node = m_r.find('m:rPr', NAMESPACES)
                    if m_rPr_node is not None:
                        insert_index = list(m_r).index(m_rPr_node) + 1
                        m_r.insert(insert_index, w_rPr)
                    else:
                        m_r.insert(0, w_rPr)
                
                math_run_style_id = None
                rStyle_node = w_rPr.find('w:rStyle', NAMESPACES)
                if rStyle_node is not None:
                    math_run_style_id = rStyle_node.get(f"{{{NAMESPACES['w']}}}val")
                    audit_logger.info(f"  Math Run has w:rStyle: '{math_run_style_id}'")
                    
                math_run_style_rpr_elem = None
                if math_run_style_id:
                    math_run_style_rpr_elem = style_manager.get_style_rpr(math_run_style_id)
                    if math_run_style_rpr_elem is not None:
                        audit_logger.info(f"  [StyleManager Resolved] math rPr: {ET.tostring(math_run_style_rpr_elem, encoding='utf-8').decode('utf-8').strip()}")
                    else:
                        audit_logger.warning(f"  StyleManager failed to resolve math rPr")

                final_rpr_for_math_run = ET.Element(f"{{{NAMESPACES['w']}}}rPr")

                if math_run_style_rpr_elem is not None:
                    for child in math_run_style_rpr_elem:
                        final_rpr_for_math_run.append(copy_element(child))
                
                if rStyle_node is not None:
                    w_rPr.remove(rStyle_node)
                    audit_logger.info(f"  Removed w:rStyle from math run")
                
                for child in list(w_rPr):
                    if child.tag == f"{{{NAMESPACES['w']}}}rStyle":
                        w_rPr.remove(child)
                        continue

                    existing_child = final_rpr_for_math_run.find(child.tag)
                    if existing_child is not None:
                        final_rpr_for_math_run.remove(existing_child)
                    final_rpr_for_math_run.append(copy_element(child))

                for child in list(w_rPr):
                    w_rPr.remove(child)
                for child in final_rpr_for_math_run:
                    w_rPr.append(child)

                audit_logger.info(f"  Final math rPr: {ET.tostring(w_rPr, encoding='utf-8').decode('utf-8').strip()}")
                audit_logger.info(f"--- End Materializing Math Run ---\n")

                if font_latin and font_east_asia:
                    for old_rFonts in w_rPr.findall('w:rFonts', NAMESPACES):
                        w_rPr.remove(old_rFonts)
                    rFonts = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}rFonts")
                    rFonts.set(f"{{{NAMESPACES['w']}}}ascii", font_latin)
                    rFonts.set(f"{{{NAMESPACES['w']}}}hAnsi", font_latin)
                    rFonts.set(f"{{{NAMESPACES['w']}}}eastAsia", font_east_asia)
                    rFonts.set(f"{{{NAMESPACES['w']}}}hint", "eastAsia")
                
                if lang_latin and lang_ea:
                    for old_lang in w_rPr.findall('w:lang', NAMESPACES):
                        w_rPr.remove(old_lang)
                    lang = ET.SubElement(w_rPr, f"{{{NAMESPACES['w']}}}lang")
                    lang.set(f"{{{NAMESPACES['w']}}}val", lang_latin)
                    lang.set(f"{{{NAMESPACES['w']}}}eastAsia", lang_ea)
    
    audit_logger.info(f"[Materialize] ========== SUMMARY for {filename} ==========")
    audit_logger.info(f"[Materialize] Total runs found: {total_runs_found}")
    audit_logger.info(f"[Materialize] Runs processed: {total_runs_processed}")
    audit_logger.info(f"[Materialize] Runs with rStyle: {runs_with_rstyle}")
    audit_logger.info(f"[Materialize] Runs materialized with BOLD: {runs_materialized_bold}")
    audit_logger.info(f"[Materialize] ========== END: {filename} ==========\n")

def copy_element(element: ET.Element) -> ET.Element:
    """Helper function to deep copy an ElementTree element."""
    new_element = ET.Element(element.tag, element.attrib)
    new_element.text = element.text
    new_element.tail = element.tail
    for child in element:
        new_element.append(copy_element(child))
    return new_element
def preserve_math_formatting(root: ET.Element) -> None:
    """
    保留公式中的所有格式（加粗、下划线、斜体、颜色等），
    但不进行字号映射。
    
    这个函数遍历所有公式元素，确保其 <w:rPr> 中的格式属性被保留。
    """
    for math_elem in root.findall('.//m:oMath', NAMESPACES):
        for m_r in math_elem.findall('.//m:r', NAMESPACES):
            w_rPr = m_r.find('w:rPr', NAMESPACES)
            
            if w_rPr is None:
                continue
            
            # 检查并记录现有的格式信息
            has_bold = w_rPr.find('w:b', NAMESPACES) is not None
            has_italic = w_rPr.find('w:i', NAMESPACES) is not None
            has_underline = w_rPr.find('w:u', NAMESPACES) is not None
            has_strike = w_rPr.find('w:strike', NAMESPACES) is not None
            has_dstrike = w_rPr.find('w:dstrike', NAMESPACES) is not None
            
            # 记录日志
            format_list = []
            if has_bold:
                format_list.append("bold")
            if has_italic:
                format_list.append("italic")
            if has_underline:
                format_list.append("underline")
            if has_strike:
                format_list.append("strike")
            if has_dstrike:
                format_list.append("double-strike")
            
            if format_list:
                m_t = m_r.find('m:t', NAMESPACES)
                text = m_t.text if m_t is not None else "unknown"
                audit_logger.info(f"[PreserveMath] Preserving formats for '{text}': {', '.join(format_list)}")
def apply_style_with_size_mapping(
    p_elem: ET.Element, 
    *, 
    style_manager: StyleManager, 
    font_east_asia: Optional[str], 
    font_latin: Optional[str], 
    lang_ea: Optional[str], 
    lang_latin: Optional[str], 
    size_map: Optional[Dict[int, int]] = None, 
    set_line_spacing_half: Optional[int] = None
):
    """
    为单个段落元素应用所有样式，包括字体、语言、行距和智能字号映射。
    同时保留段落级别的对齐、间距等属性。
    """
    pPr = p_elem.find('w:pPr', NAMESPACES)
    if pPr is None: 
        pPr = ET.SubElement(p_elem, f"{{{NAMESPACES['w']}}}pPr")

    # --- 1. 保存段落级别的格式属性 ---
    # 保留对齐、间距、缩进等
    existing_para_formats = {}
    
    # 保存对齐方式
    jc = pPr.find('w:jc', NAMESPACES)
    if jc is not None:
        w_val = f"{{{NAMESPACES['w']}}}val"
        existing_para_formats['alignment'] = jc.get(w_val)
        audit_logger.info(f"[ApplyStyle] Saved paragraph alignment: {existing_para_formats['alignment']}")
    
    # 保存间距
    spacing = pPr.find('w:spacing', NAMESPACES)
    if spacing is not None:
        existing_para_formats['spacing'] = ET.Element(f"{{{NAMESPACES['w']}}}spacing")
        for attr_key in spacing.attrib:
            existing_para_formats['spacing'].set(attr_key, spacing.get(attr_key))
    
    # 保存缩进
    ind = pPr.find('w:ind', NAMESPACES)
    if ind is not None:
        existing_para_formats['indent'] = ET.Element(f"{{{NAMESPACES['w']}}}ind")
        for attr_key in ind.attrib:
            existing_para_formats['indent'].set(attr_key, ind.get(attr_key))

    # --- 2. 应用段落级别的行距设置 ---
    if set_line_spacing_half is not None:
        spacing = pPr.find('w:spacing', NAMESPACES) or ET.SubElement(pPr, f"{{{NAMESPACES['w']}}}spacing")
        w_line = f"{{{NAMESPACES['w']}}}line"
        w_lineRule = f"{{{NAMESPACES['w']}}}lineRule"
        spacing.set(w_line, str(set_line_spacing_half))
        spacing.set(w_lineRule, "auto")

    # --- 3. 智能确定原始字号（现在优先使用已显式化的字号） ---
    original_size = None
    
    # 策略a: 查找任一 run 的直接格式字号定义（应该都有了）
    for r_in_p in p_elem.findall('.//w:r', NAMESPACES):
        rPr_in_p = r_in_p.find('w:rPr', NAMESPACES)
        if rPr_in_p is not None:
            sz_node_in_p = rPr_in_p.find('w:sz', NAMESPACES)
            if sz_node_in_p is not None:
                w_val = f"{{{NAMESPACES['w']}}}val"
                val = sz_node_in_p.get(w_val, "")
                if val and val.isdigit():
                    original_size = int(val)
                    audit_logger.info(f"[ApplyStyle] Found original size in run: {original_size}")
                    break

    # 策略b: 如果还是没有，从"样式"中查找（备用）
    if original_size is None and style_manager:
        p_style_node = pPr.find('w:pStyle', NAMESPACES)
        if p_style_node is not None:
            w_val = f"{{{NAMESPACES['w']}}}val"
            style_id = p_style_node.get(w_val)
            if style_id:
                original_size = style_manager.get_size_by_style_id(style_id)
                if original_size:
                    audit_logger.info(f"[ApplyStyle] Found size from style '{style_id}': {original_size}")
    
    # 策略c: 用默认字号（最后的备选）
    if original_size is None and style_manager:
        original_size = style_manager.get_default_size()
        if original_size:
            audit_logger.info(f"[ApplyStyle] Using default size: {original_size}")

    # --- 4. 根据原始字号确定新字号 ---
    new_size = None
    if original_size and size_map:
        if original_size in size_map:
            new_size = size_map[original_size]
            audit_logger.info(f"[ApplyStyle] Mapping size: {original_size} -> {new_size} half-points ({original_size/2} -> {new_size/2} pt)")
        else:
            audit_logger.info(f"[ApplyStyle] Original size {original_size} not in size_map, keeping original")

    # --- 5. 统一应用样式到所有文本 Run (<w:r>) ---
    for r in p_elem.findall('.//w:r', NAMESPACES):
        t = r.find('w:t', NAMESPACES)
        if t is None or not is_meaningful_text(t.text or ""): 
            continue

        rPr = r.find('w:rPr', NAMESPACES)
        if rPr is None: 
            rPr = ET.SubElement(r, f"{{{NAMESPACES['w']}}}rPr")
            r.remove(rPr)  # 移除
            r.insert(0, rPr)  # 重新插入到最前面

        # 应用字体（如果指定了）
        if font_latin and font_east_asia:
            # 移除旧的 rFonts
            for old_rFonts in rPr.findall('w:rFonts', NAMESPACES):
                rPr.remove(old_rFonts)
            
            rFonts = ET.SubElement(rPr, f"{{{NAMESPACES['w']}}}rFonts")
            w_ascii = f"{{{NAMESPACES['w']}}}ascii"
            w_hAnsi = f"{{{NAMESPACES['w']}}}hAnsi"
            w_eastAsia = f"{{{NAMESPACES['w']}}}eastAsia"
            w_hint = f"{{{NAMESPACES['w']}}}hint"
            
            rFonts.set(w_ascii, font_latin)
            rFonts.set(w_hAnsi, font_latin)
            rFonts.set(w_eastAsia, font_east_asia)
            rFonts.set(w_hint, "eastAsia")

        # 应用语言（如果指定了）
        if lang_latin and lang_ea:
            # 移除旧的 lang
            for old_lang in rPr.findall('w:lang', NAMESPACES):
                rPr.remove(old_lang)
            
            lang = ET.SubElement(rPr, f"{{{NAMESPACES['w']}}}lang")
            w_val = f"{{{NAMESPACES['w']}}}val"
            w_eastAsia = f"{{{NAMESPACES['w']}}}eastAsia"
            
            lang.set(w_val, lang_latin)
            lang.set(w_eastAsia, lang_ea)

        # 【核心】应用新字号 (如果成功映射)
        if new_size is not None:
            # 【关键步骤】清理旧字号
            for old_sz in list(rPr.findall('w:sz', NAMESPACES)):
                rPr.remove(old_sz)
            for old_szCs in list(rPr.findall('w:szCs', NAMESPACES)):
                rPr.remove(old_szCs)
            
            # 【关键步骤】创建新字号节点
            w_val = f"{{{NAMESPACES['w']}}}val"
            
            sz_node = ET.SubElement(rPr, f"{{{NAMESPACES['w']}}}sz")
            sz_node.set(w_val, str(new_size))
            
            sz_cs_node = ET.SubElement(rPr, f"{{{NAMESPACES['w']}}}szCs")
            sz_cs_node.set(w_val, str(new_size))
            
            audit_logger.info(f"[ApplyStyle] Applied size mapping to run: {original_size} -> {new_size}")
    
    # --- 6. 重新确保段落级别的格式被保留 ---
    # 特别是对齐方式
    if existing_para_formats.get('alignment'):
        # 检查是否还存在对齐设置，如果不存在则重新添加
        jc = pPr.find('w:jc', NAMESPACES)
        if jc is None:
            jc = ET.SubElement(pPr, f"{{{NAMESPACES['w']}}}jc")
        
        w_val = f"{{{NAMESPACES['w']}}}val"
        jc.set(w_val, existing_para_formats['alignment'])
        audit_logger.info(f"[ApplyStyle] Restored paragraph alignment: {existing_para_formats['alignment']}")
    
    # 恢复其他段落格式
    if existing_para_formats.get('spacing'):
        spacing = pPr.find('w:spacing', NAMESPACES)
        if spacing is not None:
            # 更新现有的间距（如果有自定义行距，保留它）
            if set_line_spacing_half is None:
                # 只有当没有设置自定义行距时才恢复原来的
                for attr_key in existing_para_formats['spacing'].attrib:
                    spacing.set(attr_key, existing_para_formats['spacing'].get(attr_key))
    
    if existing_para_formats.get('indent'):
        indent = pPr.find('w:ind', NAMESPACES)
        if indent is None:
            pPr.append(existing_para_formats['indent'])
        else:
            # 更新现有的缩进
            for attr_key in existing_para_formats['indent'].attrib:
                indent.set(attr_key, existing_para_formats['indent'].get(attr_key))

def extract_and_translate_math_text(math_elem: ET.Element, translate_function, translate_kwargs: dict) -> None:
    """
    递归地在数学元素中查找并翻译中文文本。
    
    Args:
        math_elem: 数学元素（如 <m:f>, <m:num>, <m:den> 等）
        translate_function: 翻译函数
        translate_kwargs: 翻译参数
    """
    # 查找所有 <m:t> 标签（数学文本）
    for m_t in math_elem.findall('.//m:t', NAMESPACES):
        if m_t.text and is_meaningful_text(m_t.text):
            # 检查是否包含中文或其他需要翻译的文本
            if any('\u4e00' <= char <= '\u9fff' for char in m_t.text):
                texts_to_translate = [{"full_text": m_t.text}]
                translated = translate_function(texts_to_translate, **translate_kwargs)
                if translated and translated[0] != m_t.text:
                    m_t.text = translated[0]
                    audit_logger.info(f"[Math] Translated: {m_t.text} -> {translated[0]}")
def copy_rpr_element(rpr_source: ET.Element) -> ET.Element:
    """深度复制 <w:rPr> 元素，使用通用的深拷贝函数。"""
    return copy_element(rpr_source)
def _compare_rpr_elements(rpr1: Optional[ET.Element], rpr2: Optional[ET.Element]) -> bool:
    """
    比较两个 w:rPr 元素是否具有相同的字符格式属性。
    忽略子元素的顺序，只比较存在哪些标签及其属性值。
    ⚠️ 忽略不影响视觉的属性，如 w:rFonts/@w:hint
    """
    if rpr1 is None and rpr2 is None:
        return True
    if rpr1 is None or rpr2 is None:
        # 如果一个有 rPr 另一个没有，需要检查非 None 的是否为"空样式"
        non_none_rpr = rpr1 if rpr1 is not None else rpr2
        return _is_empty_rpr_after_normalization(non_none_rpr)

    # ⚠️ 标准化两个 rPr（移除不影响视觉的属性）
    attrs1 = _extract_normalized_attributes(rpr1)
    attrs2 = _extract_normalized_attributes(rpr2)

    return attrs1 == attrs2


def _extract_normalized_attributes(rpr: ET.Element) -> dict:
    """
    提取 rPr 的所有子元素的标签和属性，并标准化（移除不影响视觉的属性）。
    
    Args:
        rpr: w:rPr 元素
    
    Returns:
        字典：{tag: {attr: value}}
    """
    attrs = {}
    
    for child in rpr:
        key = child.tag  # 例如 "{...}b" 或 "{...}rFonts"
        
        # ⚠️ 特殊处理 w:rFonts：需要过滤掉 w:hint 属性
        if key == f"{{{NAMESPACES['w']}}}rFonts":
            # 复制属性，但排除 w:hint
            value = {
                attr: child.get(attr) 
                for attr in child.attrib 
                if attr != f"{{{NAMESPACES['w']}}}hint"  # ⚠️ 忽略 hint
            }
            
            # ⚠️ 如果过滤后 rFonts 没有任何属性且没有子元素，跳过这个元素
            if not value and len(child) == 0:
                audit_logger.debug(f"[Compare] Skipping empty w:rFonts after removing w:hint")
                continue
        else:
            # 其他元素正常处理
            value = {attr: child.get(attr) for attr in child.attrib}
        
        attrs[key] = value
    
    return attrs


def _is_empty_rpr_after_normalization(rpr: ET.Element) -> bool:
    """
    检查 rPr 在标准化后是否为"空样式"（没有任何实质性的格式属性）。
    
    Args:
        rpr: w:rPr 元素
    
    Returns:
        True 如果是空样式，False 否则
    """
    if rpr is None:
        return True
    
    # 提取标准化后的属性
    normalized_attrs = _extract_normalized_attributes(rpr)
    
    # 如果标准化后没有任何属性，认为是空样式
    return len(normalized_attrs) == 0



def select_consistent_style(runs: List[ET.Element]) -> Optional[ET.Element]:
    """
    检查一组 Run 的字符样式 (w:rPr) 是否一致。
    如果一致，返回一个代表该样式的 w:rPr Element 副本；否则返回 None。
    """
    if not runs:
        return None

    # 获取第一个 Run 的 rPr
    first_rPr = runs[0].find(f"{{{NAMESPACES['w']}}}rPr", NAMESPACES)
    
    # 比较所有后续 Run 的 rPr 与第一个 Run 的 rPr 是否一致
    for i in range(1, len(runs)):
        current_rPr = runs[i].find(f"{{{NAMESPACES['w']}}}rPr", NAMESPACES)
        if not _compare_rpr_elements(first_rPr, current_rPr):
            return None # 发现不一致，立即返回 None

    # 如果所有 Run 的 rPr 都一致，返回第一个 Run 的 rPr 的副本作为代表
    if first_rPr is not None:
        return copy_element(first_rPr)
    
    # 如果所有 Run 都没有 rPr，也认为是一致的 (空样式)
    return ET.Element(f"{{{NAMESPACES['w']}}}rPr") # 返回一个空的 rPr 元素

def create_minimal_safe_style2():
    """
    创建一个最小化的、完全安全的样式
    （当检测不到一致样式时使用）
    """
    minimal_rpr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")
    
    # 不添加任何可能导致样式不一致的元素
    # 保留默认样式
    audit_logger.warning("[Style] Creating minimal safe style (no formatting)")
    
    return None  # 返回 None 表示不继承任何样式

def create_minimal_safe_style() -> Optional[ET.Element]:
    """创建一个只包含极简安全（无任何格式）属性的 w:rPr，或 None。"""
    # 视需求决定是否返回一个空的 rPr 或者 None
    # 返回 None 更严谨，表示没有可应用的样式
    return None # 或者 ET.Element(f"{{{NAMESPACES['w']}}}rPr")

def extract_safe_style_from_merged_run(merged_run):
    """
    从合并 run 中提取安全的样式
    
    合并 run 是由 merge_runs() 创建的，其 rPr 已经经过处理
    """
    rpr = merged_run.find('w:rPr', NAMESPACES)
    
    if rpr is None:
        return None
    
    # 合并 run 的 rPr 已经包含公共样式，直接复制
    pending_count = merged_run.get('_pending_count', '0')
    audit_logger.info(f"[Style] Extracting style from merged run (merged {pending_count} original runs)")
    
    return copy_rpr_element(rpr)
def reconstruct_translated_paragraph(
    original_para_data: Dict[str, any], 
    translated_full_text: str
) -> ET.Element:
    """
    根据原始段落的详细分段信息和 LLM 返回的翻译结果，重建新的段落节点。
    """
    original_p_node = original_para_data["p_node"]
    segments = original_para_data["segments"]
    segment_separator = original_para_data.get("segment_separator", "【SEG】")
    
    new_p_node = ET.Element(f"{{{NAMESPACES['w']}}}p")
    original_pPr = original_p_node.find(f"{{{NAMESPACES['w']}}}pPr", NAMESPACES)
    if original_pPr is not None:
        new_p_node.append(copy_element(original_pPr))

    if translated_full_text is None:
        translated_full_text = original_para_data["full_text_for_llm"]
        audit_logger.warning(f"[Reconstruct] translated_full_text is None, using original text as fallback.")
    
    # 分割翻译文本
    translated_text_segments = translated_full_text.split(segment_separator)
    
    # 验证分割结果数量
    # 应该与 segments 数量一致 (因为 parse_paragraph_structure 为每个 segment 都添加了分隔符/内容)
    if len(translated_text_segments) != len(segments):
        audit_logger.error(
            f"[Reconstruct] Segment count mismatch! "
            f"Expected {len(segments)} segments, but got {len(translated_text_segments)} after splitting. "
            f"Original: '{original_para_data['full_text_for_llm'][:100]}' "
            f"Translated: '{translated_full_text[:100]}'"
        )
        # 如果数量不匹配，尝试尽力而为，或者回退
        # 这里我们继续，但在取值时会做边界检查
    
    for i, segment_info in enumerate(segments):
        seg_type = segment_info['type']
        
        # 获取对应的翻译文本
        if i < len(translated_text_segments):
            translated_segment_text = translated_text_segments[i]
        else:
            # 如果翻译片段不够，回退到原文
            translated_segment_text = segment_info.get('original_text', '')
            if seg_type == 'non_text_node':
                translated_segment_text = PLACEHOLDER_TAG

        if seg_type == 'text_run_group':
            new_r = ET.Element(f"{{{NAMESPACES['w']}}}r")
            if segment_info['common_rPr'] is not None:
                new_r.append(copy_rpr_element(segment_info['common_rPr']))
            
            new_t = ET.SubElement(new_r, f"{{{NAMESPACES['w']}}}t")
            new_t.text = translated_segment_text
            if translated_segment_text and (translated_segment_text.startswith(' ') or translated_segment_text.endswith(' ')):
                new_t.set(f"{{{NAMESPACES['xml']}}}space", "preserve")
            
            audit_logger.debug(f"[Reconstruct] Text segment {i}: '{segment_info['original_text'][:20]}' -> '{translated_segment_text[:20]}'")
            new_p_node.append(new_r)
            
        elif seg_type == 'non_text_node':
            # 非文本节点直接复制，不占用翻译结果 (虽然翻译结果中有占位符)
            new_p_node.append(copy_element(segment_info['original_node']))
            
        elif seg_type == 'hyperlink':
            original_hyperlink_node = segment_info['original_node']
            new_hyperlink_node = copy_element(original_hyperlink_node)
            
            first_t_in_hyperlink = new_hyperlink_node.find(f".//{{{NAMESPACES['w']}}}t", NAMESPACES)
            if first_t_in_hyperlink is not None:
                first_t_in_hyperlink.text = translated_segment_text
                if translated_segment_text and (translated_segment_text.startswith(' ') or translated_segment_text.endswith(' ')):
                    first_t_in_hyperlink.set(f"{{{NAMESPACES['xml']}}}space", "preserve")

                all_inner_runs = new_hyperlink_node.findall(f".//{{{NAMESPACES['w']}}}r", NAMESPACES)
                for run_idx, r_elem in enumerate(all_inner_runs):
                    if run_idx > 0:
                        parent = r_elem.getparent()
                        if parent is not None:
                            parent.remove(r_elem)
            
            new_p_node.append(new_hyperlink_node)
            
    return new_p_node


def split_text_by_llm_placeholders(translated_text: str, segments: List[Dict[str, Any]]) -> List[str]:
    """
    根据 LLM 返回的翻译文本和原始 segments 信息，将翻译文本分割成对应的片段。
    假设 LLM 会精确地返回 PLACEHOLDER_TAG，且顺序一致。
    
    ⚠️ 如果 LLM 还会返回 `::` 或 `::::`，这个函数需要更复杂地解析它们。
    """
    output_segments = []
    
    # 1. 识别翻译文本中的占位符位置
    ph_indices = []
    temp_text = translated_text
    while PLACEHOLDER_TAG in temp_text:
        idx = temp_text.find(PLACEHOLDER_TAG)
        ph_indices.append(len(translated_text) - len(temp_text) + idx)
        temp_text = temp_text[idx + len(PLACEHOLDER_TAG):]

    # 2. 根据原始 segments 的类型和占位符位置进行分割
    last_split_idx = 0
    current_ph_idx = 0
    
    for i, segment_info in enumerate(segments):
        if segment_info['type'] == 'text_run_group' or segment_info['type'] == 'hyperlink':
            # 文本类型，提取翻译文本
            if current_ph_idx < len(ph_indices): # 如果有占位符
                 # 找到下一个占位符或文本结束
                 next_ph_pos = ph_indices[current_ph_idx]
                 text_segment = translated_text[last_split_idx:next_ph_pos]
                 output_segments.append(text_segment)
                 last_split_idx = next_ph_pos # 占位符会在下一个迭代中跳过
            else: # 没有更多占位符，就是剩余的文本
                text_segment = translated_text[last_split_idx:]
                output_segments.append(text_segment)
                last_split_idx = len(translated_text) # 标记已处理所有文本
                break # 没有更多占位符，也没有更多文本
            
        elif segment_info['type'] == 'non_text_node':
            # 非文本类型，它在翻译文本中表现为 PLACEHOLDER_TAG
            # 所以我们跳过占位符，并将其作为一个空字符串或占位符文本添加到 output_segments
            if current_ph_idx < len(ph_indices):
                output_segments.append(translated_text[last_split_idx : ph_indices[current_ph_idx]]) # 提取占位符前的文本
                last_split_idx = ph_indices[current_ph_idx] + len(PLACEHOLDER_TAG) # 跳过占位符本身
                output_segments.append(PLACEHOLDER_TAG) # 标记这是一个占位符对应的位置
                current_ph_idx += 1
            else:
                # 应该总是有占位符的，如果走到这里说明不匹配
                audit_logger.error(f"Mismatch in split_text_by_llm_placeholders: Non-text segment but no placeholder found in translated text.")
                output_segments.append(PLACEHOLDER_TAG) # 强行添加一个
        
    # 处理剩余的翻译文本（如果有的话）
    if last_split_idx < len(translated_text):
        output_segments.append(translated_text[last_split_idx:])

    # 过滤掉连续的空字符串，只保留有效的部分
    final_output = [s for s in output_segments if s is not None and s != '']

    # 确保 output_segments 的数量与原始的 text_run_group/hyperlink 数量匹配
    # 对于 non_text_node，其对应的翻译文本应该直接是 PLACEHOLDER_TAG
    
    # 这是一个简化版，对于复杂的 LLM 占位符处理可能不够
    # 更好的方法是让 LLM 明确地返回像 `__TEXT_0__ <PH> __TEXT_1__ <PH> ...` 这样的结构
    # 或者用 regex.split(f"({re.escape(PLACEHOLDER_TAG)})", translated_text)
    
    # 重新构建更匹配的输出，让每个 segment 都有一个对应的 translated_segment
    # 构建一个与 `segments` 列表长度相同的列表
    final_translated_segments = []
    
    # 使用 regex.split 来分割
    split_parts = re.split(f"({re.escape(PLACEHOLDER_TAG)})", translated_text)
    
    # 过滤掉可能出现的空字符串
    split_parts = [p for p in split_parts if p != '']

    # 迭代原始 segments，并尝试从 split_parts 中分配翻译文本
    split_parts_idx = 0
    for segment_info in segments:
        if segment_info['type'] == 'text_run_group' or segment_info['type'] == 'hyperlink':
            # 文本类型，应该对应 split_parts 中的一个文本段
            if split_parts_idx < len(split_parts):
                final_translated_segments.append(split_parts[split_parts_idx])
                split_parts_idx += 1
            else:
                # 翻译文本段不够，回退到原文
                audit_logger.warning(f"Translated text segment missing for original text: '{segment_info.get('original_text', '')}'. Using original text as fallback.")
                final_translated_segments.append(segment_info.get('original_text', '')) # 回退
        elif segment_info['type'] == 'non_text_node':
            # 非文本类型，应该对应 split_parts 中的一个 PLACEHOLDER_TAG
            if split_parts_idx < len(split_parts) and split_parts[split_parts_idx] == PLACEHOLDER_TAG:
                final_translated_segments.append(PLACEHOLDER_TAG) # 仍然返回占位符
                split_parts_idx += 1
            else:
                # 占位符不匹配，可能 LLM 没有返回占位符或顺序错误
                audit_logger.error(f"Translated text placeholder missing for non-text node. Expected '{PLACEHOLDER_TAG}'. Actual: '{split_parts[split_parts_idx] if split_parts_idx < len(split_parts) else 'None'}'. Using '{PLACEHOLDER_TAG}' as fallback.")
                final_translated_segments.append(PLACEHOLDER_TAG) # 强行添加占位符

    # 如果 translated_full_text 中有比原始 segments 更多的内容，将其附加到最后一个文本段
    if split_parts_idx < len(split_parts):
        remaining_text = "".join(split_parts[split_parts_idx:])
        if final_translated_segments and (segments[-1]['type'] == 'text_run_group' or segments[-1]['type'] == 'hyperlink'):
            final_translated_segments[-1] += remaining_text
        else:
            final_translated_segments.append(remaining_text) # 没有文本段可以附加，直接作为新段

    return final_translated_segments

def create_run_with_style(text, source_run):
    """
    创建一个新 run，继承 source_run 的样式
    """
    new_r = ET.Element(f"{{{NAMESPACES['w']}}}r")
    
    source_rpr = source_run.find('w:rPr', NAMESPACES)
    if source_rpr is not None:
        new_rpr = copy_rpr_element(source_rpr)
        new_r.append(new_rpr)
    
    new_t = ET.SubElement(new_r, f"{{{NAMESPACES['w']}}}t")
    new_t.text = text
    new_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    
    return new_r


def create_plain_run(text):
    """
    创建一个无格式的 run
    """
    new_r = ET.Element(f"{{{NAMESPACES['w']}}}r")
    new_t = ET.SubElement(new_r, f"{{{NAMESPACES['w']}}}t")
    new_t.text = text
    new_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    return new_r

def parse_paragraph_structure(
    p_node: ET.Element,
    *,
    style_manager: StyleManager, 
    map_math_size: bool = False
) -> Dict[str, Any]:
    """
    解析单个段落节点，提取文本和占位符信息，同时基于样式一致性合并相邻的 Run。
    返回一个包含原始节点、LLM所需文本和重构所需段落信息的结构。
    """
    
    # ⚠️ 定义分隔符
    SEGMENT_SEPARATOR = "【SEG】"
    
    translation_segments = []  
    text_for_llm_parts = []   

    all_paragraph_children = list(p_node)

    i = 0
    while i < len(all_paragraph_children):
        current_child = all_paragraph_children[i]
        tag = current_child.tag

        # --- 处理普通的 Run (<w:r>) ---
        if tag == f"{{{NAMESPACES['w']}}}r":
            # 检查 run 中是否包含特殊元素（域代码、图片、对象等）
            # 如果包含，则将其视为 non_text_node 进行保留，不提取文本进行翻译
            has_special_content = (
                current_child.find(f".//{{{NAMESPACES['w']}}}fldChar", NAMESPACES) is not None or
                current_child.find(f".//{{{NAMESPACES['w']}}}instrText", NAMESPACES) is not None or
                current_child.find(f".//{{{NAMESPACES['w']}}}drawing", NAMESPACES) is not None or
                current_child.find(f".//{{{NAMESPACES['w']}}}object", NAMESPACES) is not None or
                current_child.find(f".//{{{NAMESPACES['w']}}}pict", NAMESPACES) is not None
            )

            if has_special_content:
                translation_segments.append({
                    'type': 'non_text_node',
                    'original_node': current_child,
                    'is_math': False,
                })
                
                if text_for_llm_parts:
                    text_for_llm_parts.append(SEGMENT_SEPARATOR)
                text_for_llm_parts.append(PLACEHOLDER_TAG)
                
                i += 1
                continue

            current_runs_group = [current_child]
            
            j = i + 1
            while j < len(all_paragraph_children):
                next_child = all_paragraph_children[j]
                if next_child.tag == f"{{{NAMESPACES['w']}}}r":
                    temp_runs_for_check = current_runs_group + [next_child]
                    
                    texts_in_temp_runs = [r.find('w:t', NAMESPACES).text for r in temp_runs_for_check if r.find('w:t', NAMESPACES) is not None and r.find('w:t', NAMESPACES).text]
                    audit_logger.debug(f"Checking consistency for runs: {texts_in_temp_runs}")
                    
                    consistent_rPr_elem = select_consistent_style(temp_runs_for_check)
                    
                    if consistent_rPr_elem is not None: 
                        current_runs_group.append(next_child)
                        j += 1
                    else: 
                        audit_logger.info(f"  [Parse Paragraph] Runs cannot be merged due to style inconsistency for: {texts_in_temp_runs}. Breaking merge attempt.")
                        break 
                else: 
                    break 
            
            final_common_rpr = None
            if len(current_runs_group) == 1:
                final_common_rpr = current_runs_group[0].find('w:rPr', NAMESPACES)
                if final_common_rpr is not None:
                    final_common_rpr = copy_rpr_element(final_common_rpr)
            else:
                final_common_rpr = select_consistent_style(current_runs_group)
                if final_common_rpr is None:
                    audit_logger.error(f"[Parse Paragraph] ERROR: select_consistent_style returned None for a group it previously deemed consistent! Using first run's rPr as fallback.")
                    first_rpr_of_group = current_runs_group[0].find('w:rPr', NAMESPACES)
                    if first_rpr_of_group is not None:
                        final_common_rpr = copy_rpr_element(first_rpr_of_group)
                    else:
                        final_common_rpr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")

            merged_text_parts = []
            for run in current_runs_group:
                text_elem = run.find(f"{{{NAMESPACES['w']}}}t", NAMESPACES)
                if text_elem is not None and text_elem.text is not None:
                    merged_text_parts.append(text_elem.text)
            merged_text = "".join(merged_text_parts)

            translation_segments.append({
                'type': 'text_run_group',
                'original_text': merged_text,
                'common_rPr': final_common_rpr,
                'original_runs': current_runs_group,
            })
            
            # ⚠️⚠️⚠️ 关键修改：添加分隔符逻辑
            if text_for_llm_parts:  # 如果不是第一个 segment，先添加分隔符
                text_for_llm_parts.append(SEGMENT_SEPARATOR)
            text_for_llm_parts.append(merged_text)
            
            i = j

        # --- 处理非 Run 的占位符元素 ---
        elif tag == f"{{{NAMESPACES['m']}}}oMath" or tag == f"{{{NAMESPACES['m']}}}oMathPara" or \
             tag == f"{{{NAMESPACES['w']}}}drawing" or tag == f"{{{NAMESPACES['w']}}}object":
            
            translation_segments.append({
                'type': 'non_text_node',
                'original_node': current_child,
                'is_math': (tag == f"{{{NAMESPACES['m']}}}oMath" or tag == f"{{{NAMESPACES['m']}}}oMathPara"),
            })
            
            # ⚠️⚠️⚠️ 关键修改：占位符也需要分隔符
            if text_for_llm_parts:
                text_for_llm_parts.append(SEGMENT_SEPARATOR)
            text_for_llm_parts.append(PLACEHOLDER_TAG)
            
            i += 1 
        
        # --- 处理超链接 ---
        elif tag == f"{{{NAMESPACES['w']}}}hyperlink":
            hyperlink_runs_group = []
            hyperlink_text_parts = []
            
            for hr_elem in current_child.findall(f".//{{{NAMESPACES['w']}}}r", NAMESPACES):
                ht_elem = hr_elem.find(f"{{{NAMESPACES['w']}}}t", NAMESPACES)
                if ht_elem is not None and ht_elem.text:
                    hyperlink_runs_group.append(hr_elem)
                    hyperlink_text_parts.append(ht_elem.text)
            
            hyperlink_common_rpr = None
            if hyperlink_runs_group:
                hyperlink_common_rpr = select_consistent_style(hyperlink_runs_group)
                if hyperlink_common_rpr is None:
                    audit_logger.warning(f"[Parse Paragraph] Hyperlink internal runs have inconsistent styles. Using first run's rPr as fallback.")
                    first_hyperlink_rpr = hyperlink_runs_group[0].find('w:rPr', NAMESPACES)
                    if first_hyperlink_rpr is not None:
                        hyperlink_common_rpr = copy_rpr_element(first_hyperlink_rpr)
                    else:
                        hyperlink_common_rpr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")

            merged_hyperlink_text = "".join(hyperlink_text_parts)
            translation_segments.append({
                'type': 'hyperlink',
                'original_node': current_child,
                'original_text': merged_hyperlink_text,
                'common_rPr': hyperlink_common_rpr,
            })
            
            # ⚠️⚠️⚠️ 关键修改：超链接也需要分隔符
            if text_for_llm_parts:
                text_for_llm_parts.append(SEGMENT_SEPARATOR)
            text_for_llm_parts.append(merged_hyperlink_text)

            i += 1 

        else: 
            audit_logger.warning(f"[Parse Paragraph] Unhandled paragraph child tag: {tag}. Skipping.")
            i += 1

    # ⚠️⚠️⚠️ 添加调试日志
    full_text_with_separators = "".join(text_for_llm_parts)
    segment_count = len([s for s in translation_segments if s['type'] in ('text_run_group', 'hyperlink')])
    separator_count = full_text_with_separators.count(SEGMENT_SEPARATOR)
    
    if separator_count == segment_count - 1:
        audit_logger.debug(f"[Parse] ✓ Separators correct: {separator_count} separators for {segment_count} segments")
    else:
        audit_logger.warning(
            f"[Parse] ⚠️ Separator count mismatch! "
            f"Expected {segment_count - 1} separators, but found {separator_count}. "
            f"full_text: '{full_text_with_separators[:100]}'"
        )

    return {
        "p_node": p_node,
        "segments": translation_segments,
        "full_text_for_llm": full_text_with_separators,  # ⚠️ 包含分隔符
        "segment_separator": SEGMENT_SEPARATOR  # ⚠️ 保存分隔符供重构时使用
    }



def merge_runs(pending_runs, p_node):
    """
    将多个短 run 合并成一个虚拟的"合并 run"，用于样式分析。
    
    Args:
        pending_runs: [(run_element, text), ...] 列表
        p_node: 段落节点
    
    Returns:
        合并后的虚拟 run 元素
    """
    # 创建一个虚拟的 run 用于样式检测
    merged_run = ET.Element(f"{{{NAMESPACES['w']}}}r")
    
    # 【关键】提取所有 pending_runs 的公共样式
    common_rpr = extract_common_rpr(pending_runs)
    
    if common_rpr is not None:
        merged_run.append(common_rpr)
    
    # 创建合并文本
    merged_text = "".join([text for _, text in pending_runs])
    t = ET.SubElement(merged_run, f"{{{NAMESPACES['w']}}}t")
    t.text = merged_text
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    
    # 【标记】添加一个特殊属性，标记这是合并 run（便于调试）
    merged_run.set('_merged', 'true')
    merged_run.set('_pending_count', str(len(pending_runs)))
    
    audit_logger.info(f"[Merge] Merged {len(pending_runs)} runs into one: '{merged_text}'")
    
    return merged_run
def extract_common_rpr(pending_runs):
    """
    从多个短 run 中提取公共的样式（rPr）。
    
    策略：
    - 如果所有 run 的某个样式元素相同 → 保留
    - 如果不同或缺失 → 移除
    """
    if not pending_runs:
        return None
    
    # 提取所有 rPr
    rpr_list = []
    for run_elem, _ in pending_runs:
        rpr = run_elem.find('w:rPr', NAMESPACES)
        rpr_list.append(rpr)
    
    # 如果所有 run 都没有 rPr，返回 None
    if all(rpr is None for rpr in rpr_list):
        return None
    
    # 如果只有一个 run 有 rPr，直接用它
    if sum(1 for rpr in rpr_list if rpr is not None) == 1:
        for rpr in rpr_list:
            if rpr is not None:
                return copy_rpr_element(rpr)
    
    # 多个 run 有 rPr - 提取公共元素
    base_rpr = next((rpr for rpr in rpr_list if rpr is not None), None)
    if base_rpr is None:
        return None
    
    new_rpr = ET.Element(f"{{{NAMESPACES['w']}}}rPr")
    
    # 只保留所有 rPr 都有的元素
    for elem in base_rpr:
        tag_name = elem.tag
        
        # 检查是否所有其他 rPr 都有这个元素
        all_have_it = all(
            rpr.find(tag_name, NAMESPACES) is not None 
            if rpr is not None 
            else False
            for rpr in rpr_list
        )
        
        if all_have_it:
            # 检查属性是否相同
            all_same = all(
                rpr.find(tag_name, NAMESPACES).attrib == elem.attrib
                if rpr is not None
                else False
                for rpr in rpr_list
            )
            
            if all_same:
                # 属性也相同，保留这个元素
                new_child = ET.Element(tag_name, elem.attrib)
                new_child.text = elem.text
                new_rpr.append(new_child)
                audit_logger.info(f"[CommonStyle] Kept common element: {tag_name.split('}')[-1]}")
            else:
                audit_logger.info(f"[CommonStyle] Removed divergent attributes in: {tag_name.split('}')[-1]}")
        else:
            audit_logger.info(f"[CommonStyle] Removed inconsistent element: {tag_name.split('}')[-1]}")
    
    return new_rpr if len(list(new_rpr)) > 0 else None
# docx_processor.py
def process_xml_part(
    xml_content: bytes,
    *,
    style_manager=None,
    materialize_styles=False,
    debug_materialize=False,
    filename="",
    map_math_size=False,
    translate_function=None,
    **translate_kwargs
) -> bytes:  # <--- 添加返回类型注解
    """
    编排函数：解析 XML -> (可选)样式实体化 -> 提取段落 -> 翻译 -> 重构 -> 返回 XML bytes
    """
    try:
        # 1. 解析 XML (从 bytes 转为 ElementTree)
        root = ET.fromstring(xml_content)
        
        # 2. 可选：样式实体化
        if materialize_styles and style_manager:
            audit_logger.info(f"[Core] Materializing styles for {filename}...")
            materialize_styles_from_style_defs(
                root,
                style_manager=style_manager,
                filename=filename
            )
            audit_logger.info(f"[Core] Styles materialized for {filename}.")

        # 3. 准备翻译函数
        if translate_function is None:
            translate_function = translate_kwargs.get('translate_function', llm_translate_concurrent)

        # 4. 提取段落数据 (Extraction)
        # 构建父节点映射
        parent_map = {c: p for p in root.iter() for c in p}

        all_p_data_with_parent = []
        
        all_p_nodes = root.findall(f".//{{{NAMESPACES['w']}}}p")
        for p_node in all_p_nodes:
            # 调用辅助函数提取信息
            data = parse_paragraph_structure(
                p_node=p_node,
                style_manager=style_manager,
                map_math_size=map_math_size
            )
            # 只有当段落有实质内容（文本或占位符）时才加入待翻译列表
            if data["full_text_for_llm"].strip():
                # 找到父节点
                parent_node = parent_map.get(p_node)
                if parent_node is None:
                    # 检查是否是 root 的直接子节点
                    if p_node in root:
                        parent_node = root
                    else:
                        audit_logger.warning(f"Could not find parent for p_node in {filename}. Skipping translation for this paragraph.")
                        continue

                # 找到 p_node 在其父节点中的索引
                try:
                    index_in_parent = list(parent_node).index(p_node)
                except ValueError:
                    audit_logger.warning(f"Could not find p_node in its parent's children list in {filename}. Skipping translation for this paragraph.")
                    continue

                # 存储父节点和索引信息
                data["_parent_node"] = parent_node
                data["_index_in_parent"] = index_in_parent
                all_p_data_with_parent.append(data)

        # 5. 如果没有需要翻译的内容，直接返回原 XML
        if not all_p_data_with_parent:
            audit_logger.info(f"[Process] No paragraphs to translate in {filename}. Returning original XML.")
            return ET.tostring(root, encoding='utf-8')  # <--- ⚠️ 提前返回点1

        # 6. 批量翻译 (Translation)
        print(f"  [Process] Translating {len(all_p_data_with_parent)} paragraphs in {filename}...")
        
        api_kwargs = {k: v for k, v in translate_kwargs.items() if k != 'translate_function'}
        
        # 传递 all_p_data_with_parent 给翻译函数
        translated_full_texts = translate_function(all_p_data_with_parent, **api_kwargs)

        # 7. 重构段落 (Reconstruction)
        for i, original_para_data in enumerate(all_p_data_with_parent):
            original_p_node = original_para_data["p_node"]
            parent_node = original_para_data["_parent_node"]
            index_in_parent = original_para_data["_index_in_parent"]

            translated_full_text = translated_full_texts[i]

            new_p_node = reconstruct_translated_paragraph(original_para_data, translated_full_text)

            # 替换原始的 p_node
            try:
                parent_node.remove(original_p_node)
                parent_node.insert(index_in_parent, new_p_node)
            except ValueError as e:
                error_logger.error(f"Error replacing paragraph node (index {index_in_parent}) in {filename}: {e}. Original node: {ET.tostring(original_p_node, encoding='utf-8').decode('utf-8')[:100]}")
            except IndexError as e:
                error_logger.error(f"Index error replacing paragraph node (index {index_in_parent}) in {filename}: {e}. Parent has {len(parent_node)} children. Original node: {ET.tostring(original_p_node, encoding='utf-8').decode('utf-8')[:100]}")

        # 8. 返回序列化后的 XML (bytes)
        audit_logger.info(f"[Process] Translation completed for {filename}.")
        return ET.tostring(root, encoding='utf-8')  # <--- ⚠️ 最终返回点

    except Exception as e:
        error_logger.error(f"Unexpected error in process_xml_part for {filename}: {e}")
        # 如果出现任何错误，返回原始 XML 内容
        return xml_content  # <--- ⚠️ 异常返


def translate_docx(
    input_docx_path: str, 
    output_docx_path: str, 
    *, 
    use_modern_font_table: bool, 
    custom_styles_path: Optional[str] = None,
    materialize_styles: bool = False,
    debug_materialize: bool = False,
    map_math_size: bool = False,  # 新增参数
    **kwargs
):
    """
    翻译 DOCX 文件，并根据选项替换字体表、样式表，以及应用样式。
    """
    if not os.path.exists(input_docx_path):
        print(f"错误：找不到输入文件 '{input_docx_path}'。请检查路径和文件名是否正确。")
        return

    # --- 预处理自定义样式文件 ---
    custom_styles_content = None
    if custom_styles_path:
        if os.path.exists(custom_styles_path):
            with open(custom_styles_path, 'rb') as f:
                custom_styles_content = f.read()
            print(f"Loaded custom styles from: {custom_styles_path}")
        else:
            print(f"[Warning] Custom styles file not found at '{custom_styles_path}'. Will use original styles.")
    
    try:
        with zipfile.ZipFile(input_docx_path, 'r') as z_in:
            
            # --- 预加载样式 ---
            style_manager = None
            styles_xml_data = None
            
            if custom_styles_content:
                styles_xml_data = custom_styles_content
            elif 'word/styles.xml' in z_in.namelist():
                styles_xml_data = z_in.read('word/styles.xml')
            
            if styles_xml_data:
                print("Initializing StyleManager to read style definitions...")
                style_manager = StyleManager(styles_xml_data)
                
                # 调试：打印所有样式
                style_manager.debug_print_all_styles()
                
                all_sizes = style_manager.get_all_style_sizes()
                print(f"\nTotal styles with resolved sizes: {len(all_sizes)}")
                default_size = style_manager.get_default_size()
                if default_size:
                    print(f"Default size (Normal style): {default_size} half-points ({default_size/2} pt)")
            else:
                print("[Warning] styles.xml not found. Style-based formatting will be unavailable.")

            with zipfile.ZipFile(output_docx_path, 'w', zipfile.ZIP_DEFLATED) as z_out:
                for item in z_in.infolist():
                    filename = item.filename
                    data = z_in.read(filename)

                    # 场景 1: 替换字体表
                    if use_modern_font_table and filename == 'word/fontTable.xml':
                        z_out.writestr('word/fontTable.xml', MODERN_FONT_TABLE_XML.encode('utf-8'))
                        print("Injected: Modern font table (word/fontTable.xml)")
                    
                    # 场景 2: 替换样式表
                    elif custom_styles_content and filename == 'word/styles.xml':
                        z_out.writestr('word/styles.xml', custom_styles_content)
                        print("Injected: Custom styles from specified file (word/styles.xml)")

                    # 场景 3: Word XML 文件
                    elif filename.startswith('word/') and filename.endswith('.xml'):
                        print(f"\nProcessing: {filename}")
                        processed_data = process_xml_part(
                            data, 
                            style_manager=style_manager, 
                            materialize_styles=materialize_styles,
                            debug_materialize=debug_materialize,
                            filename=filename,
                            map_math_size=map_math_size,  # 传递参数
                            **kwargs
                        )
                        z_out.writestr(filename, processed_data)

                    # 场景 4: 其他文件
                    else:
                        z_out.writestr(item, data)

    except zipfile.BadZipFile:
        print(f"错误：'{input_docx_path}' 不是一个有效的 .docx (zip) 文件，或文件已损坏。")
        return
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"处理过程中发生未知错误: {e}")
        return

    print(f"\n完成：输出文件 -> {output_docx_path}")
    if debug_materialize:
        print(f"[DEBUG] Debug files generated:")
        print(f"  - DEBUG_01_after_materialization.xml")
        print(f"  - DEBUG_02_after_translation.xml")
        print(f"  - DEBUG_03_final_result.xml")
    print(f"注意：翻译错误记录在 {LOG_FILE}，完整翻译审计记录在 {AUDIT_LOG_FILE}。")
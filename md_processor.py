# -*- coding: utf-8 -*-
"""
Markdown 文件翻译处理模块（改进版 + 调试模式）
"""

import os
import re
from typing import Callable, Dict, Any, List, Tuple
import logging

logger = logging.getLogger(__name__)


class MarkdownTranslator:
    """改进的 Markdown 文件翻译器"""
    
    def __init__(self, translate_function: Callable, translate_kwargs: Dict[str, Any], debug=True):
        self.translate_function = translate_function
        self.translate_kwargs = translate_kwargs
        self.debug = debug

    def debug_print(self, msg: str):
        """调试打印"""
        if self.debug:
            print(f"[DEBUG] {msg}")

    def extract_frontmatter(self, content: str) -> Tuple[str, str]:
        """
        提取 YAML 前置和主体内容
        返回: (frontmatter, body)
        """
        match = re.match(r'^(---\n.*?\n---\n)', content, re.DOTALL)
        if match:
            frontmatter = match.group(1)
            body = content[len(frontmatter):]
            self.debug_print(f"检测到前置，长度: {len(frontmatter)}")
            return frontmatter, body
        return '', content

    def is_protected_line(self, line: str) -> bool:
        """
        判断一行是否应该被保护（不翻译）
        
        只保护：代码块、链接、URL、YAML键值对
        """
        stripped = line.strip()
        
        # 空行不翻译
        if not stripped:
            return True
        
        # 代码块边界
        if stripped.startswith('```'):
            return True
        
        # 缩进代码块（4个空格或制表符开头）
        if line.startswith('    ') or line.startswith('\t'):
            return True
        
        # HTML 标签单独成行（如 </think>）
        if re.match(r'^</?[a-zA-Z][^>]*>$', stripped):
            return True
        
        # 表格分隔线（|---|---|）
        if re.match(r'^\|\s*-+(\s*\|\s*-+)*\s*\|?\s*$', stripped):
            return True
        
        # YAML 前置部分（---）
        if stripped == '---':
            return True
        
        # 纯 YAML 键值对（key: value）- 但不是表格
        if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*\s*:\s*[^|]*$', stripped) and '|' not in stripped:
            # 但表格中的 `: ` 不算
            if not stripped.startswith('|'):
                return True
        
        # ❌ 不再保护以下内容（应该翻译）：
        # - 只包含 Markdown 标记的行（###、**、等）
        # - 表格行
        # - 链接和图片（需要翻译周围的文本）
        
        return False

    def extract_translatable_segments(self, content: str) -> List[Dict[str, Any]]:
        """
        从内容中提取所有可翻译的段落
        """
        lines = content.split('\n')
        segments = []
        in_code_block = False
        
        for line_num, line in enumerate(lines):
            # 追踪代码块状态
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                self.debug_print(f"行 {line_num}: 代码块开关 -> in_code_block={in_code_block}")
                continue
            
            # 代码块内的行不翻译
            if in_code_block:
                self.debug_print(f"行 {line_num}: 在代码块内，跳过")
                continue
            
            # 检查是否应该保护这一行
            if self.is_protected_line(line):
                self.debug_print(f"行 {line_num}: 被保护，跳过")
                continue
            
            stripped = line.strip()
            if stripped:  # 非空行
                segments.append({
                    'original': stripped,
                    'line_number': line_num,
                    'full_line': line,
                    'indent': len(line) - len(line.lstrip())
                })
                self.debug_print(f"行 {line_num}: 提取可翻译文本 -> '{stripped[:60]}...'")
        
        return segments

    def translate_md(self, input_md_path: str, output_md_path: str):
        """
        翻译 Markdown 文件
        """
        print(f"\n{'='*60}")
        print(f"开始翻译 Markdown 文件: {input_md_path}")
        print(f"{'='*60}\n")
        
        # 1. 读取文件
        with open(input_md_path, 'r', encoding='utf-8') as f:
            original_content = f.read()
        
        print(f"✓ 文件大小: {len(original_content)} 字符\n")
        
        # 2. 分离前置和主体
        frontmatter, body = self.extract_frontmatter(original_content)
        if frontmatter:
            print(f"✓ 检测到 YAML 前置\n")
        
        # 3. 提取可翻译的段落
        segments = self.extract_translatable_segments(body)
        print(f"\n✓ 识别到 {len(segments)} 个可翻译段落\n")
        
        if not segments:
            print("⚠ 没有发现需要翻译的文本内容")
            with open(output_md_path, 'w', encoding='utf-8') as f:
                f.write(original_content)
            return
        
        # 4. 构建翻译输入（去重）
        unique_texts = {}  # original_text -> segment_list
        for seg in segments:
            orig = seg['original']
            if orig not in unique_texts:
                unique_texts[orig] = []
            unique_texts[orig].append(seg)
        
        texts_to_translate = list(unique_texts.keys())
        print(f"✓ 需要翻译的唯一文本数: {len(texts_to_translate)}\n")
        
        # 5. 打印所有待翻译的原文
        print("="*60)
        print("【待翻译的原文段落】")
        print("="*60)
        for idx, text in enumerate(texts_to_translate, 1):
            print(f"\n[{idx}] 原文:")
            print(f"    {repr(text[:80])}")
            print(f"    长度: {len(text)} 字符")
        
        print(f"\n{'='*60}")
        print("开始调用翻译 API...")
        print(f"{'='*60}\n")
        
        # 6. 调用翻译 API
        para_data_list = [
            {"full_text": text, "paragraph_index": idx}
            for idx, text in enumerate(texts_to_translate)
        ]
        
        translated_texts = self.translate_function(
            para_data_list=para_data_list,
            **self.translate_kwargs
        )
        
        # 7. 打印翻译结果
        print(f"\n{'='*60}")
        print("【翻译结果对比】")
        print(f"{'='*60}")
        
        if translated_texts and len(translated_texts) == len(texts_to_translate):
            for idx, (orig, trans) in enumerate(zip(texts_to_translate, translated_texts), 1):
                print(f"\n[{idx}]")
                print(f"  原文: {repr(orig[:60])}")
                print(f"  译文: {repr(trans[:60] if trans else 'None')}")
                if orig == trans:
                    print(f"  ⚠ 警告: 翻译前后相同（可能未被翻译）")
        else:
            print(f"⚠ 警告: 翻译结果数量不匹配")
            print(f"  期望: {len(texts_to_translate)}")
            print(f"  实际: {len(translated_texts) if translated_texts else 0}")
            if not translated_texts:
                print("❌ 翻译失败，直接返回")
                return
        
        # 8. 构建翻译映射
        translation_map = {}
        for orig, trans in zip(texts_to_translate, translated_texts):
            translation_map[orig] = trans
        
        # 9. 替换内容
        lines = body.split('\n')
        translated_lines = []
        in_code_block = False
        replaced_count = 0
        
        for line_num, line in enumerate(lines):
            # 追踪代码块
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                translated_lines.append(line)
                continue
            
            # 代码块内不翻译
            if in_code_block:
                translated_lines.append(line)
                continue
            
            # 检查是否在翻译映射中
            stripped = line.strip()
            if stripped in translation_map:
                # 保持缩进
                indent = len(line) - len(line.lstrip())
                translated_text = translation_map[stripped]
                translated_lines.append(' ' * indent + translated_text)
                replaced_count += 1
                self.debug_print(f"行 {line_num}: 已替换")
            else:
                translated_lines.append(line)
        
        print(f"\n✓ 成功替换 {replaced_count} 行\n")
        
        # 10. 重新组合
        translated_body = '\n'.join(translated_lines)
        translated_content = frontmatter + translated_body
        
        # 11. 写入文件
        with open(output_md_path, 'w', encoding='utf-8') as f:
            f.write(translated_content)
        
        print(f"{'='*60}")
        print(f"✅ 翻译完成: {output_md_path}")
        print(f"{'='*60}\n")


def translate_markdown(
    input_md_path: str,
    output_md_path: str,
    translate_function: Callable,
    translate_kwargs: Dict[str, Any]
):
    """
    便捷函数：翻译 Markdown 文件
    """
    translator = MarkdownTranslator(translate_function, translate_kwargs, debug=True)
    translator.translate_md(input_md_path, output_md_path)

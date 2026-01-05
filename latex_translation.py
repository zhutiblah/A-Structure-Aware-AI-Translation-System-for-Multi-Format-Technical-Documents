# latex_translator.py
import re
from openai import OpenAI
import os
from typing import Optional, Dict, Any, List

class ClsStyTranslator:
    """通用LaTeX文件翻译器，支持.cls和.sty文件"""
    
    def __init__(self, api_key: Optional[str] = None, 
                 model: str = "gpt-4o-mini",
                 base_url: Optional[str] = None):
        """
        初始化翻译器
        :param api_key: API密钥
        :param model: 模型名称
        :param base_url: 自定义API端点
        """
        client_kwargs = {}
        if api_key:
            client_kwargs['api_key'] = api_key
        if base_url:
            client_kwargs['base_url'] = base_url
            
        self.client = OpenAI(**client_kwargs)
        self.model = model
    
    def extract_semantic_blocks(self, content: str) -> List[Dict]:
        """
        提取LaTeX文件中的语义块
        :param content: 文件内容
        :return: 语义块列表
        """
        blocks = []
        lines = content.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # 跳过空行和纯注释行
            if not line.strip() or line.strip().startswith('%'):
                i += 1
                continue
            
            # 1. 检测连续的 \def 命令块（重要改进）
            if re.match(r'\\def\\', line):
                block_start = i
                block_lines = [line]
                i += 1
                
                # 收集连续的 \def 命令（允许空行和注释）
                while i < len(lines):
                    next_line = lines[i]
                    # 如果是空行或注释，继续
                    if not next_line.strip() or next_line.strip().startswith('%'):
                        i += 1
                        continue
                    # 如果是 \def 命令，添加到块中
                    elif re.match(r'\\def\\', next_line):
                        block_lines.append(next_line)
                        i += 1
                    else:
                        # 遇到非 \def 命令，块结束
                        break
                
                block_content = '\n'.join(block_lines)
                blocks.append({
                    'start_line': block_start,
                    'end_line': i - 1,
                    'content': block_content,
                    'type': 'def_block'
                })
                continue
            
            # 2. 完整的命令定义（可能跨多行）
            elif re.match(r'\\(new|renew|provide)command', line):
                block_start = i
                block_lines = [line]
                brace_count = line.count('{') - line.count('}')
                i += 1
                
                # 继续读取直到括号平衡
                while i < len(lines) and brace_count > 0:
                    block_lines.append(lines[i])
                    brace_count += lines[i].count('{') - lines[i].count('}')
                    i += 1
                
                block_content = '\n'.join(block_lines)
                blocks.append({
                    'start_line': block_start,
                    'end_line': i - 1,
                    'content': block_content,
                    'type': 'command_definition'
                })
                continue
            
            # 3. 定理环境定义
            elif re.match(r'\\(newtheorem|theoremstyle)', line):
                blocks.append({
                    'start_line': i,
                    'end_line': i,
                    'content': line,
                    'type': 'theorem_definition'
                })
                i += 1
                continue
            
            # 4. 格式设置命令（可能跨多行）
            elif re.match(r'\\(titleformat|captionsetup|setlength|setcounter)', line):
                block_start = i
                block_lines = [line]
                brace_count = line.count('{') - line.count('}')
                i += 1
                
                while i < len(lines) and brace_count > 0:
                    block_lines.append(lines[i])
                    brace_count += lines[i].count('{') - lines[i].count('}')
                    i += 1
                
                block_content = '\n'.join(block_lines)
                blocks.append({
                    'start_line': block_start,
                    'end_line': i - 1,
                    'content': block_content,
                    'type': 'format_command'
                })
                continue
            
            # 5. 其他单行命令
            elif line.strip().startswith('\\'):
                blocks.append({
                    'start_line': i,
                    'end_line': i,
                    'content': line,
                    'type': 'single_command'
                })
                i += 1
                continue
            
            # 6. 普通文本行
            else:
                blocks.append({
                    'start_line': i,
                    'end_line': i,
                    'content': line,
                    'type': 'text'
                })
                i += 1
        
        return blocks
    
    def has_chinese(self, text: str) -> bool:
        """检查文本是否包含中文（排除注释）"""
        # 移除注释后再检查
        text_without_comments = re.sub(r'%.*$', '', text, flags=re.MULTILINE)
        return bool(re.search(r'[\u4e00-\u9fff]', text_without_comments))
    
    def filter_chinese_blocks(self, blocks: List[Dict]) -> List[Dict]:
        """
        过滤出包含中文的块
        :param blocks: 所有语义块
        :return: 包含中文的块
        """
        chinese_blocks = []
        for block in blocks:
            if self.has_chinese(block['content']):
                chinese_blocks.append(block)
        return chinese_blocks
    
    def group_blocks_for_translation(self, blocks: List[Dict], 
                                     max_tokens: int = 2000) -> List[List[Dict]]:
        """
        将块分组以批量翻译（提高效率）
        :param blocks: 待翻译的块列表
        :param max_tokens: 每组最大token数
        :return: 分组后的块
        """
        groups = []
        current_group = []
        current_tokens = 0
        
        for block in blocks:
            block_tokens = len(block['content']) * 1.5
            
            if current_tokens + block_tokens > max_tokens and current_group:
                groups.append(current_group)
                current_group = []
                current_tokens = 0
            
            current_group.append(block)
            current_tokens += block_tokens
        
        if current_group:
            groups.append(current_group)
        
        return groups
    def translate_blocks_group(self, group: List[Dict], retry_count: int = 3) -> List[str]:
        """
        翻译一组块
        :param group: 待翻译的块组
        :param retry_count: 重试次数
        :return: 翻译后的内容列表
        """
        # 构建翻译内容
        blocks_text = ""
        for idx, block in enumerate(group, 1):
            blocks_text += f"\n【块{idx}】\n{block['content']}\n"
        
        prompt = f"""你是LaTeX代码翻译专家。请翻译以下代码块中的中文为英文。

    **重要规则**：
    1. 只翻译中文文本，完全保留LaTeX命令、括号、反斜杠、大括号等
    2. 保持所有空格、换行、缩进不变
    3. 注释（%开头的行）不翻译，保持原样
    4. 对于 \\def 命令中的中文，翻译为对应的英文单词
    5. **重要**：翻译后的英文单词和LaTeX命令之间必须保留空格！
    - 例如："第\\xCJKnumber{{...}}章" 应翻译为 "Chapter \\xCJKnumber{{...}}"（Chapter后有空格）
    - 例如："图\\ref{{...}}" 应翻译为 "Figure \\ref{{...}}"（Figure后有空格）
    - 例如："表\\ref{{...}}" 应翻译为 "Table \\ref{{...}}"（Table后有空格）

    6. 专业术语对照：
    - 数字：零→zero, 一→one, 二→two, 三→three, 四→four, 五→five, 六→six, 七→seven, 八→eight, 九→nine, 十→ten
    - 数量：百→hundred, 千→thousand, 万→ten-thousand, 亿→hundred-million
    - 符号：负→minus, 正→plus
    - 章节结构：
        * "第...章" → "Chapter " (注意Chapter后有空格)
        * "第...节" → "Section " (注意Section后有空格)
        * "图" → "Figure " (注意Figure后有空格)
        * "表" → "Table " (注意Table后有空格)
    - 数学：定义→Definition, 例→Example, 注→Remark, 假设→Assumption, 命题→Proposition, 引理→Lemma, 定理→Theorem, 公理→Axiom, 推论→Corollary, 情形→Case, 猜想→Conjecture, 性质→Property

    **特别注意**：
    - "第\\xCJKnumber{{\\thecontentslabel}}章" 必须翻译为 "Chapter \\xCJKnumber{{\\thecontentslabel}}" （Chapter和反斜杠之间有空格）
    - 不要写成 "Chapter\\xCJKnumber" 这样会导致排版错误

    **输出要求**：
    - 直接输出翻译后的代码
    - 每个块之间用"---"分隔
    - 不要添加任何标记、编号或说明
    - 按顺序输出，第一个块的翻译，然后是"---"，然后第二个块的翻译，以此类推

    待翻译的代码块：
    {blocks_text}

    请直接输出翻译结果，块与块之间用"---"分隔："""

        for attempt in range(retry_count):
            try:
                message = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=4096,
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}]
                )
                
                response = message.choices[0].message.content.strip()
                
                # 按分隔符分割
                translations = [t.strip() for t in response.split('---') if t.strip()]
                
                # 后处理：确保英文单词和LaTeX命令之间有空格
                translations = [self._post_process_translation(t) for t in translations]
                
                if len(translations) == len(group):
                    return translations
                else:
                    print(f"  ⚠️ 翻译数量不匹配（期望{len(group)}，得到{len(translations)}）")
                    if attempt < retry_count - 1:
                        print(f"  正在重试...")
                        continue
                    # 如果最后一次还是不匹配，尝试逐个翻译
                    print(f"  改用逐个翻译模式...")
                    return self.translate_blocks_individually(group)
                
            except Exception as e:
                if attempt < retry_count - 1:
                    print(f"  ❌ 尝试 {attempt + 1}/{retry_count} 失败: {e}，重试中...")
                else:
                    print(f"  ❌ 所有重试失败: {e}，保留原文")
                    return [block['content'] for block in group]
        
        return [block['content'] for block in group]

    def _post_process_translation(self, text: str) -> str:
        """
        后处理翻译结果，确保格式正确
        :param text: 翻译后的文本
        :return: 修正后的文本
        """
        # 修复常见的空格缺失问题
        # Chapter\command → Chapter \command
        text = re.sub(r'(Chapter)(\\[a-zA-Z])', r'\1 \2', text)
        # Section\command → Section \command
        text = re.sub(r'(Section)(\\[a-zA-Z])', r'\1 \2', text)
        # Figure\command → Figure \command
        text = re.sub(r'(Figure)(\\[a-zA-Z])', r'\1 \2', text)
        # Table\command → Table \command
        text = re.sub(r'(Table)(\\[a-zA-Z])', r'\1 \2', text)
        # Definition\command → Definition \command
        text = re.sub(r'(Definition)(\\[a-zA-Z])', r'\1 \2', text)
        # Theorem\command → Theorem \command
        text = re.sub(r'(Theorem)(\\[a-zA-Z])', r'\1 \2', text)
        # Lemma\command → Lemma \command
        text = re.sub(r'(Lemma)(\\[a-zA-Z])', r'\1 \2', text)
        # Example\command → Example \command
        text = re.sub(r'(Example)(\\[a-zA-Z])', r'\1 \2', text)
        
        return text

    def translate_blocks_individually(self, blocks: List[Dict]) -> List[str]:
        """
        逐个翻译块（备用方案）
        """
        translations = []
        for block in blocks:
            prompt = f"""请翻译以下LaTeX代码中的中文为英文。只翻译中文，保持LaTeX命令和格式不变。直接输出翻译后的代码，不要添加任何说明。

{block['content']}"""
            
            try:
                message = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=2048,
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}]
                )
                translations.append(message.choices[0].message.content.strip())
            except Exception as e:
                print(f"  ⚠️ 单独翻译失败: {e}，保留原文")
                translations.append(block['content'])
        
        return translations
    
    def translate_file(self, input_file: str,
                      output_file: Optional[str] = None,
                      max_tokens_per_group: int = 2000,
                      verbose: bool = True) -> Dict[str, Any]:
        """
        翻译LaTeX文件（支持.cls和.sty）
        :param input_file: 输入文件
        :param output_file: 输出文件
        :param max_tokens_per_group: 每组最大token数
        :param verbose: 是否显示详细信息
        :return: 翻译统计
        """
        # 检查文件扩展名
        file_ext = os.path.splitext(input_file)[1]
        if file_ext not in ['.cls', '.sty']:
            raise ValueError(f"不支持的文件类型: {file_ext}。仅支持 .cls 和 .sty 文件")
        
        # 读取文件
        with open(input_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if verbose:
            print(f"📖 正在处理 {file_ext} 文件: {input_file}")
            print("📖 正在提取语义块...")
        
        # 提取语义块
        all_blocks = self.extract_semantic_blocks(content)
        
        if verbose:
            print(f"✓ 提取到 {len(all_blocks)} 个语义块")
        
        # 过滤包含中文的块
        chinese_blocks = self.filter_chinese_blocks(all_blocks)
        
        if not chinese_blocks:
            if verbose:
                print("✓ 文件中没有需要翻译的中文内容")
            return {
                'success': True,
                'blocks_found': 0,
                'blocks_translated': 0,
                'output_file': None
            }
        
        if verbose:
            print(f"✓ 找到 {len(chinese_blocks)} 个包含中文的块")
            print("\n示例块：")
            for i, block in enumerate(chinese_blocks[:3], 1):
                print(f"\n块{i} ({block['type']}):")
                preview = block['content'][:100].replace('\n', ' ')
                print(f"  {preview}{'...' if len(block['content']) > 100 else ''}")
        
        # 分组翻译
        groups = self.group_blocks_for_translation(chinese_blocks, max_tokens_per_group)
        
        if verbose:
            print(f"\n🔄 分为 {len(groups)} 组进行翻译...")
        
        # 翻译
        translations = {}
        for i, group in enumerate(groups, 1):
            if verbose:
                print(f"  翻译第 {i}/{len(groups)} 组（{len(group)}个块）...")
            
            translated = self.translate_blocks_group(group)
            
            for block, translation in zip(group, translated):
                translations[block['start_line']] = translation
        
        # 重建文件
        lines = content.split('\n')
        result_lines = []
        skip_until = -1
        
        for line_num, line in enumerate(lines):
            if line_num < skip_until:
                continue
            
            # 查找是否有对应的翻译
            translation_found = False
            for block in chinese_blocks:
                if block['start_line'] == line_num and line_num in translations:
                    result_lines.append(translations[line_num])
                    skip_until = block['end_line'] + 1
                    translation_found = True
                    break
            
            if not translation_found:
                result_lines.append(line)
        
        result = '\n'.join(result_lines)
        
        # 清理多余空行（但保留最多2个连续空行）
        result = re.sub(r'\n{4,}', '\n\n\n', result)
        
        # 保存
        if output_file is None:
            base_name = os.path.splitext(input_file)[0]
            output_file = f"{base_name}_en{file_ext}"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result)
        
        if verbose:
            print(f"\n✅ 翻译完成！")
            print(f"  输出文件: {output_file}")
            print(f"  翻译块数: {len(chinese_blocks)}")
        
        return {
            'success': True,
            'blocks_found': len(chinese_blocks),
            'blocks_translated': len(chinese_blocks),
            'output_file': output_file
        }


def translate_cls_or_sty_file(input_file: str,
                        output_file: Optional[str] = None,
                        api_key: Optional[str] = None,
                        model: str = "claude-sonnet-4-20250514",
                        base_url: Optional[str] = None,
                        max_tokens_per_group: int = 2000,
                        verbose: bool = True) -> Dict[str, Any]:
    """
    翻译LaTeX文件（.cls或.sty）
    
    示例：
        # 翻译 .cls 文件
        translate_cls_or_sty_file(
            input_file="template.cls",
            api_key="your-key"
        )
        
        # 翻译 .sty 文件
        translate_cls_or_sty_file(
            input_file="chinese_numbers.sty",
            api_key="your-key",
            base_url="https://api.example.com"
        )
    """
    translator = ClsStyTranslator(api_key=api_key, model=model, base_url=base_url)
    return translator.translate_file(
        input_file=input_file,
        output_file=output_file,
        max_tokens_per_group=max_tokens_per_group,
        verbose=verbose
    )


if __name__ == "__main__":
    # 示例1: 翻译 .cls 文件

    
    # 示例2: 翻译 .sty 文件
    result2 = translate_cls_or_sty_file(
        input_file="scutthesis.cls",
        output_file="scutthesis2.sty",
        api_key="", # 请在此处填入API Key
        model="gpt-4o-mini",
        base_url="", # 请在此处填入API Base URL
        verbose=True
    )

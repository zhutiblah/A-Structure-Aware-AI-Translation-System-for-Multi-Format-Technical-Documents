# -*- coding: utf-8 -*-
"""
Markdown 专用翻译模块
使用不同的提示词避免与 Word 翻译冲突
"""

from typing import List, Dict, Any
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import time


def llm_translate_markdown(
    para_data_list: List[Dict[str, Any]],
    source_lang: str = "English",
    target_lang: str = "Chinese",
    model: str = "gpt-4o-mini",
    api_base: str = None,
    api_key: str = None,
    max_workers: int = 10,
    timeout: int = 180,
    max_retries: int = 3,
    interval: float = 0.4,
    temperature: float = 0.3,
    **kwargs
) -> List[str]:
    """
    Markdown 专用翻译函数
    """
    
    # 初始化 OpenAI 客户端
    client = OpenAI(
        api_key=api_key,
        base_url=api_base,
        timeout=timeout
    )
    
    results = {}
    
    def translate_single(data: Dict[str, Any]) -> tuple:
        """翻译单个段落"""
        text = data.get("full_text", "")
        idx = data.get("paragraph_index", 0)
        
        if not text or not text.strip():
            return idx, ""
        
        # 改进的 Markdown 专用提示词
        system_prompt = """你是一个专业的技术文档翻译专家，精通 Markdown 格式。

你的任务是将 Markdown 文本从 {source_lang} 翻译成 {target_lang}。

【翻译规则 - 必须严格遵循】

1️⃣ 翻译所有可见的英文文本
   ✓ 翻译标题、段落、列表、表格单元格中的文本
   ✓ 翻译加粗、斜体、下划线中的文本
   ✓ 翻译按钮文本、菜单项等

2️⃣ 保护 Markdown 结构符号（不翻译）
   ✓ 保留所有 #、**、*、`、>、|、- 等格式符号
   ✓ 保留超链接格式：[文本](URL)
   ✓ 保留图片格式：![alt](src)
   ✓ 保留代码块的三个反引号：```
   ✓ 保留表格的管道符和分隔线：|、-

3️⃣ 处理特殊内容
   ✓ 代码块内容保持原样
   ✓ URL、邮箱地址、路径保持原样
   ✓ 技术缩写（如 AI、HTML、API）保持原样
   ✓ 版本号、数字保持原样

4️⃣ 表格处理规则
   ✓ 翻译表格所有单元格的文本内容
   ✓ 保留表格分隔线（第二行）完全不变
   ✓ 保留所有管道符 | 和破折号 -

5️⃣ 格式调整
   ✓ 中文和英文之间加空格：例如 "这是 Example 文本"
   ✓ 保持原有的缩进和换行

【输出要求】
⚠️ 只输出翻译后的文本
⚠️ 不要添加任何说明、标记或额外内容
⚠️ 不要输出 ```markdown 或其他代码标记
⚠️ 完全保持原始格式

【例子】
原文：| Feature | Standard | Pro |
      | --- | --- | --- |
      | Support | Email | 24/7 Live Chat |

译文：| 功能 | 标准版 | 专业版 |
      | --- | --- | --- |
      | 支持 | 电子邮件 | 24/7 实时聊天 |

注意：第二行的 | --- | --- | --- | 完全不变！

【重要】确保表格的每一行都被翻译！""".format(
            source_lang=source_lang,
            target_lang=target_lang
        )
        
        user_message = text
        
        retry_count = 0
        last_error = None
        
        while retry_count < max_retries:
            try:
                message = client.chat.completions.create(
                    model=model,
                    max_tokens=4096,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message}
                    ]
                )
                
                translated_text = message.choices[0].message.content.strip()
                
                return idx, translated_text
                
            except Exception as e:
                last_error = e
                retry_count += 1
                if retry_count < max_retries:
                    time.sleep(interval)
                continue
        
        print(f"翻译失败 (索引 {idx}, 重试 {max_retries} 次): {str(last_error)}")
        return idx, text

    # 使用线程池并发翻译
    print(f"[Markdown] Starting thread pool for {len(para_data_list)} items...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(translate_single, data): data.get("paragraph_index", 0)
            for data in para_data_list
        }
        
        with tqdm(
            as_completed(futures),
            total=len(futures),
            desc="Translating",
            unit="para",
            ncols=80
        ) as pbar:
            for future in pbar:
                try:
                    idx, translated = future.result()
                    results[idx] = translated
                except Exception as e:
                    print(f"线程错误: {str(e)}")
    
    translated_list = [results.get(i, "") for i in range(len(para_data_list))]
    return translated_list

# translation.py
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

import concurrent

from tqdm import tqdm  # <--- 这样导入的是类/函数，可以直接调用 tqdm()

from constants import *
from utils import *
import requests
from functools import partial
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import time 
import logging

from config import error_logger,audit_logger
def _translate_paragraph(para_data: dict, *, source_lang: str, target_lang: str, model: str, api_base: str, api_key: str, timeout: int, max_retries: int) -> str:
    """
    翻译单个段落，如果 LLM 拒绝翻译，则返回原文。
    """
    text_to_process = para_data["full_text_for_llm"] 
    original_full_text = text_to_process

    translated_text = ""

    if is_meaningful_text(text_to_process):
        original_ph_count = text_to_process.count(PLACEHOLDER_TAG)
        system_message = f"You are a professional translation engine, strictly translating from {source_lang} to {target_lang}."
        
        user_message = (
            f"Please translate the following text from {source_lang} to {target_lang}.\n\n"
            f"**CRITICAL RULES (MUST FOLLOW):**\n"
            f"1. **Preserve Placeholders**: The placeholder `{PLACEHOLDER_TAG}` is a marker for formulas, images, or special symbols. "
            f"You MUST preserve it exactly as-is, with the same quantity and order in the translation.\n"
            f"2. **Preserve Chinese Numerals & Enumeration**: \n"
            f"   - Chinese numerals like （一）、（二）、（三）...（十五）etc. MUST be converted to Arabic numerals: (1)、(2)、(3)...(15)\n"
            f"   - Do NOT translate them to English words like 'One', 'Two', 'Fifteen'\n"
            f"   - Preserve the parenthesis style: （X）→ (X)\n"
            f"3. **Format Preservation**: Keep colons, dashes, commas, and other punctuation marks in their original positions.\n"
            f"4. **Multi-Level Separators for Complex Formatting**: If the text contains multiple formatting styles (bold, underline, mixed) or logical sections, use these separators:\n"
            f"   - Use `::` to separate 'Label: Content' pairs (e.g., if 'Label' has a different style or is a distinct unit)\n"
            f"   - Use `::::` (quadruple colon) to separate larger sections with DIFFERENT fundamental formatting (e.g., an entire underlined sentence from a normal one).\n"
            f"   - EXAMPLE: If original input for LLM is: 'Label:: Content:::: More', translate as:\n"
            f"     '标签:: 内容:::: 更多'\n"
            f"5. **Return Only Translation**: Your response MUST contain ONLY the translated text, with no explanations, markdown formatting, or code blocks.\n\n"
            f"**EXAMPLES:**\n"
            f"  Input: （一）封面\n"
            f"  Output: (1) Cover\n"
            f"  Input: （十五）华南理工大学博士学位论文\n"
            f"  Output: (15) PhD Thesis of South China University of Technology\n"
            f"  Input: 二、论文的书写规范\n"
            f"  Output: II. Writing Specifications of Thesis\n\n"
            f"Important rules:"
            f"1. 【SEG】 is a text-separated markup, do not translate it"
            f"2. The 【SEG】 must remain in the translated text"
            f"3. The number and location of 【SEG】 must be exactly the same as the original text"
            f"Example:"
            f"Original: Operating System:【SEG】 Ubuntu 22.04 LTS"
            f"Translation: Operating system:【SEG】 Ubuntu 22.04 LTS"
            f"**--- Text to Translate ---**\n{text_to_process}"
        )
        
        headers = {"Content-Type": "application/json"}
        if api_key: 
            headers["Authorization"] = f"Bearer {api_key}"
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.1,
            "stream": False
        }
        api_url = f"{api_base}/chat/completions"
        
        for attempt in range(max_retries):
            try:
                resp = requests.post(api_url, headers=headers, data=json.dumps(payload), timeout=timeout)
                resp.raise_for_status()
                raw_translation = resp.json()['choices'][0]['message']['content']
                current_translated_text = clean_llm_output(raw_translation)
                
                # ⚠️⚠️⚠️ 新增：检测 LLM 拒绝翻译
                if is_llm_refusal(current_translated_text):
                    error_logger.warning(
                        f"[LLM Refusal] LLM refused to translate. "
                        f"Original: '{original_full_text[:100]}' "
                        f"Response: '{current_translated_text[:100]}'. "
                        f"Returning original text."
                    )
                    return original_full_text  # ⚠️ 直接返回原文

                # ⚠️⚠️⚠️ 新增：检测 LLM 返回 Prompt
                if "You are a professional translation engine" in current_translated_text or "CRITICAL RULES" in current_translated_text:
                    audit_logger.warning(
                        f"[LLM Error] Response echoes prompt. Original: '{original_full_text[:50]}...'. Attempt {attempt + 1}/{max_retries}. Retrying..."
                    )
                    continue
                
                # 检查占位符数量
                strict_placeholder_regex = re.compile(r"<\s*placeholder\s*[,>]")
                found_placeholders = strict_placeholder_regex.findall(current_translated_text)
                
                if original_ph_count == len(found_placeholders):
                    translated_text = current_translated_text
                    break
                else:
                    audit_logger.warning(
                        f"[Placeholder Mismatch] Expected: {original_ph_count}, Found: {len(found_placeholders)} "
                        f"in '{current_translated_text[:100]}'. Attempt {attempt + 1}/{max_retries}. Retrying..."
                    )
                    if attempt + 1 == max_retries:
                        error_logger.error(
                            f"Translation failed after {max_retries} retries due to placeholder mismatch. "
                            f"Original: '{original_full_text}', Last attempt: '{current_translated_text}'"
                        )
                        return original_full_text
            except Exception as e:
                error_logger.warning(
                    f"[API Error] API call failed. Attempt {attempt + 1}/{max_retries}. Error: {e}"
                )
                if attempt + 1 == max_retries:
                    error_logger.error(
                        f"Translation failed with exception for '{original_full_text}'. Error: {e}"
                    )
                    return original_full_text
        
        if not translated_text:
            return original_full_text
    else:
        translated_text = text_to_process

    audit_logger.info(
        f"[Translation] Original: '{original_full_text[:50]}...' | "
        f"Translated: '{translated_text[:50]}...'"
    )
    return translated_text
def is_llm_refusal(text: str) -> bool:
    """
    检测 LLM 返回的文本是否为"拒绝翻译"的回复。
    
    常见的拒绝模式：
    - "抱歉，我无法处理该请求。"
    - "I cannot assist with that request."
    - "对不起，我不能..."
    - "Sorry, I can't..."
    
    Args:
        text: LLM 返回的文本
    
    Returns:
        True 如果是拒绝回复，False 否则
    """
    if not text or not text.strip():
        return False
    
    text_lower = text.lower().strip()
    
    # 中文拒绝模式
    chinese_refusal_patterns = [
        "抱歉",
        "对不起",
        "无法处理",
        "不能",
        "无法翻译",
        "无法完成",
        "不支持",
        "无法提供",
    ]
    
    # 英文拒绝模式
    english_refusal_patterns = [
        "sorry",
        "i cannot",
        "i can't",
        "unable to",
        "i'm unable",
        "cannot assist",
        "can't assist",
        "cannot help",
        "can't help",
        "i apologize",
    ]
    
    # 检查是否包含拒绝关键词
    for pattern in chinese_refusal_patterns + english_refusal_patterns:
        if pattern in text_lower:
            # 进一步验证：确保不是翻译内容本身包含这些词
            # 如果文本很短（< 50 字符）且包含拒绝关键词，很可能是拒绝回复
            if len(text) < 50:
                audit_logger.warning(f"[LLM Refusal] Detected refusal response: '{text[:100]}'")
                return True
    
    return False


def llm_translate_concurrent(para_data_list, **kwargs):
    """
    并发翻译调度函数。
    自动处理 translate_kwargs 嵌套，并且只把 _translate_paragraph 需要的参数传进去。
    """

    results = [""] * len(para_data_list)

    # ---- 1. 先把真正的翻译参数拿出来（兼容 translate_kwargs 嵌套） ----
    if 'translate_kwargs' in kwargs:
        raw_api_kwargs = kwargs['translate_kwargs']
    else:
        raw_api_kwargs = kwargs

    # 只保留 _translate_paragraph 需要的字段（白名单）
    allowed_keys = {
        'source_lang',
        'target_lang',
        'model',
        'api_base',
        'api_key',
        'timeout',
        'max_retries',
    }
    real_api_kwargs = {
        k: v for k, v in raw_api_kwargs.items() if k in allowed_keys
    }

    # 从 raw_api_kwargs 里取 max_workers（用于线程池），默认 8
    max_workers = raw_api_kwargs.get('max_workers', 8)

    # ---- 2. 找出需要翻译的段落索引 ----
    indices_to_translate = []
    for i, data in enumerate(para_data_list):
        if data["full_text_for_llm"].strip():
            indices_to_translate.append(i)

    if not indices_to_translate:
        return [data["full_text_for_llm"] for data in para_data_list]

    print(f"  [Concurrent] Starting thread pool for {len(indices_to_translate)} items...")

    # ---- 3. 并发提交任务 ----
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_translate_paragraph, para_data_list[i], **real_api_kwargs): i
            for i in indices_to_translate
        }

        for future in tqdm(
            concurrent.futures.as_completed(future_to_index),
            total=len(indices_to_translate),
            desc="Translating",
            unit="para"
        ):
            index = future_to_index[future]
            try:
                translated_text = future.result()
                results[index] = translated_text
            except Exception as exc:
                error_logger.error(f"Paragraph {index} generated an exception: {exc}")
                # 出错时，用原文回填
                results[index] = para_data_list[index]["full_text_for_llm"]

    # ---- 4. 把那些本来就不需要翻译的段落补回去 ----
    for i in range(len(para_data_list)):
        if i not in indices_to_translate:
            results[i] = para_data_list[i]["full_text_for_llm"]

    return results


def fix_run_structure(root: ET.Element) -> None:
    """
    遍历所有 run，确保其内部结构正确（rPr 必须在第一位）。
    """
    for r in root.findall('.//w:r', NAMESPACES):
        rPr = r.find(f"{{{NAMESPACES['w']}}}rPr", NAMESPACES)
        if rPr is not None:
            # 如果 rPr 不是第一个子元素，则移动它
            if r.index(rPr) != 0:
                r.remove(rPr)
                r.insert(0, rPr)
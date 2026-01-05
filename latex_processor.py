# latex_processor.py (完整版 - 添加缓存清理 + 完整文件复制)
import os
import re
import logging
from typing import List, Dict
import shutil
import requests
import json
import time
import hashlib
from datetime import datetime, timedelta
from latex_translation import translate_cls_or_sty_file, ClsStyTranslator
CLS_TRANSLATOR_AVAILABLE = True

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TranslationCache:
    """翻译缓存管理器（增强版 - 带清理机制）"""
    
    def __init__(self, cache_file='translation_cache.json', max_age_days=30, max_entries=10000):
        self.cache_file = cache_file
        self.max_age_days = max_age_days
        self.max_entries = max_entries
        self.cache = self._load_cache()
        self.hits = 0
        self.misses = 0
        
        # 加载后立即清理
        self._cleanup_cache()
    
    def _load_cache(self):
        """加载缓存文件（带错误处理）"""
        if not os.path.exists(self.cache_file):
            return {}
        
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                
            if not isinstance(cache_data, dict):
                logger.warning("Invalid cache format, creating new cache")
                return {}
                
            # 过滤掉过大的条目
            filtered_cache = {}
            removed_count = 0
            for key, value in cache_data.items():
                if isinstance(value, dict):
                    text = value.get('text', '')
                    if len(text) < 50000:
                        filtered_cache[key] = value
                    else:
                        removed_count += 1
                elif isinstance(value, str):
                    if len(value) < 50000:
                        filtered_cache[key] = {
                            'text': value,
                            'timestamp': datetime.now().isoformat()
                        }
                    else:
                        removed_count += 1
                
            if removed_count > 0:
                logger.info(f"♻️ Removed {removed_count} oversized cache entries")
                
            return filtered_cache
            
        except json.JSONDecodeError as e:
            logger.warning(f"Cache file corrupted: {e}, creating new cache")
            backup_file = self.cache_file + f'.backup.{int(time.time())}'
            try:
                shutil.copy2(self.cache_file, backup_file)
                logger.info(f"Corrupted cache backed up to {backup_file}")
            except:
                pass
            return {}
            
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return {}
    
    def _cleanup_cache(self):
        """清理过期和过多的缓存"""
        if not self.cache:
            return
        
        original_size = len(self.cache)
        cutoff_date = datetime.now() - timedelta(days=self.max_age_days)
        
        # 1. 删除过期条目
        expired_keys = []
        for key, value in self.cache.items():
            if isinstance(value, dict):
                try:
                    timestamp = datetime.fromisoformat(value.get('timestamp', ''))
                    if timestamp < cutoff_date:
                        expired_keys.append(key)
                except:
                    pass
        
        for key in expired_keys:
            del self.cache[key]
        
        if expired_keys:
            logger.info(f"♻️ Removed {len(expired_keys)} expired cache entries (>{self.max_age_days} days)")
        
        # 2. 如果仍超过最大条目数，删除最旧的
        if len(self.cache) > self.max_entries:
            sorted_items = sorted(
                self.cache.items(),
                key=lambda x: x[1].get('timestamp', '') if isinstance(x[1], dict) else '',
                reverse=False
            )
            
            keep_count = self.max_entries
            items_to_remove = sorted_items[:-keep_count] if keep_count > 0 else sorted_items
            
            for key, _ in items_to_remove:
                del self.cache[key]
            
            logger.info(f"♻️ Removed {len(items_to_remove)} oldest entries (limit: {self.max_entries})")
        
        cleaned_count = original_size - len(self.cache)
        if cleaned_count > 0:
            logger.info(f"📊 Cache cleanup: {original_size} → {len(self.cache)} entries ({cleaned_count} removed)")
            self._save_cache()
    
    def _save_cache(self):
        """保存缓存（带错误处理）"""
        try:
            temp_file = self.cache_file + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
            
            with open(temp_file, 'r', encoding='utf-8') as f:
                json.load(f)
            
            if os.path.exists(self.cache_file):
                os.replace(temp_file, self.cache_file)
            else:
                os.rename(temp_file, self.cache_file)
            
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
    
    def get_cache_key(self, text: str, model: str, direction: str) -> str:
        """生成缓存键"""
        content = f"{text}|{model}|{direction}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def get(self, text: str, model: str, direction: str) -> str:
        """获取缓存"""
        try:
            key = self.get_cache_key(text, model, direction)
            if key in self.cache:
                self.hits += 1
                value = self.cache[key]
                if isinstance(value, dict):
                    return value.get('text', '')
                return value
            self.misses += 1
            return None
        except Exception as e:
            logger.warning(f"Cache get failed: {e}")
            self.misses += 1
            return None
    
    def set(self, text: str, model: str, direction: str, translation: str):
        """保存缓存（带验证和时间戳）"""
        try:
            if len(text) > 20000 or len(translation) > 20000:
                logger.warning(f"⚠️ Entry too large (text:{len(text)}, trans:{len(translation)}), skipping cache")
                return
            
            key = self.get_cache_key(text, model, direction)
            self.cache[key] = {
                'text': translation,
                'timestamp': datetime.now().isoformat()
            }
            
            if (self.hits + self.misses) % 10 == 0:
                self._save_cache()
            
        except Exception as e:
            logger.warning(f"Cache set failed: {e}")
    
    def clear_all(self):
        """清空所有缓存"""
        self.cache.clear()
        self._save_cache()
        logger.info("🗑️ All cache cleared")
    
    def clear_old(self, days: int = None):
        """清理指定天数前的缓存"""
        if days is None:
            days = self.max_age_days
        
        original_size = len(self.cache)
        cutoff_date = datetime.now() - timedelta(days=days)
        
        keys_to_remove = []
        for key, value in self.cache.items():
            if isinstance(value, dict):
                try:
                    timestamp = datetime.fromisoformat(value.get('timestamp', ''))
                    if timestamp < cutoff_date:
                        keys_to_remove.append(key)
                except:
                    pass
        
        for key in keys_to_remove:
            del self.cache[key]
        
        removed = len(keys_to_remove)
        if removed > 0:
            self._save_cache()
            logger.info(f"🗑️ Cleared {removed} cache entries older than {days} days")
        else:
            logger.info(f"✅ No cache entries older than {days} days")
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        total = self.hits + self.misses
        hit_rate = self.hits / total * 100 if total > 0 else 0
        
        cache_size_bytes = 0
        if os.path.exists(self.cache_file):
            cache_size_bytes = os.path.getsize(self.cache_file)
        cache_size_mb = cache_size_bytes / (1024 * 1024)
        
        return {
            'hits': self.hits,
            'misses': self.misses,
            'total': total,
            'hit_rate': f"{hit_rate:.1f}%",
            'cache_entries': len(self.cache),
            'cache_size_mb': f"{cache_size_mb:.2f} MB"
        }
    
    def close(self):
        """关闭并保存"""
        try:
            self._save_cache()
            stats = self.get_stats()
            logger.info(f"📊 Cache Stats: {stats['hits']} hits, {stats['misses']} misses, "
                       f"hit rate {stats['hit_rate']}, {stats['cache_entries']} entries, "
                       f"size {stats['cache_size_mb']}")
        except Exception as e:
            logger.warning(f"Failed to close cache: {e}")


class ClsStyTranslator:
    """LaTeX 文档翻译器（简化版 - 适配 LLM）"""
    
    # 🔧 简化保护模式：只保护关键的文档结构和引用系统
    PROTECTED_PATTERNS = [
        # === 文档结构（必须保护）===
        (r'\\documentclass(\[.*?\])?\{.*?\}', 'DOCUMENTCLASS'),
        (r'\\usepackage(\[.*?\])?\{.*?\}', 'USEPACKAGE'),
        (r'\\begin\{document\}', 'BEGINDOC'),
        (r'\\end\{document\}', 'ENDDOC'),
        
        # === 文件引用（必须保护）===
        (r'\\input\{.*?\}', 'INPUT'),
        (r'\\include\{.*?\}', 'INCLUDE'),
        
        # === 引用系统（必须保护）===
        (r'\\cite(\[.*?\])?(\[.*?\])?\{.*?\}', 'CITE'),
        (r'\\parencite(\[.*?\])?(\[.*?\])?\{.*?\}', 'PARENCITE'),
        (r'\\ref\{.*?\}', 'REF'),
        (r'\\label\{.*?\}', 'LABEL'),
        
        # === 参考文献（必须保护）===
        (r'\\bibliography\{.*?\}', 'BIBLIO'),
        (r'\\bibliographystyle\{.*?\}', 'BIBLIOSTYLE'),
        
        # === 自定义命令（必须保护）===
        (r'\\newcommand\{.*?\}(\[.*?\])?\{.*?\}', 'NEWCOMMAND'),
        (r'\\renewcommand\{.*?\}(\[.*?\])?\{.*?\}', 'RENEWCOMMAND')
    ]
    def __init__(self):
        self.placeholder_map = {}
        self.placeholder_counter = 0
    
    def _generate_placeholder(self, prefix: str) -> str:
        self.placeholder_counter += 1
        return f"<{prefix}_{self.placeholder_counter}>"
    
    def protect_latex_commands(self, text: str) -> str:
        """保护 LaTeX 命令"""
        protected_text = text
        
        for pattern, prefix in self.PROTECTED_PATTERNS:
            matches = re.finditer(pattern, protected_text, re.DOTALL | re.MULTILINE)
            for match in reversed(list(matches)):
                original = match.group(0)
                placeholder = self._generate_placeholder(prefix)
                self.placeholder_map[placeholder] = original
                
                protected_text = (
                    protected_text[:match.start()] + 
                    placeholder + 
                    protected_text[match.end():]
                )
        
        return protected_text
    
    def restore_latex_commands(self, text: str) -> str:
        """还原 LaTeX 命令"""
        restored_text = text
        for placeholder, original in self.placeholder_map.items():
            restored_text = restored_text.replace(placeholder, original)
        return restored_text
    
    def split_into_chunks(
        self, 
        text: str, 
        max_length: int = 2000,  # 🔧 增加到 2000 字符
        min_length: int = 500    # 🔧 增加最小长度
    ) -> List[str]:
        """
        智能切分文本（简化版 - 让 LLM 处理结构）
        
        策略：
        1. 按段落自然切分（\n\n）
        2. 不再特殊保护表格、列表等环境
        3. 让 LLM 自行理解和翻译这些结构
        """
        chunks = []
        
        # 按段落切分
        paragraphs = re.split(r'\n\s*\n', text)
        
        current_chunk = ""
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # 如果加入当前段落不超过限制，就添加
            if len(current_chunk) + len(para) + 2 < max_length:
                current_chunk += ("\n\n" if current_chunk else "") + para
            else:
                # 保存当前块
                if current_chunk:
                    chunks.append(current_chunk)
                
                # 如果单个段落超长，需要进一步切分
                if len(para) > max_length:
                    # 按句子切分（简单处理）
                    sentences = re.split(r'(?<=[.!?])\s+', para)
                    temp_chunk = ""
                    for sent in sentences:
                        if len(temp_chunk) + len(sent) < max_length:
                            temp_chunk += (" " if temp_chunk else "") + sent
                        else:
                            if temp_chunk:
                                chunks.append(temp_chunk)
                            temp_chunk = sent
                    if temp_chunk:
                        chunks.append(temp_chunk)
                else:
                    current_chunk = para
        
        # 添加最后一块
        if current_chunk:
            chunks.append(current_chunk)
        
        return chunks if chunks else [""]


def build_latex_translation_prompt(source_lang: str, target_lang: str) -> str:
    """构建适配 LLM 的 LaTeX 翻译提示词"""
    
    if target_lang == 'English':
        lang_guide = "Use formal academic English with standard terminology"
    else:
        lang_guide = "使用规范学术中文，术语准确"
    
    prompt = f"""You are a professional LaTeX document translator. Translate {source_lang} to {target_lang}.

**Critical Rules**:
1. **Preserve ALL placeholders** exactly as-is: <CITE_1>, <REF_2>, <LABEL_3>, <DOCUMENTCLASS_1>, etc.
2. **Preserve LaTeX delimiters**: :::, $$, \\begin{{...}}, \\end{{...}}
3. **Translate natural language** while keeping:
   - LaTeX commands unchanged
   - Math expressions unchanged
   - Code blocks unchanged
   - All placeholders unchanged

4. **Handle structures naturally**:
   - Tables (tabular/table): Translate content, keep LaTeX structure
   - Lists (itemize/enumerate): Translate items, keep structure
   - Sections/subsections: Translate titles, keep commands
   - Figures: Translate captions, keep structure

5. **Output requirements**:
   - {lang_guide}
   - Keep original formatting (line breaks, indentation)
   - Translate ALL text content (don't skip paragraphs)
   - Output ONLY the translation, no explanations

**Example**:
Input: "这是一个<REF_1>示例\\cite{{example}}。"
Output: "This is a <REF_1> example\\cite{{example}}."

Now translate the following text:"""
    
    return prompt


def call_llm_api(
    text: str,
    system_prompt: str,
    model: str,
    api_base: str,
    api_key: str,
    timeout: int = 180,
    max_retries: int = 3,
    interval: float = 0.5
) -> str:
    """调用 LLM API 进行翻译"""
    url = f"{api_base.rstrip('/')}/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        "temperature": 0.3,  # 🔧 略微提高温度，让翻译更自然
        "top_p": 0.9,
        "max_tokens": int(len(text) * 2.0),  # 🔧 增加输出长度限制
    }
    
    last_error = None
    
    for attempt in range(max_retries):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout
            )
            
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}: {response.text}"
                logger.warning(f"   API error, retrying... ({attempt + 1}/{max_retries})")
                
                if attempt < max_retries - 1:
                    time.sleep(interval * (attempt + 1))
                continue
            
            result = response.json()
            
            if "error" in result:
                last_error = result["error"].get("message", str(result["error"]))
                logger.warning(f"   API error: {last_error}, retrying...")
                
                if attempt < max_retries - 1:
                    time.sleep(interval * (attempt + 1))
                continue
            
            if "choices" in result and len(result["choices"]) > 0:
                translated_text = result["choices"][0]["message"]["content"].strip()
                
                finish_reason = result["choices"][0].get("finish_reason", "")
                if finish_reason == "length":
                    logger.warning("   ⚠️ Output was truncated due to length limit!")
                
                usage = result.get("usage", {})
                if usage:
                    logger.debug(f"   Token usage: {usage.get('prompt_tokens', 0)} prompt + "
                               f"{usage.get('completion_tokens', 0)} completion = "
                               f"{usage.get('total_tokens', 0)} total")
                
                return translated_text
            else:
                last_error = "No choices in API response"
                
                if attempt < max_retries - 1:
                    time.sleep(interval)
                continue
            
        except requests.exceptions.Timeout:
            last_error = "Request timeout"
            logger.warning(f"   Timeout, retrying... ({attempt + 1}/{max_retries})")
            
            if attempt < max_retries - 1:
                time.sleep(interval * 2)
            continue
        
        except Exception as e:
            last_error = str(e)
            logger.warning(f"   Error: {e}, retrying...")
            
            if attempt < max_retries - 1:
                time.sleep(interval)
            continue
    
    raise Exception(f"Translation failed after {max_retries} attempts. Last error: {last_error}")


def find_referenced_files(tex_file: str, base_dir: str, visited: set = None) -> List[str]:
    """递归查找引用的 .tex 文件"""
    if visited is None:
        visited = set()
    
    tex_file_abs = os.path.abspath(tex_file)
    if tex_file_abs in visited:
        return []
    
    visited.add(tex_file_abs)
    referenced_files = []
    
    try:
        with open(tex_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        for pattern in [r'\\input\{([^}]+)\}', r'\\include\{([^}]+)\}']:
            for match in re.findall(pattern, content):
                ref_file = match.strip()
                if not ref_file.endswith('.tex'):
                    ref_file += '.tex'
                
                ref_path = ref_file if os.path.isabs(ref_file) else os.path.join(base_dir, ref_file)
                
                if os.path.exists(ref_path):
                    referenced_files.append(ref_path)
                    sub_refs = find_referenced_files(ref_path, os.path.dirname(ref_path), visited)
                    referenced_files.extend(sub_refs)
                else:
                    logger.warning(f"Referenced file not found: {ref_path}")
    
    except Exception as e:
        logger.error(f"Error reading {tex_file}: {e}")
    
    return referenced_files
def convert_article_to_ctexart(content: str, direction: str) -> str:
    """英译中时转换为 ctexart 并配置字体"""
    if direction != 'en-to-zh':
        return content
    
    # 1. 转换文档类
    content = re.sub(
        r'\\documentclass(\[.*?\])?\{article\}',
        r'\\documentclass\1{ctexart}',
        content
    )
    
    # 2. 添加字体支持（如果没有）
    if 'ctexart' in content and 'xeCJK' not in content:
        font_settings = r"""
% ==================== 中文字体配置 ====================
\usepackage{xeCJK}
\usepackage{fontspec}

% Windows 系统字体
\setCJKmainfont{SimSun}[
    BoldFont=SimHei,
    ItalicFont=KaiTi
]
\setCJKsansfont{SimHei}
\setCJKmonofont{FangSong}

% 英文字体
\setmainfont{Times New Roman}
\setsansfont{Arial}
\setmonofont{Courier New}

% 数学字体（避免中文污染数学环境）
\usepackage{amsmath}
\usepackage{amssymb}
% ====================================================
"""
        # 在 \begin{document} 前插入
        content = content.replace(r'\begin{document}', font_settings + r'\begin{document}')
    
    return content
def translate_latex_file(
    input_file: str,
    output_file: str,
    model: str,
    api_base: str,
    api_key: str,
    source_lang: str,
    target_lang: str,
    direction: str,
    cache: TranslationCache,
    timeout: int = 180,
    max_retries: int = 3,
    interval: float = 0.5
) -> bool:
    """翻译单个 LaTeX 文件（增强错误处理）"""
    try:
        logger.info(f"📄 Translating: {os.path.basename(input_file)}")
        
        try:
            with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"   ❌ Failed to read file: {e}")
            return False
        
        original_length = len(content)
        logger.info(f"   Original length: {original_length} characters")
        
        translator = ClsStyTranslator()
        
        try:
            protected_content = translator.protect_latex_commands(content)
            logger.info(f"   Protected {len(translator.placeholder_map)} LaTeX elements")
        except Exception as e:
            logger.error(f"   ❌ Protection failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        
        try:
            chunks = translator.split_into_chunks(protected_content, max_length=1500, min_length=200)
            logger.info(f"   Split into {len(chunks)} chunks")
        except Exception as e:
            logger.error(f"   ❌ Splitting failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        
        for i, chunk in enumerate(chunks):
            logger.debug(f"   Chunk {i+1}: {len(chunk)} chars")
        
        system_prompt = build_latex_translation_prompt(source_lang, target_lang)
        
        translated_chunks = []
        cache_hits = 0
        failed_chunks = []
        
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                translated_chunks.append(chunk)
                continue
            
            try:
                logger.info(f"   🔄 Processing chunk {i+1}/{len(chunks)}...")
                
                if len(chunk) > 10000:
                    logger.warning(f"   ⚠️ Chunk {i+1} is very large ({len(chunk)} chars), may fail")
                
                try:
                    cached_translation = cache.get(chunk, model, direction)
                except Exception as e:
                    logger.warning(f"   ⚠️ Cache read failed: {e}, skipping cache")
                    cached_translation = None
                
                if cached_translation:
                    translated_chunks.append(cached_translation)
                    cache_hits += 1
                    logger.info(f"   ♻️ Chunk {i+1}/{len(chunks)} (cached)")
                else:
                    try:
                        translated = call_llm_api(
                            text=chunk,
                            system_prompt=system_prompt,
                            model=model,
                            api_base=api_base,
                            api_key=api_key,
                            timeout=timeout,
                            max_retries=max_retries,
                            interval=interval
                        )
                        
                        if not translated or len(translated) < 10:
                            logger.warning(f"   ⚠️ Chunk {i+1} returned suspiciously short translation!")
                            logger.warning(f"   Original length: {len(chunk)}, translated: {len(translated) if translated else 0}")
                            failed_chunks.append(i+1)
                            translated_chunks.append(chunk)
                        else:
                            try:
                                cache.set(chunk, model, direction, translated)
                            except Exception as e:
                                logger.warning(f"   ⚠️ Cache write failed: {e}")
                            
                            translated_chunks.append(translated)
                            logger.info(f"   ✓ Chunk {i+1}/{len(chunks)} (translated, {len(translated)} chars)")
                    
                    except Exception as e:
                        logger.error(f"   ✗ Chunk {i+1} API call failed: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        translated_chunks.append(chunk)
                        failed_chunks.append(i+1)
                
            except Exception as e:
                logger.error(f"   ✗ Chunk {i+1} processing failed: {e}")
                import traceback
                logger.error(traceback.format_exc())
                translated_chunks.append(chunk)
                failed_chunks.append(i+1)
        
        if cache_hits > 0:
            logger.info(f"   📊 Cache hits: {cache_hits}/{len(chunks)} "
                       f"({cache_hits/len(chunks)*100:.1f}% saved)")
        
        if failed_chunks:
            logger.warning(f"   ⚠️ Failed/suspicious chunks: {failed_chunks}")
        
        try:
            merged = "\n\n".join(translated_chunks)
            final_content = translator.restore_latex_commands(merged)
        except Exception as e:
            logger.error(f"   ❌ Merging/restoration failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
        
        translated_length = len(final_content)
        logger.info(f"   Translated length: {translated_length} characters "
                   f"({translated_length/original_length*100:.1f}% of original)")
        
        try:
            final_content = convert_article_to_ctexart(final_content, direction)
        except Exception as e:
           logger.warning(f"   ⚠️ Format conversion failed: {e}, using original")
       
        try:
           os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
           with open(output_file, 'w', encoding='utf-8') as f:
               f.write(final_content)
           logger.info(f"   ✅ Saved: {os.path.basename(output_file)}")
        except Exception as e:
           logger.error(f"   ❌ Save failed: {e}")
           import traceback
           logger.error(traceback.format_exc())
           return False
       
        return True
       
    except Exception as e:
       logger.error(f"   ❌ File translation failed: {e}")
       import traceback
       logger.error(traceback.format_exc())
       return False


def test_api_connection(api_base: str, api_key: str) -> bool:
   """测试 API 连接"""
   try:
       url = f"{api_base.rstrip('/')}/models"
       headers = {"Authorization": f"Bearer {api_key}"}
       
       response = requests.get(url, headers=headers, timeout=5)
       
       if response.status_code == 200:
           logger.info("✅ API connection test passed")
           return True
       else:
           logger.error(f"❌ API connection failed: HTTP {response.status_code}")
           return False
   except Exception as e:
       logger.error(f"❌ API connection test failed: {e}")
       return False
def copy_all_project_files(source_dir: str, dest_dir: str, processed_files: List[str] = None):
    """
    复制项目所有文件到目标目录（修复版 - 排除已处理的文件）
    
    Args:
        source_dir: 源项目根目录
        dest_dir: 目标目录
        processed_files: 已翻译的文件列表（绝对路径），这些文件不会被覆盖
    """
    # 排除的目录
    exclude_dirs = {
        '.git', '.svn', '__pycache__', 'node_modules',
        '.vscode', '.idea', 'build', 'dist', '__MACOSX'
    }
    
    # 排除的临时文件扩展名
    exclude_extensions = {
        '.aux', '.log', '.out', '.toc', '.synctex.gz',
        '.fdb_latexmk', '.fls', '.bbl', '.blg', '.bcf',
        '.run.xml', '.nav', '.snm', '.vrb', '.lof', '.lot',
        '.bak', '.swp', '.tmp', '~', '.xdv'
    }
    
    copied_files = 0
    copied_dirs = 0
    skipped_files = 0
    skipped_processed = 0
    
    logger.info("\n📦 Copying remaining project files...")
    
    # 转换 processed_files 为绝对路径集合
    processed_set = set()
    if processed_files:
        processed_set = {os.path.abspath(f) for f in processed_files}
    
    # 获取源目录和目标目录的绝对路径
    source_dir_abs = os.path.abspath(source_dir)
    dest_dir_abs = os.path.abspath(dest_dir)
    
    for root, dirs, files in os.walk(source_dir):
        # 过滤排除的目录
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith('.')]
        
        # 跳过输出目录本身
        root_abs = os.path.abspath(root)
        if root_abs == dest_dir_abs or root_abs.startswith(dest_dir_abs + os.sep):
            continue
        
        # 计算相对路径
        rel_dir = os.path.relpath(root, source_dir)
        dest_subdir = os.path.join(dest_dir, rel_dir) if rel_dir != '.' else dest_dir
        
        # 创建目标子目录
        try:
            if not os.path.exists(dest_subdir):
                os.makedirs(dest_subdir, exist_ok=True)
                copied_dirs += 1
                logger.debug(f"   📁 Created directory: {rel_dir}")
        except Exception as e:
            logger.warning(f"   ⚠️ Failed to create directory {rel_dir}: {e}")
            continue
        
        # 复制文件
        for file in files:
            source_file = os.path.join(root, file)
            source_file_abs = os.path.abspath(source_file)
            rel_path = os.path.relpath(source_file, source_dir)
            dest_file = os.path.join(dest_subdir, file)
            
            # 获取文件扩展名
            _, ext = os.path.splitext(file)
            ext_lower = ext.lower()
            
            # 跳过隐藏文件和临时文件
            if file.startswith('.') or ext_lower in exclude_extensions:
                skipped_files += 1
                continue
            
            # 🆕 关键修复：跳过已经翻译过的文件
            if source_file_abs in processed_set:
                skipped_processed += 1
                logger.debug(f"   ⏭️ Skipped processed file: {rel_path}")
                continue
            
            # 🆕 检查目标文件是否已存在（被翻译生成）
            if os.path.exists(dest_file):
                # 如果目标文件存在且比源文件新，说明是翻译生成的，不覆盖
                if os.path.getmtime(dest_file) > os.path.getmtime(source_file):
                    skipped_processed += 1
                    logger.debug(f"   ⏭️ Skipped existing translated file: {rel_path}")
                    continue
            
            # 复制其他所有文件
            try:
                shutil.copy2(source_file, dest_file)
                copied_files += 1
                
                # 记录重要文件类型
                important_extensions = {
                    '.bib', '.cls', '.sty', '.bst',      # 样式文件
                    '.jpg', '.jpeg', '.png', '.pdf',     # 图片
                    '.eps', '.svg', '.tif', '.tiff',     # 更多图片格式
                    '.bat', '.sh',                       # 脚本文件
                }
                if ext_lower in important_extensions:
                    logger.debug(f"   ✓ Copied: {rel_path}")
                
            except Exception as e:
                logger.warning(f"   ⚠️ Failed to copy {rel_path}: {e}")
    
    # 显示统计信息
    logger.info(f"   ✅ Copied {copied_files} files")
    logger.info(f"   📁 Created {copied_dirs} directories")
    logger.info(f"   ⏭️ Skipped {skipped_processed} processed files (already translated)")
    logger.info(f"   🗑️ Skipped {skipped_files} temporary/hidden files")
    
    # 显示文件类型统计
    show_file_type_stats(dest_dir)

def show_file_type_stats(directory: str):
    """显示目录中的文件类型统计"""
    file_types = {}
    dir_count = 0
    
    for root, dirs, files in os.walk(directory):
        dir_count += len(dirs)
        for file in files:
            _, ext = os.path.splitext(file)
            ext = ext.lower() if ext else '(no extension)'
            file_types[ext] = file_types.get(ext, 0) + 1
    
    if file_types or dir_count > 0:
        logger.info("\n   📊 Output directory statistics:")
        logger.info(f"      Total directories: {dir_count}")
        logger.info(f"      Total files: {sum(file_types.values())}")
        
        # 按数量排序，显示前 15 种
        sorted_types = sorted(file_types.items(), key=lambda x: x[1], reverse=True)
        logger.info("\n   File types (top 15):")
        for ext, count in sorted_types[:15]:
            logger.info(f"      {ext:20s}: {count:3d} files")
def translate_style_file(
    input_file: str,
    output_file: str,
    model: str,
    api_base: str,
    api_key: str,
    source_lang: str,
    target_lang: str,
    direction: str,
    cache: TranslationCache,
    timeout: int = 180,
    max_retries: int = 3,
    interval: float = 0.5
) -> bool:
    """
    翻译样式文件（.cls/.sty）
    
    策略：
    1. 只翻译注释和中文字符串
    2. 完全保留 LaTeX 命令结构
    3. 逐行替换翻译结果
    """
    try:
        logger.info(f"📄 Translating style file: {os.path.basename(input_file)}")
        
        # 读取原文件
        with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
            original_content = f.read()
        
        translator = StyleFileTranslator()
        translatable_parts = translator.extract_translatable_parts(original_content)
        
        if not translatable_parts:
            logger.info(f"   ℹ️ No translatable content found, copying file as-is")
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            shutil.copy2(input_file, output_file)
            return True
        
        logger.info(f"   Found {len(translatable_parts)} translatable segments")
        
        # 构建翻译映射
        translations = {}
        system_prompt = build_style_file_translation_prompt(source_lang, target_lang)
        
        # 分块翻译
        chunks = translator.split_into_chunks(original_content)
        logger.info(f"   Split into {len(chunks)} chunks")
        
        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            
            logger.info(f"   🔄 Processing chunk {i+1}/{len(chunks)}...")
            
            # 检查缓存
            cached = cache.get(chunk, model, direction)
            if cached:
                translated = cached
                logger.info(f"   ♻️ Chunk {i+1} (cached)")
            else:
                try:
                    translated = call_llm_api(
                        text=chunk,
                        system_prompt=system_prompt,
                        model=model,
                        api_base=api_base,
                        api_key=api_key,
                        timeout=timeout,
                        max_retries=max_retries,
                        interval=interval
                    )
                    cache.set(chunk, model, direction, translated)
                    logger.info(f"   ✓ Chunk {i+1} (translated)")
                except Exception as e:
                    logger.error(f"   ✗ Chunk {i+1} failed: {e}")
                    continue
            
            # 解析翻译结果，构建映射
            # 格式：[comment] 原文 -> 译文
            for line in translated.split('\n'):
                match = re.match(r'\[(comment|chinese_string)\]\s*(.+)', line)
                if match:
                    typ, translated_text = match.groups()
                    # 在 translatable_parts 中找到对应的原文
                    for start, end, original_text, part_type in translatable_parts:
                        if part_type == typ and original_text.strip() == translated_text.strip():
                            translations[original_text] = translated_text
                            break
        
        # 替换原文中的翻译部分
        result_content = original_content
        for original, translated in translations.items():
            result_content = result_content.replace(original, translated)
        
        # 保存结果
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(result_content)
        
        logger.info(f"   ✅ Saved: {os.path.basename(output_file)}")
        return True
    
    except Exception as e:
        logger.error(f"   ❌ Style file translation failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def translate_cls_or_sty_file_wrapper(
    input_file: str,
    output_file: str,
    api_base: str,
    api_key: str,
    model: str,
    direction: str,
    verbose: bool = True
) -> bool:
    """
    使用 latex_translation.py 翻译 .cls 文件
    
    :param input_file: 输入 .cls 文件
    :param output_file: 输出 .cls 文件
    :param api_base: API 地址
    :param api_key: API 密钥
    :param model: 模型名称
    :param direction: 翻译方向（zh-to-en 或 en-to-zh）
    :param verbose: 是否显示详细信息
    :return: 是否成功
    """
    if not CLS_TRANSLATOR_AVAILABLE:
        logger.error("❌ latex_translation module not available, cannot translate .cls files")
        return False
    
    try:
        logger.info(f"📄 Translating .cls file: {os.path.basename(input_file)}")
        
        # 调用你的翻译器
        result = translate_cls_or_sty_file(
            input_file=input_file,
            output_file=output_file,
            api_key=api_key,
            model=model,
            base_url=api_base,
            max_tokens_per_group=2000,
            verbose=verbose
        )
        
        if result['success']:
            logger.info(f"   ✅ Translated {result['blocks_translated']} blocks")
            logger.info(f"   💾 Saved: {os.path.basename(output_file)}")
            return True
        else:
            logger.error(f"   ❌ Translation failed")
            return False
            
    except Exception as e:
        logger.error(f"   ❌ CLS translation error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def build_style_file_translation_prompt(source_lang: str, target_lang: str) -> str:
    """样式文件专用翻译提示词"""
    
    prompt = f"""You are translating comments and Chinese strings from a LaTeX style file (.cls/.sty).

**Critical Rules**:
1. Input format: Each line is `[type] text`, where type is 'comment' or 'chinese_string'
2. **Only translate the text part**, keep the `[type]` prefix
3. Output format: Same as input, one line per entry
4. Keep LaTeX commands (\\xxx) unchanged
5. Keep special characters (~, {{}}, []) unchanged
6. Translate from {source_lang} to {target_lang}

**Example**:
Input:
[comment] 表格名及图名
[chinese_string] 定义~
[comment] 去掉图标签后的冒号

Output:
[comment] Table name and figure name
[chinese_string] Definition~
[comment] Remove colon after figure label

Now translate:"""
    
    return prompt
def translate_latex_project(
    input_main_file: str,
    output_dir: str,
    translate_function,
    translate_kwargs: dict,
    translate_style_files: bool = False,
    progress_callback=None
) -> bool:
    """翻译整个 LaTeX 项目（支持 CLS 翻译）"""
    
    if not test_api_connection(translate_kwargs['api_base'], translate_kwargs['api_key']):
        logger.error("API connection test failed, aborting")
        return False
    
    cache = TranslationCache(
        max_age_days=translate_kwargs.get('cache_max_age_days', 30),
        max_entries=translate_kwargs.get('cache_max_entries', 10000)
    )
    
    try:
        direction = "zh-to-en" if translate_kwargs['source_lang'] == 'Chinese' else "en-to-zh"
        
        logger.info("=" * 80)
        logger.info(f"🚀 LaTeX Project Translation: {direction}")
        logger.info(f"📂 Main file: {os.path.basename(input_main_file)}")
        logger.info(f"⚙️  Style files translation: {'Enabled' if translate_style_files else 'Disabled'}")
        if translate_style_files and CLS_TRANSLATOR_AVAILABLE:
            logger.info(f"🔧 Using enhanced CLS translator (latex_translation.py)")
        logger.info("=" * 80)
        
        project_root = os.path.dirname(os.path.abspath(input_main_file))
        
        # 1. 查找所有 .tex 文件
        all_tex_files = [input_main_file]
        referenced_files = find_referenced_files(input_main_file, project_root)
        all_tex_files.extend(referenced_files)
        all_tex_files = list(dict.fromkeys(all_tex_files))
        
        logger.info(f"\n📚 Found {len(all_tex_files)} .tex files")
        
        # 2. 🆕 查找样式文件（区分 CLS 和 STY）
        style_files_dict = {'cls': [], 'sty': []}
        if translate_style_files:
            logger.info("\n🔍 Searching for style files...")
            style_files_dict = find_style_files(input_main_file, project_root)
            
            total_style_files = len(style_files_dict['cls']) + len(style_files_dict['sty'])
            
            if total_style_files > 0:
                logger.info(f"📋 Found {total_style_files} style files:")
                if style_files_dict['cls']:
                    logger.info(f"   • {len(style_files_dict['cls'])} .cls files")
                    for cls in style_files_dict['cls']:
                        logger.info(f"     - {os.path.relpath(cls, project_root)}")
                if style_files_dict['sty']:
                    logger.info(f"   • {len(style_files_dict['sty'])} .sty files")
                    for sty in style_files_dict['sty']:
                        logger.info(f"     - {os.path.relpath(sty, project_root)}")
            else:
                logger.info("   ℹ️ No local style files found")
        
        
        os.makedirs(output_dir, exist_ok=True)
        all_processed_files = []
        
        # 3. 翻译 .tex 文件
        success_count = 0
        total_style_files = len(style_files_dict['cls']) + len(style_files_dict['sty'])
        total_files = len(all_tex_files) + total_style_files
        
        for idx, tex_file in enumerate(all_tex_files, 1):
            logger.info(f"\n{'='*80}")
            logger.info(f"[{idx}/{total_files}] Processing .tex file...")
            
            rel_path = os.path.relpath(tex_file, project_root)
            output_file = os.path.join(output_dir, rel_path)
            
            if progress_callback:
                progress_callback(
                    current_file=os.path.basename(tex_file),
                    current=idx,
                    total=total_files,
                    message=f"Translating {rel_path}"
                )
            
            if translate_latex_file(
                input_file=tex_file,
                output_file=output_file,
                model=translate_kwargs['model'],
                api_base=translate_kwargs['api_base'],
                api_key=translate_kwargs['api_key'],
                source_lang=translate_kwargs['source_lang'],
                target_lang=translate_kwargs['target_lang'],
                direction=direction,
                cache=cache,
                timeout=translate_kwargs.get('timeout', 180),
                max_retries=translate_kwargs.get('max_retries', 3),
                interval=translate_kwargs.get('interval', 0.5)
            ):
                success_count += 1
                all_processed_files.append(tex_file)  # 🆕 记录已处理文件
        
        # 4. 翻译 .cls 文件
        if translate_style_files and style_files_dict['cls']:
            logger.info(f"\n{'='*80}")
            logger.info("📋 Translating .cls files with enhanced translator...")
            
            for idx, cls_file in enumerate(style_files_dict['cls'], len(all_tex_files) + 1):
                logger.info(f"\n{'='*80}")
                logger.info(f"[{idx}/{total_files}] Processing .cls file...")
                
                rel_path = os.path.relpath(cls_file, project_root)
                output_file = os.path.join(output_dir, rel_path)
                
                if progress_callback:
                    progress_callback(
                        current_file=os.path.basename(cls_file),
                        current=idx,
                        total=total_files,
                        message=f"Translating {rel_path}"
                    )
                
                if translate_cls_or_sty_file_wrapper(
                    input_file=cls_file,
                    output_file=output_file,
                    api_base=translate_kwargs['api_base'],
                    api_key=translate_kwargs['api_key'],
                    model=translate_kwargs['model'],
                    direction=direction,
                    verbose=True
                ):
                    success_count += 1
                    all_processed_files.append(cls_file)  # 🆕 记录已处理文件
        
        # 5. 翻译 .sty 文件（如果有）
        if translate_style_files and style_files_dict['sty']:
            logger.info(f"\n{'='*80}")
            logger.info("📋 Translating .sty files with enhanced translator...")
            
            current_idx = len(all_tex_files) + len(style_files_dict['cls']) + 1
            
            for idx, sty_file in enumerate(style_files_dict['sty'], current_idx):
                logger.info(f"\n{'='*80}")
                logger.info(f"[{idx}/{total_files}] Processing .sty file...")
                
                rel_path = os.path.relpath(sty_file, project_root)
                output_file = os.path.join(output_dir, rel_path)
                
                if progress_callback:
                    progress_callback(
                        current_file=os.path.basename(sty_file),
                        current=idx,
                        total=total_files,
                        message=f"Translating {rel_path}"
                    )
                
                # 🆕 使用专用翻译器（与 .cls 相同的处理方式）
                if translate_cls_or_sty_file_wrapper(
                    input_file=sty_file,
                    output_file=output_file,
                    api_base=translate_kwargs['api_base'],
                    api_key=translate_kwargs['api_key'],
                    model=translate_kwargs['model'],
                    direction=direction,
                    verbose=True
                ):
                    success_count += 1
                    all_processed_files.append(sty_file)  # 记录已处理文件
                else:
                    logger.error(f"   ❌ Failed to translate .sty file")
        
        # 6. 复制其他文件
        logger.info("\n" + "=" * 80)
        if not translate_style_files:
            # 如果不翻译样式文件，需要复制它们
            logger.info("📋 Copying style files without translation...")
            for cls_file in style_files_dict['cls']:
                rel_path = os.path.relpath(cls_file, project_root)
                dest_file = os.path.join(output_dir, rel_path)
                try:
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    shutil.copy2(cls_file, dest_file)
                    logger.info(f"   ✓ Copied .cls: {rel_path}")
                except Exception as e:
                    logger.warning(f"   ⚠️ Failed to copy {rel_path}: {e}")
            
            for sty_file in style_files_dict['sty']:
                rel_path = os.path.relpath(sty_file, project_root)
                dest_file = os.path.join(output_dir, rel_path)
                try:
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    shutil.copy2(sty_file, dest_file)
                    logger.info(f"   ✓ Copied .sty: {rel_path}")
                except Exception as e:
                    logger.warning(f"   ⚠️ Failed to copy {rel_path}: {e}")
        
        # 🆕 关键修复：传递已处理文件列表，避免覆盖
        copy_all_project_files(
            project_root, 
            output_dir, 
            processed_files=all_processed_files  # 传递已处理文件列表
        )
        
        logger.info("\n" + "=" * 80)
        logger.info(f"✅ Translation Complete: {success_count}/{total_files} files succeeded")
        logger.info(f"📁 Output directory: {os.path.abspath(output_dir)}")
        logger.info("=" * 80)
        
        return success_count == total_files
    
    except Exception as e:
        logger.error(f"❌ Project translation failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
    
    finally:
        cache.close()
class StyleFileTranslator(ClsStyTranslator):
    """
    样式文件翻译器（更保守的策略）
    
    特点：
    1. 只翻译行末注释（% 开头）
    2. 只翻译字符串中的中文（{定义~}）
    3. 完全保护所有 LaTeX 命令
    """
    
    # 🔧 更严格的保护模式
    PROTECTED_PATTERNS = [
        # 保护所有 LaTeX 命令（除了注释）
        (r'\\[a-zA-Z@]+\*?(?:\[[^\]]*\])*(?:\{[^}]*\})*', 'COMMAND'),
        
        # 保护所有环境
        (r'\\begin\{[^}]+\}.*?\\end\{[^}]+\}', 'ENVIRONMENT'),
        
        # 保护所有选项
        (r'\[[^\]]*\]', 'OPTION'),
        
        # 保护数字和长度单位
        (r'\d+(?:\.\d+)?(?:pt|bp|cm|mm|em|ex|sp)', 'LENGTH'),
    ]
    
    def extract_translatable_parts(self, content: str) -> List[tuple]:
        """
        提取可翻译的部分（注释和中文字符串）
        
        返回: [(start_pos, end_pos, text, type), ...]
        type: 'comment' 或 'chinese_string'
        """
        translatable = []
        
        lines = content.split('\n')
        current_pos = 0
        
        for line_idx, line in enumerate(lines):
            # 1. 提取行末注释（% 后面的中文）
            comment_match = re.search(r'%\s*(.+)$', line)
            if comment_match:
                comment_text = comment_match.group(1).strip()
                # 检查是否包含中文
                if re.search(r'[\u4e00-\u9fff]', comment_text):
                    start = current_pos + comment_match.start(1)
                    end = current_pos + comment_match.end(1)
                    translatable.append((start, end, comment_text, 'comment'))
            
            # 2. 提取花括号中的纯中文字符串（如 {定义~}）
            # 排除命令和环境
            for match in re.finditer(r'\{([^}]+)\}', line):
                text = match.group(1)
                # 必须包含中文，且不能包含反斜杠（排除命令）
                if re.search(r'[\u4e00-\u9fff]', text) and '\\' not in text:
                    start = current_pos + match.start(1)
                    end = current_pos + match.end(1)
                    translatable.append((start, end, text, 'chinese_string'))
            
            current_pos += len(line) + 1  # +1 for newline
        
        return translatable
    
    def split_into_chunks(self, text: str, max_length: int = 1000) -> List[str]:
        """
        样式文件专用切分（按注释块）
        
        策略：每 N 条注释/中文字符串组成一个块
        """
        chunks = []
        translatable_parts = self.extract_translatable_parts(text)
        
        if not translatable_parts:
            return [text]  # 无可翻译内容，返回原文
        
        # 按位置分组（每 20 个一组）
        chunk_size = 20
        for i in range(0, len(translatable_parts), chunk_size):
            chunk_parts = translatable_parts[i:i+chunk_size]
            
            # 提取这些部分的文本
            chunk_texts = []
            for start, end, txt, typ in chunk_parts:
                chunk_texts.append(f"[{typ}] {txt}")
            
            chunks.append("\n".join(chunk_texts))
        
        return chunks
def find_style_files(tex_file: str, base_dir: str, visited: set = None) -> Dict[str, List[str]]:
    """
    递归查找 .cls 和 .sty 文件（避免循环引用）
    """
    if visited is None:
        visited = set()
    
    style_files = {'cls': [], 'sty': []}
    
    # 获取文件的绝对路径
    tex_file_abs = os.path.abspath(tex_file)
    
    # 如果已访问过，直接返回
    if tex_file_abs in visited:
        return style_files
    
    # 标记为已访问
    visited.add(tex_file_abs)
    
    try:
        with open(tex_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 1. 查找 \documentclass{xxx}
        for match in re.findall(r'\\documentclass(?:\[.*?\])?\{([^}]+)\}', content):
            cls_file = match.strip()
            if not cls_file.endswith('.cls'):
                cls_file += '.cls'
            
            # 尝试在项目目录中查找
            cls_path = os.path.join(base_dir, cls_file)
            if os.path.exists(cls_path):
                cls_path_abs = os.path.abspath(cls_path)
                
                # 如果这个 .cls 文件还没被处理过
                if cls_path_abs not in visited:
                    style_files['cls'].append(cls_path_abs)
                    logger.info(f"   Found document class: {cls_file}")
                    
                    # 🆕 递归查找这个 .cls 文件中引用的样式文件
                    sub_styles = find_style_files(cls_path, base_dir, visited)
                    style_files['cls'].extend(sub_styles['cls'])
                    style_files['sty'].extend(sub_styles['sty'])
        
        # 2. 查找 \usepackage{xxx}（只查找本地文件）
        for match in re.findall(r'\\usepackage(?:\[.*?\])?\{([^}]+)\}', content):
            packages = match.split(',')
            for pkg in packages:
                pkg = pkg.strip()
                if not pkg.endswith('.sty'):
                    pkg += '.sty'
                
                # 尝试在项目目录中查找
                sty_path = os.path.join(base_dir, pkg)
                if os.path.exists(sty_path):
                    sty_path_abs = os.path.abspath(sty_path)
                    
                    # 如果这个 .sty 文件还没被处理过
                    if sty_path_abs not in visited:
                        style_files['sty'].append(sty_path_abs)
                        logger.info(f"   Found package: {pkg}")
                        
                        # 🆕 递归查找这个 .sty 文件中引用的样式文件
                        sub_styles = find_style_files(sty_path, base_dir, visited)
                        style_files['cls'].extend(sub_styles['cls'])
                        style_files['sty'].extend(sub_styles['sty'])
        
        # 3. 🆕 查找 \RequirePackage{xxx}（.cls 文件常用）
        for match in re.findall(r'\\RequirePackage(?:\[.*?\])?\{([^}]+)\}', content):
            packages = match.split(',')
            for pkg in packages:
                pkg = pkg.strip()
                if not pkg.endswith('.sty'):
                    pkg += '.sty'
                
                sty_path = os.path.join(base_dir, pkg)
                if os.path.exists(sty_path):
                    sty_path_abs = os.path.abspath(sty_path)
                    
                    if sty_path_abs not in visited:
                        style_files['sty'].append(sty_path_abs)
                        logger.info(f"   Found required package: {pkg}")
                        
                        # 递归查找
                        sub_styles = find_style_files(sty_path, base_dir, visited)
                        style_files['cls'].extend(sub_styles['cls'])
                        style_files['sty'].extend(sub_styles['sty'])
        
        # 4. 🆕 查找 \input{xxx.sty} 或 \input{xxx.cls}（少见但可能存在）
        for match in re.findall(r'\\input\{([^}]+\.(?:cls|sty))\}', content):
            style_file = match.strip()
            style_path = os.path.join(base_dir, style_file)
            
            if os.path.exists(style_path):
                style_path_abs = os.path.abspath(style_path)
                ext = os.path.splitext(style_file)[1].lower()
                
                if style_path_abs not in visited:
                    if ext == '.cls':
                        style_files['cls'].append(style_path_abs)
                        logger.info(f"   Found input class: {style_file}")
                    elif ext == '.sty':
                        style_files['sty'].append(style_path_abs)
                        logger.info(f"   Found input package: {style_file}")
                    
                    # 递归查找
                    sub_styles = find_style_files(style_path, base_dir, visited)
                    style_files['cls'].extend(sub_styles['cls'])
                    style_files['sty'].extend(sub_styles['sty'])
    
    except Exception as e:
        logger.error(f"Error reading {tex_file}: {e}")
    
    # 去重（保持顺序）
    style_files['cls'] = list(dict.fromkeys(style_files['cls']))
    style_files['sty'] = list(dict.fromkeys(style_files['sty']))
    
    return style_files
def copy_style_files(source_dir: str, dest_dir: str):
    """
    复制样式文件（不翻译时使用）
    """
    style_extensions = {'.cls', '.sty', '.bst'}
    copied_count = 0
    
    for root, dirs, files in os.walk(source_dir):
        root_abs = os.path.abspath(root)
        dest_dir_abs = os.path.abspath(dest_dir)
        if root_abs == dest_dir_abs or root_abs.startswith(dest_dir_abs + os.sep):
            continue
        
        for file in files:
            _, ext = os.path.splitext(file)
            if ext.lower() in style_extensions:
                source_file = os.path.join(root, file)
                rel_path = os.path.relpath(source_file, source_dir)
                dest_file = os.path.join(dest_dir, rel_path)
                
                try:
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    shutil.copy2(source_file, dest_file)
                    copied_count += 1
                    logger.info(f"   ✓ Copied style file: {rel_path}")
                except Exception as e:
                    logger.warning(f"   ⚠️ Failed to copy {rel_path}: {e}")
    
    if copied_count > 0:
        logger.info(f"   📋 Total style files copied: {copied_count}")
    else:
        logger.info("   ℹ️ No style files to copy")

# ========== 缓存管理工具函数 ==========

def clear_cache(cache_file='translation_cache.json'):
   """清空所有缓存"""
   cache = TranslationCache(cache_file=cache_file)
   cache.clear_all()
   cache.close()


def clear_old_cache(days: int = 30, cache_file='translation_cache.json'):
   """清理指定天数前的缓存"""
   cache = TranslationCache(cache_file=cache_file)
   cache.clear_old(days=days)
   cache.close()


def show_cache_stats(cache_file='translation_cache.json'):
   """显示缓存统计信息"""
   cache = TranslationCache(cache_file=cache_file)
   stats = cache.get_stats()
   
   print("\n" + "=" * 60)
   print("📊 Translation Cache Statistics")
   print("=" * 60)
   print(f"Cache file: {cache_file}")
   print(f"Total entries: {stats['cache_entries']}")
   print(f"Cache size: {stats['cache_size_mb']}")
   print(f"Session hits: {stats['hits']}")
   print(f"Session misses: {stats['misses']}")
   print(f"Session hit rate: {stats['hit_rate']}")
   print("=" * 60)
   
   # 显示缓存条目的时间分布
   if cache.cache:
       from collections import defaultdict
       age_distribution = defaultdict(int)
       now = datetime.now()
       
       for value in cache.cache.values():
           if isinstance(value, dict) and 'timestamp' in value:
               try:
                   timestamp = datetime.fromisoformat(value['timestamp'])
                   age_days = (now - timestamp).days
                   
                   if age_days == 0:
                       age_distribution['Today'] += 1
                   elif age_days <= 7:
                       age_distribution['This week'] += 1
                   elif age_days <= 30:
                       age_distribution['This month'] += 1
                   elif age_days <= 90:
                       age_distribution['Last 3 months'] += 1
                   else:
                       age_distribution['Older'] += 1
               except:
                   age_distribution['Unknown'] += 1
       
       if age_distribution:
           print("\n📅 Cache age distribution:")
           for period, count in sorted(age_distribution.items()):
               percentage = count / len(cache.cache) * 100
               print(f"   {period}: {count} entries ({percentage:.1f}%)")
           print()
   
   cache.close()
   # 2. 查看缓存统计
   # show_cache_stats()
   
   # 3. 清理 30 天前的缓存
   # clear_old_cache(days=30)
   
   # 4. 清空所有缓存
   # clear_cache()

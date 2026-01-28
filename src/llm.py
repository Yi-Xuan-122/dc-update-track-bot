from src import config
import httpx
import re
import logging
import json
log = logging.getLogger(__name__)
import copy
from src.chat.tool_src import tool_manager

def parse_yaml(text:str)->dict:
    if not text or not text.strip():
        return {}
    result = {}
    lines = text.strip().split('\n')
    for line in lines:
        cleaned_line = line.strip()
        if not cleaned_line or not cleaned_line.startswith('-'):
            continue    

        content = cleaned_line.lstrip('-').strip()
        parts = content.split(':',1)
        if len(parts) == 2:
            key = parts[0].strip()
            value = parts[1].strip()
            if re.fullmatch(r'-?\d+', value):
                result[key] = int(value)
            elif re.fullmatch(r'-?\d+\.\d+', value):
                result[key] = float(value)
            elif value.lower() == 'true':
                result[key] = True
            elif value.lower() == 'false':
                result[key] = False
            else:
                result[key] = value  
    return result

def smart_split_message(text: str, max_length: int = 1900) -> list[str]:
    """
    智能分块逻辑 V3 (修复长单行溢出问题):
    1. 结构化解析：区分代码块和普通文本。
    2. 代码块处理：保留完整性，仅在过大时递归拆分。
    3. 文本处理：贪婪填充，遇到超长单行立即强制切片，防止缓冲区溢出。
    """
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = ""
    
    parts = re.split(r'(```[\s\S]*?```)', text)

    for part in parts:
        if not part:
            continue
            
        is_code_block = part.startswith("```") and part.endswith("```")
        part_len = len(part)

        if is_code_block:
            # Case 1: 代码块本身超长 -> 结算当前 + 递归拆分代码块
            if part_len > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""
                chunks.extend(_split_large_code_block(part, max_length))
            
            # Case 2: 代码块能放入 -> 检查是否需要换行
            else:
                if len(current_chunk) + part_len > max_length:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = part
                else:
                    current_chunk += part
        else:
            # Case 3: 普通文本
            if len(current_chunk) + part_len <= max_length:
                current_chunk += part
            else:
                # 文本过长，逐行处理
                lines = part.split('\n')
                for i, line in enumerate(lines):
                    # 补回换行符（最后一行如果原文本没换行，这里也不加，但 split 行为通常会吞掉分隔符）
                    # 简单起见，除了最后一行，都加换行。如果原串末尾有\n，split会产生空串在最后。
                    line_text = line + ("\n" if i < len(lines) - 1 else "")
                    
                    if len(current_chunk) + len(line_text) > max_length:
                        # 当前缓冲已满，先推送
                        if current_chunk:
                            chunks.append(current_chunk)
                            current_chunk = ""
                        
                        # 检查新的一行是否本身就超长
                        if len(line_text) > max_length:
                            # 强制切片长单行
                            while len(line_text) > max_length:
                                chunks.append(line_text[:max_length])
                                line_text = line_text[max_length:]
                            current_chunk = line_text
                        else:
                            current_chunk = line_text
                    else:
                        current_chunk += line_text

    if current_chunk:
        chunks.append(current_chunk)

    return chunks

def _split_large_code_block(code_block: str, max_length: int) -> list[str]:
    """辅助函数：切割超大代码块，处理内部超长行"""
    first_line_end = code_block.find('\n')
    if first_line_end == -1:
        # 极端的单行代码块...直接强切
        return [code_block[i:i+max_length] for i in range(0, len(code_block), max_length)]
    
    header = code_block[:first_line_end].strip() # ```python
    language = header.lstrip('`').strip()
    content = code_block[first_line_end+1:-3] 
    
    sub_chunks = []
    current_sub = header + "\n"
    
    # 预留闭合标记的长度
    footer_len = 5 # \n```
    
    lines = content.split('\n')
    for line in lines:
        line_with_nl = line + "\n"
        
        # 检查是否溢出
        if len(current_sub) + len(line_with_nl) + footer_len > max_length:
            # 闭合当前块
            if not current_sub.endswith("\n"):
                current_sub += "\n"
            current_sub += "```"
            sub_chunks.append(current_sub)
            
            # 开启新块
            current_sub = f"```{language}\n"
            
            # 处理新行本身就超长的情况
            if len(current_sub) + len(line_with_nl) + footer_len > max_length:
                # 必须强制切断这一行，否则会死循环或溢出
                available_space = max_length - len(current_sub) - footer_len
                remaining_line = line_with_nl
                
                while len(remaining_line) > available_space:
                    part = remaining_line[:available_space]
                    remaining_line = remaining_line[available_space:]
                    
                    current_sub += part + "```" # 闭合
                    sub_chunks.append(current_sub)
                    
                    current_sub = f"```{language}\n" # 重开
                    available_space = max_length - len(current_sub) - footer_len
                
                current_sub += remaining_line
            else:
                current_sub += line_with_nl
        else:
            current_sub += line_with_nl
            
    # 收尾
    if current_sub.strip() != f"```{language}":
        stripped = current_sub.strip()
        if not stripped.endswith("```"):
             if not current_sub.endswith("\n") and not current_sub.endswith("```"):
                 current_sub += "\n"
             if not current_sub.endswith("```"):
                 current_sub += "```"
        sub_chunks.append(current_sub)
        
    return sub_chunks

class LLM():
    def __init__(self):
        try:
            if config.BASE_URL.endswith('#'):
                self.base_url = config.BASE_URL.rstrip('#') 
            else:
                self.base_url = f"{config.BASE_URL.rstrip('/')}/v1/chat/completions"
            
            if config.MODEL.endswith('#'):
                self.model = config.MODEL.rstrip('#')
            else:
                self.model = config.MODEL
            
            self.additional_header = parse_yaml(config.ADDITIONAL_HEADER)
            self.body_argument = parse_yaml(config.BODY_ARGUMENT)

        except Exception as e:
            logging.critical(f"Critical in LLM(__init__):{e}")
            raise

    async def llm_call(self,text:str,include_tools: bool = False)-> list[str]:
        try:
            payload_data = json.loads(text)
            if not isinstance(payload_data,list):
                log.error(f"Error : Parsed_text is not a list")
                raise ValueError("Provided text does not represent a valid conversation list.")
        except json.JSONDecodeError as e:
            log.error(f"Error : Failed to decode text JSON string : {e}")
            raise ValueError(f"Invaild JSON format for 'text' : {e}") from e
        
        headers = {"Content-Type": "application/json"}
        if self.additional_header:
            for key, value in self.additional_header.items():
                headers[str(key)] = str(value)
        body = {}
        body.update(self.body_argument)

        # ---------------- Gemini 格式处理逻辑 ----------------
        if config.LLM_FORMAT == "gemini":
            final_contents = []
            system_instruction_payload = None

            # 1. 分离 systemInstruction 和 contents
            for item in payload_data:
                if "systemInstruction" in item:
                    system_instruction_payload = item["systemInstruction"]
                else:
                    final_contents.append(item)
            
            body["contents"] = final_contents
            
            # 2. 处理工具调用配置 (GEMINI_SEARCH / NATIVE / CUSTOM)

            #  用户自定义工具逻辑
            if include_tools:
                if config.GEMINI_TOOL_CALL == "custom":
                    # [CUSTOM 模式]
                    # 注入 System Prompt 和 Stop Sequences
                    custom_tool_prompt = tool_manager.get_custom_tools_prompt()
                    
                    # 将自定义工具提示词追加到 System Instruction 中
                    if not system_instruction_payload:
                        system_instruction_payload = {"parts": [{"text": ""}]}
                    
                    # 确保 parts 存在
                    if "parts" not in system_instruction_payload:
                        system_instruction_payload["parts"] = [{"text": ""}]
                    
                    # 追加提示词
                    system_instruction_payload["parts"][0]["text"] += f"\n{custom_tool_prompt}"

                    # 设置停止序列，防止模型自己输出执行结果，并作为解析锚点
                    current_stops = body.get("generationConfig", {}).get("stopSequences", [])
                    # 1.工具
                    if "<||tool_end||>" not in current_stops:
                        current_stops.append("<||tool_end||>")
                    # 2.正常回复
                    if "<||reply_end||>" not in current_stops:
                        current_stops.append("<||reply_end||>")
                    
                    if "generationConfig" not in body:
                        body["generationConfig"] = {}
                    body["generationConfig"]["stopSequences"] = current_stops

                else:
                    tools_payload = tool_manager.get_gemini_tools_payload()
                    if tools_payload:
                        body["tools"] = tools_payload

                    if config.GEMINI_SEARCH:
                        body["tools"] = [{"google_search": {}}]

            # 3. 注入 System Instruction
            if system_instruction_payload:
                body["systemInstruction"] = system_instruction_payload

        else:
            # OpenAI 格式
            body["messages"] = payload_data

        if body.get("stream"):
            body["stream"] = False

        # Debug Log
        try:
            log.info(f"Sending request to {self.base_url}")
        except Exception as e:
            log.error(f"Failed to generate debug log: {e}")

        async with httpx.AsyncClient(timeout=300,proxy=None) as client:
            response = await client.post(url=self.base_url,headers=headers,json=body)
            if response.status_code != 200:
                e = f"Failed to fetch LLM response:Status code {response.status_code}, Response: {response.text}"
                log.error(e)
                raise RuntimeError(e)
            try:
                data = response.json()
                finish_reason = "UNKNOWN"

                if config.LLM_FORMAT == "gemini":
                    if "candidates" not in data or not data["candidates"]:
                        return {"type": "text", "chunks": ["(无响应/内容被安全策略过滤)"], "raw": data}
                    
                    first_candidate = data["candidates"][0]
                    finish_reason = first_candidate.get("finishReason", "UNKNOWN")
                    content_obj = first_candidate.get("content", {})
                    parts = content_obj.get("parts", [])

                    if not parts:
                        finish_reason = first_candidate.get("finishReason", "UNKNOWN")
                        log.warning(f"Gemini returned empty parts. FinishReason: {finish_reason}")
                        fallback_text = f"(模型未返回内容，结束原因: {finish_reason})"
                        return {
                            "type": "text",
                            "chunks": [fallback_text],
                            "raw": data,
                            "text": fallback_text
                        }

                    # --- Native Function Call 检测 ---
                    function_call_part = None
                    for part in parts:
                        if "functionCall" in part:
                            function_call_part = part
                            break

                    if function_call_part:
                        fc = function_call_part["functionCall"]
                        return {
                            "type": "tool_call",
                            "name": fc["name"],
                            "args": fc["args"],
                            "parts_trace": parts, 
                            "raw": data
                        }
                    
                    content = "".join([p.get("text", "") for p in parts])

                else:
                    content = data.get("choices",[{}])[0].get("message", {}).get("content", "")

            except Exception as e:
                log.error(f"Failed parsing response. Data snippet: {str(data)[:200]}")
                log.error(f"Exception: {e}", exc_info=True)
                raise ValueError(f"Invalid response format: {e}")

            # 使用修复后的智能分块
            chunks = smart_split_message(content)
            
            return {
                "type": "text",
                "chunks": chunks,
                "raw": data,
                "text": content,
                "finish_reason": finish_reason
            }
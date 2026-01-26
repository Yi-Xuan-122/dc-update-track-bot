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
            debug_body = copy.deepcopy(body)
            truncate_sensitive_data(debug_body)
            log.info(f"Sending request to {self.base_url}")
            log.debug(f"Request Body (Truncated):\n{json.dumps(debug_body, ensure_ascii=False, indent=2)}")
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

            chunk_size = 1800
            chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
            return {
                "type": "text",
                "chunks": chunks,
                "raw": data,
                "text": content,
                "finish_reason": finish_reason
            }

def truncate_sensitive_data(obj):
    """递归遍历字典或列表，截断 Base64 数据"""
    if isinstance(obj, list):
        for item in obj:
            truncate_sensitive_data(item)
    elif isinstance(obj, dict):
        if "inline_data" in obj and isinstance(obj["inline_data"], dict):
            data = obj["inline_data"].get("data")
            if isinstance(data, str) and len(data) > 20:
                obj["inline_data"]["data"] = data[:20] + "..."
        if "image_url" in obj and isinstance(obj["image_url"], dict):
            url = obj["image_url"].get("url")
            if isinstance(url, str) and url.startswith("data:") and len(url) > 40:
                obj["image_url"]["url"] = url[:40] + "..."
        for value in obj.values():
            truncate_sensitive_data(value)
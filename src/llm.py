from src import config
import httpx
import re
import logging
import json
log = logging.getLogger(__name__)
import copy

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
                # 包含字母、特殊字符，或者混合类型的，一律按原样保存为字符串
                result[key] = value  
    return result

class LLM():
    def __init__(self):
        try:
            if config.BASE_URL.endswith('#'):
                self.base_url = config.BASE_URL.rstrip('#') 
            else:
                self.base_url = f"{config.BASE_URL.rstrip('/')}/v1/chat/completions"
            
            # 同理处理 Model
            if config.MODEL.endswith('#'):
                self.model = config.MODEL.rstrip('#')
            else:
                self.model = config.MODEL
            # parse start
            self.additional_header = parse_yaml(config.ADDITIONAL_HEADER)
            self.body_argument = parse_yaml(config.BODY_ARGUMENT)

        except Exception as e:
            logging.critical(f"Critical in LLM(__init__):{e}")
            raise

    async def llm_call(self,text:str)-> list[str]:
        #我们期望获得一个JSON包装的text
        try:
            payload_data = json.loads(text)
            if not isinstance(payload_data,list):
                log.error(f"Error : Parsed_text is not a list")
                raise ValueError("Provided text does not represent a valid conversation list.")
        except json.JSONDecodeError as e:
            log.error(f"Error : Failed to decode text JSON string : {e}")
            raise ValueError(f"Invaild JSON format for 'text' : {e}") from e
        
        #开始构建请求
        headers = {"Content-Type": "application/json"}
        if self.additional_header:
            for key, value in self.additional_header.items():
                headers[str(key)] = str(value)
        body = {}
        body.update(self.body_argument)
        if config.LLM_FORMAT == "gemini":
            final_contents = []
            system_instruction_payload = None

            # 遍历 payload_data，分离 System Instruction 和 普通 Content
            for item in payload_data:
                if "systemInstruction" in item:
                    system_instruction_payload = item["systemInstruction"]
                else:
                    final_contents.append(item)
            
            body["contents"] = final_contents
            
            # 如果提取到了 systemInstruction，放到 Body 根节点
            if system_instruction_payload:
                body["systemInstruction"] = system_instruction_payload
        else:
            body["messages"] = payload_data
        if body.get("stream"):
            body["stream"] = False

        try:
            debug_body = copy.deepcopy(body)
            truncate_sensitive_data(debug_body)
            log.info(f"Sending request to {self.base_url}")
            log.debug(f"Request Body (Truncated):\n{json.dumps(debug_body, ensure_ascii=False, indent=2)}")
        except Exception as e:
            log.error(f"Failed to generate debug log: {e}")

        # 打印截断后的 debug_body
        log.info(f"Sending request to {self.base_url},Header:{headers}")
        log.debug(f"Request Body: {json.dumps(debug_body, ensure_ascii=False, indent=2)}")
        async with httpx.AsyncClient(timeout=300,proxy=None) as client:
            response = await client.post(url=self.base_url,headers=headers,json=body)
            if response.status_code != 200:
                e = f"Failed to fetch LLM response:Status code {response.status_code}, Response: {response.text}"
                log.error(e)
                raise RuntimeError(e)
            try:
                data = response.json()
                # Gemini 的返回路径通常是 candidates[0].content.parts[0].text
                if config.LLM_FORMAT == "gemini":
                    content = data["candidates"][0]["content"]["parts"][0]["text"]
                else:
                    content = data.get("choices",[{}])[0].get("message", {}).get("content", "")
            except Exception as e:
                log.error(f"Failed parsing response: {e}")
                raise ValueError(f"Invalid response format")

            chunk_size = 1800
            chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
            return {
                "chunks": chunks,
                "raw": data,
                "text": content,
            }
        

def truncate_sensitive_data(obj):
    """递归遍历字典或列表，截断 Base64 数据"""
    if isinstance(obj, list):
        for item in obj:
            truncate_sensitive_data(item)
    elif isinstance(obj, dict):
        # 模式 1: Gemini 原生格式 {"inline_data": {"data": "..."}}
        if "inline_data" in obj and isinstance(obj["inline_data"], dict):
            data = obj["inline_data"].get("data")
            if isinstance(data, str) and len(data) > 20:
                obj["inline_data"]["data"] = data[:20] + "..."
        
        # 模式 2: OpenAI 格式的 Data URL {"image_url": {"url": "data:image/jpeg;base64,..."}}
        if "image_url" in obj and isinstance(obj["image_url"], dict):
            url = obj["image_url"].get("url")
            if isinstance(url, str) and url.startswith("data:") and len(url) > 40:
                obj["image_url"]["url"] = url[:40] + "..."

        # 继续递归遍历字典的所有值
        for value in obj.values():
            truncate_sensitive_data(value)
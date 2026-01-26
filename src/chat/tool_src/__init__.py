# src/tools/__init__.py
from typing import List, Dict, Any, Optional
from src.chat.tool_src.tool_base import BaseTool
# 导入具体工具
from src.chat.tool_src.custom_tool.searxng import SearxngTool
from src.chat.tool_src.custom_tool.WebPageContextTool import WebPageContextTool
from src.chat.chat_env import ENABLE_CUSTOM_TOOLS 
from src.chat.chat_env import CUSTOM_TOOL_PROMPT
import json

class ToolManager:
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        if ENABLE_CUSTOM_TOOLS:
            self.register(SearxngTool())
            self.register(WebPageContextTool())

    def register(self, tool: BaseTool):
        self.tools[tool.name] = tool

    def get_gemini_tools_payload(self) -> Optional[List[Dict]]:
        """获取发送给 Gemini API 的 tools 结构"""
        if not self.tools:
            return None
        
        declarations = [t.to_gemini_schema() for t in self.tools.values()]
        return [{"function_declarations": declarations}]

    async def handle_tool_call(self, tool_name: str, args: Dict) -> str:
        """分发执行"""
        if tool_name not in self.tools:
            return f"Error: Tool '{tool_name}' not found."
        try:
            return await self.tools[tool_name].execute(**args)
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"
        
    def get_custom_tools_prompt(self) -> str:
        """获取 Custom 模式下的 System Prompt 补充"""
        if not self.tools:
            return ""
        
        # 获取纯净的 schema 定义
        declarations = [t.to_gemini_schema() for t in self.tools.values()]
        json_schema = json.dumps({"function_declarations": declarations}, ensure_ascii=False, indent=2)
        prompt = f"<custom_tool_list>\n{json_schema}\n</custom_tool_list>"
        custom_tool_prompt = prompt + CUSTOM_TOOL_PROMPT
        return custom_tool_prompt

# 单例模式
tool_manager = ToolManager()
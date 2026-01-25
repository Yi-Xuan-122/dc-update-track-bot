# src/tools/__init__.py
from typing import List, Dict, Any, Optional
from src.chat.tool_src.tool_base import BaseTool
# 导入具体工具
from src.chat.tool_src.custom_tool.searxng import SearxngTool
from src.chat.tool_src.custom_tool.WebPageContextTool import WebPageContextTool
from src.chat.chat_env import ENABLE_CUSTOM_TOOLS

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

# 单例模式
tool_manager = ToolManager()
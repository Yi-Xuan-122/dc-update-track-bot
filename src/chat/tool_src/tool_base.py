from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称，例如 'internet_search'"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，给 LLM 看的"""
        pass

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema 格式的参数定义"""
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """执行逻辑"""
        pass

    def to_gemini_schema(self) -> Dict[str, Any]:
        """转换为 Gemini 格式的声明"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "OBJECT",
                "properties": self.parameters,
                "required": list(self.parameters.keys())
            }
        }
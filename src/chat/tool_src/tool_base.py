from abc import ABC, abstractmethod
from typing import Dict, Any
import re
import json

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
    
class ToolLexer:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.tokens = []

    def tokenize(self):
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char.isspace() or char == ',':
                self.pos += 1
            elif char == '=':
                self.tokens.append(('EQUALS', '='))
                self.pos += 1
            elif char == '[':
                self.tokens.append(('LBRACKET', '['))
                self.pos += 1
            elif char == ']':
                self.tokens.append(('RBRACKET', ']'))
                self.pos += 1
            elif char == '"':
                self.tokens.append(('STRING', self.read_string()))
            elif char.isdigit() or char == '-': # 处理数字（包括负数）
                self.tokens.append(('NUMBER', self.read_number()))
            elif char.isalpha() or char == '_': # 处理标识符和布尔值
                ident = self.read_identifier()
                if ident.lower() == 'true':
                    self.tokens.append(('BOOLEAN', True))
                elif ident.lower() == 'false':
                    self.tokens.append(('BOOLEAN', False))
                else:
                    self.tokens.append(('IDENTIFIER', ident))
            else:
                self.pos += 1 # 忽略未知字符
        return self.tokens

    def read_number(self):
        start = self.pos
        if self.text[self.pos] == '-': self.pos += 1
        has_dot = False
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == '.':
                if has_dot: break
                has_dot = True
            elif not char.isdigit():
                break
            self.pos += 1
        val_str = self.text[start:self.pos]
        # 处理只有 "-" 的异常情况
        if val_str == '-': return 0 
        return float(val_str) if has_dot else int(val_str)

    def read_string(self):
        result = ""
        self.pos += 1 # 跳过开头的引号
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == '\\' and self.pos + 1 < len(self.text):
                # 简单的转义处理
                next_char = self.text[self.pos+1]
                if next_char == '"': result += '"'
                elif next_char == 'n': result += '\n'
                elif next_char == '\\': result += '\\'
                else: result += next_char
                self.pos += 2
            elif char == '"':
                self.pos += 1
                return result
            else:
                result += char
                self.pos += 1
        return result

    def read_identifier(self):
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == '_'):
            self.pos += 1
        return self.text[start:self.pos]

# ToolLexer: 词法分析器
class ToolLexer:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.tokens = []

    def tokenize(self):
        while self.pos < len(self.text):
            char = self.text[self.pos]
            start_pos = self.pos
            
            if char.isspace() or char == ',':
                self.pos += 1
                continue # 跳过空白和逗号
            elif char == '=':
                self.tokens.append(('EQUALS', '=', start_pos))
                self.pos += 1
            elif char == '[':
                self.tokens.append(('LBRACKET', '[', start_pos))
                self.pos += 1
            elif char == ']':
                self.tokens.append(('RBRACKET', ']', start_pos))
                self.pos += 1
            elif char == '"':
                self.tokens.append(('STRING', self.read_string(), start_pos))
            elif char.isdigit() or char == '-': 
                num = self.read_number()
                self.tokens.append(('NUMBER', num, start_pos))
            elif char.isalpha() or char == '_': 
                ident = self.read_identifier()
                if ident.lower() == 'true':
                    self.tokens.append(('BOOLEAN', True, start_pos))
                elif ident.lower() == 'false':
                    self.tokens.append(('BOOLEAN', False, start_pos))
                else:
                    self.tokens.append(('IDENTIFIER', ident, start_pos))
            else:
                raise SyntaxError(self.format_error(f"无法识别的字符 '{char}'", self.pos))
        return self.tokens

    def read_number(self):
        start = self.pos
        if self.text[self.pos] == '-': self.pos += 1
        
        # 处理只有负号没有数字的情况
        if self.pos >= len(self.text) or not self.text[self.pos].isdigit():
             return 0 # 或者报错
             
        has_dot = False
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == '.':
                if has_dot: break
                has_dot = True
            elif not char.isdigit():
                break
            self.pos += 1
        val_str = self.text[start:self.pos]
        return float(val_str) if has_dot else int(val_str)

    def read_string(self):
        result = ""
        self.pos += 1  # skip opening "
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == '\\' and self.pos + 1 < len(self.text):
                next_char = self.text[self.pos+1]
                if next_char == '"': result += '"'
                elif next_char == 'n': result += '\n'
                elif next_char == '\\': result += '\\'
                else: result += next_char
                self.pos += 2
            elif char == '"':
                self.pos += 1
                return result
            else:
                result += char
                self.pos += 1
        return result

    def read_identifier(self):
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] == '_'):
            self.pos += 1
        return self.text[start:self.pos]
    
    def format_error(self, message, error_pos):
        context_range = 20
        start = max(0, error_pos - context_range)
        end = min(len(self.text), error_pos + context_range)
        snippet = self.text[start:end].replace('\n', ' ')
        pointer = " " * (error_pos - start) + "^"
        return f"{message}\n位置: {error_pos}\n片段: {snippet}\n指引: {pointer}"


# ToolParser: 语法解析器
class ToolParser:
    def __init__(self, tokens, original_text):
        self.tokens = tokens
        self.text = original_text
        self.pos = 0

    def parse(self):
        result = {"name": "", "args": {}}
        
        # 1. 必须以 name="xxx" 开头
        if self.match('IDENTIFIER', 'name'):
            self.consume('EQUALS')
            result["name"] = self.consume('STRING')
        else:
            raise SyntaxError(self.format_error("未在起始位置找到 name=\"工具名\""))

        # 2. 解析后续参数 "key"=value
        while self.pos < len(self.tokens):
            key = self.consume('STRING') 
            self.consume('EQUALS')
            value = self.parse_value()
            result["args"][key] = value
            
        return result

    def parse_value(self):
        if self.pos >= len(self.tokens):
            raise EOFError(self.format_error("意外的输入结束"))
        
        token_type, value, _ = self.tokens[self.pos] # 修复：解包 3 个值
        
        if token_type in ['STRING', 'NUMBER', 'BOOLEAN']:
            self.pos += 1
            return value
        elif token_type == 'LBRACKET':
            return self.parse_array()
        
        raise TypeError(self.format_error(f"遇到不合法的类型 {token_type} ('{value}')"))

    def parse_array(self):
        self.consume('LBRACKET')
        items = []
        while self.pos < len(self.tokens):
            if self.tokens[self.pos][0] == 'RBRACKET':
                break
            items.append(self.parse_value())
        self.consume('RBRACKET')
        return items

    def consume(self, expected_type):
        if self.pos >= len(self.tokens):
            raise EOFError(self.format_error(f"期待类型 {expected_type} 但输入已结束"))
        
        token_type, value, start_pos = self.tokens[self.pos]
        if token_type == expected_type:
            self.pos += 1
            return value
        
        raise TypeError(self.format_error(f"类型不匹配。期待: {expected_type}, 实际看到: {token_type} ('{value}')"))

    def match(self, t_type, value):
        if self.pos < len(self.tokens) and self.tokens[self.pos][0] == t_type and self.tokens[self.pos][1] == value:
            self.pos += 1
            return True
        return False
    
    def get_err_pos(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos][2]
        return len(self.text)
    
    def format_error(self, message):
        error_pos = self.get_err_pos()
        context_range = 20
        start = max(0, error_pos - context_range)
        end = min(len(self.text), error_pos + context_range)
        snippet = self.text[start:end].replace('\n', ' ')
        pointer = " " * (error_pos - start) + "^"
        return f"语法错误: {message}\n片段: {snippet}\n位置: {pointer}"
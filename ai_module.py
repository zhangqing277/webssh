#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI集成模块 - 支持多种AI服务提供商"""

import json
import requests
import logging
import re

logger = logging.getLogger(__name__)


class AIProvider:
    """AI服务提供商基类"""

    def __init__(self, config):
        self.config = config
        self.enabled = config.get('enabled', False)

    def chat(self, messages, stream=False):
        raise NotImplementedError

    def test_connection(self):
        """测试连接是否正常"""
        try:
            resp = self.chat([{"role": "user", "content": "你好"}])
            return bool(resp)
        except Exception as e:
            logger.error(f"AI连接测试失败: {e}")
            return False


class QwenProvider(AIProvider):
    """阿里云百炼 Qwen 模型"""

    def chat(self, messages, stream=False):
        api_key = self.config.get('api_key', '')
        model = self.config.get('model', 'qwen-turbo')
        base_url = self.config.get('base_url', 'https://dashscope.aliyuncs.com/compatible-mode/v1')

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': model,
            'messages': messages,
            'stream': stream
        }

        url = f"{base_url.rstrip('/')}/chat/completions"

        try:
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
            return ''
        except Exception as e:
            logger.error(f"Qwen API调用失败: {e}")
            raise


class AzureOpenAIProvider(AIProvider):
    """微软Azure OpenAI"""

    def chat(self, messages, stream=False):
        api_key = self.config.get('api_key', '')
        endpoint = self.config.get('endpoint', '')
        deployment = self.config.get('deployment', 'gpt-4')
        api_version = self.config.get('api_version', '2024-02-01')

        headers = {
            'api-key': api_key,
            'Content-Type': 'application/json'
        }

        data = {
            'messages': messages,
            'stream': stream
        }

        url = f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

        try:
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
            return ''
        except Exception as e:
            logger.error(f"Azure OpenAI API调用失败: {e}")
            raise


class OpenAICompatibleProvider(AIProvider):
    """通用OpenAI兼容接口（支持DeepSeek、ChatGLM等）"""

    def chat(self, messages, stream=False):
        api_key = self.config.get('api_key', '')
        base_url = self.config.get('base_url', 'https://api.openai.com/v1')
        model = self.config.get('model', 'gpt-3.5-turbo')

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': model,
            'messages': messages,
            'stream': stream
        }

        url = f"{base_url.rstrip('/')}/chat/completions"

        try:
            resp = requests.post(url, headers=headers, json=data, timeout=60)
            resp.raise_for_status()
            result = resp.json()
            if 'choices' in result and len(result['choices']) > 0:
                return result['choices'][0]['message']['content']
            return ''
        except Exception as e:
            logger.error(f"OpenAI Compatible API调用失败: {e}")
            raise


# 提供商注册表
PROVIDERS = {
    'qwen': QwenProvider,
    'azure_openai': AzureOpenAIProvider,
    'openai_compatible': OpenAICompatibleProvider,
}

# 系统提示词
SYSTEM_PROMPT = """你是一个专业的网络设备管理助手。你的职责：
1. 帮助用户执行网络设备配置和管理任务
2. 将用户的中文指令翻译为对应的网络设备命令（如Cisco IOS、Huawei VRP、Linux等）
3. 分析终端输出中的错误信息，提供解决方案
4. 提供网络设备管理的最佳实践建议

当用户描述需求时，请提供具体可执行的命令。
当收到错误信息时，请分析原因并给出修复建议。
请用中文回答，命令部分保持原始格式。"""


class AIManager:
    """AI管理器 - 管理所有AI提供商配置和调用"""

    def __init__(self, config_file='ai_config.json'):
        self.config_file = config_file
        self.config = {}
        self.provider = None
        self.load_config()

    def load_config(self):
        """加载AI配置"""
        import os
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.config_file)
        try:
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                self._init_provider()
        except Exception as e:
            logger.error(f"加载AI配置失败: {e}")
            self.config = {}

    def save_config(self, config):
        """保存AI配置"""
        import os
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), self.config_file)
        try:
            self.config = config
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self._init_provider()
            return True
        except Exception as e:
            logger.error(f"保存AI配置失败: {e}")
            return False

    def _init_provider(self):
        """初始化当前活跃的AI提供商"""
        active = self.config.get('active_provider', '')
        if active and active in PROVIDERS:
            provider_config = self.config.get('providers', {}).get(active, {})
            if provider_config.get('enabled', False):
                self.provider = PROVIDERS[active](provider_config)
                logger.info(f"AI提供商已初始化: {active}")
            else:
                self.provider = None
        else:
            self.provider = None

    def is_available(self):
        """检查AI是否可用"""
        return self.provider is not None

    def chat(self, user_message, context=None):
        """发送消息给AI"""
        if not self.is_available():
            return {"error": "AI服务未配置或未启用"}

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if context:
            messages.append({
                "role": "system",
                "content": f"以下是当前终端的最近输出，请根据这些信息回答用户问题：\n{context}"
            })

        messages.append({"role": "user", "content": user_message})

        try:
            response = self.provider.chat(messages)
            return {"response": response}
        except Exception as e:
            return {"error": f"AI调用失败: {str(e)}"}

    def analyze_error(self, error_output):
        """分析终端错误输出"""
        if not self.is_available():
            return {"error": "AI服务未配置或未启用"}

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"请分析以下终端输出中的错误，给出原因和解决方案：\n\n```\n{error_output}\n```"}
        ]

        try:
            response = self.provider.chat(messages)
            return {"response": response}
        except Exception as e:
            return {"error": f"错误分析失败: {str(e)}"}

    def get_config(self):
        """获取当前配置（隐藏敏感信息）"""
        safe_config = json.loads(json.dumps(self.config))
        for pname, pconf in safe_config.get('providers', {}).items():
            if 'api_key' in pconf and pconf['api_key']:
                key = pconf['api_key']
                if len(key) > 8:
                    pconf['api_key'] = key[:4] + '****' + key[-4:]
                else:
                    pconf['api_key'] = '****'
        return safe_config

    def get_full_config(self):
        """获取完整配置（含密钥，仅内部使用）"""
        return self.config

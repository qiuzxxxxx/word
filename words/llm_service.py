"""统一 LLM 调用服务

提供集中化的 AI 模型调用入口，统一处理：
- 错误分类与重试
- 调用日志（AICallLog）
- 健康状态（AIModelHealth）
- 成本估算（tokens × 单价）
- 上下文预算管理

Usage:
    from words.llm_service import llm_service, LLMCallError

    try:
        result = llm_service.chat(
            action='review_coach',
            model_pk=1,
            prompt='Explain the word "apple"',
            max_tokens=2000,
        )
    except LLMCallError as e:
        # e.category: 'timeout' | 'network' | 'ratelimit' | 'server' | 'config' | 'invalid'
        # e.reason_code: 'ai_timeout' | 'ai_network' | ...
        handle_error(e)
"""

import json
import logging
import socket
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── 错误分类 ─────────────────────────────────────────────

class LLMCallError(Exception):
    """LLM 调用失败的基类"""
    def __init__(self, category: str, message: str, status_code: int = None):
        self.category = category          # timeout / network / ratelimit / server / config / invalid
        self.message = message
        self.status_code = status_code
        self.reason_code = self._to_reason_code(category)
        super().__init__(message)

    @staticmethod
    def _to_reason_code(category: str) -> str:
        mapping = {
            'timeout': 'ai_timeout',
            'network': 'ai_network',
            'ratelimit': 'ai_ratelimit',
            'server': 'ai_server_error',
            'config': 'ai_config_error',
            'invalid': 'ai_invalid_response',
        }
        return mapping.get(category, 'ai_unknown_error')


class LLMCircuitOpenError(LLMCallError):
    """熔断器开启，拒绝请求"""
    def __init__(self):
        super().__init__('circuit_open', 'AI 服务熔断中，请稍后再试')
        self.reason_code = 'ai_circuit_open'


# ─── 成本估算 ─────────────────────────────────────────────

# 默认价格（美元 / 1K tokens）—— 可在 settings.LLM_PRICES 中覆盖
DEFAULT_PRICES = {
    'gpt-4': {'input': 0.03, 'output': 0.06},
    'gpt-4-turbo': {'input': 0.01, 'output': 0.03},
    'gpt-4o': {'input': 0.005, 'output': 0.015},
    'gpt-4o-mini': {'input': 0.00015, 'output': 0.0006},
    'gpt-3.5-turbo': {'input': 0.0005, 'output': 0.0015},
    'deepseek-chat': {'input': 0.00014, 'output': 0.00028},
    'deepseek-coder': {'input': 0.00014, 'output': 0.00028},
    'qwen-turbo': {'input': 0.0008, 'output': 0.002},
    'qwen-plus': {'input': 0.004, 'output': 0.012},
}


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> dict:
    """估算调用成本（美元）"""
    prices = getattr(settings, 'LLM_PRICES', DEFAULT_PRICES)
    price = prices.get(model_id) or prices.get(model_id.split('-')[0])
    if not price:
        return {'input_cost': None, 'output_cost': None, 'total_cost': None,
                'input_tokens': input_tokens, 'output_tokens': output_tokens}
    input_cost = input_tokens / 1000 * price['input']
    output_cost = output_tokens / 1000 * price['output']
    return {
        'input_cost': round(input_cost, 6),
        'output_cost': round(output_cost, 6),
        'total_cost': round(input_cost + output_cost, 6),
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
    }


# ─── 上下文预算管理 ─────────────────────────────────────────

DEFAULT_CONTEXT_BUDGET = 4000   # 默认上下文预算（tokens）
MAX_OUTPUT_TOKENS = 4000        # 最大输出 tokens


def truncate_prompt(prompt: str, budget: int = DEFAULT_CONTEXT_BUDGET) -> str:
    """按 token 预算截断提示词（粗略估算：1 token ≈ 4 字符）"""
    max_chars = budget * 4
    if len(prompt) <= max_chars:
        return prompt
    return prompt[:max_chars] + '\n\n[...上下文已截断...]'


# ─── 核心服务类 ────────────────────────────────────────────

class LLMService:
    """统一 LLM 调用服务

    所有 AI 调用应通过此服务进行，以便：
    1. 统一记录调用日志（AICallLog）
    2. 统一处理错误与健康状态（AIModelHealth）
    3. 统一成本估算
    4. 统一上下文预算管理
    """

    # 调用这些内部函数（从 views 模块导入会导致循环依赖，故延迟导入）
    _post_json = None
    _resolve_model = None
    _health_record_success = None
    _health_record_failure = None
    _acquire_slot = None
    _reason_code = None

    def __init__(self):
        self._initialized = False

    def _ensure_initialized(self):
        if self._initialized:
            return
        # 延迟导入，避免循环依赖
        from words.views import (
            _ai_post_json, resolve_ai_model,
            _ai_health_record_success, _ai_health_record_failure,
            _acquire_ai_slot, _ai_reason_code,
        )
        self._post_json = _ai_post_json
        self._resolve_model = resolve_ai_model
        self._health_record_success = _ai_health_record_success
        self._health_record_failure = _ai_health_record_failure
        self._acquire_slot = _acquire_ai_slot
        self._reason_code = _ai_reason_code
        self._initialized = True

    def chat(
        self,
        action: str,
        model_pk: int,
        prompt: str,
        max_tokens: int = 2000,
        temperature: float = 0.5,
        context: dict = None,
        context_budget: int = DEFAULT_CONTEXT_BUDGET,
        timeout: int = 30,
        log_preview: bool = True,
    ) -> dict:
        """统一 AI 对话调用

        Args:
            action: 调用功能标识（对应 AICallLog.action）
            model_pk: AIModel 主键
            prompt: 提示词内容
            max_tokens: 最大输出 tokens
            temperature: 温度参数
            context: 额外上下文信息（会附加到日志）
            context_budget: 上下文 token 预算
            timeout: 请求超时秒数
            log_preview: 是否记录提示词/响应预览到日志

        Returns:
            {
                'content': str,           # AI 响应文本
                'model_id': str,          # 使用的模型 ID
                'model_pk': int,          # 使用的模型主键
                'duration_ms': int,       # 调用耗时（毫秒）
                'cost': dict | None,      # 成本估算（如服务商返回 token 数）
                'input_tokens': int,      # 输入 tokens（估算或实际）
                'output_tokens': int,     # 输出 tokens（估算或实际）
            }

        Raises:
            LLMCallError: 调用失败（含分类信息）
            LLMCircuitOpenError: 熔断器开启
        """
        self._ensure_initialized()
        start_time = time.time()

        # 解析模型配置
        cfg = self._resolve_model({'model': model_pk})
        if not cfg:
            raise LLMCallError('config', 'AI 模型配置无效或不存在')

        model_id = cfg.get('model_id', '')
        now = timezone.now()

        # 熔断检查
        from words.models import AIModelHealth
        try:
            health = AIModelHealth.objects.filter(model_id=model_pk).first()
        except Exception:
            health = None
        allowed, reason = self._acquire_slot(health, now)
        if not allowed:
            raise LLMCircuitOpenError()

        # 上下文预算截断
        truncated_prompt = truncate_prompt(prompt, context_budget)

        # 构造请求 payload
        payload = {
            'model': model_id,
            'messages': [{'role': 'user', 'content': truncated_prompt}],
            'max_tokens': min(max_tokens, MAX_OUTPUT_TOKENS),
            'temperature': temperature,
        }

        # 执行调用
        try:
            content = self._post_json(cfg, payload, timeout)
            duration_ms = int((time.time() - start_time) * 1000)

            # 记录成功
            self._health_record_success(model_pk)

            # 估算 token 数（粗略：1 token ≈ 4 字符）
            input_tokens = len(prompt) // 4
            output_tokens = len(content) // 4 if content else 0
            cost = estimate_cost(model_id, input_tokens, output_tokens)

            # 写调用日志
            self._write_log(
                action=action,
                model_pk=model_pk,
                model_id=model_id,
                success=True,
                duration_ms=duration_ms,
                prompt_preview=prompt if log_preview else '',
                response_preview=content if log_preview else '',
                error='',
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
            )

            return {
                'content': content,
                'model_id': model_id,
                'model_pk': model_pk,
                'duration_ms': duration_ms,
                'cost': cost,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
            }

        except urllib.error.HTTPError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            body = e.read(4096).decode('utf-8', 'ignore')
            code = e.code
            if code in (401, 403):
                err = LLMCallError('config', f'密钥或权限无效 (HTTP {code})', code)
            elif code == 404:
                err = LLMCallError('config', '模型或接口地址不存在 (HTTP 404)', code)
            elif code == 429:
                err = LLMCallError('ratelimit', '请求过于频繁 (HTTP 429)', code)
            elif code >= 500:
                err = LLMCallError('server', f'服务端错误 (HTTP {code})', code)
            else:
                err = LLMCallError('config', f'接口错误 (HTTP {code}): {body[:200]}', code)
            self._health_record_failure(model_pk, err.category, err.message)
            self._write_log(action, model_pk, model_id, False, duration_ms,
                           prompt if log_preview else '', '', err.message,
                           input_tokens=len(prompt)//4, output_tokens=0, cost=None)
            raise err

        except (socket.timeout, TimeoutError) as e:
            duration_ms = int((time.time() - start_time) * 1000)
            err = LLMCallError('timeout', 'AI 请求超时')
            self._health_record_failure(model_pk, err.category, err.message)
            self._write_log(action, model_pk, model_id, False, duration_ms,
                           prompt if log_preview else '', '', err.message,
                           input_tokens=len(prompt)//4, output_tokens=0, cost=None)
            raise err

        except urllib.error.URLError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            reason = getattr(e, 'reason', e)
            err = LLMCallError('network', f'无法连接 AI 服务: {reason}')
            self._health_record_failure(model_pk, err.category, err.message)
            self._write_log(action, model_pk, model_id, False, duration_ms,
                           prompt if log_preview else '', '', err.message,
                           input_tokens=len(prompt)//4, output_tokens=0, cost=None)
            raise err

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            duration_ms = int((time.time() - start_time) * 1000)
            err = LLMCallError('invalid', f'AI 响应解析失败: {str(e)[:100]}')
            self._health_record_failure(model_pk, err.category, err.message)
            self._write_log(action, model_pk, model_id, False, duration_ms,
                           prompt if log_preview else '', '', err.message,
                           input_tokens=len(prompt)//4, output_tokens=0, cost=None)
            raise err

    def _write_log(
        self, action, model_pk, model_id, success, duration_ms,
        prompt_preview, response_preview, error,
        input_tokens=0, output_tokens=0, cost=None,
    ):
        """写 AICallLog 记录"""
        try:
            from words.models import AICallLog
            from decimal import Decimal
            AICallLog.objects.create(
                action=action[:40],
                model_id=f'{model_id} (pk={model_pk})'[:120],
                success=success,
                error=error[:500] if error else '',
                duration_ms=duration_ms,
                prompt_preview=prompt_preview[:500] if prompt_preview else '',
                response_preview=response_preview[:500] if response_preview else '',
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=Decimal(str(cost.get('total_cost', 0) or 0)) if cost else Decimal('0'),
            )
        except Exception as e:
            logger.warning('Failed to write AICallLog: %s', e)


# ─── 全局单例 ─────────────────────────────────────────────

llm_service = LLMService()


# ─── 便捷函数 ─────────────────────────────────────────────

def chat(action: str, model_pk: int, prompt: str, **kwargs) -> dict:
    """便捷函数：调用 llm_service.chat()"""
    return llm_service.chat(action, model_pk, prompt, **kwargs)


def get_model_choices() -> list:
    """获取可用 AI 模型列表（用于下拉选择）"""
    from words.models import AIModel
    return list(
        AIModel.objects.filter(enabled=True)
        .values('id', 'model_id', 'display_name', 'provider')
        .order_by('display_name')
    )


def get_default_model_pk() -> int | None:
    """获取默认模型 PK（第一个启用的模型）"""
    from words.models import AIModel
    m = AIModel.objects.filter(enabled=True).first()
    return m.id if m else None

import traceback
import os
from module.logger import logger

import hashlib

_analyzed_errors_cache = {}
LLM_CONFIG_WARNING = 'LLM 错误分析不可用，请检查 LLM 配置、API Key、API Base、模型名称以及账户余额。'
LLM_EMPTY_RESULT_WARNING = 'LLM API 返回了空结果，请检查模型服务配置、模型名称或账户余额。'


def _get_analysis_from_response(response):
    """从 OpenAI 兼容响应中提取分析文本。"""
    choices = getattr(response, 'choices', None)
    if not choices:
        return ''
    message = getattr(choices[0], 'message', None)
    if message is None:
        return ''
    content = getattr(message, 'content', None)
    if content is None:
        return ''
    return content.strip()


def analyze_exception(config, e):
    """
    使用 LLM 分析异常原因。

    Args:
        config (AzurLaneConfig): 配置对象。
        e (Exception): 异常对象。
    """
    if not hasattr(config, 'Error_LlmAnalysis') or not config.Error_LlmAnalysis:
        return
        
    tb = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
    
    error_hash = hashlib.md5(tb.encode('utf-8')).hexdigest()
    if error_hash in _analyzed_errors_cache:
        cached_result = _analyzed_errors_cache[error_hash]
        model = getattr(config, 'Error_LlmModel', 'gpt-4o-mini')
        logger.hr('LLM 错误分析（严禁提交此模块的相关日志 群内机器人检测到将自动移出群聊）', level=1)
        logger.info('该错误已被 LLM 分析过，直接复用上次的分析结果以节省 API （严禁提交此模块的相关日志 群内机器人检测到将自动移出群聊） ...')
        logger.info(f"\n[LLM 分析报告 (由 {model} 提供, 复用缓存)（严禁提交此模块的相关日志 群内机器人检测到将自动移出群聊）]\n{cached_result}\n")
        logger.hr('LLM 分析结束', level=1)
        return
        
    api_key = getattr(config, 'Error_LlmApiKey', '')
    api_base = getattr(config, 'Error_LlmApiBase', 'https://api.openai.com/v1')
    model = getattr(config, 'Error_LlmModel', 'gpt-4o-mini')
    
    if not api_key:
        logger.warning('LLM 错误分析已启用，但 API Key 未配置。')
        logger.warning(LLM_CONFIG_WARNING)
        return

    _analyzed_errors_cache[error_hash] = "该错误正在被 LLM 分析中，暂无结果。（严禁提交此模块的相关日志 群内机器人检测到将自动移出群聊）"
    if len(_analyzed_errors_cache) > 50:
        _analyzed_errors_cache.clear()
        _analyzed_errors_cache[error_hash] = "该错误正在被 LLM 分析中，暂无结果。（严禁提交此模块的相关日志 群内机器人检测到将自动移出群聊）"

    logger.hr('LLM 错误分析（严禁提交此模块的相关日志 群内机器人检测到将自动移出群聊）', level=1)
    logger.info('正在调用 LLM 分析异常原因...')
    
    try:
        from openai import OpenAI
        
        log_context = ""
        try:
            if hasattr(logger, 'log_file') and logger.log_file and os.path.exists(logger.log_file):
                with open(logger.log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    log_context = "".join(lines[-500:])  # 取最后 500 行日志作为上下文
        except Exception:
            pass

        tb = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
        
        # 将总上下文限制在 64K 字符以内
        def truncate(text, limit):
            if len(text) > limit:
                return f"... [已截断] ...\n{text[-limit:]}"
            return text

        # 日志上下文优先级更高，但两者都需控制在限制范围内
        # 假设系统提示词 + 元数据约占 1K，剩余 63K 分配给日志和堆栈追踪
        tb = truncate(tb, 20000)
        log_context = truncate(log_context, 40000)

        prompt = f"""
你是一名碧蓝航线辅助脚本 AzurPilot 的专家开发者。
脚本运行中发生了异常。请分析以下堆栈追踪以及最近的日志，并提供简洁的原因解释和改进建议。

异常信息: {type(e).__name__}: {str(e)}

堆栈追踪:
{tb}

最近日志上下文:
{log_context}

请直接提供建议（中文）。
"""
        client = OpenAI(api_key=api_key, base_url=api_base)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个专门分析 Alas 错误的助手。"},
                {"role": "user", "content": prompt}
            ],
            timeout=60
        )
        
        analysis = _get_analysis_from_response(response)
        if not analysis:
            _analyzed_errors_cache.pop(error_hash, None)
            logger.warning(LLM_EMPTY_RESULT_WARNING)
            logger.warning(LLM_CONFIG_WARNING)
            logger.hr('LLM 分析结束', level=1)
            return

        # 覆写真正的成果进字典
        _analyzed_errors_cache[error_hash] = analysis
        logger.info(f"\n[LLM 分析报告 (由 {model} 提供)]\n{analysis}\n")
        logger.hr('LLM 分析结束', level=1)
        
    except ImportError:
        _analyzed_errors_cache.pop(error_hash, None)
        logger.error('未安装 openai 库，无法进行 LLM 错误分析。')
    except Exception as ex:
        _analyzed_errors_cache.pop(error_hash, None)
        # 避免循环日志问题，LLM 本身失败时使用简化的错误日志
        logger.error(f'LLM 分析调用失败（严禁提交此模块的相关日志 群内机器人检测到将自动移出群聊）: {ex}')
        logger.warning(LLM_CONFIG_WARNING)

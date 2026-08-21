"""LLM 每日总结的事实聚合与异步推送。"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from typing import Any

import yaml

import module.config.server as server_config
from module.config.utils import SERVER_TO_TIMEZONE, server_time_offset
from module.logger import logger
from module.statistics.daily_summary_store import (
    DailySummaryStore,
    get_daily_summary_store,
)
from module.statistics.daily_summary_text import DAILY_SUMMARY_SYSTEM_PROMPT


DAILY_SUMMARY_TITLE = 'AzurPilot <{config_name}> 每日总结'
DAILY_SUMMARY_KEEP_DAYS = 35
DAILY_SUMMARY_LLM_ATTEMPTS = 3
DAILY_SUMMARY_NOTIFY_ATTEMPTS = 3
DAILY_SUMMARY_TRIGGER_GRACE = timedelta(minutes=5)

SERVER_TIMEZONE_LABELS = {
    'cn': 'UTC+08:00',
    'en': 'UTC-07:00',
    'jp': 'UTC+09:00',
    'tw': 'UTC+08:00',
}
RESOURCE_LABELS = {
    'Oil': '石油',
    'Coin': '金币',
    'Gem': '钻石',
    'Pt': '活动点数',
    'Cube': '魔方',
    'Core': '核心数据',
    'Medal': '勋章',
    'Merit': '功勋',
    'GuildCoin': '大舰队币',
    'ActionPoint': '行动力',
    'YellowCoin': '作战补给凭证',
    'PurpleCoin': '特别兑换凭证',
}


def parse_daily_summary_trigger(value: Any) -> tuple[int, int] | None:
    """校验日报触发时间，格式固定为 24 小时制 ``HH:MM``。"""
    match = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d)', str(value or '').strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def resolve_daily_summary_server(
    config: Any, current_server: str | None = None
) -> str | None:
    """在不初始化设备的前提下解析实例所使用的游戏服务器。

    显式包名或游戏内服务器优先；只有设备已确认运行时服务器时，才使用
    ``current_server`` 作为自动包名的回退。
    """
    package = getattr(config, 'Emulator_PackageName', 'auto')
    if package in server_config.VALID_PACKAGE or package in server_config.VALID_CHANNEL_PACKAGE:
        return server_config.to_server(package)
    server_name = getattr(config, 'Emulator_ServerName', '')
    if isinstance(server_name, str) and server_name != 'disabled':
        configured_group = server_name.rpartition('-')[0]
        if configured_group in server_config.SERVER_CHECKER_SERVER_LIST:
            return server_config.to_server(configured_group.split('_')[0])
    if current_server in server_config.VALID_SERVER:
        return current_server
    return None


def get_daily_summary_window(
    now: datetime,
    server: str,
    trigger: tuple[int, int],
) -> tuple[datetime, datetime, str, bool]:
    """返回最近一个服务器日触发周期及当前是否处于允许发送窗口。"""
    server = server if server in SERVER_TO_TIMEZONE else 'cn'
    offset = server_time_offset_for(server, now)
    server_now = now - offset
    scheduled_server = server_now.replace(
        hour=trigger[0], minute=trigger[1], second=0, microsecond=0
    )
    if server_now < scheduled_server:
        scheduled_server -= timedelta(days=1)
    window_end = scheduled_server + offset
    window_start = window_end - timedelta(days=1)
    period_key = f'{server}:{scheduled_server.date().isoformat()}:{trigger[0]:02d}{trigger[1]:02d}'
    due = window_end <= now <= window_end + DAILY_SUMMARY_TRIGGER_GRACE
    return window_start, window_end, period_key, due


def server_time_offset_for(server: str, now: datetime | None = None) -> timedelta:
    """将服务器时间转换为本地 naive 时间所需的偏移量。"""
    current = server_config.server
    current_server_offset = SERVER_TO_TIMEZONE.get(
        current, SERVER_TO_TIMEZONE['cn']
    )
    local_utc_offset = server_time_offset() + current_server_offset
    return local_utc_offset - SERVER_TO_TIMEZONE.get(
        server, SERVER_TO_TIMEZONE['cn']
    )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=' ')
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def _has_onepush_provider(config_text: Any) -> bool:
    """仅检查是否已配置提供者，避免缺配置时调用模型。"""
    if not isinstance(config_text, str) or not config_text.strip():
        return False
    try:
        merged: dict[str, Any] = {}
        for item in yaml.safe_load_all(config_text):
            if isinstance(item, dict):
                merged.update(item)
        return bool(merged.get('provider'))
    except Exception:
        return False


class DailySummaryService:
    """协调日报触发、异步生成和 OnePush 推送。"""

    def __init__(self, instance: str, store: DailySummaryStore | None = None) -> None:
        self.instance = instance
        self.store = store or get_daily_summary_store()
        self._lock = threading.Lock()
        self._active_periods: set[str] = set()
        self._processed_periods: set[str] = set()
        self._invalid_trigger_value: str | None = None
        self._unresolved_server_value: str | None = None

    def check_due(
        self,
        config: Any,
        *,
        current_server: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """如到达日报窗口则启动后台生成；返回是否已提交处理。"""
        if not bool(getattr(config, 'DailySummary_Enable', False)):
            return False
        trigger = parse_daily_summary_trigger(
            getattr(config, 'DailySummary_TriggerTime', '20:00')
        )
        if trigger is None:
            trigger_value = str(getattr(config, 'DailySummary_TriggerTime', ''))
            if trigger_value != self._invalid_trigger_value:
                logger.warning('[日报] 触发时间格式无效，日报已跳过')
                self._invalid_trigger_value = trigger_value
            return False
        self._invalid_trigger_value = None

        now = now or datetime.now()
        server = resolve_daily_summary_server(config, current_server)
        if server is None:
            unresolved_value = '|'.join(
                str(getattr(config, key, ''))
                for key in ('Emulator_PackageName', 'Emulator_ServerName')
            )
            if unresolved_value != self._unresolved_server_value:
                logger.warning('[日报] 尚未确认实例服务器，暂不创建日报周期')
                self._unresolved_server_value = unresolved_value
            return False
        self._unresolved_server_value = None
        window_start, window_end, period_key, due = get_daily_summary_window(
            now, server, trigger
        )
        with self._lock:
            if period_key in self._processed_periods:
                return False
        try:
            if not due:
                logger.warning(
                    f'[日报] 已错过 {period_key} 的触发窗口，本期不补发'
                )
                self.store.mark_period_skipped(
                    self.instance, period_key, server, window_start, window_end
                )
                self.store.cleanup(
                    now=now, keep_days=DAILY_SUMMARY_KEEP_DAYS
                )
                with self._lock:
                    self._processed_periods.add(period_key)
                return False
            if not self.store.claim_period(
                self.instance, period_key, server, window_start, window_end
            ):
                period = self.store.get_period(self.instance, period_key)
                status = period.get('status') if period is not None else 'unknown'
                logger.info(f'[日报] {period_key} 已处理，当前状态: {status}')
                with self._lock:
                    self._processed_periods.add(period_key)
                return False
        except Exception as error:
            logger.warning(
                f'[日报] 读取周期状态失败，已跳过本次检查: {type(error).__name__}'
            )
            return False

        request = {
            'period_key': period_key,
            'server': server,
            'window_start': window_start,
            'window_end': window_end,
            'llm_api_key': getattr(config, 'Error_LlmApiKey', ''),
            'llm_api_base': getattr(config, 'Error_LlmApiBase', ''),
            'llm_model': getattr(config, 'Error_LlmModel', ''),
            'onepush_config': getattr(config, 'Error_OnePushConfig', ''),
        }
        with self._lock:
            self._active_periods.add(period_key)
            self._processed_periods.add(period_key)
        logger.info(f'[日报] 已到达 {period_key} 触发时间，开始生成每日总结')
        thread = threading.Thread(
            target=self._generate_and_send,
            args=(request,),
            daemon=True,
            name=f'daily-summary-{self.instance}',
        )
        thread.start()
        return True

    def _generate_and_send(self, request: dict[str, Any]) -> None:
        period_key = request['period_key']
        try:
            logger.info(f'[日报] 开始处理 {period_key}')
            if not request['llm_api_key'] or not request['llm_api_base'] or not request['llm_model']:
                self.store.update_period(
                    self.instance, period_key, 'failed', error_kind='configuration'
                )
                logger.warning('[日报] LLM 配置不完整，本期日报未发送')
                return
            if not _has_onepush_provider(request['onepush_config']):
                self.store.update_period(
                    self.instance, period_key, 'failed', error_kind='configuration'
                )
                logger.warning('[日报] OnePush 配置不可用，本期日报未发送')
                return

            from module.base.async_executor import async_executor

            async_executor.flush(timeout=5)
            facts = self.build_facts(
                server=request['server'],
                window_start=request['window_start'],
                window_end=request['window_end'],
            )
            logger.info(f'[日报] {period_key} 统计数据已聚合，开始生成文案')
            report_text, llm_attempts = self._generate_report(request, facts)
            if report_text is None:
                self.store.update_period(
                    self.instance,
                    period_key,
                    'failed',
                    llm_attempts=llm_attempts,
                    error_kind='llm',
                )
                logger.warning('[日报] LLM 未能生成正文，本期不发送')
                return

            self.store.update_period(
                self.instance,
                period_key,
                'sending',
                report_text=report_text,
                llm_attempts=llm_attempts,
            )
            logger.info(f'[日报] {period_key} 文案已生成，开始发送推送')
            sent, send_attempts = self._send_report(
                request['onepush_config'], report_text
            )
            if sent:
                self.store.update_period(
                    self.instance,
                    period_key,
                    'sent',
                    report_text=report_text,
                    llm_attempts=llm_attempts,
                    send_attempts=send_attempts,
                )
                logger.info('[日报] 每日总结推送成功')
            else:
                self.store.update_period(
                    self.instance,
                    period_key,
                    'failed',
                    report_text=report_text,
                    llm_attempts=llm_attempts,
                    send_attempts=send_attempts,
                    error_kind='notify',
                )
                logger.warning('[日报] 每日总结推送失败，本期不再重试')
        except Exception as error:
            logger.warning(f'[日报] 后台处理失败，已隔离: {type(error).__name__}')
            try:
                self.store.update_period(
                    self.instance, period_key, 'failed', error_kind='internal'
                )
            except Exception:
                pass
        finally:
            try:
                self.store.cleanup(keep_days=DAILY_SUMMARY_KEEP_DAYS)
            except Exception as error:
                logger.warning(f'[日报] 清理过期记录失败，已忽略: {type(error).__name__}')
            with self._lock:
                self._active_periods.discard(period_key)

    def build_facts(
        self,
        *,
        server: str,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[str, Any]:
        """聚合窗口内的可信事实，所有读取失败都降为明确的缺失信息。

        ``window_start`` 和 ``window_end`` 是本机时间，用于本地数据库查询；
        发给模型的时间则转换回游戏服务器时区，避免正文误报跨时区的本机边界。
        """
        quality_unavailable: list[str] = []
        quality_warnings = ['仅统计可精确归属到本周期的带时间戳数据。']
        server_offset = server_time_offset_for(server, window_end)
        report_start = window_start - server_offset
        report_end = window_end - server_offset
        try:
            automation = self.store.get_task_summary(
                self.instance, window_start, window_end, limit=15
            )
        except Exception as error:
            logger.warning(f'[日报] 读取任务摘要失败: {type(error).__name__}')
            automation = {
                'available': False,
                'collection_started_at': None,
                'run_count': None,
                'success_count': None,
                'recoverable_count': None,
                'failed_count': None,
                'duration_seconds': None,
                'task_breakdown': [],
            }
            quality_unavailable.append('任务运行记录未采集')
        if not automation.get('available'):
            quality_unavailable.append('任务运行记录未完整覆盖本周期')

        try:
            from module.statistics.resource_stats import get_resource_interval_summary

            resource_result = get_resource_interval_summary(
                self.instance, window_start, window_end
            )
            resources = []
            for key, item in resource_result.get('resources', {}).items():
                resources.append({
                    'key': key,
                    'label': RESOURCE_LABELS.get(key, key),
                    **item,
                })
            if not any(
                item.get('baseline_known') or item.get('end_known')
                for item in resources
            ):
                quality_unavailable.append('资源快照未采集')
            elif any(item.get('delta') is None for item in resources):
                quality_warnings.append('部分资源缺少窗口首末快照，未计算变化。')
        except Exception as error:
            logger.warning(f'[日报] 读取资源摘要失败: {type(error).__name__}')
            resources = []
            quality_unavailable.append('资源变化未采集')

        try:
            from module.statistics.commission_income_stats import (
                get_commission_income_interval_summary,
            )

            commission = get_commission_income_interval_summary(
                self.instance, window_start, window_end
            )
            if not commission.get('available'):
                quality_unavailable.append('委托收益未完整采集')
        except Exception as error:
            logger.warning(f'[日报] 读取委托收益失败: {type(error).__name__}')
            commission = {'available': False, 'settled_count': None, 'items': {}}
            quality_unavailable.append('委托收益未采集')

        try:
            from module.statistics.ship_exp_stats import get_cl1_interval_summary

            cl1 = get_cl1_interval_summary(self.instance, window_start, window_end)
            if not cl1.get('available'):
                quality_unavailable.append('侵蚀1专项未完整采集')
        except Exception as error:
            logger.warning(f'[日报] 读取侵蚀1统计失败: {type(error).__name__}')
            cl1 = {
                'available': False,
                'battles': None,
                'estimated_exp': None,
                'duration_seconds': None,
            }
            quality_unavailable.append('侵蚀1专项未采集')

        facts = {
            'schema_version': 1,
            'window': {
                # 实例名可能由用户自定义；模型仅需知道这是当前实例，无需接收原文。
                'instance': '当前实例',
                'server': server,
                'server_timezone': SERVER_TIMEZONE_LABELS.get(server, 'UTC+08:00'),
                'start': report_start,
                'end': report_end,
                'duration_hours': 24,
            },
            'automation': automation,
            'resources': resources,
            'commission': commission,
            'cl1': cl1,
            'data_quality': {
                'unavailable': quality_unavailable,
                'warnings': quality_warnings,
            },
        }
        return _to_jsonable(facts)

    def _generate_report(
        self, request: dict[str, Any], facts: dict[str, Any]
    ) -> tuple[str | None, int]:
        """调用 OpenAI 兼容接口，空响应或调用失败时最多重试三次。"""
        user_content = '<facts>\n' + json.dumps(
            facts, ensure_ascii=False, separators=(',', ':')
        ) + '\n</facts>'
        for attempt in range(1, DAILY_SUMMARY_LLM_ATTEMPTS + 1):
            try:
                logger.info(f'[日报] 调用 LLM 生成文案（第 {attempt} 次）')
                from openai import OpenAI

                client = OpenAI(
                    api_key=request['llm_api_key'],
                    base_url=request['llm_api_base'],
                )
                response = client.chat.completions.create(
                    model=request['llm_model'],
                    messages=[
                        {'role': 'system', 'content': DAILY_SUMMARY_SYSTEM_PROMPT},
                        {'role': 'user', 'content': user_content},
                    ],
                    timeout=60,
                )
                text = self._get_response_text(response)
                if text:
                    return text, attempt
                logger.warning(f'[日报] 第 {attempt} 次 LLM 返回空正文')
            except Exception as error:
                logger.warning(f'[日报] 第 {attempt} 次生成失败: {type(error).__name__}')
        return None, DAILY_SUMMARY_LLM_ATTEMPTS

    @staticmethod
    def _get_response_text(response: Any) -> str:
        choices = getattr(response, 'choices', None)
        if not choices:
            return ''
        message = getattr(choices[0], 'message', None)
        content = getattr(message, 'content', None)
        return content.strip() if isinstance(content, str) else ''

    def _send_report(self, onepush_config: str, report_text: str) -> tuple[bool, int]:
        """复用同一份日报文本完成最多三次 OnePush 推送。"""
        from module.notify import handle_notify

        title = DAILY_SUMMARY_TITLE.format(config_name=self.instance)
        for attempt in range(1, DAILY_SUMMARY_NOTIFY_ATTEMPTS + 1):
            try:
                logger.info(f'[日报] 发送 OnePush（第 {attempt} 次）')
                if handle_notify(
                    onepush_config,
                    title=title,
                    content=report_text,
                ):
                    return True, attempt
            except Exception as error:
                logger.warning(f'[日报] 第 {attempt} 次推送异常: {type(error).__name__}')
        return False, DAILY_SUMMARY_NOTIFY_ATTEMPTS


__all__ = [
    'DAILY_SUMMARY_SYSTEM_PROMPT',
    'DailySummaryService',
    'get_daily_summary_window',
    'parse_daily_summary_trigger',
    'resolve_daily_summary_server',
]

from datetime import timedelta

from module.config.time_source import now as current_time
from module.equipment.assets import EQUIPMENT_OPEN
from module.exception import ScriptError
from module.logger import logger
from module.os.assets import FLEET_FLAGSHIP
from module.os.dock_mixin import DockMixin
from module.os.map import OSMap
from module.os.ship_exp import ship_info_get_level_exp
from module.os.ship_exp_data import LIST_SHIP_EXP
from module.os.tasks.scheduling import CoinTaskMixin
from module.os_handler.assets import (
    DEPART_CONFIRM_BUTTON,
    DEPART_CONFIRM_TEMPLATE,
    DEPART_IMMEDIATELY_BUTTON,
    FAVORITE_BUTTON,
    FAVORITE_TEMPLATE,
    FLEET_DEPLOY_BUTTON,
    FLEET_DEPLOYMENT,
    FLEET_SLOT_1_BUTTON,
    FLEET_SLOT_1_TEMPLATE,
    FLEET_SLOT_2_BUTTON,
    FLEET_SLOT_2_TEMPLATE,
    FLEET_SLOT_3_BUTTON,
    FLEET_SLOT_3_TEMPLATE,
    FLEET_SLOT_4_BUTTON,
    FLEET_SLOT_4_TEMPLATE,
    FLEET_SLOT_5_BUTTON,
    FLEET_SLOT_5_TEMPLATE,
    FLEET_SLOT_6_BUTTON,
    FLEET_SLOT_6_TEMPLATE,
    FLEET_SLOT_CONFIRM_BUTTON,
    FLEET_SLOT_CONFIRM_TEMPLATE,
    OS_FLEET_SLOT_NAV_1_BUTTON,
    OS_FLEET_SLOT_NAV_2_BUTTON,
    OS_FLEET_SLOT_NAV_3_BUTTON,
    OS_FLEET_SLOT_NAV_4_BUTTON,
    OS_FLEET_SLOT_NAV_5_BUTTON,
    OS_FLEET_SLOT_NAV_6_BUTTON,
    PORT_GOTO_SUPPLY,
)
from module.retire.assets import DOCK_EMPTY
from module.ui.assets import BACK_ARROW


class OpsiFleetAutoChange(CoinTaskMixin, DockMixin, OSMap):
    """
    侵蚀一舰队自动配队
    
    当经验检测发现指定舰位已满经验时，自动进入船坞选择替换舰船
    """
    
    def run(self):
        """
        主入口方法
        
        流程:
        1. 检查冷却时间
        2. 回到NY港区
        3. 获取自定义舰位配置
        4. 执行自动配队
        5. 设置冷却时间
        6. 运行经验检测
        7. 推送结果
        
        注意：此方法由经验检测触发，不需要重新收集舰船数据
        """
        if not self._check_cooldown():
            logger.info("[大世界-自动配队] 自动配队冷却中，跳过")
            return
        
        try:
            self._goto_azur_port()
            
            custom_positions = self._parse_custom_positions()
            
            logger.info(f"[大世界-自动配队] 开始执行自动配队，舰位: {custom_positions}")
            self._execute_fleet_auto_change(custom_positions)
            
            self._set_cooldown()
            logger.info("[大世界-自动配队] 自动配队完成")
            
            self._run_exp_check_after_auto_change(custom_positions)
            
            self._notify_auto_change_complete(custom_positions)
            
        except Exception as e:
            logger.error(f"[大世界-自动配队] 自动配队执行失败: {e}")
            self._handle_auto_change_error(str(e))
            raise
    
    def _run_exp_check_after_auto_change(self, custom_positions):
        """
        自动配队后运行经验检测
        
        Args:
            custom_positions: 自定义舰位列表
        """
        logger.info("[大世界-自动配队] 自动配队后运行经验检测")
        
        if not self._ensure_return_to_os_map():
            logger.warning("[大世界-自动配队] 无法返回大世界地图，尝试回到主界面")
            self._return_to_main_page()
        
        try:
            from module.os.tasks.hazard_leveling import OpsiHazard1Leveling
            
            leveling = OpsiHazard1Leveling(config=self.config, device=self.device)
            leveling.os_check_leveling()
            logger.info("[大世界-自动配队] 经验检测完成")
        except Exception as e:
            logger.warning(f"[大世界-自动配队] 经验检测失败: {e}")
    
    def _ensure_return_to_os_map(self):
        """
        确保返回大世界地图
        
        Returns:
            bool: 是否成功返回大世界地图
        """
        timeout = 10
        for _ in range(timeout * 2):
            self.device.screenshot()
            
            if self.appear(PORT_GOTO_SUPPLY, offset=(20, 20)):
                logger.info("[大世界-自动配队] 检测到仍在港口界面，退出港口")
                self.port_quit(skip_first_screenshot=True)
                self.wait_os_map_buttons()
                continue
            
            if self.is_in_map():
                if not self.appear(PORT_GOTO_SUPPLY, offset=(20, 20)):
                    logger.info("[大世界-自动配队] 已确认返回大世界地图")
                    return True
        
        logger.warning("[大世界-自动配队] 超时未能返回大世界地图")
        return False
    
    def _return_to_main_page(self):
        """回到主界面"""
        from module.ui.page import page_main
        logger.info("[大世界-自动配队] 尝试回到主界面")
        
        try:
            self.ui_goto(page_main)
            logger.info("[大世界-自动配队] 已回到主界面")
        except Exception as e:
            logger.warning(f"[大世界-自动配队] 回到主界面失败: {e}")
    
    def _notify_auto_change_complete(self, custom_positions):
        """
        推送自动配队完成通知
        
        Args:
            custom_positions: 自定义舰位列表
        """
        try:
            positions_str = ', '.join(map(str, custom_positions))
            self.notify_push(
                title="大世界自动配队完成",
                content=f"<{self.config.config_name}>\n\n已更换舰位: {positions_str}\n\n自动配队冷却时间: {self.config.OpsiFleetAutoChange_CooldownHours} 小时"
            )
        except Exception as e:
            logger.warning(f"[大世界-自动配队] 推送通知失败: {e}")
    
    def _goto_azur_port(self):
        """前往最近的碧蓝航线港口"""
        logger.info("[大世界-自动配队] 前往碧蓝航线港口")
        
        if not hasattr(self, 'zone') or self.zone is None:
            logger.info("[大世界-自动配队] 初始化当前区域信息")
            self.zone_init()
        
        if not self.zone.is_azur_port:
            self.globe_goto(self.zone_nearest_azur_port(self.zone))
        
        logger.info(f"[大世界-自动配队] 已到达港口: {self.zone}")
    
    def _handle_auto_change_error(self, error_msg):
        """
        处理自动配队错误
        
        Args:
            error_msg: 错误信息
        """
        logger.error(f"[大世界-自动配队] 自动配队发生错误: {error_msg}")
        
        self.config.OpsiFleetAutoChange_Enable = False
        logger.info("[大世界-自动配队] 已禁用大世界自动配队功能")
        
        try:
            self.notify_push(
                title="大世界自动配队错误",
                content=f"<{self.config.config_name}>\n\n自动配队执行失败: {error_msg}\n\n已禁用自动配队功能，请检查后手动启用。"
            )
        except Exception as e:
            logger.warning(f"[大世界-自动配队] 推送通知失败: {e}")
        
        logger.info("[大世界-自动配队] 尝试重启游戏以恢复状态")
        self.config.task_call('Restart')
    
    def _check_cooldown(self):
        """
        检查冷却时间
        
        Returns:
            bool: 是否可以运行
        """
        last_run = self.config.OpsiFleetAutoChange_LastRun
        if last_run is None:
            return True
        
        cooldown_hours = self.config.OpsiFleetAutoChange_CooldownHours
        next_run_time = last_run + timedelta(hours=cooldown_hours)
        
        return current_time() >= next_run_time
    
    def _parse_custom_positions(self):
        """
        解析自定义舰位配置
        
        Returns:
            list: 舰位列表，如 [1, 3, 5]
        """
        enable_custom_check = self.config.OpsiCheckLeveling_EnableCustomCheck
        if not enable_custom_check:
            return [1, 2, 3, 4, 5, 6]
        
        custom_str = self.config.OpsiCheckLeveling_CustomCheckPositions
        if not custom_str:
            return [1, 2, 3, 4, 5, 6]
        
        try:
            positions = [int(p.strip()) for p in str(custom_str).split(',')]
            return [p for p in positions if 1 <= p <= 6]
        except:
            logger.warning(f"[大世界-自动配队] 自定义舰位配置格式错误: {custom_str}")
            return [1, 2, 3, 4, 5, 6]
    
    def _check_trigger_condition(self, ship_data_list, target_level, custom_positions):
        """
        检查是否触发自动配队
        
        Args:
            ship_data_list: 舰船数据列表
            target_level: 目标等级
            custom_positions: 自定义舰位列表
            
        Returns:
            bool: 是否触发自动配队
        """
        target_exp = LIST_SHIP_EXP[target_level - 1]
        
        for ship in ship_data_list:
            position = ship['position']
            
            if position not in custom_positions:
                continue
            
            if ship['total_exp'] < target_exp:
                logger.info(f"[大世界-自动配队] 舰位 {position} 未满经验，不触发自动配队")
                return False
        
        logger.info(f"[大世界-自动配队] 所有指定舰位 {custom_positions} 已满经验，触发自动配队")
        return True
    
    def _execute_fleet_auto_change(self, positions):
        """
        执行自动配队
        
        Args:
            positions: 需要更换的舰位列表
        """
        self._cancel_favorite_for_positions(positions)
        self._enter_fleet_deploy()
        self._select_ships_at_positions(positions)
        self._confirm_departure()
    
    def _cancel_favorite_for_positions(self, positions):
        """
        取消指定舰位的常用标记
        
        Args:
            positions: 舰位列表，如 [1, 3, 5]
        """
        logger.info(f"[大世界-自动配队] 取消舰位 {positions} 的常用标记")
        
        slot_buttons = {
            1: OS_FLEET_SLOT_NAV_1_BUTTON,
            2: OS_FLEET_SLOT_NAV_2_BUTTON,
            3: OS_FLEET_SLOT_NAV_3_BUTTON,
            4: OS_FLEET_SLOT_NAV_4_BUTTON,
            5: OS_FLEET_SLOT_NAV_5_BUTTON,
            6: OS_FLEET_SLOT_NAV_6_BUTTON,
        }
        
        for position in positions:
            button = slot_buttons.get(position)
            if not button:
                logger.warning(f"[大世界-自动配队] 无效的舰位: {position}")
                continue
            
            logger.info(f"[大世界-自动配队] 长按舰位 {position} 进入详情界面")
            
            self.equip_enter(button, check_button=EQUIPMENT_OPEN, long_click=True)
            
            if self.appear(FAVORITE_TEMPLATE, offset=(20, 20)):
                self.device.click(FAVORITE_BUTTON)
                logger.info(f"[大世界-自动配队] 已取消舰位 {position} 的常用标记")
                self.device.sleep(0.5)
            else:
                logger.info(f"[大世界-自动配队] 舰位 {position} 未设置常用标记")
            
            self.ui_back(check_button=self.is_in_map)
            self.device.sleep(0.5)
    
    def _enter_fleet_deploy(self):
        """进入舰队部署界面
        
        Raises:
            ScriptError: 当无法进入舰队部署界面时抛出
        """
        logger.info("[大世界-自动配队] 进入舰队部署界面")
        
        self.order_enter()
        
        self.device.click(FLEET_DEPLOY_BUTTON)
        self.device.screenshot()
        
        timeout = 10
        enter_timeout = 0
        while not self.appear(FLEET_DEPLOYMENT, offset=(20, 20)):
            self.device.screenshot()
            enter_timeout += 1
            if enter_timeout > timeout * 2:
                logger.error("[大世界-自动配队] 无法进入舰队部署界面")
                raise ScriptError("无法进入舰队部署界面")
    
    def _select_ships_at_positions(self, positions):
        """
        在指定舰位选择舰船
        
        选择逻辑：
        1. 将舰位列表排序
        2. 第N个要更换的舰位（从0开始）→ 船坞第一排第(N+1)个位置
           - 第0个舰位 → grid_index=1 (1,0)
           - 第1个舰位 → grid_index=2 (2,0)
           - 第2个舰位 → grid_index=3 (3,0)
           - 以此类推...
        
        Args:
            positions: 舰位列表，如 [1, 4, 5, 6]
            
        Raises:
            ScriptError: 当船坞中没有可用舰船时抛出
        """
        sorted_positions = sorted(positions)
        logger.info(f"[大世界-自动配队] 在舰位 {sorted_positions} 选择舰船")
        
        slot_buttons = {
            1: FLEET_SLOT_1_BUTTON,
            2: FLEET_SLOT_2_BUTTON,
            3: FLEET_SLOT_3_BUTTON,
            4: FLEET_SLOT_4_BUTTON,
            5: FLEET_SLOT_5_BUTTON,
            6: FLEET_SLOT_6_BUTTON,
        }
        
        for index, position in enumerate(sorted_positions):
            button = slot_buttons.get(position)
            if button:
                logger.info(f"[大世界-自动配队] 点击舰位 {position}")
                self.device.click(button)
                self.device.screenshot()
                
                if self.appear(DOCK_EMPTY, offset=(20, 20)):
                    logger.error("[大世界-自动配队] 船坞中没有可用的常用舰船")
                    raise ScriptError("船坞中没有可用的常用舰船，无法完成自动配队")
                
                self.dock_favourite_set(enable=True, wait_loading=False)
                
                self.device.screenshot()
                if self.appear(DOCK_EMPTY, offset=(20, 20)):
                    logger.error("[大世界-自动配队] 船坞中没有可用的常用舰船")
                    raise ScriptError("船坞中没有可用的常用舰船，无法完成自动配队")
                
                grid_index = index + 1
                self.dock_select_ship_at_grid(grid_index)
                
                self._confirm_ship_selection()
    
    def _confirm_ship_selection(self):
        """确认舰船选择
        
        Raises:
            ScriptError: 当无法确认舰船选择时抛出
        """
        logger.info("[大世界-自动配队] 确认舰船选择")
        
        timeout = 10
        confirm_timeout = 0
        while not self.appear(FLEET_SLOT_CONFIRM_TEMPLATE, offset=(20, 20)):
            self.device.screenshot()
            confirm_timeout += 1
            if confirm_timeout > timeout * 2:
                logger.error("[大世界-自动配队] 无法找到确认按钮")
                raise ScriptError("无法找到确认按钮，舰船选择失败")
        
        self.device.click(FLEET_SLOT_CONFIRM_BUTTON)
        self.device.screenshot()
        
        return_timeout = 0
        while not self.appear(FLEET_DEPLOYMENT, offset=(20, 20)):
            self.device.screenshot()
            return_timeout += 1
            if return_timeout > timeout * 2:
                logger.error("[大世界-自动配队] 确认舰船选择后未返回舰队部署界面")
                raise ScriptError("确认舰船选择后未返回舰队部署界面")
    
    def _confirm_departure(self):
        """确认出发
        
        Raises:
            ScriptError: 当无法完成出发确认时抛出
        """
        logger.info("[大世界-自动配队] 确认出发")
        
        self.device.click(DEPART_IMMEDIATELY_BUTTON)
        
        confirm_timeout = 0
        confirm_max_timeout = 10
        while confirm_timeout < confirm_max_timeout * 2:
            self.device.screenshot()
            
            if self.appear(DEPART_CONFIRM_TEMPLATE, offset=(20, 20)):
                logger.info("[大世界-自动配队] 检测到出发确认弹窗，点击确认")
                self.device.click(DEPART_CONFIRM_BUTTON)
                break
            
            confirm_timeout += 1
        else:
            logger.info("[大世界-自动配队] 未检测到出发确认弹窗，继续执行")
        
        for _ in range(5):
            self.device.screenshot()
        
        timeout = 15
        for _ in range(timeout * 2):
            self.device.screenshot()
            
            if self.appear(PORT_GOTO_SUPPLY, offset=(20, 20)):
                logger.info("[大世界-自动配队] 检测到进入港口界面，退出港口")
                self.port_quit(skip_first_screenshot=True)
                self.wait_os_map_buttons()
                continue
            
            if self.is_in_map():
                if not self.appear(PORT_GOTO_SUPPLY, offset=(20, 20)):
                    logger.info("[大世界-自动配队] 已返回大世界地图")
                    return
        
        logger.error("[大世界-自动配队] 出发确认超时")
        raise ScriptError("出发确认超时，无法返回大世界地图")
    
    def _set_cooldown(self):
        """设置冷却时间"""
        self.config.OpsiFleetAutoChange_LastRun = current_time().replace(microsecond=0)
        logger.info(f"[大世界-自动配队] 已设置冷却时间，下次可运行时间: {self.config.OpsiFleetAutoChange_LastRun}")
    
    def _collect_ship_data_with_retry(self, target_level):
        """
        收集舰船数据，带重试机制
        
        Args:
            target_level: 目标等级
            
        Returns:
            dict: {'ships': list, 'error': str} 
                  ships为舰船数据列表，失败时为None
                  error为错误信息，成功时为None
        """
        max_retry = 3
        non_standard_retry_count = 0
        last_error = None
        
        for attempt in range(max_retry):
            logger.info(f"[大世界-自动配队] 开始收集舰船数据 (尝试 {attempt + 1}/{max_retry})")
            
            self.fleet_set(self.config.OpsiFleet_Fleet)
            self.equip_enter(FLEET_FLAGSHIP)
            
            ship_data_list = []
            position = 1
            
            while True:
                self.device.screenshot()
                level, exp = ship_info_get_level_exp(main=self)
                
                if level < 1 or level > len(LIST_SHIP_EXP):
                    logger.warning(f"[大世界-自动配队] 舰船等级识别异常: {level}")
                    ship_data_list.append({
                        "position": position,
                        "level": level,
                        "current_exp": exp,
                        "total_exp": 0,
                    })
                    if not self.equip_view_next():
                        break
                    position += 1
                    continue
                
                total_exp = LIST_SHIP_EXP[level - 1] + exp
                logger.info(
                    f"位置: {position}, 等级: {level}, 经验: {exp}, 总经验: {total_exp}, 目标经验: {LIST_SHIP_EXP[target_level - 1]}"
                )
                
                ship_data_list.append({
                    "position": position,
                    "level": level,
                    "current_exp": exp,
                    "total_exp": total_exp,
                })
                
                if not self.equip_view_next():
                    break
                position += 1
            
            self.ui_back(appear_button=EQUIPMENT_OPEN, check_button=self.is_in_map)
            
            validation_result = self._validate_ship_data(ship_data_list)
            if validation_result['valid']:
                if validation_result.get('need_retry', False):
                    current_ship_count = len(ship_data_list)
                    non_standard_retry_count += 1
                    
                    if non_standard_retry_count >= 3:
                        logger.info(f"[大世界-自动配队] 非标准舰船数量({current_ship_count}艘)已重试3次，使用当前检测结果")
                        return {'ships': ship_data_list, 'error': None}
                    
                    logger.warning(f"[大世界-自动配队] 舰船数量非标准({current_ship_count}艘)，重试确认 ({non_standard_retry_count}/3)")
                    if attempt < max_retry - 1:
                        logger.info("[大世界-自动配队] 等待后重试...")
                        self.device.click_record_clear()
                        self.interval_reset()
                    else:
                        logger.info(f"[大世界-自动配队] 已达到最大重试次数，使用当前检测结果({current_ship_count}艘)")
                        return {'ships': ship_data_list, 'error': None}
                else:
                    logger.info("[大世界-自动配队] 舰船数据验证通过")
                    return {'ships': ship_data_list, 'error': None}
            else:
                logger.warning(f"[大世界-自动配队] 舰船数据验证失败: {validation_result['reason']}")
                last_error = validation_result['reason']
                if attempt < max_retry - 1:
                    logger.info("[大世界-自动配队] 等待后重试...")
                    self.device.click_record_clear()
                    self.interval_reset()
                else:
                    logger.error("[大世界-自动配队] 已达到最大重试次数，舰船数据收集失败")
                    return {'ships': None, 'error': f"验证失败: {last_error}"}
        
        return {'ships': None, 'error': f"未知错误: {last_error}"}
    
    def _validate_ship_data(self, ship_data_list):
        """
        验证舰船数据有效性
        
        Args:
            ship_data_list: 舰船数据列表
            
        Returns:
            dict: {'valid': bool, 'reason': str}
        """
        if not ship_data_list:
            return {'valid': False, 'reason': '舰船数据为空'}
        
        ship_count = len(ship_data_list)
        if ship_count < 1 or ship_count > 6:
            return {
                'valid': False, 
                'reason': f'舰船数量异常: {ship_count}，应为1-6艘'
            }
        
        positions = [ship['position'] for ship in ship_data_list]
        if len(positions) != len(set(positions)):
            return {
                'valid': False, 
                'reason': f'存在重复的舰船位置: {positions}'
            }
        
        for ship in ship_data_list:
            if ship['level'] < 1 or ship['level'] > 125:
                return {
                    'valid': False, 
                    'reason': f"舰船等级异常: {ship['level']}"
                }
        
        if ship_count != 6:
            return {
                'valid': True, 
                'reason': f'舰船数量为{ship_count}，非标准6艘',
                'need_retry': True
            }
        
        return {'valid': True, 'reason': ''}

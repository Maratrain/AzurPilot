from module.campaign.gems_farming import GemsFarming
from module.logger import logger
from module.exception import CampaignEnd

class Ambush11(GemsFarming):
    
    def flagship_change_execute(self):
        """
        Overridden to populate 3 Main fleet slots instead of 1.
        """
        from module.equipment.assets import FLEET_ENTER_FLAGSHIP
        from module.base.button import Button
        
        # Coordinates for the 3 rear ships in Formation screen
        MAIN_1 = Button(area=(771, 80, 832, 106), color=(), button=(771, 80, 832, 106), name='FLEET_ENTER_MAIN_1')
        MAIN_3 = Button(area=(771, 320, 832, 346), color=(), button=(771, 320, 832, 346), name='FLEET_ENTER_MAIN_3')
        MAIN_2 = Button(area=(771, 200, 832, 226), color=(), button=(771, 200, 832, 226), name='FLEET_ENTER_MAIN_2')

        success = False
        # Main 2 is flagship and must be set first to avoid empty fleet errors
        for button in [MAIN_2]:
            if self.hard_mode:
                if not self.dock_enter(self.fleet_detail_enter_flagship):
                    continue
                self.ship_down_hard()
                
            if not self.dock_enter(button):
                continue
                
            ship = self.get_common_rarity_cv()
            if ship:
                self.flagship_change_with_emotion(ship)
                logger.info(f'Change flagship {button.name} success')
                success = True
            else:
                logger.info(f'Change flagship {button.name} failed, no CV in common rarity.')
                if self.config.SERVER in ['cn']:
                    max_level = 100
                else:
                    max_level = 70
                # Fallback logic
                ship = self.get_common_rarity_cv(lv=max_level, emotion=0)
                if ship and self.hard_mode:
                    self.flagship_change_with_emotion(ship)
                else:
                    if self.hard_mode:
                        from module.exception import RequestHumanTakeover
                        raise RequestHumanTakeover
                    self._dock_reset()
                    self.ui_back(check_button=self.page_fleet_check_button)

        return success

    def vanguard_change_execute(self):
        """
        Overridden to use correct vanguard click position for 1-1 Ambush.
        """
        from module.base.button import Button
        VANGUARD_1 = Button(area=(315, 256, 397, 331), color=(), button=(315, 256, 397, 331), name='FLEET_ENTER_VANGUARD_1')

        if self.hard_mode:
            if not self.dock_enter(self.fleet_detail_enter):
                return True
            self.ship_down_hard()
        if not self.dock_enter(VANGUARD_1):
            return True

        ship = self.get_common_rarity_dd()
        if ship:
            self.vanguard_change_with_emotion(ship)
            logger.info('Change vanguard ship success')
            return True
        else:
            logger.info('Change vanguard ship failed, no DD in common rarity.')
            ship = self.get_common_rarity_dd(emotion=0)
            if ship and self.hard_mode:
                self.vanguard_change_with_emotion(ship)
            else:
                if self.hard_mode:
                    from module.exception import RequestHumanTakeover
                    raise RequestHumanTakeover
                self._dock_reset()
                self.ui_back(check_button=self.page_fleet_check_button)
            return False

    def get_common_rarity_dd(self, emotion=16):
        """
        Ambush 1-1 specific DD finding logic.
        Ensures level limits are strictly followed and defaults to < 28 if not set.
        """
        # Strictly follow GUI settings
        min_level = self.config.GemsFarming_VanguardLevelMin
        max_level = self.config.GemsFarming_VanguardLevelMax
        
        # User explicitly requested 28 as default for 1-1
        # If it's still at absolute defaults (1, 125), we force it to 1-28
        if min_level <= 1 and max_level >= 125:
            logger.info('Vanguard level limit is at default (1-125), forcing to 1-28 for 1-1 Ambush')
            max_level = 28
            
        logger.info(f'Finding vanguard with level: {min_level} ~ {max_level}')
        
        # Implementation similar to GemsFarming but without the 100-level fallback
        from module.retire.scanner import ShipScanner
        
        rarity = 'common'
        extra = 'can_limit_break'
        if self.config.GemsFarming_CommonDD in ['any', 'custom']:
            faction = ['eagle', 'iron']
        elif self.config.GemsFarming_CommonDD == 'favourite':
            faction = 'all'
        elif self.config.GemsFarming_CommonDD == 'z20_or_z21':
            faction = 'iron'
        elif self.config.GemsFarming_CommonDD == 'DDG':
            faction = 'dragon'
            rarity = 'super_rare'
            extra = 'no_limit'
        elif self.config.GemsFarming_CommonDD in ['aulick_or_foote', 'cassin_or_downes']:
            faction = 'eagle'
        else:
            faction = ['eagle', 'iron']
            
        favourite = self.config.GemsFarming_CommonDD == 'favourite'
        self.dock_favourite_set(favourite, wait_loading=False)
        self.dock_sort_method_dsc_set(True, wait_loading=False)
        self.dock_filter_set(index='dd', rarity=rarity, faction=faction, extra=extra)

        emotion_lower_bound = 0 if emotion == 0 else self.emotion_lower_bound
        scanner = ShipScanner(level=(min_level, max_level), emotion=(emotion_lower_bound, 150),
                              fleet=[0, self.fleet_to_attack], status='free')
        scanner.disable('rarity')

        if self.config.GemsFarming_UseEmotionFirst:
            if self.config.GemsFarming_CommonDD == 'custom':
                filter_string = self.config.GemsFarming_CommonDDFilter
                common_ship = self.get_common_ship_filter(filter_string, ship_type='dd')
            elif self.config.GemsFarming_CommonDD == 'any':
                filter_string = self.config.COMMON_DD_FILTER
                common_ship = self.get_common_ship_filter(filter_string, ship_type='dd')
            elif self.config.GemsFarming_CommonDD == 'cassin_or_downes':
                common_ship = ['cassin', 'downes']
            elif self.config.GemsFarming_CommonDD == 'aulick_or_foote':
                common_ship = ['aulick', 'foote']
            elif self.config.GemsFarming_CommonDD == 'z20_or_z21':
                common_ship = ['z20', 'z21']
            else:
                common_ship = None

            if common_ship is not None:
                candidates = self.find_all_vanguard_candidates(scanner, common_ship)
                if candidates:
                    return candidates

                logger.info('No specific DD was found, try reversed order.')
                self.dock_sort_method_dsc_set(False)
                candidates = self.find_all_vanguard_candidates(scanner, common_ship)
                if not candidates and self.config.GemsFarming_CommonDD == 'custom':
                    return scanner.scan(self.device.image, output=False)
                return candidates
            else:
                candidates = scanner.scan(self.device.image, output=False)
                if candidates:
                    candidates.sort(key=lambda s: s.emotion, reverse=True)
                    return candidates

        if self.config.GemsFarming_CommonDD in ['any', 'favourite', 'z20_or_z21', 'DDG']:
            return scanner.scan(self.device.image)
        elif self.config.GemsFarming_CommonDD == 'custom':
            candidates = self.find_custom_candidates(scanner, ship_type='dd')
            return candidates if candidates else scanner.scan(self.device.image, output=False)
        else:
            candidates = self.find_candidates(self.get_templates(self.config.GemsFarming_CommonDD), scanner)
            if candidates:
                return candidates
            self.dock_sort_method_dsc_set(False)
            return self.find_candidates(self.get_templates(self.config.GemsFarming_CommonDD), scanner)
    def run(self, name='campaign_1_1_f', folder='campaign_main', mode='normal', total=0):
        """
        Specialized runner for 1-1 Ambush. 
        Forces auto-search and clear mode off, then uses GemsFarming's 
        ship switching logic before executing the map script.
        """
        logger.hr('Ambush 1-1 Runner', level=1)
        
        # Enforce manual play and disable clear mode options
        self.config.override(Campaign_UseClearMode=False, Campaign_UseAutoSearch=False)
        self.config.override(Campaign_Name=name, Campaign_Event=folder)
        
        name, folder = self.handle_stage_name(name, folder, mode=mode)
        self.load_campaign(name, folder=folder)
        
        self.run_count = 0
        self.run_limit = self.config.StopCondition_RunCount
        
        self.config.STOP_IF_REACH_LV32 = self.change_flagship and not self.config.GemsFarming_AllowHighFlagshipLevel
        initial_check = (
            self.change_flagship
            and not self.config.GemsFarming_AllowHighFlagshipLevel
            and not self._initial_flagship_check_done
        )
        self._initial_flagship_check_done = True
        
        while 1:
            self._trigger_lv32 = initial_check
            initial_check = False
            is_limit = self.config.StopCondition_RunCount
            
            # Use GemsFarming's run method inside loop for standard behavior
            try:
                # We do not use super().run here because it loops infinitely inside map.
                # However, campaign_1_1_f loops infinitely inside itself! 
                # So we simply ensure UI, do configs, handle ships, then call campaign.run() and handle End exceptions.
                logger.hr(name, level=1)
                if self.config.StopCondition_RunCount > 0:
                    logger.info(f'Count remain: {self.config.StopCondition_RunCount}')
                else:
                    logger.info(f'Count: {self.run_count}')

                self.device.stuck_record_clear()
                self.device.click_record_clear()
                if not self.device.has_cached_image:
                    self.device.screenshot()
                self.campaign.device.image = self.device.image
                
                if self.campaign.is_in_map():
                    logger.info('Already in map, retreating.')
                    try:
                        self.campaign.withdraw()
                    except CampaignEnd:
                        pass
                    
                self.campaign.ensure_campaign_ui(name=self.stage, mode=mode)
                self.disable_raid_on_event()
                self.handle_commission_notice()
                
                # Check level to trigger ship switching
                self.campaign.lv_get()
                
                if self.triggered_stop_condition(oil_check=False):
                    if self._trigger_lv32 or self._trigger_emotion:
                        # Ship switching triggered, skip run and proceed to switching block
                        pass
                    else:
                        break
                else:
                    self.device.stuck_record_clear()
                    self.device.click_record_clear()
                    # Run map loop
                    self.campaign.run()
                
            except CampaignEnd as e:
                # E.g. ship leveled up or emotion triggered, handled normally
                if e.args[0] == 'Emotion control':
                    self._trigger_emotion = True
                elif e.args[0] == 'Emotion withdraw':
                    self._trigger_emotion = True
                    self.set_emotion(0)
                pass
                
            # Post-run ship switching block (like in GemsFarming)
            if self._trigger_lv32 or self._trigger_emotion:
                success = True
                self.hard_mode_override()
                emotion = self.get_emotion()
                if self.change_flagship:
                    success = self.flagship_change()
                if self.change_vanguard and success:
                    success = self.vanguard_change()
                    if not success and self.config.GemsFarming_AllowHighFlagshipLevel:
                        self.set_emotion(emotion)
                
                if is_limit and self.config.StopCondition_RunCount <= 0:
                    logger.hr('Triggered stop condition: Run count')
                    self.config.StopCondition_RunCount = 0
                    self.config.Scheduler_Enable = False
                    break

                self._trigger_lv32 = False
                self.config.LV32_TRIGGERED = False
                self.campaign.config.LV32_TRIGGERED = False
                self.campaign.config.GEMS_EMOTION_TRIGGERED = False

                if self.config.task_switched():
                    self._trigger_emotion = False
                    self.campaign.ensure_auto_search_exit()
                    self.config.task_stop()
                elif not success and (self.config.GemsFarming_DelayTaskIFNoFlagship \
                        or self._trigger_emotion):
                    self._trigger_emotion = False
                    self.config.task_delay(server_update=True)
                    self.campaign.ensure_auto_search_exit()
                    self.config.task_stop()            
            
            else:
                # If we legitimately exited the map script without exception, we're likely done with runs.
                break

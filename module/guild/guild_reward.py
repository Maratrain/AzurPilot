"""大舰队奖励任务入口，统一调度大厅、后勤和作战三个子任务。
从主页面进入大舰队，依次执行各子任务后返回。
"""

from module.guild.lobby import GuildLobby
from module.guild.logistics import GuildLogistics
from module.guild.operations import GuildOperations
from module.ui.page import page_guild, page_main


class RewardGuild(GuildLobby, GuildLogistics, GuildOperations):
    def run(self):
        """
        AzurPilot handler function for guild reward loop

        Returns:
            bool: If executed

        Pages:
            in: page_main
            out: page_main
        """
        if not self.config.GuildLogistics_Enable and not self.config.GuildOperation_Enable:
            self.config.Scheduler_Enable = False
            self.config.task_stop()

        self.ui_ensure(page_guild)

        # Lobby
        self.guild_lobby()

        # Logistics
        if self.config.GuildLogistics_Enable:
            self.guild_logistics()

        # Operation
        if self.config.GuildOperation_Enable:
            self.guild_operations()

        self.ui_goto(page_main)

        # Scheduler
        self.config.task_delay(server_update=True)

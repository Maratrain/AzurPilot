class CommissionDebugHandler:

    def __init__(self, bot):
        self.bot = bot

    def trigger_gem_test(self):
        self._send(gem=50)

    def trigger_cube_test(self):
        self._send(gem=0, cube=5)

    def trigger_big_success(self):
        self._send(gem=120, cube=3)

    def trigger_notify_only(self):
        self.bot.debug_commission_notify()

    def _send(self, gem=0, cube=0):
        instance = self.bot.config.config_name

        merged_items = {
            "Gem": gem,
            "Cube": cube
        }

        from module.statistics.cl1_database import db as cl1_db

        cl1_db.add_commission_income(instance, merged_items, commission_count=1)

        reward_stats = cl1_db.get_commission_reward_stats(instance)

        msg = f"💎钻石 * {gem}\n🧊魔方 * {cube}"

        self.bot.handle_notify(
            self.bot.config.Error_OnePushConfig,
            title=f"DEBUG TEST <{instance}>",
            content=msg
        )

        self.bot.notify_webui(
            instance,
            title="DEBUG TEST",
            content=msg
        )
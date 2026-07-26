"""
商店资源余额监测模块。

通过 OCR 提取各类商店的货币余额（钻石、金币、勋章、功勋、
大舰队币、核心数据、代币等），并将数据同步至 Dashboard。
各商店子类通过继承 ShopStatus 获取当前货币数量。
"""

# 此文件专门用于监测普通商店（包含勋章、功勋、核心商店等）中的资源余额状态。
# 通过 OCR 提取钻石、金币、各类奖章的数值，并利用 LogRes 类将数据同步至 Dashboard。
import module.config.server as server
from module.ocr.ocr import Digit
from module.shop.assets import *
from module.ui.ui import UI
from module.log_res import LogRes

if server.server != 'jp':
    OCR_SHOP_GEMS = Digit(SHOP_GEMS, letter=(255, 243, 82), name='OCR_SHOP_GEMS')
else:
    OCR_SHOP_GEMS = Digit(SHOP_GEMS, letter=(190, 180, 82), name='OCR_SHOP_GEMS')
# UI update in 20250814, but server TW is still old UI.
if server.server == 'jp':
    OCR_SHOP_GOLD_COINS = Digit(SHOP_OCR_BALANCE, letter=(110, 120, 130), name='OCR_SHOP_GOLD_COINS')
    OCR_SHOP_MEDAL = Digit(SHOP_OCR_BALANCE, letter=(110, 120, 130), name='OCR_SHOP_MEDAL')
    OCR_SHOP_MERIT = Digit(SHOP_OCR_BALANCE, letter=(110, 120, 130), name='OCR_SHOP_MERIT')
    OCR_SHOP_GUILD_COINS = Digit(SHOP_OCR_BALANCE, letter=(110, 120, 130), name='OCR_SHOP_GUILD_COINS')
    OCR_SHOP_CORE = Digit(SHOP_OCR_BALANCE, letter=(110, 120, 130), name='OCR_SHOP_CORE')
else:
    OCR_SHOP_GOLD_COINS = Digit(SHOP_OCR_BALANCE, letter=(100, 100, 100), name='OCR_SHOP_GOLD_COINS')
    OCR_SHOP_MEDAL = Digit(SHOP_OCR_BALANCE, letter=(100, 100, 100), name='OCR_SHOP_MEDAL')
    OCR_SHOP_MERIT = Digit(SHOP_OCR_BALANCE, letter=(100, 100, 100), name='OCR_SHOP_MERIT')
    OCR_SHOP_GUILD_COINS = Digit(SHOP_OCR_BALANCE, letter=(100, 100, 100), name='OCR_SHOP_GUILD_COINS')
    OCR_SHOP_CORE = Digit(SHOP_OCR_BALANCE, letter=(100, 100, 100), name='OCR_SHOP_CORE')

OCR_SHOP_VOUCHER = Digit(SHOP_VOUCHER, letter=(255, 255, 255), name='OCR_SHOP_VOUCHER')

class ShopStatus(UI):
    """商店货币余额读取器。

    提供各商店货币类型的 OCR 读取接口，同时将余额数据
    同步至 LogRes Dashboard。子类商店通过继承此类获取
    shop_currency() 的默认实现。

    Attributes:
        _currency (int): 当前货币余额缓存。
    """
    def status_get_gold_coins(self):
        """
        Returns:
            int:

        Pages:
            in:
        """
        amount = OCR_SHOP_GOLD_COINS.ocr(self.device.image)
        LogRes(self.config).Coin = amount
        self.config.update()
        return amount

    def status_get_gems(self):
        """
        Returns:
            int:

        Pages:
            in: page_shop, medal shop
        """
        amount = OCR_SHOP_GEMS.ocr(self.device.image)
        LogRes(self.config).Gem = amount
        self.config.update()
        return amount

    def status_get_medal(self):
        """
        Returns:
            int:

        Pages:
            in: page_shop, medal shop
        """
        amount = OCR_SHOP_MEDAL.ocr(self.device.image)
        LogRes(self.config).Medal = amount
        self.config.update()
        return amount

    def status_get_merit(self):
        """
        Returns:
            int:

        Pages:
            in: page_shop, merit shop
        """
        amount = OCR_SHOP_MERIT.ocr(self.device.image)
        LogRes(self.config).Merit = amount
        self.config.update()
        return amount

    def status_get_guild_coins(self):
        """
        Returns:
            int:

        Pages:
            in: page_shop, guild shop
        """
        amount = OCR_SHOP_GUILD_COINS.ocr(self.device.image)
        LogRes(self.config).GuildCoin = amount
        self.config.update()
        return amount

    def status_get_core(self):
        """
        Returns:
            int:

        Pages:
            in: page_shop, core shop
        """
        amount = OCR_SHOP_CORE.ocr(self.device.image)
        LogRes(self.config).Core = amount
        self.config.update()
        return amount

    def status_get_voucher(self):
        """
        Returns:
            int:

        Pages:
            in: OpSi voucher shop
        """
        amount = OCR_SHOP_VOUCHER.ocr(self.device.image)
        return amount

"""
Discord Rich Presence 集成。

通过 pypresence 库连接 Discord RPC，展示 AzurPilot 的运行状态。
提供初始化和关闭接口，异步更新 Discord 状态信息。
"""

import asyncio
import time

from pypresence import AioPresence

RPC: AioPresence = None


async def run():
    assert RPC is not None
    await RPC.connect()
    await RPC.update(state="Alas is playing Azurlane", start=time.time(), large_image="alas")


def init_discord_rpc():
    global RPC
    RPC = AioPresence("929437173764223057")
    asyncio.create_task(run())


def close_discord_rpc():
    if RPC:
        RPC.send_data(2, {'v': 1, 'client_id': RPC.client_id})
        RPC.sock_writer.close()

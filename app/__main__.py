import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from pytonapi import AsyncTonapi
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .bot.commands import (
    bot_commands_setup,
    bot_commands_delete,
    bot_admin_commands_setup,
    bot_admin_commands_delete,
)
from .bot.handlers import bot_routers_include
from .bot.middlewares import bot_middlewares_register
from .config import Config, load_config
from .db.models import Base, AdminDB
from .logger import setup_logger
from .scheduler import Scheduler


async def on_startup(
        dispatcher: Dispatcher,
        bot: Bot,
        config: Config,
        tonapi: AsyncTonapi,
        redis: Redis,
        scheduler: Scheduler,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker,
) -> None:
    """
    Startup event handler. This runs when the bot starts up.
    """
    loop = asyncio.get_event_loop()
    loop.__setattr__("dispatcher", dispatcher)
    loop.__setattr__("bot", bot)
    loop.__setattr__("config", config)
    loop.__setattr__("tonapi", tonapi)
    loop.__setattr__("sessionmaker", sessionmaker)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bot_middlewares_register(
        dispatcher,
        redis=redis,
        config=config,
        scheduler=scheduler,
        sessionmaker=sessionmaker,
    )
    bot_routers_include(dispatcher)
    scheduler.run()

    admins_ids = await AdminDB.get_all_ids(sessionmaker, config)
    await bot_commands_setup(bot)
    await bot_admin_commands_setup(bot, admins_ids)


async def on_shutdown(
        bot: Bot,
        config: Config,
        scheduler: Scheduler,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker,
        tonapi: AsyncTonapi,
) -> None:
    """
    Shutdown event handler. This runs when the bot shuts down.
    """
    admins_ids = await AdminDB.get_all_ids(sessionmaker, config)

    # Delete bot commands
    await bot_commands_delete(bot)
    await bot_admin_commands_delete(bot, admins_ids)

    # Delete webhook
    await bot.delete_webhook()

    # Close bot session
    if bot.session:
        await bot.session.close()

    # Close tonapi session
    if tonapi.session:
        await tonapi.session.close()

    # Dispose database engine
    await engine.dispose()

    # Shutdown scheduler
    scheduler.shutdown()


async def main() -> None:
    """
    Main function that initializes the bot and starts the event loop.
    """
    config = load_config()

    scheduler = Scheduler(config=config)

    # Initialize AsyncTonapi
    tonapi = AsyncTonapi(
        config.tonapi.KEY,
        is_testnet=config.IS_TESTNET,
        max_retries=5,
    )

    # Setup database
    engine = create_async_engine(
        config.database.dsn(),
        pool_pre_ping=True,
    )
    sessionmaker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    # Setup Redis storage
    storage = RedisStorage.from_url(
        url=config.redis.dsn(),
    )

    # Initialize Bot
    bot = Bot(
        token=config.bot.TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    # Setup Dispatcher
    dp = Dispatcher(
        bot=bot,
        storage=storage,
        config=config,
        redis=storage.redis,
        engine=engine,
        sessionmaker=sessionmaker,
        scheduler=scheduler,
        tonapi=tonapi,
    )

    # Register startup/shutdown handlers
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Ensure clean webhook deletion before polling
    await bot.delete_webhook()

    # Start polling
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    # Setup logging
    setup_logger()
    # Run the bot
    asyncio.run(main())

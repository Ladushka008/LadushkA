import os


class Config:
    TOKEN: str = os.getenv("BOT_TOKEN", "")
    GROUP_CHAT_ID: str = os.getenv("GROUP_CHAT_ID", "@ladushka09")
    PORT: int = int(os.getenv("PORT", 8080))


if not Config.TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не установлена!")

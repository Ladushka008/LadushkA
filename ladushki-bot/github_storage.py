import base64
import logging
import os
import aiohttp
from config import Config


def _get_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "TelegramBot-Database-Sync"
    }
    if Config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {Config.GITHUB_TOKEN}"
    return headers


def _is_configured() -> bool:
    if not (Config.GITHUB_TOKEN and Config.GITHUB_OWNER and Config.GITHUB_REPO and Config.GITHUB_DB_PATH):
        logging.warning("⚠️ Переменные окружения GitHub не настроены. Автосинхронизация отключена.")
        return False
    return True


async def download_database() -> None:
    """При запуске скачивает database.db из GitHub."""
    if not _is_configured():
        return

    url = f"https://api.github.com/repos/{Config.GITHUB_OWNER}/{Config.GITHUB_REPO}/contents/{Config.GITHUB_DB_PATH}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_get_headers(), timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    data = await response.json()
                    content_b64 = data.get("content", "")
                    file_bytes = base64.b64decode(content_b64)
                    
                    with open(Config.DB_FILE, "wb") as f:
                        f.write(file_bytes)
                    logging.info(f"✅ База данных {Config.DB_FILE} успешно скачана из GitHub!")
                elif response.status == 404:
                    logging.info("ℹ️ Файл базы данных в репозитории не найден. Будет создана новая база данных.")
                else:
                    logging.error(f"🔴 Ошибка при скачивании БД из GitHub. Статус: {response.status}")
    except Exception as e:
        logging.error(f"🔴 Исключение при скачивании БД из GitHub: {e}")


async def upload_database() -> None:
    """Загружает актуальный database.db обратно в GitHub (создает или обновляет через SHA)."""
    if not _is_configured():
        return

    if not os.path.exists(Config.DB_FILE):
        return

    url = f"https://api.github.com/repos/{Config.GITHUB_OWNER}/{Config.GITHUB_REPO}/contents/{Config.GITHUB_DB_PATH}"
    headers = _get_headers()

    try:
        with open(Config.DB_FILE, "rb") as f:
            content_bytes = f.read()

        content_b64 = base64.b64encode(content_bytes).decode("utf-8")

        async with aiohttp.ClientSession() as session:
            # Запрос SHA существующего файла
            sha = None
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as get_res:
                if get_res.status == 200:
                    data = await get_res.json()
                    sha = data.get("sha")

            payload = {
                "message": "Auto-sync database update",
                "content": content_b64,
            }
            if sha:
                payload["sha"] = sha

            async with session.put(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as put_res:
                if put_res.status in (200, 201):
                    logging.info("☁️ База данных успешно сохранена в GitHub!")
                else:
                    err_text = await put_res.text()
                    logging.error(f"🔴 Не удалось выгрузить БД в GitHub. Код: {put_res.status}, Сообщение: {err_text}")
    except Exception as e:
        logging.error(f"🔴 Исключение при выгрузке БД в GitHub: {e}")

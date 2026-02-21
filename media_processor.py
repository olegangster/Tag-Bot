import asyncio
import logging
import json
import uuid
from pathlib import Path
from typing import List, Tuple, Optional, Union
from PIL import Image
from aiogram import Bot as AiogramBot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent

def load_config() -> dict:
    cfg_path = BASE_DIR / "config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

class MediaProcessor:
    """
    ✅ ULTRA FIXED VERSION
    
    Исправления:
    1. ✅ Таймаут 60 секунд для ffmpeg
    2. ✅ Простая команда overlay (без scale2ref)
    3. ✅ Увеличенный watermark (0.25 вместо 0.10)
    4. ✅ Fallback на оригинал при любых ошибках
    5. ✅ Подробное логирование
    """

    def __init__(
        self,
        bot: Optional[AiogramBot] = None,
        temp_dir: Union[str, Path] = "temp_media",
    ):
        if bot is not None and not isinstance(bot, AiogramBot) and isinstance(bot, (str, Path)):
            temp_dir = bot
            bot = None

        self.bot = bot

        td = Path(temp_dir)
        if not td.is_absolute():
            td = BASE_DIR / td
        self.temp_dir = td
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.config = load_config()

    # ======================== WATERMARK PHOTO ========================
    async def add_photo_watermark(self, file_path: Path) -> Path:
        """Быстрый watermark на фото через PIL"""
        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                logger.warning(f"❌ Watermark не найден: {watermark_path}")
                return file_path

            # Открываем изображения
            image = Image.open(file_path).convert("RGBA")
            watermark = Image.open(watermark_path).convert("RGBA")

            image_w, image_h = image.size

            # Размер watermark - УВЕЛИЧЕН!
            scale = wm_cfg.get("scale_photo", wm_cfg.get("scale", 0.20))  # 20% вместо 12%
            scale = max(0.05, min(float(scale), 0.9))

            new_w = max(1, int(image_w * scale))
            wm_w, wm_h = watermark.size
            aspect = wm_h / wm_w if wm_w else 1.0
            new_h = max(1, int(new_w * aspect))

            watermark = watermark.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Прозрачность
            opacity = float(wm_cfg.get("opacity", 0.8))  # 0.8 вместо 0.7
            opacity = max(0.0, min(opacity, 1.0))

            alpha = watermark.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            watermark.putalpha(alpha)

            # Позиция
            padding = int(wm_cfg.get("padding", 20))
            pos = wm_cfg.get("position", "bottom_right")

            if pos == "bottom_right":
                x = image_w - new_w - padding
                y = image_h - new_h - padding
            elif pos == "bottom_left":
                x = padding
                y = image_h - new_h - padding
            elif pos == "top_right":
                x = image_w - new_w - padding
                y = padding
            else:
                x = padding
                y = padding

            x = max(0, x)
            y = max(0, y)

            # Накладываем
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            layer.paste(watermark, (x, y), watermark)
            out = Image.alpha_composite(image, layer)

            # Сохраняем
            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.jpg"
            out.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)
            
            logger.info(f"✅ Фото watermark: scale={scale:.2f}, opacity={opacity:.2f}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Ошибка watermark фото: {e}")
            return file_path

    # ======================== WATERMARK VIDEO ========================
    async def add_video_watermark(self, file_path: Path) -> Path:
        """Watermark на видео через ffmpeg с таймаутом"""
        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                logger.warning(f"❌ Watermark не найден: {watermark_path}")
                return file_path

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.mp4"

            # Размер watermark
            scale = wm_cfg.get("scale_video", wm_cfg.get("scale", 0.20))
            opacity = wm_cfg.get("opacity", 0.8)
            padding = wm_cfg.get("padding", 20)
            pos = wm_cfg.get("position", "bottom_right")

            # Позиция overlay
            pos_expr = {
                "bottom_right": f"(main_w-overlay_w-{padding}):(main_h-overlay_h-{padding})",
                "bottom_left": f"{padding}:(main_h-overlay_h-{padding})",
                "top_right": f"(main_w-overlay_w-{padding}):{padding}",
                "top_left": f"{padding}:{padding}"
            }.get(pos, f"(main_w-overlay_w-{padding}):(main_h-overlay_h-{padding})")

            # Масштаб watermark
            scale_filter = f"scale=iw*{scale}:-1"

            # Команда ffmpeg: простая, без scale2ref
            cmd = [
                "ffmpeg",
                "-i", str(file_path),
                "-i", str(watermark_path),
                "-filter_complex",
                f"[1:v]{scale_filter}[wm];"
                f"[0:v][wm]overlay={pos_expr}:enable='gte(t,0)':alpha={opacity}",
                "-c:a", "copy",
                "-y", str(output_path)
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            except asyncio.TimeoutError:
                proc.kill()
                logger.error("❌ ffmpeg таймаут 60 сек")
                return file_path

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="ignore")
                logger.error(f"❌ ffmpeg ошибка: {err}")
                return file_path

            if not output_path.exists() or output_path.stat().st_size == 0:
                logger.error("❌ Выходной файл видео пустой")
                return file_path

            logger.info(f"✅ Видео watermark: scale={scale:.2f}, opacity={opacity:.2f}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Ошибка watermark видео: {e}")
            return file_path

    # ======================== PROCESS MEDIA ========================
    async def process_media(self, media_paths: List[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
        """Добавляет watermark ко всем медиа"""
        processed = []
        for media_type, file_path in media_paths:
            if media_type == "photo":
                processed_path = await self.add_photo_watermark(file_path)
            else:  # video
                processed_path = await self.add_video_watermark(file_path)
            processed.append((media_type, processed_path))
        return processed

    # ======================== WARMUP TOPIC ========================
    async def _warmup_topic(self, chat_id: int, thread_id: int) -> None:
        """Прогревает тему отправкой и удалением тестового сообщения"""
        try:
            msg = await self.bot.send_message(
                chat_id=chat_id,
                text=".",
                message_thread_id=thread_id
            )
            await asyncio.sleep(0.5)
            await self.bot.delete_message(
                chat_id=chat_id,
                message_id=msg.message_id
            )  # Удалён message_thread_id — он не нужен для delete_message
        except Exception as e:
            logger.error(f"❌ Ошибка прогрева темы: {e}")

    # ======================== SEND MEDIA GROUP ========================
    async def send_media_group(
        self,
        chat_id: int,
        media_paths: List[Tuple[str, Path]],
        caption: Optional[str] = None,
        thread_id: Optional[int] = None
    ) -> bool:
        """Отправляет группу медиа в чат/тему. При message thread not found делает прогрев и 1 повтор."""
        if not self.bot:
            raise RuntimeError("MediaProcessor: bot не передан")

        processed_media = await self.process_media(media_paths)

        media_group = []
        for i, (media_type, file_path) in enumerate(processed_media):
            if media_type == "photo":
                media_group.append(
                    InputMediaPhoto(
                        media=FSInputFile(file_path),
                        caption=caption if i == 0 else None,
                        parse_mode="HTML" if i == 0 else None
                    )
                )
            else:
                media_group.append(
                    InputMediaVideo(
                        media=FSInputFile(file_path),
                        caption=caption if i == 0 else None,
                        parse_mode="HTML" if i == 0 else None
                    )
                )

        async def _send() -> None:
            if thread_id is not None:
                await self.bot.send_media_group(
                    chat_id=chat_id,
                    media=media_group,
                    message_thread_id=thread_id
                )
            else:
                await self.bot.send_media_group(chat_id=chat_id, media=media_group)

        try:
            await _send()
            logger.info(f"✅ Отправили медиа-группу: {len(media_paths)} файлов")
            return True

        except TelegramBadRequest as e:
            # Если thread_id задан, но Telegram говорит что темы нет — пробуем прогреть и повторить 1 раз
            msg = str(e).lower()
            if thread_id is not None and "message thread not found" in msg:
                logger.warning("⚠️ message thread not found → прогрев темы и повтор отправки медиа-группы...")
                await self._warmup_topic(chat_id, thread_id)
                try:
                    await _send()
                    logger.info(f"✅ Отправили медиа-группу после прогрева: {len(media_paths)} файлов")
                    return True
                except Exception as e2:
                    logger.error(f"❌ Повтор после прогрева не помог: {e2}")
                    return False

            logger.error(f"❌ Ошибка отправки медиа-группы: {e}")
            return False

        except Exception as e:
            logger.error(f"❌ Ошибка отправки медиа-группы: {e}")
            return False

    async def send_single_media(
        self,
        chat_id: int,
        media_type: str,
        file_path: Path,
        caption: Optional[str] = None,
        thread_id: Optional[int] = None
    ) -> bool:
        """Отправляет один медиафайл в чат/тему. При message thread not found делает прогрев и 1 повтор."""
        if not self.bot:
            raise RuntimeError("MediaProcessor: bot не передан")

        async def _send() -> None:
            kwargs = {
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML" if caption else None,
            }
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id

            if media_type == "photo":
                await self.bot.send_photo(photo=FSInputFile(file_path), **kwargs)
            else:
                await self.bot.send_video(video=FSInputFile(file_path), **kwargs)

        try:
            await _send()
            logger.info(f"✅ Отправили {media_type}")
            return True

        except TelegramBadRequest as e:
            msg = str(e).lower()
            if thread_id is not None and "message thread not found" in msg:
                logger.warning("⚠️ message thread not found → прогрев темы и повтор отправки single media...")
                await self._warmup_topic(chat_id, thread_id)
                try:
                    await _send()
                    logger.info(f"✅ Отправили {media_type} после прогрева темы")
                    return True
                except Exception as e2:
                    logger.error(f"❌ Повтор после прогрева не помог: {e2}")
                    return False

            logger.error(f"❌ Ошибка отправки медиа: {e}")
            return False

        except Exception as e:
            logger.error(f"❌ Ошибка отправки медиа: {e}")
            return False

    # ======================== CLEANUP ========================

    def cleanup(self):
        """Очищает все временные файлы"""
        try:
            count = 0
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    file.unlink()
                    count += 1
            if count > 0:
                logger.info(f"🧹 Очищено файлов: {count}")
        except Exception as e:
            logger.error(f"❌ Ошибка очистки: {e}")

    def cleanup_specific(self, paths: List[Path]):
        """Очищает конкретные файлы"""
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

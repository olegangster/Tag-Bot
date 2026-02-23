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
    Обработчик медиафайлов: вотермарк на фото/видео, отправка в Telegram.

    Исправления:
    - Таймаут 5 минут для ffmpeg
    - Увеличенный watermark (20% фото / 25% видео)
    - Fallback на оригинал при любых ошибках
    """

    def __init__(
        self,
        bot: Optional[AiogramBot] = None,
        temp_dir: Union[str, Path] = "temp_media",
    ):
        # Обратная совместимость: если первый аргумент — путь, а не бот
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

    # ──────────────────────────────────────────────────────
    #  WATERMARK — ФОТО (PIL)
    # ──────────────────────────────────────────────────────

    async def add_photo_watermark(self, file_path: Path) -> Path:
        """Накладывает watermark на фото через PIL."""
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

            image = Image.open(file_path).convert("RGBA")
            watermark = Image.open(watermark_path).convert("RGBA")
            image_w, image_h = image.size

            scale = float(wm_cfg.get("scale_photo", wm_cfg.get("scale", 0.20)))
            scale = max(0.05, min(scale, 0.9))
            new_w = max(1, int(image_w * scale))
            wm_w, wm_h = watermark.size
            aspect = wm_h / wm_w if wm_w else 1.0
            new_h = max(1, int(new_w * aspect))
            watermark = watermark.resize((new_w, new_h), Image.Resampling.LANCZOS)

            opacity = max(0.0, min(float(wm_cfg.get("opacity", 0.8)), 1.0))
            alpha = watermark.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            watermark.putalpha(alpha)

            padding = int(wm_cfg.get("padding", 20))
            pos = wm_cfg.get("position", "bottom_right")
            if pos == "bottom_right":
                x, y = image_w - new_w - padding, image_h - new_h - padding
            elif pos == "bottom_left":
                x, y = padding, image_h - new_h - padding
            elif pos == "top_right":
                x, y = image_w - new_w - padding, padding
            else:
                x, y = padding, padding
            x, y = max(0, x), max(0, y)

            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            layer.paste(watermark, (x, y), watermark)
            out = Image.alpha_composite(image, layer)

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.jpg"
            out.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)
            logger.info(f"✅ Фото watermark: scale={scale:.2f}, opacity={opacity:.2f}")
            return output_path

        except Exception as e:
            logger.error(f"❌ Ошибка watermark фото: {e}")
            return file_path

    # ──────────────────────────────────────────────────────
    #  WATERMARK — ВИДЕО (ffmpeg)
    # ──────────────────────────────────────────────────────

    async def add_video_watermark(self, file_path: Path) -> Path:
        """Накладывает watermark на видео через ffmpeg."""
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

            scale = float(wm_cfg.get("scale_video", wm_cfg.get("scale", 0.25)))
            scale = max(0.05, min(scale, 0.9))
            opacity = max(0.0, min(float(wm_cfg.get("opacity", 0.8)), 1.0))
            padding = int(wm_cfg.get("padding", 20))
            pos = wm_cfg.get("position", "bottom_right")

            if pos == "bottom_right":
                overlay_pos = f"W-w-{padding}:H-h-{padding}"
            elif pos == "bottom_left":
                overlay_pos = f"{padding}:H-h-{padding}"
            elif pos == "top_right":
                overlay_pos = f"W-w-{padding}:{padding}"
            else:
                overlay_pos = f"{padding}:{padding}"

            filter_complex = (
                f"[0:v]scale='min(720,iw)':-2[scaled];"
                f"[1:v]scale=iw*{scale}:-2,format=rgba,"
                f"colorchannelmixer=aa={opacity}[wm];"
                f"[scaled][wm]overlay={overlay_pos}:format=auto"
            )

            cmd = [
                "ffmpeg",
                "-hide_banner", "-loglevel", "warning",
                "-y",
                "-i", str(file_path),
                "-i", str(watermark_path),
                "-filter_complex", filter_complex,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "28",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ]

            logger.info(
                f"🎬 FFmpeg: scale={scale:.2f}, opacity={opacity:.2f}, "
                f"preset=ultrafast, max_w=720px"
            )

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=300.0
                )
            except asyncio.TimeoutError:
                process.kill()
                logger.error("❌ FFmpeg таймаут (>5 мин) — используем оригинал")
                return file_path

            if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                logger.info("✅ Видео watermark применён успешно")
                return output_path

            err = stderr.decode(errors="ignore").strip()
            if err:
                logger.error(f"❌ FFmpeg error (код {process.returncode}):\n{err[:500]}")
            return file_path

        except FileNotFoundError:
            logger.error("❌ FFmpeg не установлен на сервере")
            return file_path
        except Exception as e:
            logger.error(f"❌ Ошибка watermark видео: {e}")
            return file_path

    # ──────────────────────────────────────────────────────
    #  СКАЧИВАНИЕ И ОБРАБОТКА
    # ──────────────────────────────────────────────────────

    async def download_and_process(
        self, media_type: str, file_id: str
    ) -> Optional[Tuple[str, Path]]:
        """Скачивает файл из Telegram и применяет watermark."""
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан")

            file = await self.bot.get_file(file_id)
            file_bytes = await self.bot.download_file(file.file_path)

            ext = "jpg" if media_type == "photo" else "mp4"
            temp_path = self.temp_dir / f"{uuid.uuid4().hex}.{ext}"
            with open(temp_path, "wb") as f:
                f.write(file_bytes.read())
            logger.info(f"📥 Скачали {media_type}: {temp_path.name}")

            if media_type == "photo":
                processed = await self.add_photo_watermark(temp_path)
            else:
                processed = await self.add_video_watermark(temp_path)

            return media_type, processed

        except Exception as e:
            logger.error(f"❌ Ошибка обработки медиа: {e}")
            return None

    async def process_batch(
        self, media_list: List[Tuple[str, str]]
    ) -> List[Tuple[str, Path]]:
        """Параллельная обработка пакета медиафайлов."""
        logger.info(f"📦 Обрабатываем {len(media_list)} файлов...")
        tasks = [self.download_and_process(mtype, fid) for mtype, fid in media_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: List[Tuple[str, Path]] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"❌ Exception при обработке: {r}")
                continue
            if r is None:
                continue
            mtype, path = r
            if isinstance(mtype, str) and path is not None:
                processed.append((mtype, path))

        logger.info(f"✅ Обработано {len(processed)}/{len(media_list)} файлов")
        return processed

    # ──────────────────────────────────────────────────────
    #  ОТПРАВКА В TELEGRAM
    # ──────────────────────────────────────────────────────

    async def _warmup_topic(self, chat_id: int, thread_id: int) -> None:
        """Прогрев темы: отправляет невидимый символ и пробует удалить сообщение."""
        if not self.bot:
            return
        try:
            msg = await self.bot.send_message(
                chat_id=chat_id,
                text="\u200b",  # zero-width space
                message_thread_id=thread_id,
            )
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"⚠️ Прогрев темы не удался: {e}")

    async def send_media_group(
        self,
        chat_id: int,
        media_paths: List[Tuple[str, Path]],
        caption: Optional[str] = None,
        thread_id: Optional[int] = None,
    ) -> bool:
        """Отправляет медиагруппу. При ошибке «thread not found» делает прогрев и повтор."""
        if not self.bot:
            raise RuntimeError("MediaProcessor: bot не передан")

        media_group = []
        for i, (media_type, file_path) in enumerate(media_paths):
            kwargs = dict(
                media=FSInputFile(file_path),
                caption=caption if i == 0 else None,
                parse_mode="HTML" if i == 0 else None,
            )
            if media_type == "photo":
                media_group.append(InputMediaPhoto(**kwargs))
            else:
                media_group.append(InputMediaVideo(**kwargs))

        async def _send() -> None:
            send_kwargs = dict(chat_id=chat_id, media=media_group)
            if thread_id is not None:
                send_kwargs["message_thread_id"] = thread_id
            await self.bot.send_media_group(**send_kwargs)

        try:
            await _send()
            logger.info(f"✅ Отправлена медиагруппа: {len(media_paths)} файлов")
            return True
        except TelegramBadRequest as e:
            if thread_id is not None and "message thread not found" in str(e).lower():
                logger.warning("⚠️ message thread not found → прогрев и повтор...")
                await self._warmup_topic(chat_id, thread_id)
                try:
                    await _send()
                    logger.info("✅ Медиагруппа отправлена после прогрева")
                    return True
                except Exception as e2:
                    logger.error(f"❌ Повтор после прогрева не помог: {e2}")
                    return False
            logger.error(f"❌ Ошибка отправки медиагруппы: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка отправки медиагруппы: {e}")
            return False

    async def send_single_media(
        self,
        chat_id: int,
        media_type: str,
        file_path: Path,
        caption: Optional[str] = None,
        thread_id: Optional[int] = None,
    ) -> bool:
        """Отправляет одиночный медиафайл. При ошибке «thread not found» делает прогрев и повтор."""
        if not self.bot:
            raise RuntimeError("MediaProcessor: bot не передан")

        async def _send() -> None:
            kwargs = dict(
                chat_id=chat_id,
                caption=caption,
                parse_mode="HTML" if caption else None,
            )
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id
            if media_type == "photo":
                await self.bot.send_photo(photo=FSInputFile(file_path), **kwargs)
            else:
                await self.bot.send_video(video=FSInputFile(file_path), **kwargs)

        try:
            await _send()
            logger.info(f"✅ Отправлен {media_type}")
            return True
        except TelegramBadRequest as e:
            if thread_id is not None and "message thread not found" in str(e).lower():
                logger.warning("⚠️ message thread not found → прогрев и повтор...")
                await self._warmup_topic(chat_id, thread_id)
                try:
                    await _send()
                    logger.info(f"✅ {media_type} отправлен после прогрева")
                    return True
                except Exception as e2:
                    logger.error(f"❌ Повтор после прогрева не помог: {e2}")
                    return False
            logger.error(f"❌ Ошибка отправки медиа: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка отправки медиа: {e}")
            return False

    # ──────────────────────────────────────────────────────
    #  ОЧИСТКА ВРЕМЕННЫХ ФАЙЛОВ
    # ──────────────────────────────────────────────────────

    def cleanup(self):
        """Удаляет все файлы из temp_dir."""
        try:
            count = sum(
                1 for f in self.temp_dir.glob("*") if f.is_file() and not f.unlink()
            )
            if count:
                logger.info(f"🧹 Очищено файлов: {count}")
        except Exception as e:
            logger.error(f"❌ Ошибка очистки: {e}")

    def cleanup_specific(self, paths: List[Path]):
        """Удаляет конкретные файлы."""
        for path in paths:
            try:
                if path and Path(path).exists():
                    Path(path).unlink()
            except Exception:
                pass

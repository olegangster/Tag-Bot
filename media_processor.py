import asyncio
import logging
import json
import uuid
from pathlib import Path
from typing import List, Tuple, Optional, Union
from PIL import Image
from aiogram import Bot as AiogramBot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

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
        """
        ✅ ИСПРАВЛЕННАЯ версия для видео
        
        Изменения:
        1. Простая команда overlay (без scale2ref)
        2. Таймаут 60 секунд
        3. Увеличенный watermark (25% вместо 10%)
        4. Fallback на оригинал
        """
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

            # Параметры - УВЕЛИЧЕНЫ!
            scale = wm_cfg.get("scale_video", wm_cfg.get("scale", 0.25))  # 25% вместо 10%
            scale = max(0.05, min(float(scale), 0.9))
            
            opacity = float(wm_cfg.get("opacity", 0.8))  # 0.8 вместо 0.7
            opacity = max(0.0, min(opacity, 1.0))
            
            padding = int(wm_cfg.get("padding", 20))
            pos = wm_cfg.get("position", "bottom_right")

            # Позиция overlay
            if pos == "bottom_right":
                overlay_pos = f"W-w-{padding}:H-h-{padding}"
            elif pos == "bottom_left":
                overlay_pos = f"{padding}:H-h-{padding}"
            elif pos == "top_right":
                overlay_pos = f"W-w-{padding}:{padding}"
            else:
                overlay_pos = f"{padding}:{padding}"

            # ✅ RENDER-ОПТИМИЗИРОВАННАЯ команда ffmpeg
            # Сначала уменьшаем видео до max 720px ширины (в 4x меньше пикселей → в 4x быстрее)
            # -2 вместо -1 чтобы размеры были чётными (требование libx264)
            filter_complex = (
                f"[0:v]scale='min(720,iw)':-2[scaled];"
                f"[1:v]scale=iw*{scale}:-2,format=rgba,"
                f"colorchannelmixer=aa={opacity}[wm];"
                f"[scaled][wm]overlay={overlay_pos}:format=auto"
            )

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "warning",
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

            logger.info(f"🎬 FFmpeg (Render): scale={scale:.2f}, opacity={opacity:.2f}, preset=ultrafast, max_w=720px")

            # ✅ ТАЙМАУТ 5 МИНУТ (Render.com медленный, нужно больше времени)
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=300.0  # ← 5 минут вместо 60 секунд
                )
            except asyncio.TimeoutError:
                process.kill()
                logger.error("❌ FFmpeg таймаут (>5 мин) - используем оригинал")
                return file_path

            # Проверяем результат
            if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                logger.info(f"✅ Видео watermark применен успешно")
                return output_path

            # Логируем ошибку
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

    # ======================== DOWNLOAD + PROCESS ========================
    async def download_and_process(self, media_type: str, file_id: str) -> Optional[Tuple[str, Path]]:
        """Скачивает и обрабатывает один файл"""
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан")

            # Скачиваем
            file = await self.bot.get_file(file_id)
            file_bytes = await self.bot.download_file(file.file_path)

            ext = "jpg" if media_type == "photo" else "mp4"
            temp_path = self.temp_dir / f"{uuid.uuid4().hex}.{ext}"

            with open(temp_path, "wb") as f:
                f.write(file_bytes.read())

            logger.info(f"📥 Скачали {media_type}: {temp_path.name}")

            # Обрабатываем
            if media_type == "photo":
                processed = await self.add_photo_watermark(temp_path)
            else:
                processed = await self.add_video_watermark(temp_path)

            return media_type, processed

        except Exception as e:
            logger.error(f"❌ Ошибка обработки медиа: {e}")
            return None

    async def process_batch(self, media_list: List[Tuple[str, str]]) -> List[Tuple[str, Path]]:
        """Параллельная обработка пакета медиа"""
        logger.info(f"📦 Обрабатываем {len(media_list)} файлов...")
        
        tasks = [self.download_and_process(mtype, fid) for mtype, fid in media_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: List[Tuple[str, Path]] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"❌ Exception: {r}")
                continue
            if r is None:
                continue
            mtype, path = r
            if isinstance(mtype, str) and path is not None:
                processed.append((mtype, path))

        logger.info(f"✅ Обработано {len(processed)}/{len(media_list)} файлов")
        return processed

    # ======================== SEND MEDIA ========================
    async def send_media_group(
        self,
        chat_id: int,
        media_paths: List[Tuple[str, Path]],
        caption: Optional[str] = None,
        thread_id: Optional[int] = None
    ) -> bool:
        """Отправляет медиа-группу"""
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан")

            media_group = []
            for i, (media_type, file_path) in enumerate(media_paths):
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

            if thread_id:
                await self.bot.send_media_group(
                    chat_id=chat_id, 
                    media=media_group, 
                    message_thread_id=thread_id
                )
            else:
                await self.bot.send_media_group(chat_id=chat_id, media=media_group)

            logger.info(f"✅ Отправили медиа-группу: {len(media_paths)} файлов")
            return True

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
        """Отправляет один медиафайл"""
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан")

            if media_type == "photo":
                await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=FSInputFile(file_path),
                    caption=caption,
                    parse_mode="HTML" if caption else None,
                    message_thread_id=thread_id
                )
            else:
                await self.bot.send_video(
                    chat_id=chat_id,
                    video=FSInputFile(file_path),
                    caption=caption,
                    parse_mode="HTML" if caption else None,
                    message_thread_id=thread_id
                )

            logger.info(f"✅ Отправили {media_type}")
            return True

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

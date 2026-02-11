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
    Процессор медиа:
    - скачивает файлы
    - добавляет watermark на фото (PIL)
    - добавляет watermark на видео (ffmpeg)
    - отправляет single/group медиа

    Совместимость:
      MediaProcessor(message.bot)
      MediaProcessor(bot=message.bot)
      MediaProcessor("temp_dir")
    """

    def __init__(
        self,
        bot: Optional[AiogramBot] = None,
        temp_dir: Union[str, Path] = "temp_media",
    ):
        # если кто-то случайно передал temp_dir первым аргументом
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

    # ------------------------ WATERMARK PHOTO ------------------------

    async def add_photo_watermark(self, file_path: Path) -> Path:
        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                logger.warning(f"Watermark не найден: {watermark_path.resolve()}")
                return file_path

            image = Image.open(file_path).convert("RGBA")
            watermark = Image.open(watermark_path).convert("RGBA")

            image_w, image_h = image.size

            scale = wm_cfg.get("scale_photo", wm_cfg.get("scale", 0.12))
            scale = max(0.01, min(float(scale), 0.9))

            new_w = max(1, int(image_w * scale))
            wm_w, wm_h = watermark.size
            aspect = wm_h / wm_w if wm_w else 1.0
            new_h = max(1, int(new_w * aspect))

            watermark = watermark.resize((new_w, new_h), Image.Resampling.LANCZOS)

            opacity = float(wm_cfg.get("opacity", 0.7))
            opacity = max(0.0, min(opacity, 1.0))

            alpha = watermark.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            watermark.putalpha(alpha)

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
            else:  # top_left
                x = padding
                y = padding

            x = max(0, x)
            y = max(0, y)

            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            layer.paste(watermark, (x, y), watermark)
            out = Image.alpha_composite(image, layer)

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.jpg"
            out.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)
            return output_path

        except Exception as e:
            logger.exception(f"Ошибка watermark на фото: {e}")
            return file_path

    # ------------------------ WATERMARK VIDEO ------------------------

    async def add_video_watermark(self, file_path: Path) -> Path:
        """
        FIX для ffmpeg (ваша версия):
        - В scale2ref НЕ использовать ref_w/ref_h и НЕ использовать ow
        - Масштабируем watermark от ширины reference-видео через iw (в scale2ref это ширина reference)
        """
        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                logger.warning(f"Watermark не найден: {watermark_path.resolve()}")
                return file_path

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.mp4"

            scale = wm_cfg.get("scale_video", wm_cfg.get("scale", 0.10))
            scale = max(0.01, min(float(scale), 0.9))

            opacity = float(wm_cfg.get("opacity", 0.7))
            opacity = max(0.0, min(opacity, 1.0))

            padding = int(wm_cfg.get("padding", 20))
            pos = wm_cfg.get("position", "bottom_right")

            if pos == "bottom_right":
                ox, oy = f"W-w-{padding}", f"H-h-{padding}"
            elif pos == "bottom_left":
                ox, oy = f"{padding}", f"H-h-{padding}"
            elif pos == "top_right":
                ox, oy = f"W-w-{padding}", f"{padding}"
            else:
                ox, oy = f"{padding}", f"{padding}"

            # ВАЖНО: в scale2ref iw/ih относятся к reference (0:v), т.е. к ВИДЕО
            # Значит watermark (1:v) масштабируем от ширины видео: iw*scale
            filter_complex = (
                f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[wmrgba];"
                f"[wmrgba][0:v]scale2ref=w=iw*{scale}:h=-1[wm][base];"
                f"[base][wm]overlay={ox}:{oy}:format=auto,format=yuv420p[v]"
            )

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-y",
                "-i", str(file_path),
                "-i", str(watermark_path),
                "-filter_complex", filter_complex,
                "-map", "[v]",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                str(output_path),
            ]

            logger.info(f"FFmpeg CMD: {' '.join(cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()

            if process.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                logger.info(f"✅ Видео watermark применен (scale_video={scale}, opacity={opacity})")
                return output_path

            err = stderr.decode(errors="ignore").strip()
            logger.error(f"❌ FFmpeg error (code={process.returncode}):\n{err}")
            return file_path

        except FileNotFoundError:
            logger.error("❌ FFmpeg не найден на сервере (команда ffmpeg отсутствует).")
            return file_path
        except Exception as e:
            logger.exception(f"Ошибка watermark на видео: {e}")
            return file_path

    # ------------------------ DOWNLOAD + PROCESS ------------------------

    async def download_and_process(self, media_type: str, file_id: str) -> Optional[Tuple[str, Path]]:
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан (self.bot is None)")

            file = await self.bot.get_file(file_id)
            file_bytes = await self.bot.download_file(file.file_path)

            ext = "jpg" if media_type == "photo" else "mp4"
            temp_path = self.temp_dir / f"{uuid.uuid4().hex}.{ext}"

            with open(temp_path, "wb") as f:
                f.write(file_bytes.read())

            if media_type == "photo":
                processed = await self.add_photo_watermark(temp_path)
            else:
                processed = await self.add_video_watermark(temp_path)

            return media_type, processed

        except Exception as e:
            logger.exception(f"Ошибка обработки медиа: {e}")
            return None

    async def process_batch(self, media_list: List[Tuple[str, str]]) -> List[Tuple[str, Path]]:
        tasks = [self.download_and_process(mtype, fid) for mtype, fid in media_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: List[Tuple[str, Path]] = []
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"process_batch exception: {r}")
                continue
            if r is None:
                continue
            mtype, path = r
            if isinstance(mtype, str) and path is not None:
                processed.append((mtype, path))

        return processed

    # ------------------------ SEND MEDIA ------------------------

    async def send_media_group(
        self,
        chat_id: int,
        media_paths: List[Tuple[str, Path]],
        caption: Optional[str] = None,
        thread_id: Optional[int] = None
    ) -> bool:
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан (self.bot is None)")

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
                await self.bot.send_media_group(chat_id=chat_id, media=media_group, message_thread_id=thread_id)
            else:
                await self.bot.send_media_group(chat_id=chat_id, media=media_group)

            return True

        except Exception as e:
            logger.exception(f"Ошибка отправки медиа-группы: {e}")
            return False

    async def send_single_media(
        self,
        chat_id: int,
        media_type: str,
        file_path: Path,
        caption: Optional[str] = None,
        thread_id: Optional[int] = None
    ) -> bool:
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан (self.bot is None)")

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

            return True

        except Exception as e:
            logger.exception(f"Ошибка отправки одиночного медиа: {e}")
            return False

    # ------------------------ CLEANUP ------------------------

    def cleanup(self):
        try:
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    file.unlink()
        except Exception as e:
            logger.exception(f"Ошибка очистки временных файлов: {e}")

    def cleanup_specific(self, paths: List[Path]):
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

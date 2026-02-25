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
    ⚡ BALANCED VERSION - Баланс качества и скорости
    
    Оптимизация:
    1. Preset ultrafast (быстро)
    2. CRF 23 (хорошее качество)
    3. Downscale до 720px (нормальное разрешение)
    4. Watermark 8% (небольшой размер)
    5. Таймаут 3 минуты
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

    async def add_photo_watermark(self, file_path: Path) -> Path:
        """Быстрый watermark на фото"""
        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                return file_path

            image = Image.open(file_path).convert("RGBA")
            watermark = Image.open(watermark_path).convert("RGBA")
            image_w, image_h = image.size

            scale = wm_cfg.get("scale_photo", 0.12)
            scale = max(0.05, min(float(scale), 0.5))

            new_w = max(1, int(image_w * scale))
            wm_w, wm_h = watermark.size
            aspect = wm_h / wm_w if wm_w else 1.0
            new_h = max(1, int(new_w * aspect))

            watermark = watermark.resize((new_w, new_h), Image.Resampling.LANCZOS)
            opacity = float(wm_cfg.get("opacity", 0.75))
            opacity = max(0.0, min(opacity, 1.0))

            alpha = watermark.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity))
            watermark.putalpha(alpha)

            padding = int(wm_cfg.get("padding", 15))
            pos = wm_cfg.get("position", "bottom_right")

            if pos == "bottom_right":
                x, y = image_w - new_w - padding, image_h - new_h - padding
            elif pos == "bottom_left":
                x, y = padding, image_h - new_h - padding
            elif pos == "top_right":
                x, y = image_w - new_w - padding, padding
            else:
                x, y = padding, padding

            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            layer.paste(watermark, (max(0, x), max(0, y)), watermark)
            out = Image.alpha_composite(image, layer)

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.jpg"
            out.convert("RGB").save(output_path, "JPEG", quality=90, optimize=True)
            
            return output_path

        except Exception as e:
            logger.error(f"❌ Watermark фото: {e}")
            return file_path

    async def add_video_watermark(self, file_path: Path) -> Path:
        """
        ⚡ BALANCED QUALITY watermark - баланс качества и скорости
        
        - Downscale до 720px (хорошее качество)
        - CRF 23 (отличное качество)
        - Preset ultrafast (быстро)
        - Watermark 8% (небольшой размер)
        - Таймаут 3 минуты
        """
        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                return file_path

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.mp4"

            # ⚡ БАЛАНС: качество + скорость
            scale = wm_cfg.get("scale_video", 0.08)  # 8% - небольшой размер
            scale = max(0.05, min(float(scale), 0.3))
            
            opacity = float(wm_cfg.get("opacity", 0.75))
            opacity = max(0.0, min(opacity, 1.0))
            
            padding = int(wm_cfg.get("padding", 10))
            pos = wm_cfg.get("position", "bottom_right")

            if pos == "bottom_right":
                overlay_pos = f"W-w-{padding}:H-h-{padding}"
            elif pos == "bottom_left":
                overlay_pos = f"{padding}:H-h-{padding}"
            elif pos == "top_right":
                overlay_pos = f"W-w-{padding}:{padding}"
            else:
                overlay_pos = f"{padding}:{padding}"

            # ⚡ УЛУЧШЕННОЕ КАЧЕСТВО команда ffmpeg
            filter_complex = (
                f"[0:v]scale='min(720,iw)':-2[scaled];"  # 720px - хорошее качество
                f"[1:v]scale=iw*{scale}:-2,format=rgba,"
                f"colorchannelmixer=aa={opacity}[wm];"
                f"[scaled][wm]overlay={overlay_pos}:format=auto"
            )

            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(file_path),
                "-i", str(watermark_path),
                "-filter_complex", filter_complex,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",  # Улучшенное качество
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ]

            logger.info(f"⚡ FFmpeg: ultrafast, crf=23 (качество), max_w=720px, watermark=8%")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                # ⚡ Таймаут 3 минуты (больше времени для лучшего качества)
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=180.0
                )
            except asyncio.TimeoutError:
                process.kill()
                logger.error("❌ Таймаут (3 мин) - используем оригинал")
                return file_path

            if process.returncode == 0 and output_path.exists():
                logger.info(f"✅ Видео watermark (качество улучшено)")
                return output_path

            return file_path

        except FileNotFoundError:
            logger.error("❌ FFmpeg не установлен")
            return file_path
        except Exception as e:
            logger.error(f"❌ Watermark видео: {e}")
            return file_path

    async def download_and_process(self, media_type: str, file_id: str) -> Optional[Tuple[str, Path]]:
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан")

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
            logger.error(f"❌ Обработка: {e}")
            return None

    async def process_batch(self, media_list: List[Tuple]) -> List[Tuple[str, Path]]:
        # Поддерживаем 2-тупл (mtype, fid) и 3-тупл (mtype, fid, unique_id)
        tasks = [self.download_and_process(item[0], item[1]) for item in media_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: List[Tuple[str, Path]] = []
        for r in results:
            if isinstance(r, Exception):
                continue
            if r is None:
                continue
            mtype, path = r
            if isinstance(mtype, str) and path is not None:
                processed.append((mtype, path))

        return processed

    async def send_media_group(
        self,
        chat_id: int,
        media_paths: List[Tuple[str, Path]],
        caption: Optional[str] = None,
        thread_id: Optional[int] = None
    ) -> bool:
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

            return True

        except Exception as e:
            logger.error(f"❌ Отправка: {e}")
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

            return True

        except Exception as e:
            logger.error(f"❌ Отправка: {e}")
            return False

    def cleanup(self):
        try:
            count = 0
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    file.unlink()
                    count += 1
        except Exception:
            pass

    def cleanup_specific(self, paths: List[Path]):
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

import asyncio
import logging
from pathlib import Path
from typing import List, Tuple, Optional
from PIL import Image
from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo, Message
import json
import uuid   # ← добавлено для уникальных имён файлов

logger = logging.getLogger(__name__)


def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)


class MediaProcessor:
    def __init__(self, temp_dir: str = "temp"):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(exist_ok=True)
        self.config = load_config()

    def cleanup_temp_files(self):
        """Очищает временные файлы"""
        try:
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    file.unlink()
        except Exception as e:
            logger.error(f"Ошибка очистки temp файлов: {e}")

    def cleanup_specific(self, file_paths: List[Path]):
        """Очищает конкретные файлы"""
        try:
            for file_path in file_paths:
                if file_path.exists():
                    file_path.unlink()
        except Exception as e:
            logger.error(f"Ошибка очистки файлов: {e}")

    async def download_file(self, file_id: str, bot: Bot) -> Optional[Path]:
        """Скачивает файл по file_id"""
        try:
            file = await bot.get_file(file_id)
            file_path = self.temp_dir / f"{file_id}_{uuid.uuid4().hex}"

            await bot.download_file(file.file_path, destination=file_path)
            return file_path
        except Exception as e:
            logger.error(f"Ошибка скачивания файла: {e}")
            return None

    def add_watermark(self, file_path: Path) -> Path:
        """Добавляет водяной знак на фото через PIL"""
        try:
            if not self.config['watermark']['enabled']:
                return file_path

            watermark_path = Path(self.config['watermark']['image_path'])
            if not watermark_path.exists():
                return file_path

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}{file_path.suffix}"

            with Image.open(file_path).convert("RGBA") as base_image:
                with Image.open(watermark_path).convert("RGBA") as watermark:
                    # Масштабирование ватермарки
                    scale_percent = self.config['watermark'].get('scale_image', self.config['watermark'].get('scale', 0.15))
                    new_width = int(base_image.width * scale_percent)
                    ratio = new_width / watermark.width
                    new_height = int(watermark.height * ratio)
                    watermark = watermark.resize((new_width, new_height), Image.Resampling.LANCZOS)

                    # Прозрачность
                    opacity = self.config['watermark'].get('opacity', 0.7)
                    alpha = watermark.split()[3]
                    alpha = alpha.point(lambda p: int(p * opacity))
                    watermark.putalpha(alpha)

                    # Позиция
                    padding = self.config['watermark'].get('padding', 20)
                    x = base_image.width - watermark.width - padding
                    y = base_image.height - watermark.height - padding

                    base_image.paste(watermark, (x, y), watermark)
                    base_image.convert("RGB").save(output_path, quality=95)

            return output_path

        except Exception as e:
            logger.error(f"Ошибка watermark на фото: {e}")
            return file_path

    async def add_video_watermark(self, file_path: Path) -> Path:
        """
        Добавляет водяной знак на видео через ffmpeg с ДИНАМИЧЕСКИМ размером
        (ватермарка масштабируется от размера ВИДЕО, а не от размера картинки ватермарки)
        """
        try:
            if not self.config['watermark']['enabled']:
                return file_path

            watermark_path = Path(self.config['watermark']['image_path'])
            if not watermark_path.exists():
                return file_path

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.mp4"

            # ВАЖНО: scale_video — доля от ширины видео (например 0.08 = 8% ширины видео)
            scale_percent = self.config['watermark'].get('scale_video', self.config['watermark'].get('scale', 0.15))
            padding = self.config['watermark'].get('padding', 20)
            opacity = self.config['watermark'].get('opacity', 0.7)

            cmd = [
                'ffmpeg',
                '-i', str(file_path),
                '-i', str(watermark_path),
                '-filter_complex',
                (
                    f'[1:v]format=rgba,colorchannelmixer=aa={opacity}[wmrgba];'
                    f'[wmrgba][0:v]scale2ref=w=ow*{scale_percent}:h=ow*{scale_percent}*ih/iw[wm][base];'
                    f'[base][wm]overlay=W-w-{padding}:H-h-{padding}:format=auto,format=yuv420p'
                ),
                '-c:a', 'copy',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-crf', '23',
                '-y',
                str(output_path)
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info(f"Видео watermark успешно применен с масштабом {scale_percent*100}%")
                return output_path
            else:
                logger.error(f"FFmpeg error: {stderr.decode()}")
                return file_path

        except Exception as e:
            logger.error(f"Ошибка watermark на видео: {e}")
            return file_path

    async def download_and_process(self, media_type: str, file_id: str, bot: Bot) -> Optional[Tuple[str, Path]]:
        """Скачивает и обрабатывает медиа файл"""
        file_path = await self.download_file(file_id, bot)
        if not file_path:
            return None

        try:
            # Определяем расширение файла
            file_extension = Path(file_path).suffix.lower()

            # Если файл без расширения, пытаемся определить по типу
            if not file_extension:
                if media_type == 'photo':
                    file_path_with_ext = file_path.with_suffix('.jpg')
                    file_path.rename(file_path_with_ext)
                    file_path = file_path_with_ext
                elif media_type == 'video':
                    file_path_with_ext = file_path.with_suffix('.mp4')
                    file_path.rename(file_path_with_ext)
                    file_path = file_path_with_ext

            # Применяем watermark
            if media_type == 'photo':
                processed_path = self.add_watermark(file_path)
            elif media_type == 'video':
                processed_path = await self.add_video_watermark(file_path)
            else:
                processed_path = file_path

            return (media_type, processed_path)

        except Exception as e:
            logger.error(f"Ошибка обработки медиа: {e}")
            return (media_type, file_path)

    async def process_batch(self, media_files: List[dict], bot: Bot = None) -> List[Tuple[str, Path]]:
        """Обрабатывает пачку медиа файлов"""
        processed_files = []

        for media in media_files:
            try:
                media_type = media.get('type')
                file_id = media.get('file_id')

                if bot and file_id:
                    processed = await self.download_and_process(media_type, file_id, bot)
                    if processed:
                        processed_files.append(processed)

            except Exception as e:
                logger.error(f"Ошибка обработки файла в batch: {e}")
                continue

        return processed_files

    async def send_single_media(
        self,
        chat_id: int,
        media_type: str,
        file_path: Path,
        caption: str,
        thread_id: Optional[int] = None,
        bot: Optional[Bot] = None
    ):
        """Отправляет один медиа файл"""
        try:
            if not bot:
                raise ValueError("Bot instance is required")

            input_file = FSInputFile(file_path)

            if media_type == 'photo':
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=input_file,
                    caption=caption,
                    parse_mode='HTML',
                    message_thread_id=thread_id
                )
            elif media_type == 'video':
                await bot.send_video(
                    chat_id=chat_id,
                    video=input_file,
                    caption=caption,
                    parse_mode='HTML',
                    message_thread_id=thread_id
                )

        except Exception as e:
            logger.error(f"Ошибка отправки медиа: {e}")

    async def send_media_group(
        self,
        chat_id: int,
        media_paths: List[Tuple[str, Path]],
        caption: str,
        thread_id: Optional[int] = None,
        bot: Optional[Bot] = None
    ):
        """Отправляет группу медиа файлов"""
        try:
            if not bot:
                raise ValueError("Bot instance is required")

            media_group = []

            for i, (media_type, file_path) in enumerate(media_paths):
                input_file = FSInputFile(file_path)

                if media_type == 'photo':
                    media_group.append(
                        InputMediaPhoto(
                            media=input_file,
                            caption=caption if i == 0 else None,
                            parse_mode='HTML' if i == 0 else None
                        )
                    )
                elif media_type == 'video':
                    media_group.append(
                        InputMediaVideo(
                            media=input_file,
                            caption=caption if i == 0 else None,
                            parse_mode='HTML' if i == 0 else None
                        )
                    )

            await bot.send_media_group(
                chat_id=chat_id,
                media=media_group,
                message_thread_id=thread_id
            )

        except Exception as e:
            logger.error(f"Ошибка отправки media group: {e}")

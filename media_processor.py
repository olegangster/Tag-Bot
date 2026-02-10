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
    """
    Процессор медиа с водяными знаками
    
    РЕШЕНИЕ ЗАДАЧИ 2: Динамический размер водяного знака
    """
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.temp_dir = Path("temp_media")
        self.temp_dir.mkdir(exist_ok=True)
        self.config = load_config()
    
    async def add_photo_watermark(self, file_path: Path) -> Path:
        """
        Добавляет водяной знак на фото с ДИНАМИЧЕСКИМ размером
        """
        try:
            if not self.config['watermark']['enabled']:
                return file_path
            
            watermark_path = Path(self.config['watermark']['image_path'])
            if not watermark_path.exists():
                logger.warning(f"Watermark не найден: {watermark_path}")
                return file_path
            
            image = Image.open(file_path).convert("RGBA")
            watermark = Image.open(watermark_path).convert("RGBA")
            
            image_width, image_height = image.size
            scale = self.config['watermark'].get('scale_photo', self.config['watermark'].get('scale', 0.15))
            wm_new_width = int(image_width * scale)
            wm_original_width, wm_original_height = watermark.size
            aspect_ratio = wm_original_height / wm_original_width
            wm_new_height = int(wm_new_width * aspect_ratio)
            
            watermark_resized = watermark.resize(
                (wm_new_width, wm_new_height), 
                Image.Resampling.LANCZOS
            )
            
            logger.info(
                f"Watermark масштабирован: {wm_original_width}x{wm_original_height} → "
                f"{wm_new_width}x{wm_new_height} для изображения {image_width}x{image_height}"
            )
            
            opacity = self.config['watermark'].get('opacity', 0.7)
            alpha = watermark_resized.split()[3]
            alpha = alpha.point(lambda p: int(p * opacity * 255 / 255))
            watermark_resized.putalpha(alpha)
            
            padding = self.config['watermark'].get('padding', 20)
            position = (
                image_width - wm_new_width - padding,
                image_height - wm_new_height - padding
            )
            
            transparent_layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
            transparent_layer.paste(watermark_resized, position)
            watermarked = Image.alpha_composite(image, transparent_layer)
            
            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.jpg"
            watermarked.convert("RGB").save(output_path, "JPEG", quality=95, optimize=True)
            
            return output_path
            
        except Exception as e:
            logger.error(f"Ошибка watermark на фото: {e}")
            return file_path
    
    async def add_video_watermark(self, file_path: Path) -> Path:
        """
        Добавляет водяной знак на видео через ffmpeg с ДИНАМИЧЕСКИМ размером
        """
        try:
            if not self.config['watermark']['enabled']:
                return file_path
            
            watermark_path = Path(self.config['watermark']['image_path'])
            if not watermark_path.exists():
                return file_path
            
            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.mp4"
            
            scale_percent = self.config['watermark'].get('scale_video', self.config['watermark'].get('scale', 0.15))
            padding = self.config['watermark'].get('padding', 20)
            
            cmd = [
                'ffmpeg',
                '-i', str(file_path),
                '-i', str(watermark_path),
                '-filter_complex',
                f'[1:v]scale=iw*{scale_percent}:-1[wm];'
                f'[0:v][wm]overlay=W-w-{padding}:H-h-{padding}:format=auto,format=yuv420p',
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
    
    async def download_and_process(self, media_type: str, file_id: str) -> Tuple[str, Path]:
        """Скачивает и обрабатывает один файл — теперь с уникальным именем"""
        try:
            file = await self.bot.get_file(file_id)
            file_bytes = await self.bot.download_file(file.file_path)
            
            ext = 'jpg' if media_type == 'photo' else 'mp4'
            # Уникальное имя файла — решает проблему дублирования из альбома
            temp_path = self.temp_dir / f"{uuid.uuid4().hex}.{ext}"
            
            with open(temp_path, 'wb') as f:
                f.write(file_bytes.read())
            
            if media_type == 'photo':
                processed_path = await self.add_photo_watermark(temp_path)
            else:
                processed_path = await self.add_video_watermark(temp_path)
            
            return media_type, processed_path
            
        except Exception as e:
            logger.error(f"Ошибка обработки медиа: {e}")
            return None, None
    
    async def process_batch(self, media_list: List[Tuple[str, str]]) -> List[Tuple[str, Path]]:
        """Параллельная обработка пакета медиа"""
        tasks = [
            self.download_and_process(media_type, file_id) 
            for media_type, file_id in media_list
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        processed = [
            (mtype, path) for mtype, path in results 
            if isinstance(mtype, str) and path is not None
        ]
        
        return processed
    
    async def send_media_group(self, chat_id: int, media_paths: List[Tuple[str, Path]], 
                              caption: str = None, thread_id: int = None) -> bool:
        """Отправляет медиа-группу"""
        try:
            media_group = []
            
            for i, (media_type, file_path) in enumerate(media_paths):
                if media_type == 'photo':
                    media = InputMediaPhoto(
                        media=FSInputFile(file_path),
                        caption=caption if i == 0 else None,
                        parse_mode='HTML'
                    )
                else:
                    media = InputMediaVideo(
                        media=FSInputFile(file_path),
                        caption=caption if i == 0 else None,
                        parse_mode='HTML'
                    )
                media_group.append(media)
            
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
            logger.error(f"Ошибка отправки медиа-группы: {e}")
            return False
    
    async def send_single_media(self, chat_id: int, media_type: str, 
                               file_path: Path, caption: str = None,
                               thread_id: int = None) -> bool:
        """Отправляет один медиафайл"""
        try:
            if media_type == 'photo':
                if thread_id:
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=FSInputFile(file_path),
                        caption=caption,
                        parse_mode='HTML',
                        message_thread_id=thread_id
                    )
                else:
                    await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=FSInputFile(file_path),
                        caption=caption,
                        parse_mode='HTML'
                    )
            else:
                if thread_id:
                    await self.bot.send_video(
                        chat_id=chat_id,
                        video=FSInputFile(file_path),
                        caption=caption,
                        parse_mode='HTML',
                        message_thread_id=thread_id
                    )
                else:
                    await self.bot.send_video(
                        chat_id=chat_id,
                        video=FSInputFile(file_path),
                        caption=caption,
                        parse_mode='HTML'
                    )
            return True
        except Exception as e:
            logger.error(f"Ошибка отправки одиночного медиа: {e}")
            return False
    
    def cleanup(self):
        """Очищает временные файлы"""
        try:
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    file.unlink()
        except Exception as e:
            logger.error(f"Ошибка очистки временных файлов: {e}")
    
    def cleanup_specific(self, paths: List[Path]):
        """Очищает конкретные файлы"""
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

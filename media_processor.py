import asyncio
import logging
import json
import uuid
from pathlib import Path
from typing import List, Tuple, Optional, Union
from PIL import Image, ImageDraw
from aiogram import Bot as AiogramBot
from aiogram.types import FSInputFile, InputMediaPhoto, InputMediaVideo

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent


def _find_ffmpeg_bin(name: str) -> str:
    """
    Находит полный путь к ffmpeg/ffprobe.
    На Render.com он лежит в /opt/render/project/.render/ffmpeg/
    """
    import shutil
    # 1. Render.com
    render = Path(f"/opt/render/project/.render/ffmpeg/{name}")
    if render.exists():
        logger.info(f"✅ {name} найден на Render: {render}")
        return str(render)
    # 2. Глобальный PATH
    found = shutil.which(name)
    if found:
        return found
    # 3. Рядом со скриптом
    local = BASE_DIR / name
    if local.exists():
        return str(local)
    local_exe = BASE_DIR / f"{name}.exe"
    if local_exe.exists():
        return str(local_exe)
    logger.error(f"❌ {name} не найден! Установите ffmpeg или проверьте render-build.sh")
    return name


FFMPEG = _find_ffmpeg_bin("ffmpeg")
FFPROBE = _find_ffmpeg_bin("ffprobe")


def load_config() -> dict:
    cfg_path = BASE_DIR / "config.json"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


class MediaProcessor:
    """
    ✅ ИСПРАВЛЕНО:
    - Watermark на фото: исправлена обработка RGBA/RGB, правильное сохранение
    - Watermark на видео: исправлен filter_complex, таймаут, обработка ошибок
    - Правильная очистка временных файлов
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
        
        # Перезагружаем конфиг каждый раз чтобы подхватить изменения
        try:
            self.config = load_config()
        except Exception as e:
            logger.error(f"Ошибка загрузки config: {e}")
            self.config = {}

    def _reload_config(self):
        """Перезагружает конфиг (для актуальных настроек watermark)"""
        try:
            self.config = load_config()
        except Exception:
            pass

    async def add_photo_watermark(self, file_path: Path) -> Path:
        """
        ✅ ИСПРАВЛЕНО:
        - Правильная обработка всех форматов изображений
        - Корректное наложение watermark с прозрачностью
        - Надёжное сохранение результата
        """
        self._reload_config()
        original_path = file_path
        
        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                logger.warning(f"⚠️ Watermark файл не найден: {watermark_path}")
                return file_path

            # Открываем исходное изображение
            with Image.open(file_path) as img_orig:
                # Конвертируем в RGBA для работы с прозрачностью
                image = img_orig.convert("RGBA")
            
            image_w, image_h = image.size
            
            if image_w == 0 or image_h == 0:
                logger.warning("⚠️ Изображение имеет нулевой размер")
                return file_path

            # Открываем watermark
            with Image.open(watermark_path) as wm_orig:
                watermark = wm_orig.convert("RGBA")

            scale = float(wm_cfg.get("scale_photo", 0.15))
            scale = max(0.03, min(scale, 0.5))

            # Рассчитываем размер watermark
            new_w = max(1, int(image_w * scale))
            wm_w, wm_h = watermark.size
            if wm_w == 0:
                return file_path
            aspect = wm_h / wm_w
            new_h = max(1, int(new_w * aspect))

            # Масштабируем watermark
            watermark = watermark.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Применяем прозрачность
            opacity = float(wm_cfg.get("opacity", 0.75))
            opacity = max(0.0, min(opacity, 1.0))

            # Разбиваем на каналы и изменяем альфа
            r, g, b, a = watermark.split()
            a = a.point(lambda p: int(p * opacity))
            watermark = Image.merge("RGBA", (r, g, b, a))

            # Определяем позицию
            padding = int(wm_cfg.get("padding", 15))
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

            # Создаём слой для наложения
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            layer.paste(watermark, (x, y), watermark)
            
            # Накладываем watermark на изображение
            result = Image.alpha_composite(image, layer)

            # Сохраняем результат
            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.jpg"
            result.convert("RGB").save(str(output_path), "JPEG", quality=92, optimize=True)
            
            if not output_path.exists() or output_path.stat().st_size == 0:
                logger.error("❌ Файл watermark не был создан корректно")
                return file_path

            logger.info(f"✅ Фото watermark применён ({new_w}x{new_h} @ {opacity:.0%} opacity)")
            return output_path

        except Exception as e:
            logger.error(f"❌ Watermark фото: {e}", exc_info=True)
            return original_path

    async def add_video_watermark(self, file_path: Path) -> Path:
        """
        ✅ ИСПРАВЛЕНО:
        - Корректный filter_complex без синтаксических ошибок
        - setsar=1 для исправления сплющивания
        - Правильная обработка таймаутов
        - Fallback на оригинал при любой ошибке
        """
        self._reload_config()
        original_path = file_path

        try:
            wm_cfg = self.config.get("watermark", {})
            if not wm_cfg.get("enabled", False):
                return file_path

            watermark_path = Path(wm_cfg.get("image_path", "watermark.png"))
            if not watermark_path.is_absolute():
                watermark_path = BASE_DIR / watermark_path

            if not watermark_path.exists():
                logger.warning(f"⚠️ Watermark файл не найден: {watermark_path}")
                return file_path

            # Проверяем наличие ffmpeg
            import shutil
            ffmpeg_bin = FFMPEG
            if not Path(ffmpeg_bin).exists() and not shutil.which(ffmpeg_bin):
                logger.error("❌ ffmpeg не найден — watermark на видео недоступен")
                return file_path

            output_path = self.temp_dir / f"wm_{uuid.uuid4().hex}.mp4"

            scale = float(wm_cfg.get("scale_video", 0.07))
            scale = max(0.03, min(scale, 0.3))

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

            # Определяем длительность видео
            duration_sec = await self._get_video_duration(file_path)

            # Параметры в зависимости от длины видео
            if duration_sec > 90:
                max_width = 480
                timeout_sec = 300.0
                logger.info(f"🎬 Длинное видео ({duration_sec:.0f}с) — 480px, timeout 5мин")
            else:
                max_width = 720
                timeout_sec = 180.0
                logger.info(f"🎬 Видео ({duration_sec:.0f}с) — 720px, timeout 3мин")

            # ✅ ИСПРАВЛЕНО: корректный filter_complex
            # scale с сохранением aspect ratio + setsar=1 для исправления сплющивания
            filter_complex = (
                f"[0:v]"
                f"scale='if(gt(iw,ih),min({max_width},iw),-2)':'if(gt(iw,ih),-2,min({max_width},ih))',"
                f"setsar=1"
                f"[scaled];"
                f"[1:v]"
                f"scale=iw*{scale}:-1,"
                f"format=rgba,"
                f"colorchannelmixer=aa={opacity:.4f}"
                f"[wm];"
                f"[scaled][wm]overlay={overlay_pos}:format=auto[out]"
            )

            cmd = [
                ffmpeg_bin,
                "-hide_banner", "-loglevel", "error",
                "-y",
                "-i", str(file_path),
                "-i", str(watermark_path),
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "23",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(output_path),
            ]

            logger.info(f"🎬 FFmpeg watermark: scale={scale}, opacity={opacity:.2f}, pos={pos}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_sec
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                logger.error(f"❌ FFmpeg таймаут ({timeout_sec:.0f}с) — используем оригинал")
                # Удаляем незавершённый файл
                if output_path.exists():
                    output_path.unlink(missing_ok=True)
                return original_path

            if process.returncode == 0:
                if output_path.exists() and output_path.stat().st_size > 0:
                    logger.info(f"✅ Видео watermark применён")
                    return output_path
                else:
                    logger.error("❌ FFmpeg завершился успешно но выходной файл пуст")
                    return original_path
            else:
                stderr_text = stderr.decode(errors='replace')[-500:] if stderr else ""
                logger.error(f"❌ FFmpeg returncode={process.returncode}: {stderr_text}")
                if output_path.exists():
                    output_path.unlink(missing_ok=True)
                return original_path

        except FileNotFoundError:
            logger.error("❌ ffmpeg не установлен")
            return original_path
        except Exception as e:
            logger.error(f"❌ Watermark видео: {e}", exc_info=True)
            return original_path

    async def _get_video_duration(self, file_path: Path) -> float:
        """Получает длительность видео через ffprobe. Возвращает 0 при ошибке."""
        try:
            import shutil
            ffprobe_bin = FFPROBE
            if not Path(ffprobe_bin).exists() and not shutil.which(ffprobe_bin):
                return 0.0

            cmd = [
                ffprobe_bin,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path)
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
                text = stdout.decode(errors='replace').strip()
                if text:
                    return float(text)
                return 0.0
            except (asyncio.TimeoutError, ValueError):
                return 0.0
        except Exception:
            return 0.0

    async def download_and_process(self, media_type: str, file_id: str) -> Optional[Tuple[str, Path]]:
        """Скачивает и обрабатывает медиафайл"""
        temp_path = None
        try:
            if not self.bot:
                raise RuntimeError("MediaProcessor: bot не передан")

            file = await self.bot.get_file(file_id)
            file_bytes = await self.bot.download_file(file.file_path)

            ext = "jpg" if media_type == "photo" else "mp4"
            temp_path = self.temp_dir / f"orig_{uuid.uuid4().hex}.{ext}"

            content = file_bytes.read() if hasattr(file_bytes, 'read') else file_bytes
            with open(temp_path, "wb") as f:
                f.write(content)

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                logger.error(f"❌ Скачанный файл пуст или не существует: {temp_path}")
                return None

            logger.info(f"✅ Скачан {media_type}: {temp_path.name} ({temp_path.stat().st_size} bytes)")

            if media_type == "photo":
                processed = await self.add_photo_watermark(temp_path)
            else:
                processed = await self.add_video_watermark(temp_path)

            # Удаляем оригинал если он отличается от обработанного
            if processed != temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

            return media_type, processed

        except Exception as e:
            logger.error(f"❌ Обработка {media_type}: {e}", exc_info=True)
            # Удаляем временный файл при ошибке
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            return None

    async def process_batch(self, media_list: List[Tuple]) -> List[Tuple[str, Path]]:
        """Обрабатывает пакет медиафайлов параллельно"""
        if not media_list:
            return []
            
        tasks = [self.download_and_process(item[0], item[1]) for item in media_list]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: List[Tuple[str, Path]] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(f"❌ Ошибка обработки файла {i}: {r}")
                continue
            if r is None:
                logger.warning(f"⚠️ Файл {i} вернул None")
                continue
            mtype, path = r
            if isinstance(mtype, str) and path is not None and Path(path).exists():
                processed.append((mtype, path))
            else:
                logger.warning(f"⚠️ Файл {i}: некорректный результат {r}")

        logger.info(f"✅ Обработано {len(processed)}/{len(media_list)} файлов")
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
                file_path = Path(file_path)
                if not file_path.exists():
                    logger.error(f"❌ Файл не существует: {file_path}")
                    continue
                    
                if media_type == "photo":
                    media_group.append(
                        InputMediaPhoto(
                            media=FSInputFile(file_path),
                            caption=caption if i == 0 else None,
                            parse_mode="HTML" if (i == 0 and caption) else None
                        )
                    )
                else:
                    media_group.append(
                        InputMediaVideo(
                            media=FSInputFile(file_path),
                            caption=caption if i == 0 else None,
                            parse_mode="HTML" if (i == 0 and caption) else None
                        )
                    )

            if not media_group:
                logger.error("❌ Нет файлов для отправки в медиагруппе")
                return False

            kwargs = dict(chat_id=chat_id, media=media_group)
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id

            await self.bot.send_media_group(**kwargs)
            logger.info(f"✅ Медиагруппа отправлена: {len(media_group)} файлов")
            return True

        except Exception as e:
            logger.error(f"❌ Отправка медиагруппы: {e}", exc_info=True)
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

            file_path = Path(file_path)
            if not file_path.exists():
                logger.error(f"❌ Файл не существует: {file_path}")
                return False

            kwargs = dict(
                chat_id=chat_id,
                caption=caption,
                parse_mode="HTML" if caption else None,
            )
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id

            if media_type == "photo":
                await self.bot.send_photo(
                    photo=FSInputFile(file_path),
                    **kwargs
                )
            else:
                await self.bot.send_video(
                    video=FSInputFile(file_path),
                    **kwargs
                )

            logger.info(f"✅ Одиночный {media_type} отправлен")
            return True

        except Exception as e:
            logger.error(f"❌ Отправка {media_type}: {e}", exc_info=True)
            return False

    def cleanup(self):
        """Очищает все временные файлы"""
        try:
            count = 0
            for file in self.temp_dir.glob("*"):
                if file.is_file():
                    file.unlink(missing_ok=True)
                    count += 1
            if count > 0:
                logger.info(f"🗑️ Очищено {count} временных файлов")
        except Exception as e:
            logger.error(f"Ошибка очистки: {e}")

    def cleanup_specific(self, paths: List[Path]):
        """Удаляет конкретные файлы"""
        for path in paths:
            try:
                p = Path(path)
                if p.exists():
                    p.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"Не удалось удалить {path}: {e}")

"""
محوّلات الوسائط:
  - بصمة صوتية  ←→  MP3
  - صورة         →   ملصق WebP + PNG 512×512
  - ملصق         →   صورة
  - فيديو        →   MP3 + ملصق متحرك WebM
  - فيديو دائري →   فيديو عادي + بصمة صوتية
  - ملف (Office) →   PDF
"""

import os
import subprocess
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from config import MAX_FILE_SIZE, SOFFICE, PDF_SUPPORTED_MIME, PDF_SUPPORTED_EXT

logger = logging.getLogger(__name__)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بصمة صوتية (voice) → MP3"""
    msg = await update.message.reply_text("🔄 جاري التحويل إلى MP3…")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            vf       = await update.message.voice.get_file()
            ogg_path = Path(tmpdir) / "voice.oga"
            await vf.download_to_drive(str(ogg_path))

            mp3_path = Path(tmpdir) / "voice.mp3"
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(ogg_path),
                 "-c:a", "libmp3lame", "-q:a", "2", str(mp3_path)],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0 or not mp3_path.exists():
                await msg.edit_text("❌ فشل التحويل إلى MP3.")
                return

            await msg.edit_text("📤 جاري الإرسال…")
            with open(mp3_path, "rb") as f:
                await context.bot.send_audio(
                    chat_id=update.message.chat_id, audio=f,
                    title="صوت", caption="🎵 ملف MP3",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_voice error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_audio_to_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """MP3 / صوت → بصمة صوتية (OGG Opus)"""
    msg = await update.message.reply_text("🔄 جاري التحويل إلى بصمة…")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_obj = update.message.audio
            af        = await audio_obj.get_file()
            ext       = Path(audio_obj.file_name or "audio.mp3").suffix or ".mp3"
            dl_path   = Path(tmpdir) / f"audio{ext}"
            await af.download_to_drive(str(dl_path))

            ogg_path = Path(tmpdir) / "voice.ogg"
            r = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(dl_path),
                    "-c:a", "libopus", "-b:a", "64k",
                    "-vbr", "on", "-compression_level", "10",
                    str(ogg_path),
                ],
                capture_output=True, timeout=60,
            )
            if r.returncode != 0 or not ogg_path.exists():
                await msg.edit_text("❌ فشل التحويل إلى بصمة.")
                return

            await msg.edit_text("📤 جاري الإرسال…")
            with open(ogg_path, "rb") as f:
                await context.bot.send_voice(
                    chat_id=update.message.chat_id, voice=f,
                    caption="🎙 بصمة صوتية",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_audio_to_voice error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_photo_to_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """صورة → ملصق WebP + PNG 512×512"""
    msg = await update.message.reply_text("🔄 جاري تحويل الصورة إلى ملصق…")
    try:
        from PIL import Image as PILImage
        with tempfile.TemporaryDirectory() as tmpdir:
            photo = update.message.photo[-1]
            pf    = await photo.get_file()
            src   = Path(tmpdir) / "photo.jpg"
            await pf.download_to_drive(str(src))

            img    = PILImage.open(src).convert("RGBA")
            img.thumbnail((512, 512), PILImage.LANCZOS)
            canvas = PILImage.new("RGBA", (512, 512), (0, 0, 0, 0))
            x      = (512 - img.width)  // 2
            y      = (512 - img.height) // 2
            canvas.paste(img, (x, y))

            webp_path = Path(tmpdir) / "sticker.webp"
            png_path  = Path(tmpdir) / "sticker_512.png"
            canvas.save(str(webp_path), "WebP", lossless=True)
            canvas.save(str(png_path),  "PNG")

            await msg.edit_text("📤 جاري الإرسال…")
            with open(webp_path, "rb") as f:
                await context.bot.send_sticker(
                    chat_id=update.message.chat_id, sticker=f
                )
            with open(png_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.message.chat_id, document=f,
                    filename="sticker_512.png", caption="🖼 PNG 512×512",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_photo_to_sticker error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_sticker_to_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ملصق (WebP / WebM) → صورة JPEG"""
    msg = await update.message.reply_text("🔄 جاري تحويل الملصق إلى صورة…")
    try:
        sticker = update.message.sticker
        sf      = await sticker.get_file()
        with tempfile.TemporaryDirectory() as tmpdir:
            if sticker.is_animated:
                await msg.edit_text("⚠️ الملصقات المتحركة (TGS/Lottie) غير مدعومة حالياً.")
                return
            elif sticker.is_video:
                webm_path = Path(tmpdir) / "sticker.webm"
                await sf.download_to_drive(str(webm_path))
                jpg_path = Path(tmpdir) / "sticker.jpg"
                r = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(webm_path), "-vframes", "1", str(jpg_path)],
                    capture_output=True, timeout=30,
                )
                if r.returncode != 0 or not jpg_path.exists():
                    await msg.edit_text("❌ فشل تحويل الملصق المتحرك.")
                    return
                send_path = jpg_path
            else:
                from PIL import Image as PILImage
                webp_path = Path(tmpdir) / "sticker.webp"
                await sf.download_to_drive(str(webp_path))
                jpg_path = Path(tmpdir) / "sticker.jpg"
                PILImage.open(str(webp_path)).convert("RGB").save(
                    str(jpg_path), "JPEG", quality=95
                )
                send_path = jpg_path

            await msg.edit_text("📤 جاري الإرسال…")
            with open(send_path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=update.message.chat_id, photo=f,
                    caption="🖼 صورة من الملصق",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_sticker_to_photo error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_video_convert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فيديو مُرسَل → صوت MP3 + ملصق متحرك WebM"""
    msg = await update.message.reply_text("⏳ جاري معالجة الفيديو…")
    try:
        video = update.message.video
        vf    = await video.get_file()
        with tempfile.TemporaryDirectory() as tmpdir:
            vid_path = Path(tmpdir) / "video.mp4"
            await vf.download_to_drive(str(vid_path))

            await msg.edit_text("🎵 جاري استخراج الصوت…")
            mp3_path = Path(tmpdir) / "audio.mp3"
            r1 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(vid_path), "-vn",
                 "-c:a", "libmp3lame", "-q:a", "2", str(mp3_path)],
                capture_output=True, timeout=120,
            )

            await msg.edit_text("🎬 جاري تحويل إلى ملصق متحرك…")
            webm_path = Path(tmpdir) / "sticker.webm"
            r2 = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(vid_path), "-t", "8",
                    "-vf",
                    "scale=512:512:force_original_aspect_ratio=decrease,"
                    "pad=512:512:(ow-iw)/2:(oh-ih)/2:color=black@0",
                    "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "40",
                    "-an", "-r", "24",
                    str(webm_path),
                ],
                capture_output=True, timeout=120,
            )

            await msg.edit_text("📤 جاري الإرسال…")
            sent = False

            if r1.returncode == 0 and mp3_path.exists() and mp3_path.stat().st_size > 0:
                if mp3_path.stat().st_size <= MAX_FILE_SIZE:
                    with open(mp3_path, "rb") as f:
                        await context.bot.send_audio(
                            chat_id=update.message.chat_id, audio=f,
                            title=video.file_name or "صوت",
                            caption="🎵 الصوت المستخرج",
                        )
                    sent = True

            if r2.returncode == 0 and webm_path.exists() and webm_path.stat().st_size > 0:
                try:
                    with open(webm_path, "rb") as f:
                        await context.bot.send_sticker(
                            chat_id=update.message.chat_id, sticker=f,
                        )
                except Exception:
                    with open(webm_path, "rb") as f:
                        await context.bot.send_animation(
                            chat_id=update.message.chat_id, animation=f,
                            caption="🎬 ملصق متحرك",
                        )
                sent = True

            if sent:
                await msg.delete()
            else:
                await msg.edit_text("❌ فشل تحويل الفيديو.")
    except Exception as e:
        logger.error(f"handle_video_convert error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء تحويل الفيديو.")


async def handle_video_note_convert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فيديو دائري (video_note) → فيديو عادي + بصمة صوتية"""
    msg = await update.message.reply_text("🔄 جاري معالجة الفيديو الدائري…")
    try:
        vn = update.message.video_note
        vf = await vn.get_file()
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = Path(tmpdir) / "vidnote.mp4"
            await vf.download_to_drive(str(src_path))

            await msg.edit_text("🎬 جاري التحويل إلى فيديو عادي…")
            mp4_path = Path(tmpdir) / "video.mp4"
            r1 = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(src_path),
                    "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", str(mp4_path),
                ],
                capture_output=True, timeout=60,
            )

            ogg_path = Path(tmpdir) / "voice.ogg"
            r2 = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src_path), "-vn",
                 "-c:a", "libopus", "-b:a", "64k", str(ogg_path)],
                capture_output=True, timeout=60,
            )

            await msg.edit_text("📤 جاري الإرسال…")
            sent = False

            if r1.returncode == 0 and mp4_path.exists() and mp4_path.stat().st_size <= MAX_FILE_SIZE:
                with open(mp4_path, "rb") as f:
                    await context.bot.send_video(
                        chat_id=update.message.chat_id, video=f,
                        supports_streaming=True, caption="📹 فيديو عادي",
                    )
                sent = True

            if r2.returncode == 0 and ogg_path.exists() and ogg_path.stat().st_size > 0:
                with open(ogg_path, "rb") as f:
                    await context.bot.send_voice(
                        chat_id=update.message.chat_id, voice=f,
                        caption="🎙 البصمة الصوتية",
                    )
                sent = True

            if sent:
                await msg.delete()
            else:
                await msg.edit_text("❌ فشل تحويل الفيديو الدائري.")
    except Exception as e:
        logger.error(f"handle_video_note_convert error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء التحويل.")


async def handle_document_to_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ملف (Word / Excel / PowerPoint / TXT / HTML / RTF / ODF) → PDF"""
    doc = update.message.document
    if not doc:
        return

    mime  = doc.mime_type or ""
    fname = doc.file_name or "file"
    ext   = Path(fname).suffix.lower()

    if mime == "application/pdf" or ext == ".pdf":
        await update.message.reply_text("ℹ️ الملف بالفعل بصيغة PDF.")
        return

    if mime not in PDF_SUPPORTED_MIME and ext not in PDF_SUPPORTED_EXT:
        await update.message.reply_text(
            "⚠️ نوع الملف غير مدعوم للتحويل إلى PDF.\n"
            "الأنواع المدعومة: Word · Excel · PowerPoint · TXT · HTML · RTF · ODF"
        )
        return

    msg = await update.message.reply_text("🔄 جاري تحويل الملف إلى PDF…")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            df      = await doc.get_file()
            dl_path = Path(tmpdir) / fname
            await df.download_to_drive(str(dl_path))

            r = subprocess.run(
                [SOFFICE, "--headless", "--convert-to", "pdf",
                 "--outdir", tmpdir, str(dl_path)],
                capture_output=True, timeout=120,
                env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
            )
            if r.returncode != 0:
                logger.error(f"soffice error: {r.stderr.decode()[:300]}")
                await msg.edit_text("❌ فشل تحويل الملف إلى PDF.")
                return

            pdf_name = Path(fname).stem + ".pdf"
            pdf_path = Path(tmpdir) / pdf_name
            if not pdf_path.exists():
                pdfs = list(Path(tmpdir).glob("*.pdf"))
                if pdfs:
                    pdf_path = pdfs[0]
                    pdf_name = pdf_path.name
                else:
                    await msg.edit_text("❌ لم يتم إنشاء ملف PDF.")
                    return

            if pdf_path.stat().st_size > MAX_FILE_SIZE:
                await msg.edit_text("⚠️ حجم PDF يتجاوز حد 50 ميجابايت.")
                return

            await msg.edit_text("📤 جاري الإرسال…")
            with open(pdf_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.message.chat_id, document=f,
                    filename=pdf_name, caption=f"📄 {pdf_name}",
                )
            await msg.delete()
    except Exception as e:
        logger.error(f"handle_document_to_pdf error: {e}")
        await msg.edit_text("❌ حدث خطأ أثناء تحويل الملف.")

import re
import os

# ── ثوابت الأحجام والمهل ──────────────────────────────────────────────────────
MAX_FILE_SIZE = 50 * 1024 * 1024   # 50 MB
SESSION_TTL   = 3600               # ساعة واحدة

# ── بيانات Supabase ───────────────────────────────────────────────────────────
SUPABASE_URL = "https://ocjytwphvzrhoxmmgujh.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9janl0d3BodnpyaG94bW1ndWpoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODAzMzY2MTUsImV4cCI6MjA5NTkxMjYxNX0.SPp__zCpFSIEE56oFItvFqJar2FxNF5e-t2HNJozNxs"

# ── بيانات المطور والقنوات ────────────────────────────────────────────────────
DEVELOPER_ID       = 8055247329
CHROMA_CHANNEL_ID  = -1003978029981
NATURE_CHANNEL_ID  = -1003904589422

# ── روابط APIs ────────────────────────────────────────────────────────────────
TIKWM_API             = "https://tikwm.com/api/"
PINTEREST_WIDGETS_API = "https://widgets.pinterest.com/v3/pidgets/pins/info/?pin_ids={}"

# ── sessionid إنستقرام الثابت ─────────────────────────────────────────────────
IG_SESSIONID = "70097632584%3ATQuAgk17CobNm9%3A18%3AAYiMNf-GmQbcVFu0gr15HLSHWVgBK0vaViLsX6viuQ"

# ── تعبيرات الكشف عن الروابط ─────────────────────────────────────────────────
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.|m\.|music\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"[\w\-]+(\?[\w=&\-%]*)?"
)
TIKTOK_REGEX = re.compile(
    r"https?://(www\.|vm\.|vt\.|m\.)?tiktok\.com/[\w@/\-\?=&\.]+"
    r"|https?://vt\.tiktok\.com/[\w]+"
    r"|https?://vm\.tiktok\.com/[\w]+"
)
INSTAGRAM_REGEX = re.compile(
    r"https?://(www\.)?instagram\.com/(p|reel|tv|stories)/[\w\-]+(/?)(\?[\w=&\-%]*)?"
)
PINTEREST_REGEX = re.compile(
    r"https?://(?:(?:www\.|[a-z]{2}\.)?pinterest\.(?:com|[a-z]{2,3})/pin/[\d]+"
    r"|pin\.it/[\w]+)(\?[\w=&\-%]*)?"
)

# ── معلومات المنصات ───────────────────────────────────────────────────────────
PLATFORM_INFO = {
    "youtube":   {"name": "يوتيوب",   "icon": "▶️",  "supports_audio": True},
    "tiktok":    {"name": "تيك توك",  "icon": "🎵",  "supports_audio": False},
    "instagram": {"name": "إنستقرام", "icon": "📸",  "supports_audio": False},
    "pinterest": {"name": "بينترست",  "icon": "📌",  "supports_audio": False},
}

# ── رسائل الانتظار المتناوبة ──────────────────────────────────────────────────
WAIT_MSGS = [
    "⏳ جاري التحميل…",
    "📡 يتم جلب الفيديو…",
    "🔄 يتم المعالجة…",
    "🚀 اللمسات الأخيرة…",
    "📦 يتم تجهيز الملف…",
]

# ── جلسات المستخدمين (في الذاكرة) ────────────────────────────────────────────
SESSIONS: dict[str, dict] = {}

# ── مسار LibreOffice ──────────────────────────────────────────────────────────
SOFFICE = os.path.expanduser("~/.nix-profile/bin/soffice")

# ── أنواع الملفات المدعومة للتحويل إلى PDF ────────────────────────────────────
PDF_SUPPORTED_MIME = {
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/html", "application/rtf",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "text/csv",
}
PDF_SUPPORTED_EXT = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".html", ".htm", ".rtf", ".odt", ".ods", ".odp", ".csv",
}

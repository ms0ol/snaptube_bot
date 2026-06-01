# متطلبات مشروع Snaptube Bot

---

## 1. 🖥️ المنصة

**Replit** — يجب إنشاء المشروع على [replit.com](https://replit.com) واختيار قالب **Python**.

---

## 2. 🐍 إصدار Python

```
Python 3.11 أو أحدث
```

---

## 3. 📦 مكتبات Python (packages)

تُنصَّب عبر `pip install` أو تُكتب في `pyproject.toml`:

| المكتبة | الإصدار الأدنى | الوظيفة |
|---|---|---|
| `httpx` | `>=0.28.1` | طلبات HTTP لـ tikwm API وتحميل الملفات |
| `Pillow` | `>=11.0.0` | معالجة الصور (ملصقات، PNG 512×512) |
| `python-telegram-bot[webhooks]` | `>=22.7` | مكتبة تيليجرام الأساسية + دعم Webhook |
| `yt-dlp` | `>=2026.3.17` | تحميل فيديوهات يوتيوب/إنستقرام/بينترست |

أمر التنصيب:
```bash
pip install httpx "Pillow>=11.0.0" "python-telegram-bot[webhooks]>=22.7" "yt-dlp>=2026.3.17"
```

أو يُكتب ملف `pyproject.toml` هكذا:
```toml
[project]
name = "python-template"
version = "0.1.0"
description = ""
authors = ["Your Name <you@example.com>"]
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.28.1",
    "Pillow>=11.0.0",
    "python-telegram-bot[webhooks]>=22.7",
    "yt-dlp>=2026.3.17",
]
```

---

## 4. 🛠️ برامج النظام (System dependencies)

### ffmpeg — عبر ملف `replit.nix`

يُكتب ملف `replit.nix` هكذا:
```nix
{pkgs}: {
  deps = [
    pkgs.ffmpeg
  ];
}
```

### libreoffice — يُنصَّب يدوياً مرة واحدة

افتح Shell في Replit ونفّذ:
```bash
nix-env -iA nixpkgs.libreoffice
```

| البرنامج | الوظيفة |
|---|---|
| `ffmpeg` | تحويل الصوت والفيديو (MP3، H.264، WebM، OGG Opus) |
| `libreoffice` | تحويل الملفات (Word/Excel/PowerPoint/TXT) إلى PDF |

---

## 5. 🔑 المتغيرات البيئية (Secrets)

متغير **واحد إلزامي** يُحفظ في **Replit Secrets** (لا يُكتب في الكود أبداً):

| الاسم | القيمة | كيفية الحصول عليه |
|---|---|---|
| `BOT_TOKEN` | توكن البوت | من @BotFather على تيليجرام |

---

## 6. 🤖 إنشاء البوت على تيليجرام

1. افتح تيليجرام وابحث عن **@BotFather**
2. أرسل `/newbot`
3. اختر اسماً للبوت (مثال: `Snaptube Bot`)
4. اختر username ينتهي بـ `bot` (مثال: `snaptubedownloader_bot`)
5. ستحصل على **BOT_TOKEN** بهذا الشكل:
   ```
   8692645411:AAEcwqp-_bVWLRA3oIHo_AqXU9RTZCOIv8
   ```
6. انسخه واحفظه في **Replit Secrets** باسم `BOT_TOKEN`

---

## 7. 📁 ملفات المشروع

| الملف | الوصف |
|---|---|
| `main.py` | كل الكود الرئيسي للبوت (~1300 سطر) |
| `pyproject.toml` | قائمة مكتبات Python |
| `replit.nix` | برامج النظام (ffmpeg) |
| `.replit` | إعدادات Replit (المنفذ، طريقة التشغيل، الـ workflow) |

---

## 8. ⚙️ إعدادات Replit (`.replit`)

```toml
entrypoint = "main.py"
modules = ["python-3.11", "nodejs-20"]

[nix]
channel = "stable-25_05"

[deployment]
run = ["python3", "main.py"]
deploymentTarget = "cloudrun"

[[workflows.workflow]]
name = "Start application"
author = "agent"

[[workflows.workflow.tasks]]
task = "shell.exec"
args = "python main.py"

[[ports]]
localPort = 8080
externalPort = 80
```

المنفذ المستخدم: **8080** داخلياً، **80** خارجياً.

---

## 9. 🌐 الـ APIs المستخدمة (مجانية — بدون مفاتيح)

| الـ API | الرابط | المنصة |
|---|---|---|
| tikwm | `https://tikwm.com/api/` | تيك توك |
| Pinterest Widgets | `https://widgets.pinterest.com/v3/pidgets/pins/info/?pin_ids={id}` | بينترست |
| yt-dlp (مدمج في المكتبة) | — | يوتيوب + إنستقرام + بينترست |

---

## 10. 📝 ملاحظات مهمة

- **sessionid إنستقرام:** مُضمَّن مباشرة في الكود داخل المتغير `IG_SESSIONID`. إذا انتهت صلاحيته مستقبلاً يجب تحديثه بـ sessionid جديد من حساب إنستقرام.
- **حد الملفات:** 50 ميجابايت كحد أقصى لكل ملف يُرسَل عبر تيليجرام (قيود تيليجرام للبوتات).
- **الـ Webhook:** البوت يعمل بنظام Webhook (لا polling) لتجنب تعارض الاتصالات.

---

## 11. 📋 خطوات إعادة البناء من الصفر

```
1. أنشئ مشروع Python جديد على Replit
2. أنشئ بوتاً جديداً عبر @BotFather على تيليجرام
3. أضف BOT_TOKEN في Replit Secrets
4. انسخ ملف main.py كاملاً إلى المشروع
5. اكتب pyproject.toml بالمكتبات الأربعة
6. اكتب replit.nix لإضافة ffmpeg
7. افتح Shell ونفّذ: nix-env -iA nixpkgs.libreoffice
8. اضغط Run لتشغيل البوت
```

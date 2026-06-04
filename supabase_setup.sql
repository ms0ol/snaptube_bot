-- ══════════════════════════════════════════════════════════════════════════════
-- جدول videos — نظام إدارة محتوى قنوات تيليجرام
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.videos (
    id                 BIGSERIAL PRIMARY KEY,
    video_id           TEXT        NOT NULL UNIQUE,
    file_id            TEXT        NOT NULL,
    type               TEXT        NOT NULL
                           CHECK (type IN ('chroma', 'nature')),
    description        TEXT        NOT NULL DEFAULT '',
    channel_message_id BIGINT      NOT NULL,
    link               TEXT        NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_videos_type       ON public.videos (type);
CREATE INDEX IF NOT EXISTS idx_videos_created_at ON public.videos (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_video_id   ON public.videos (video_id);

COMMENT ON TABLE  public.videos                    IS 'فيديوهات المطور المنشورة في قنوات تيليجرام';
COMMENT ON COLUMN public.videos.video_id           IS 'file_unique_id الخاص بتيليجرام — مفتاح فريد';
COMMENT ON COLUMN public.videos.file_id            IS 'file_id لإعادة إرسال الفيديو مستقبلاً';
COMMENT ON COLUMN public.videos.type               IS 'نوع القناة: chroma أو nature';
COMMENT ON COLUMN public.videos.description        IS 'الوصف المرافق للفيديو في القناة';
COMMENT ON COLUMN public.videos.channel_message_id IS 'message_id داخل القناة';
COMMENT ON COLUMN public.videos.link               IS 'رابط t.me المباشر للرسالة';

ALTER TABLE public.videos ENABLE ROW LEVEL SECURITY;

CREATE POLICY "bot_insert"
    ON public.videos FOR INSERT TO anon
    WITH CHECK (true);

CREATE POLICY "bot_select"
    ON public.videos FOR SELECT TO anon
    USING (true);

CREATE POLICY "admin_all"
    ON public.videos FOR ALL TO service_role
    USING (true) WITH CHECK (true);

# 🎬 AR-Clipper

> أداة إعادة توظيف البودكاست بالذكاء الاصطناعي — **محلية، مجانية، ومفتوحة المصدر بالكامل**، مع تركيز حقيقي على جودة اللغة العربية.

تحوّل بودكاست طويلاً إلى مقاطع عمودية (9:16) مترجمة للعربية وجاهزة للنشر على TikTok / Reels / Shorts — **بدون أي اشتراك شهري وبدون إرسال بياناتك لأي خدمة سحابية**.

**الحالة:** المرحلة 1 (النواة التشغيلية) ✅ مكتملة — راجع [`docs/PROGRESS.md`](docs/PROGRESS.md) للتقدّم التفصيلي.

---

## 📚 الوثائق

| الملف | المحتوى |
|---|---|
| [`docs/PROJECT_MASTER.md`](docs/PROJECT_MASTER.md) | **المرجع الرسمي الوحيد** — الرؤية، المعمارية، المراحل الست، دليل المساهمة |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | **لوحة تتبّع التقدّم** — ما أُنجِز، ما تبقّى، والدليل على كل إنجاز |

---

## ⚡ البدء السريع

### 1) المتطلبات

- Python 3.10 أو أحدث
- ffmpeg (مستحسن نظامياً — أو يُثبَّت تلقائياً عبر `imageio-ffmpeg`)

```bash
# Debian / Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```

### 2) التثبيت

```bash
git clone https://github.com/noctis-ops/ar-clipper.git
cd ar-clipper

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# للترجمة المحلية للعربية عبر NLLB-200:
pip install transformers torch sentencepiece
# أو بديل أخف بكثير (بدون torch):
pip install argostranslate
```

### 3) تأكّد أن البيئة سليمة

```bash
python -m cli.main doctor
```

يعرض جدولاً بحالة كل مكوّن ويخبرك بالضبط بما ينقص.

### 4) أنتج أول مقطع

```bash
python -m cli.main clip "https://www.youtube.com/watch?v=XXXX" \
    --start 00:12:30 \
    --end   00:13:20 \
    --license "إذن كتابي من صاحب القناة بتاريخ 2026-09-18"
```

> ⚠️ **الخيار `--license` إلزامي.** هذا تطبيق مباشر للمبدأ الرابع في الوثيقة: لا معالجة لأي فيديو دون رخصة أو إذن واضح، ويُسجَّل السند مع كل مقطع.

الناتج في `data/clips/<اسم-الفيديو>/<اسم-المقطع>/`:

```
clip.mp4               ← الفيديو النهائي 1080×1920 بترجمة عربية محروقة
clip.srt / .vtt / .ass ← ملفات الترجمة المرافقة
clip.transcript.json   ← الترانسكربت الخاص بالمقطع
clip.metadata.json     ← البيانات الوصفية (المصدر، الترخيص، المحطات المنفَّذة)
```

---

## 🛠️ الاستخدام

### الوضع التفاعلي (لا تعرف التوقيت بعد؟)

```bash
python -m cli.main clip video.mp4 --license "ملكي" --interactive
```

يفرّغ الفيديو، يعرض لك الترانسكربت بالتوقيت (أصلي + عربي)، ثم تختار المدى.

### عدة مقاطع في تشغيلة واحدة

```bash
python -m cli.main clip video.mp4 --license "ملكي" \
    --range 00:01:10..00:01:50 \
    --range 00:05:00..00:05:45 \
    --range 00:12:20..00:13:05
```

التفريغ يحدث **مرة واحدة فقط** ويُعاد استخدامه لكل المقاطع.

### تفريغ وترجمة فقط

```bash
python -m cli.main transcribe video.mp4 --license "ملكي"
python -m cli.main show data/transcripts/<الملف>.json
```

### أوامر مفيدة

```bash
python -m cli.main info video.mp4            # معلومات ملف وسائط
python -m cli.main clip --help               # كل الخيارات
```

### خيارات شائعة

| الخيار | الوظيفة |
|---|---|
| `--model tiny\|base\|small\|medium\|large-v3` | نموذج التفريغ (الأصغر أسرع) |
| `--language en` | تحديد لغة المصدر بدل الكشف التلقائي |
| `--track ar\|source\|bilingual` | نص الترجمة المعروض على الشاشة |
| `--no-translate` | تعطيل الترجمة (للمحتوى العربي أصلاً) |
| `--no-silence` | إبقاء فترات الصمت |
| `--no-reframe` | إبقاء الأبعاد الأصلية |
| `--no-burn` | توليد ملفات الترجمة دون حرقها |
| `--fast-cut` | قص أسرع بدون إعادة ترميز |
| `--keep-temp` | الاحتفاظ بالملفات الوسيطة للتشخيص |

---

## ⚙️ الإعدادات

كل شيء في [`config/settings.yaml`](config/settings.yaml). أي قيمة قابلة للتجاوز بمتغيّر بيئة:

```bash
ARCLIPPER__TRANSCRIBE__MODEL=base \
ARCLIPPER__TRANSLATE__ENGINE=argos \
python -m cli.main clip video.mp4 --license "ملكي" -s 10 -e 40
```

**جهاز ضعيف؟** جرّب:

```yaml
transcribe:
  model: base          # بدل small
translate:
  engine: argos        # أخف بكثير من NLLB
export:
  preset: veryfast
```

---

## 🧩 المعمارية

خط أنابيب من محطات مستقلة؛ كل محطة تأخذ مدخلاً محدداً وتنتج مخرجاً محدداً، ويمكن استبدالها دون كسر البقية:

```
Ingest → Transcribe → Translate → Cut → Silence → Reframe → Subtitles → Burn → Export
```

```
core/
├── common/      ← الإعدادات، السجلات، غلاف ffmpeg، نماذج البيانات، أدوات النص
├── ingest/      ← تحميل من رابط (yt-dlp) أو ملف محلي + فرض الترخيص
├── transcribe/  ← faster-whisper (افتراضي) | whisperx
├── translate/   ← NLLB-200 | Argos | passthrough
├── clip/        ← القص + محاذاة الحدود مع الكلام
├── silence/     ← كشف الصمت + حذفه + إعادة حساب توقيت الترجمة
├── reframe/     ← تحويل 9:16 (قص مركزي؛ تتبّع الوجه في المرحلة 3)
├── subtitles/   ← SRT / VTT / ASS + الحرق على الفيديو
├── export/      ← التصدير المنظّم + البيانات الوصفية
└── pipeline.py  ← منسّق المحطات
```

**قاعدة معمارية:** `core/` لا يستورد شيئاً من `cli/` — وهذا ما يجعل إضافة واجهة ويب أو REST API لاحقاً (المرحلة 6) مجرد طبقة إضافية.

### لماذا تعمل العربية هنا بشكل صحيح؟

- النص يُكتب بترتيبه **المنطقي** ولا يُعكس يدوياً — التشكيل والاتجاه يتولاهما libass عبر HarfBuzz/FriBidi.
- تُضاف علامة RLM لكل سطر عربي لضبط الاتجاه عند اختلاط الأرقام واللاتينية.
- لفّ الأسطر متوازن وبلا فقدان كلمات.
- التوقيت يُعاد حسابه بعد حذف الصمت، فلا تنزاح الترجمة عن الكلام.

---

## 🧪 الاختبارات

```bash
pytest                 # 168 اختباراً
pytest -m "not slow"   # وحدة فقط (أقل من ثانية، بلا ffmpeg)
pytest -m slow         # تكامل حقيقي مع ffmpeg (~65 ثانية)

python scripts/e2e_check.py   # فحص شامل بلا إنترنت وبلا نماذج
```

---

## 🤝 المساهمة

راجع القسم 12 في [`docs/PROJECT_MASTER.md`](docs/PROJECT_MASTER.md). باختصار:

1. الأداة المستخدمة **يجب** أن تكون مجانية ومفتوحة المصدر وتعمل محلياً.
2. كل ميزة = وحدة مستقلة في مكانها الصحيح من الهيكل.
3. كل ميزة تحتاج اختباراً يثبت عملها بمعزل.
4. حدّث [`docs/PROJECT_MASTER.md`](docs/PROJECT_MASTER.md) و [`docs/PROGRESS.md`](docs/PROGRESS.md) في نفس الـ PR.

**يُرفض:** أي اشتراك مدفوع إجباري، أي انتهاك لحقوق النشر، أي تزييف للمشاهدات، أي كود بلا توثيق أو اختبار.

---

## 🗺️ خارطة الطريق

| المرحلة | المحتوى | الحالة |
|:---:|---|:---:|
| 1 | النواة التشغيلية (MVP) | ✅ مكتملة |
| 2 | الذكاء في الاختيار والمحتوى | ⬜ التالية |
| 3 | الهوية البصرية والتصميم | ⬜ |
| 4 | إدارة الحملات والأرباح | ⬜ |
| 5 | المكتبة والبحث والطابور | ⬜ |
| 6 | التوسّع إلى SaaS | ⏸️ مؤجَّلة |

التفاصيل الكاملة في [`docs/PROGRESS.md`](docs/PROGRESS.md).

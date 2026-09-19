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

## ⚡ البدء السريع (3 خطوات)

### 1) ثبّت

```bash
git clone https://github.com/noctis-ops/ar-clipper.git
cd ar-clipper
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2) افحص

```bash
python -m cli.main doctor
```

يخبرك **بالضبط** بما ينقص مع أمر التثبيت جاهزاً للنسخ. لا حاجة لتخمين المتطلبات.

### 3) شغّل

اختر أسلوبك:

```bash
python -m cli.main quickstart    # ⭐ ثلاثة أسئلة وينتج أول مقطع
python -m cli.main serve         # 🌐 واجهة في المتصفح
```

هذا كل شيء. الباقي في هذا الملف تفاصيل لمن يحتاجها.

---

## 🖥️ الواجهة الرسومية

```bash
pip install fastapi "uvicorn[standard]"
python -m cli.main serve
# ثم افتح http://127.0.0.1:8000
```

ثلاثة حقول: المصدر، المدى، الإعداد — وزر واحد، مع سجل تقدّم حي ومعاينة للفيديو الناتج داخل الصفحة. تعمل محلياً بالكامل، ولا تُرفع أي بيانات لأي خادم.

---

## 🛠️ الاستخدام

### المسارات الجاهزة — بدل حفظ عشرة خيارات

```bash
python -m cli.main presets     # اعرضها كلها
```

| المسار | متى تستخدمه |
|---|---|
| `campaign` | **حملات Whop** — جودة عالية، ترجمة عربية محروقة، 9:16 |
| `fast` | معاينة سريعة للتجريب |
| `quality` | المقاطع النهائية المهمة |
| `arabic_source` | المصدر عربي أصلاً (بلا ترجمة) |
| `subtitles_only` | ملفات ترجمة فقط للمونتاج في برنامج آخر |
| `no_subtitles` | قصّ وتأطير فقط — **لا يحمّل أي نموذج** |

```bash
python -m cli.main clip video.mp4 --preset campaign -s 00:12:30 -e 00:13:20 -L campaign
```

### أساس الاستخدام (`--license`)

كلمة واحدة بدل جملة:

| القيمة | المعنى | نشر علني |
|---|---|:---:|
| `campaign` | مرخّص ضمن حملة clipping (Whop أو مشابه) | ✅ |
| `owner_permission` | إذن صريح من صاحب المحتوى | ✅ |
| `own_content` | محتوى من إنتاجك | ✅ |
| `cc_by` | مشاع إبداعي (مع نسب المصدر) | ✅ |
| `fair_use_edu` | اقتباس تعليمي/نقدي محدود | ✅ |
| `personal_test` | شخصي/تجريبي | — |

**لا تريد كتابته أصلاً؟** اضبطه مرة واحدة في `config/settings.yaml`:

```yaml
ingest:
  license_mode: default       # required | default | off
  default_license: campaign
```

بعدها لن تحتاج `--license` إطلاقاً، وتبقى القيمة مسجَّلة تلقائياً في `metadata.json` لكل مقطع. يمكنك إضافة تفصيل عند الحاجة: `-L "campaign:حملة بودكاست فلان"`.

### الوضع التفاعلي (لا تعرف التوقيت بعد؟)

```bash
python -m cli.main clip video.mp4 -p campaign --interactive
```

يفرّغ الفيديو، يعرض الترانسكربت بالتوقيت، ثم تختار المدى.

### عدة مقاطع في تشغيلة واحدة

```bash
python -m cli.main clip video.mp4 -p campaign \
    --range 00:01:10..00:01:50 \
    --range 00:05:00..00:05:45
```

التفريغ يحدث **مرة واحدة فقط** ويُعاد استخدامه لكل المقاطع.

### أوامر أخرى

```bash
python -m cli.main transcribe video.mp4     # تفريغ وترجمة فقط
python -m cli.main show <transcript.json>   # عرض ترانسكربت محفوظ
python -m cli.main info video.mp4           # معلومات ملف
python -m cli.main clip --help              # كل الخيارات التفصيلية
```

### تجاوز المسار الجاهز

أي علم صريح يتجاوز المسار:

```bash
python -m cli.main clip video.mp4 -p campaign --model tiny --no-silence -s 10 -e 40
```

| الخيار | الوظيفة |
|---|---|
| `--model tiny\|base\|small\|medium\|large-v3` | نموذج التفريغ |
| `--language en` | لغة المصدر بدل الكشف التلقائي |
| `--track ar\|source\|bilingual` | نص الترجمة المعروض |
| `--no-translate` / `--no-silence` / `--no-reframe` | تعطيل محطة |
| `--no-burn` | ملفات ترجمة دون حرق |
| `--fast-cut` | قص أسرع بلا إعادة ترميز |
| `--keep-temp` | إبقاء الملفات الوسيطة للتشخيص |

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

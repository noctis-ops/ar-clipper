# دليل التثبيت والتجربة الكاملة

> كل أمر هنا **نُفِّذ فعلاً** وسُجِّلت نتيجته ووقته. لا خطوة نظرية.
> آخر تحقق: 2026-09-22 · على بيئة نظيفة من الصفر.

---

## الجزء الأول: التثبيت (‏~25 ثانية)

### 1. المتطلبات

| المتطلب | الإصدار | ملاحظة |
|---|---|---|
| Python | 3.10+ | مُختبَر على 3.11 |
| مساحة قرص | ~2 GB | للحزم والمخرجات |
| ffmpeg | — | **لا تحتاج تثبيته**، يأتي مع `imageio-ffmpeg` |
| إنترنت | لأول تثبيت فقط | بعدها كل شيء محلي |

### 2. الأوامر

```bash
cd ar-clipper

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

**النتيجة المقيسة: 22.5 ثانية.**

> **على ويندوز** استبدل `.venv/bin/` بـ `.venv\Scripts\` في كل الأوامر التالية.

### 3. تأكيد التثبيت

```bash
.venv/bin/python -m cli.main doctor
```

يجب أن ينتهي بـ **«البيئة جاهزة ✅»**. علامات ⚠️ للترجمة وGPU وpyannote **طبيعية
ولا تمنع العمل** — هذه إضافات اختيارية.

### 4. اختصار مريح (اختياري)

```bash
alias arc='.venv/bin/python -m cli.main'
```

بعدها تكتب `arc doctor` بدل الأمر الطويل. الدليل يستخدم الصيغة الكاملة ليعمل
عند الجميع.

---

## الجزء الثاني: التحقق الآلي (‏~2 دقيقة)

شغّل هذين قبل أي شيء — يثبتان أن كل شيء سليم عندك.

```bash
# 1) الاختبارات الوحدوية
.venv/bin/python -m pytest tests/ -q
```
**المتوقع: `462 passed` خلال ~52 ثانية.**

```bash
# 2) الفحص الشامل من طرف لطرف
.venv/bin/python scripts/e2e_check.py
```
**المتوقع: `نجح 19/19 فحصاً` خلال ~24 ثانية.**

هذا الفحص يُنتج فيديو حقيقياً ويتحقق من: المقاس 9:16، حذف الصمت، ملفات
SRT/VTT/ASS، النص العربي، إعادة ضبط التوقيت، سند الترخيص، الصورة المصغّرة،
وتنظيف الملفات المؤقتة.

> إن فشل شيء هنا، توقّف وأبلغ قبل المتابعة.

---

## الجزء الثالث: توليد عيّنات للتجربة

لا تحتاج تنزيل أي فيديو. المشروع يولّد عيّناته محلياً:

```bash
.venv/bin/python scripts/make_sample.py
```

**النتيجة (~21 ثانية):**

| العيّنة | المدة | الغرض |
|---|---|---|
| `data/samples/speaker.mp4` | 30s | متحدث يتحرّك أفقياً — لاختبار تتبّع الوجه |
| `data/samples/dialogue.mp4` | 20s | متحدثان على الجانبين — لاختبار الشاشة المنقسمة |
| `data/samples/static.mp4` | 15s | بلا وجوه — لاختبار التراجع الآمن |

العيّنات مُولَّدة بالكامل محلياً: **بلا إنترنت وبلا حقوق طرف ثالث**.

---

## الجزء الرابع: تجربة الميزات — الطريق السريع

### 4.0 أسهل بداية على الإطلاق

```bash
.venv/bin/python -m cli.main quickstart
```
يسألك ثلاثة أسئلة فقط وينتج أول مقطع. إن أردت فهم ما يجري، تابع بالخطوات التالية.

### 4.1 معاينة بلا تنفيذ (ابدأ دائماً بهذا)

```bash
.venv/bin/python -m cli.main clip data/samples/speaker.mp4 \
    -s 0 -e 20 --dry-run
```
يعرض جدول الخطة — المحطات، المقاس، النموذج — **بلا معالجة**. مفيد للتأكد قبل
مقطع طويل.

### 4.2 تتبّع الوجه ⭐ (أهم ميزة في المرحلة 3)

```bash
.venv/bin/python -m cli.main clip data/samples/speaker.mp4 \
    -s 0 -e 20 --face-track --no-subtitles -L personal_test -n demo-track
```

**النتيجة المقيسة: 11.7 ثانية** → `data/clips/speaker__*/demo-track/demo-track.mp4`

لاحظ في السجل: `تتبّع الوجه (opencv): 94 وجه في 94 عيّنة (100%)`.
الوجه يبقى موسّطاً طوال المقطع رغم تحرّكه. وحُذف الصمت تلقائياً: 20s → 15s.

**للمقارنة**، شغّل نفس الأمر بلا `--face-track` وقارن: القص المركزي الثابت
يقطع الوجه حين يتحرّك.

### 4.3 الشاشة المنقسمة (حوار بين شخصين)

```bash
.venv/bin/python -m cli.main clip data/samples/dialogue.mp4 \
    -s 0 -e 15 --face-track --no-subtitles --no-silence \
    -L personal_test -n demo-split
```

في السجل: `حوار ثنائي مكتشف (ثقة 100%): وجهان عند 0.25 و0.75` ثم
`شاشة منقسمة — متحدثان`. تعمل **تلقائياً** بلا علم إضافي.

### 4.4 التراجع الآمن (لا وجوه في الفيديو)

```bash
.venv/bin/python -m cli.main clip data/samples/static.mp4 \
    -s 0 -e 10 --face-track --no-subtitles -L personal_test -n demo-safe
```
السجل يقول `0 وجه` ثم يتراجع للقص المركزي **بلا خطأ**. هذا مقصود.

### 4.5 قوالب التصميم

```bash
.venv/bin/python -m cli.main templates                      # اعرض الخمسة
.venv/bin/python -m cli.main clip data/samples/speaker.mp4 \
    -s 0 -e 10 -T karaoke_pop --no-subtitles -L personal_test
```

### 4.6 النسخ المتعددة للمقارنة (A/B)

```bash
.venv/bin/python -m cli.main clip data/samples/speaker.mp4 \
    -s 0 -e 10 --variants classic,bold_yellow,news \
    --no-subtitles --no-silence -L personal_test
```
**النتيجة المقيسة: 18.5 ثانية لثلاث نسخ** — تجدها بلواحق `__classic`,
`__bold_yellow`, `__news`.

### 4.7 الصورة المصغّرة والهوية البصرية

الصورة المصغّرة تُولَّد **تلقائياً** مع كل مقطع (ملف `.jpg` بجانب الفيديو).
لنص جذاب عليها:

```bash
.venv/bin/python -m cli.main clip data/samples/speaker.mp4 \
    -s 0 -e 10 --hook "الذكاء الاصطناعي سيغيّر كل شيء" \
    --no-subtitles -L personal_test
```

الهوية (شعار + علامة مائية) **معطّلة افتراضياً**. لتفعيلها حرّر
`config/settings.yaml`:

```yaml
branding:
  enabled: true
  logo_path: /مسار/إلى/شعارك.png
  handle: "@حسابك"
```

### 4.8 الواجهة في المتصفح

```bash
.venv/bin/python -m cli.main serve
```
افتح `http://localhost:8000`. كل ما سبق متاح بالنقر: القوالب، تتبّع الوجه،
الكاريوكي، نص المصغّرة — وتعرض لك **أمر الطرفية المكافئ** لتتعلّمه.

---

## الجزء الخامس: الميزات التي تحتاج تنزيل نماذج

كل ما سبق يعمل **بلا إنترنت**. الميزات التالية تحتاج تنزيلاً أول مرة فقط،
وكلها **مجانية**.

### 5.1 الترجمة العربية والتفريغ

```bash
.venv/bin/pip install transformers torch sentencepiece   # أفضل جودة (NLLB-200)
# أو الأخف:
.venv/bin/pip install argostranslate
```

ثم جرّب المسار الكامل مع الترجمة (سيُنزّل نموذج Whisper أول مرة):

```bash
.venv/bin/python -m cli.main clip <رابط-أو-ملف> -s 0 -e 45 -L personal_test
```

### 5.2 الاقتراح التلقائي للمقاطع (المرحلة 2)

> **انتبه:** `suggest` يحتاج تفريغ الكلام أولاً، فهو يتطلب نموذج Whisper
> (يُنزَّل مرة واحدة، حجم `small` ≈ 460 MB). بدونه سيفشل برسالة
> «تعذّر تحميل نموذج small» — وهذا ليس عطلاً.

بعد توفّر النموذج، محرّك الاستدلال المحلي يعمل بلا أي تنزيل إضافي:

```bash
.venv/bin/python -m cli.main suggest data/samples/speaker.mp4 --engine heuristic
```

> عيّنات `make_sample.py` **صامتة أو بنغمة بسيطة** بلا كلام حقيقي، فلن تعطي
> اقتراحات مفيدة. استخدم بودكاست حقيقياً لتجربة هذه الميزة.

ولجودة أعلى، بنموذج لغوي محلي مجاني:
```bash
# ثبّت Ollama من https://ollama.com ثم:
ollama pull qwen2.5:7b
.venv/bin/python -m cli.main suggest <مصدر> --engine llm
```

### 5.3 فصل المتحدثين

```bash
.venv/bin/pip install pyannote.audio
```
يتطلب حساباً مجانياً على HuggingFace وقبول شروط النموذج يدوياً.

---

## الجزء السادس: من رابط يوتيوب حقيقي

```bash
.venv/bin/python -m cli.main clip "https://www.youtube.com/watch?v=..." \
    -s 00:05:30 -e 00:06:15 -L fair_use_edu --face-track --animated-subs
```

`-L` هو **أساس الاستخدام** (التزام أخلاقي في المشروع). القيم:
`personal_test` · `fair_use_edu` · `cc_by_source` · `owner_permission`.
اعرضها بـ `.venv/bin/python -m cli.main presets`.

---

## الجزء السابع: الصيانة

```bash
.venv/bin/python -m cli.main clean --dry-run   # ماذا سيُحذف؟
.venv/bin/python -m cli.main clean             # نفّذ
.venv/bin/python -m cli.main info <ملف>        # معلومات وسائط
```

لإعادة إنتاج مقطع موجود بسرعة، أضف `--resume` (يتخطّى المُنتَج مسبقاً في 0.03s).

---

## ملخص: قائمة تحقق سريعة

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 1) ثبّت
.venv/bin/python -m cli.main doctor                                  # 2) افحص
.venv/bin/python -m pytest tests/ -q                                 # 3) 462 ✅
.venv/bin/python scripts/e2e_check.py                                # 4) 19/19 ✅
.venv/bin/python scripts/make_sample.py                              # 5) عيّنات
.venv/bin/python -m cli.main clip data/samples/speaker.mp4 \
    -s 0 -e 20 --face-track --no-subtitles -L personal_test          # 6) جرّب
.venv/bin/python -m cli.main serve                                   # 7) الواجهة
```

**الزمن الكلي من الصفر إلى أول مقطع: أقل من 3 دقائق.**

---

## حلّ المشكلات

| العَرَض | السبب | الحل |
|---|---|---|
| `ModuleNotFoundError` | البيئة غير مفعّلة | استخدم `.venv/bin/python` لا `python` |
| `libGL.so.1` مفقود | حزمة opencv الخاطئة | `pip uninstall opencv-python opencv-contrib-python` ثم `pip install "opencv-python-headless<5"` |
| تعذّر تحميل نموذج التفريغ | لا إنترنت لأول تنزيل | جرّب بـ `--no-subtitles` أولاً |
| التتبّع بطيء | طبيعي (~2.6s) | يُخزَّن مؤقتاً: إعادة التشغيل تكلف 0.001s |
| لا يُكتشف وجه | إضاءة سيئة أو وجه صغير | خفّض `reframe.min_face_ratio` في `settings.yaml` |
| مقطع بلا صوت | المصدر بلا مسار صوتي | تحقق بـ `cli.main info <ملف>` |

---

## للمطوّرين

```bash
.venv/bin/python -m pytest tests/ -m "not slow"      # سريع، بلا ترميز
.venv/bin/python -m pytest tests/test_phase3_design.py -v
.venv/bin/pip install coverage && \
  .venv/bin/python -m coverage run -m pytest tests/ && \
  .venv/bin/python -m coverage report
```

الوثائق: `docs/PROJECT_MASTER.md` (مصدر الحقيقة) · `docs/PROGRESS.md` (التقدّم)
· `docs/REVIEW-PHASE-1-2.md` و`docs/REVIEW-PHASE-3.md` (التقييمات).

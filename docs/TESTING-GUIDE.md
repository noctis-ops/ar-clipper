# دليل التثبيت والتجربة الكاملة

> **الدليل مكتوب لويندوز أولاً** (مع مكافئ لينكس/ماك في كل خطوة).
> كل أمر هنا نُفِّذ فعلاً وسُجِّلت نتيجته. آخر تحقق: 2026-09-22.

---

## قبل أن تبدأ: المشغّل المختصر

بدل كتابة `.venv\Scripts\python -m cli.main` في كل مرة، المشروع يوفّر مشغّلاً:

| النظام | الأمر |
|---|---|
| **ويندوز — PowerShell** | `.\arc doctor` |
| **ويندوز — Command Prompt** | `arc doctor` |
| لينكس/ماك | `./arc.sh doctor` |

ثلاثة ملفات في جذر المشروع تخدم هذا: `arc.bat` (cmd و PowerShell)،
`arc.ps1` (PowerShell أصلي برسائل عربية)، و`arc.sh` (لينكس/ماك).
كلها تضبط ترميز UTF-8 تلقائياً فتظهر المخرجات العربية صحيحة.

> ### ⚠️ لماذا `.\` قبل `arc` في PowerShell؟
> لأن PowerShell **لا يشغّل أي برنامج من المجلد الحالي** بلا مسار صريح —
> إجراء أمان مقصود يمنع تنفيذ ملف خبيث بالخطأ لمجرد أنه يحمل اسم أمر شائع.
> كتابة `arc` وحدها تعطي `CommandNotFoundException`، و`.\arc` تعمل.
>
> **Command Prompt (cmd) لا يشترط ذلك** — فيه `arc` وحدها تكفي.

### اجعل `arc` تعمل بلا `.\` (اختياري)

إن أزعجك تكرار `.\`، أضف مجلد المشروع إلى PATH لهذه الجلسة:

```powershell
$env:Path = "$PWD;$env:Path"
arc doctor          # تعمل الآن بلا .\
```

يسري على النافذة الحالية فقط. لجعله دائماً أضف السطر إلى ملف `$PROFILE`.

**الدليل يستخدم `.\arc`** لأنها تعمل في PowerShell وcmd معاً.
على لينكس/ماك اقرأها `./arc.sh`.

---

## الجزء الأول: التثبيت (~25 ثانية)

### 1. المتطلبات

| المتطلب | التفصيل |
|---|---|
| Python | 3.10 أو أحدث — [python.org/downloads](https://www.python.org/downloads/) |
| مساحة قرص | ~2 GB |
| ffmpeg | **لا تحتاج تثبيته** — يأتي داخل الحزم |
| إنترنت | لأول تثبيت فقط |

> **مهم عند تثبيت Python على ويندوز:** فعّل خيار **“Add Python to PATH”**
> في أول شاشة من المثبّت، وإلا لن يعمل أمر `python` من موجّه الأوامر.

### 2. أوامر التثبيت

افتح **PowerShell** أو **Command Prompt** داخل مجلد المشروع:

```powershell
cd ar-clipper

python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
```

<details>
<summary>المكافئ على لينكس/ماك</summary>

```bash
cd ar-clipper
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```
</details>

**النتيجة المقيسة: 22.5 ثانية.**

> **إن رفض PowerShell التشغيل** برسالة عن “execution policy”، استخدم
> Command Prompt بدلاً منه، أو نفّذ مرة واحدة:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 3. تأكيد التثبيت

```powershell
.\arc doctor
```

يجب أن ينتهي بـ **«البيئة جاهزة ✅»**، ومن ضمن الصفوف:

```
│ الخط العربي │ ✅ │ Segoe UI (windows) │
```

هذا الصف مهم: بدون خط عربي ستظهر الترجمة والصور المصغّرة **مربّعات فارغة**.
ويندوز يأتي بـ Segoe UI جاهزاً فالأمر يُحلّ تلقائياً.

علامات ⚠️ للترجمة وGPU وpyannote **طبيعية** — إضافات اختيارية.

---

## الجزء الثاني: التحقق الآلي (~2 دقيقة)

```powershell
.venv\Scripts\python -m pytest tests\ -q
```
**المتوقع: `491 passed` خلال ~50 ثانية.**

```powershell
.venv\Scripts\python scripts\e2e_check.py
```
**المتوقع: `نجح 19/19 فحصاً` خلال ~24 ثانية.**

الفحص الثاني يُنتج فيديو حقيقياً ويتحقق من: المقاس 9:16، حذف الصمت، ملفات
SRT/VTT/ASS، النص العربي، إعادة ضبط التوقيت، سند الترخيص، الصورة المصغّرة،
وتنظيف الملفات المؤقتة.

> إن فشل شيء هنا، توقّف وأبلغ قبل المتابعة.

---

## الجزء الثالث: توليد عيّنات للتجربة

لا تحتاج تنزيل أي فيديو:

```powershell
.venv\Scripts\python scripts\make_sample.py
```

**النتيجة (~21 ثانية):**

| العيّنة | المدة | الغرض |
|---|---|---|
| `data\samples\speaker.mp4` | 30s | متحدث يتحرّك — لاختبار تتبّع الوجه |
| `data\samples\dialogue.mp4` | 20s | متحدثان — لاختبار الشاشة المنقسمة |
| `data\samples\static.mp4` | 15s | بلا وجوه — لاختبار التراجع الآمن |

مُولَّدة محلياً بالكامل: بلا إنترنت وبلا حقوق طرف ثالث.

---

## الجزء الرابع: تجربة الميزات

> المسارات أدناه بخط مائل خلفي (`\`) لويندوز. على لينكس/ماك استخدم `/`.
> **ملاحظة:** Python يقبل `/` على ويندوز أيضاً، فالأمران يعملان عندك.

### 4.0 أسرع بداية

```powershell
.\arc quickstart
```
ثلاثة أسئلة وينتج أول مقطع.

### 4.1 معاينة بلا تنفيذ (ابدأ بهذا دائماً)

```powershell
.\arc clip data\samples\speaker.mp4 -s 0 -e 20 --dry-run
```
يعرض جدول الخطة بلا معالجة.

### 4.2 تتبّع الوجه ⭐ (أهم ميزة)

```powershell
.\arc clip data\samples\speaker.mp4 -s 0 -e 20 --face-track --no-subtitles -L personal_test -n demo-track
```

**النتيجة المقيسة: 11.7 ثانية.**
في السجل: `تتبّع الوجه (opencv): 94 وجه في 94 عيّنة (100%)`.
حُذف الصمت تلقائياً: 20s → 15s.

**للمقارنة** شغّل نفس الأمر بلا `--face-track` — القص الثابت يقطع الوجه حين يتحرّك.

### 4.3 الشاشة المنقسمة (حوار بين شخصين)

```powershell
.\arc clip data\samples\dialogue.mp4 -s 0 -e 15 --face-track --no-subtitles --no-silence -L personal_test -n demo-split
```
في السجل: `حوار ثنائي مكتشف (ثقة 100%)` ثم `شاشة منقسمة — متحدثان`.
تعمل **تلقائياً** بلا علم إضافي.

### 4.4 التراجع الآمن (فيديو بلا وجوه)

```powershell
.\arc clip data\samples\static.mp4 -s 0 -e 10 --face-track --no-subtitles -L personal_test -n demo-safe
```
`0 وجه` ثم يتراجع للقص المركزي **بلا خطأ**. هذا مقصود.

### 4.5 قوالب التصميم

```powershell
.\arc templates
.\arc clip data\samples\speaker.mp4 -s 0 -e 10 -T karaoke_pop --no-subtitles -L personal_test
```

### 4.6 النسخ المتعددة للمقارنة (A/B)

```powershell
.\arc clip data\samples\speaker.mp4 -s 0 -e 10 --variants classic,bold_yellow,news --no-subtitles --no-silence -L personal_test
```
**المقيس: 18.5 ثانية لثلاث نسخ.**

### 4.7 الصورة المصغّرة والهوية

المصغّرة تُولَّد **تلقائياً** مع كل مقطع (`.jpg` بجانب الفيديو). لنص عليها:

```powershell
.\arc clip data\samples\speaker.mp4 -s 0 -e 10 --hook "الذكاء الاصطناعي سيغيّر كل شيء" --no-subtitles -L personal_test
```

الهوية (شعار + علامة مائية) معطّلة افتراضياً. لتفعيلها حرّر
`config\settings.yaml`:

```yaml
branding:
  enabled: true
  logo_path: C:/Users/اسمك/Pictures/logo.png   # استخدم / حتى على ويندوز
  handle: "@حسابك"
```

> **مهم:** داخل ملفات YAML اكتب المسار بـ `/` لا `\`، لأن `\` محرف هروب في YAML.

### 4.8 الواجهة في المتصفح

```powershell
.\arc serve
```
افتح `http://localhost:8000`. كل ما سبق متاح بالنقر، وتعرض لك **أمر الطرفية
المكافئ** لتتعلّمه.

---

## الجزء الخامس: ميزات تحتاج تنزيل نماذج

كل ما سبق يعمل بلا إنترنت. التالي يحتاج تنزيلاً أول مرة فقط، وكله **مجاني**.

### 5.1 الترجمة العربية والتفريغ

```powershell
.venv\Scripts\pip install transformers torch sentencepiece
```
أو الأخف: `.venv\Scripts\pip install argostranslate`

ثم المسار الكامل (سيُنزَّل نموذج Whisper أول مرة، `small` ≈ 460 MB):

```powershell
.\arc clip <رابط-أو-ملف> -s 0 -e 45 -L personal_test
```

### 5.2 الاقتراح التلقائي (المرحلة 2)

> **انتبه:** `suggest` يحتاج تفريغ الكلام أولاً، فيتطلب نموذج Whisper.
> بدونه يفشل برسالة «تعذّر تحميل نموذج small» — وهذا ليس عطلاً.

```powershell
.\arc suggest <فيديو-فيه-كلام> --engine heuristic
```

> عيّنات `make_sample.py` بلا كلام حقيقي، فلن تعطي اقتراحات مفيدة.
> استخدم بودكاست حقيقياً لهذه الميزة.

ولجودة أعلى بنموذج محلي مجاني: ثبّت [Ollama](https://ollama.com) ثم
`ollama pull qwen2.5:7b` واستخدم `--engine llm`.

### 5.3 فصل المتحدثين

```powershell
.venv\Scripts\pip install pyannote.audio
```
يتطلب حساباً مجانياً على HuggingFace وقبول شروط النموذج.

---

## الجزء السادس: من رابط يوتيوب

```powershell
.\arc clip "https://www.youtube.com/watch?v=..." -s 00:05:30 -e 00:06:15 -L fair_use_edu --face-track --animated-subs
```

`-L` هو **أساس الاستخدام** (التزام أخلاقي في المشروع):
`personal_test` · `fair_use_edu` · `cc_by_source` · `owner_permission`.
اعرضها بـ `.\arc presets`.

> ضع الرابط **بين علامتَي اقتباس** دائماً على ويندوز — الرموز `&` و`?` في
> روابط يوتيوب يفسّرها موجّه الأوامر.

---

## الجزء السابع: الصيانة

```powershell
.\arc clean --dry-run     # ماذا سيُحذف؟
.\arc clean               # نفّذ
.\arc info <ملف>          # معلومات وسائط
```

أضف `--resume` لتخطّي المقاطع المُنتَجة مسبقاً (**0.2 ثانية** بدل إعادة الإنتاج).

---

## ملخص: قائمة تحقق سريعة

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        REM 1) ثبّت (22s)
.\arc doctor                                            REM 2) افحص
.venv\Scripts\python -m pytest tests\ -q              REM 3) 491 ✅
.venv\Scripts\python scripts\e2e_check.py             REM 4) 19/19 ✅
.venv\Scripts\python scripts\make_sample.py           REM 5) عيّنات
.\arc clip data\samples\speaker.mp4 -s 0 -e 20 --face-track --no-subtitles -L personal_test
.\arc serve                                             REM 7) الواجهة
```

**من الصفر إلى أول مقطع: أقل من 3 دقائق.**

---

## حلّ المشكلات

### خاص بويندوز

| العَرَض | السبب | الحل |
|---|---|---|
| `'python' is not recognized` | لم يُضَف لـPATH | أعد تثبيت Python وفعّل “Add Python to PATH” |
| `arc : The term 'arc' is not recognized` | **PowerShell لا يشغّل من المجلد الحالي** | اكتب `.\arc` بدل `arc` — أو استخدم Command Prompt |
| `.\arc` لا يعمل أيضاً | لست في مجلد المشروع | `cd` إلى المجلد الذي فيه `arc.bat` |
| مسار المشروع طويل | قرب حدّ 260 محرفاً | انقله إلى `C:\ar-clipper` — `.\arc doctor` ينبّهك |
| PowerShell يرفض التشغيل | execution policy | استخدم Command Prompt، أو `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| نص عربي = مربّعات | لا خط عربي | شغّل `.\arc doctor` وراجع صف «الخط العربي» |
| خطأ في مسار داخل YAML | `\` محرف هروب | اكتب المسارات بـ `/` داخل `settings.yaml` |
| المسار طويل جداً | حدّ 260 محرفاً | انقل المشروع قرب جذر القرص (`C:\ar-clipper`) |
| الطرفية تعرض `????` أو رموزاً | ترميز الطرفية | المشغّلات تضبطه تلقائياً؛ يدوياً: `chcp 65001` |

### عام

| العَرَض | السبب | الحل |
|---|---|---|
| `ModuleNotFoundError` | البيئة غير مفعّلة | استخدم `.venv\Scripts\python` لا `python` |
| `libGL.so.1` مفقود (لينكس) | حزمة opencv الخاطئة | `pip install "opencv-python-headless<5"` |
| تعذّر تحميل نموذج التفريغ | لا إنترنت لأول تنزيل | جرّب بـ `--no-subtitles` أولاً |
| التتبّع بطيء | طبيعي (~2.6s) | يُخزَّن مؤقتاً: الإعادة 0.001s |
| لا يُكتشف وجه | إضاءة سيئة أو وجه صغير | خفّض `reframe.min_face_ratio` |

---

## للمطوّرين

```powershell
.venv\Scripts\python -m pytest tests\ -m "not slow"
.venv\Scripts\python -m pytest tests\test_cross_platform.py -v
```

الوثائق: `docs/PROJECT_MASTER.md` (مصدر الحقيقة) · `docs/PROGRESS.md` ·
`docs/REVIEW-PHASE-1-2.md` و`docs/REVIEW-PHASE-3.md`.

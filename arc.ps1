# ============================================================
#  AR-Clipper — مشغّل PowerShell
#
#  الاستخدام:   .\arc.ps1 doctor
#               .\arc.ps1 clip video.mp4 -s 0 -e 30 -L personal_test
#
#  ملاحظة: PowerShell لا يشغّل ملفاً من المجلد الحالي بلا ".\" — هذا
#  إجراء أمان مقصود، لذا اكتب .\arc.ps1 وليس arc.ps1.
# ============================================================

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Host ''
    Write-Host '[خطأ] البيئة الافتراضية غير موجودة.' -ForegroundColor Red
    Write-Host ''
    Write-Host '  شغّل هذين الأمرين أولاً من مجلد المشروع:'
    Write-Host '      python -m venv .venv' -ForegroundColor Cyan
    Write-Host '      .venv\Scripts\pip install -r requirements.txt' -ForegroundColor Cyan
    Write-Host ''
    exit 1
}

# ترميز UTF-8 حتى تظهر المخرجات العربية صحيحة
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {
    # بعض إصدارات PowerShell القديمة لا تسمح بذلك — غير حرج
}

& $py -m cli.main @args
exit $LASTEXITCODE

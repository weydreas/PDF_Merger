<#
.SYNOPSIS
  Install/test Surya OCR 2.5 Q8_0 on CPU in a separate environment.
.DESCRIPTION
  No patient documents are opened. The only input is a generated fictional PNG.
  No changes to PDF Merger .venv/.env/input/output or existing AI models.
#>
param([switch]$Install)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$EnvDir = Join-Path $ProjectRoot '.venv-surya-cpu'
$Python = Join-Path $EnvDir 'Scripts\python.exe'
$Cli = Join-Path $EnvDir 'Scripts\surya_ocr.exe'
$Work = Join-Path $ProjectRoot 'logs\surya-cpu-pilot'
$Sample = Join-Path $Work 'synthetic-medical-page.png'
$ResultDir = Join-Path $Work ('results-' + [guid]::NewGuid().ToString('N'))
$ReleasePage = 'https://github.com/ggml-org/llama.cpp/releases'

function New-SyntheticSample([string]$Path) {
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap(1150, 450)
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $font = [System.Drawing.Font]::new('Arial', 34)
    $fontTitle = [System.Drawing.Font]::new('Arial', 44, [System.Drawing.FontStyle]::Bold)
    try {
        $gfx.Clear([System.Drawing.Color]::White)
        $gfx.DrawString('ЭКГ — Электрокардиография', $fontTitle, [System.Drawing.Brushes]::Black, 24, 22)
        $gfx.DrawString('Дата выполнения: 18.11.2026', $font, [System.Drawing.Brushes]::Black, 24, 119)
        $gfx.DrawString('Рекомендовано УЗИ (не выполнялось)', $font, [System.Drawing.Brushes]::Black, 24, 206)
        $gfx.DrawString('ФИО: Иванов Иван (ВЫМЫШЛЕННЫЙ ПРИМЕР)', $font, [System.Drawing.Brushes]::Black, 24, 298)
        $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $fontTitle.Dispose()
        $font.Dispose()
        $gfx.Dispose()
        $bmp.Dispose()
    }
}

try {
    Write-Host 'Surya OCR 2.5 — тест только CPU, без реальных медицинских PDF.' -ForegroundColor Cyan
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Нужна 64-разрядная Windows.'
    }
    $ramGB = [Math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    Write-Host "Оперативная память: $ramGB ГБ"
    if ($ramGB -lt 8) {
        throw 'Меньше 8 ГБ RAM. CPU-пилот не запускается, чтобы избежать вытеснения памяти.'
    }
    if ($ramGB -lt 16) {
        Write-Host 'Менее 16 ГБ RAM: модель может работать медленно, с повышенной нагрузкой на память.' -ForegroundColor Yellow
    }

    $llamaCmd = Get-Command 'llama-server.exe' -ErrorAction SilentlyContinue
    if ($null -eq $llamaCmd) { $llamaCmd = Get-Command 'llama-server' -ErrorAction SilentlyContinue }
    $knownLocal = Join-Path $ProjectRoot '.local-tools\llama.cpp\llama-server.exe'
    if ($null -ne $llamaCmd) {
        $llamaBin = $llamaCmd.Source
    } elseif (Test-Path -LiteralPath $knownLocal -PathType Leaf) {
        $llamaBin = $knownLocal
    } else {
        if (-not $Install) {
            Write-Host 'Нужен llama-server CPU x64: запускайте с -Install либо установите его вручную.'
            Write-Host "Официальные сборки: $ReleasePage"
            exit 3
        }
        # Pinned to a verified official CPU release, with SHA-256 from the
        # ScoopInstaller/Versions maintained manifest; never execute if hash mismatches.
        $releaseName = 'b12606'
        $archiveName = 'llama-b12606-bin-win-cpu-x64.zip'
        $archiveSHA = '425eeb2a94a9600dadf369a7c9c7dd0141948921efc0fa9ec7872970fffc845d'
        $archiveURL = "https://github.com/ggml-org/llama.cpp/releases/download/$releaseName/$archiveName"
        $localRoot = Join-Path $ProjectRoot '.local-tools'
        New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
        $archive = Join-Path $localRoot $archiveName
        $unpacked = Join-Path $localRoot ('llama-cpu-staging-' + [guid]::NewGuid().ToString('N'))
        try {
            Write-Host 'Загружаю официальную CPU-сборку llama.cpp. Проверяю SHA-256.' -ForegroundColor Cyan
            Invoke-WebRequest -UseBasicParsing -Uri $archiveURL -OutFile $archive -TimeoutSec 300
            $actualSHA = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualSHA -ne $archiveSHA) {
                throw 'Хеш SHA-256 скачанного llama.cpp не совпал. Выполнение заблокировано.'
            }
            Expand-Archive -LiteralPath $archive -DestinationPath $unpacked -Force
            $binary = Get-ChildItem -LiteralPath $unpacked -Recurse -File -Filter 'llama-server.exe' | Select-Object -First 1
            if ($null -eq $binary) { throw 'В официальном архиве не найден llama-server.exe.' }
            $targetFolder = Split-Path -Parent $knownLocal
            New-Item -ItemType Directory -Path $targetFolder -Force | Out-Null
            Copy-Item -Path (Join-Path $binary.Directory.FullName '*') -Destination $targetFolder -Recurse -Force
            if (-not (Test-Path -LiteralPath $knownLocal -PathType Leaf)) {
                throw 'Не удалось установить llama-server.exe в изолированную папку.'
            }
        } finally {
            if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
            if (Test-Path -LiteralPath $unpacked) { Remove-Item -LiteralPath $unpacked -Recurse -Force }
        }
        $llamaBin = $knownLocal
    }
    Write-Host "Используется llama-server: $llamaBin"

    $pythonInstalled = Test-Path -LiteralPath $Python -PathType Leaf
    if (-not $pythonInstalled) {
        if (-not $Install) {
            Write-Host 'Окружение Surya отсутствует. Запустите с -Install для установки только в .venv-surya-cpu.'
            exit 3
        }
        $diskRoot = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($ProjectRoot))
        $freeGB = [Math]::Round(([IO.DriveInfo]::new($diskRoot)).AvailableFreeSpace / 1GB, 1)
        Write-Host "Свободно на диске проекта: $freeGB ГБ"
        if ($freeGB -lt 8) { throw 'Требуется минимум 8 ГБ свободного места.' }

        $basePython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $basePython -PathType Leaf) {
            & $basePython -m venv $EnvDir
        } elseif (Get-Command 'py.exe' -ErrorAction SilentlyContinue) {
            & py.exe -3 -m venv $EnvDir
        } elseif (Get-Command 'python.exe' -ErrorAction SilentlyContinue) {
            & python.exe -m venv $EnvDir
        } else {
            throw 'Python 3.10+ не найден. Установи его для создания изолированного окружения.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение для Surya.' }
    }

    if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
        if (-not $Install) {
            Write-Host 'Пакет surya-ocr отсутствует; запуск с -Install установит его в отдельное окружение.'
            exit 3
        }
        Write-Host 'Установка surya-ocr==0.24.0 только в .venv-surya-cpu ...' -ForegroundColor Cyan
        & $Python -m pip install --disable-pip-version-check 'surya-ocr==0.24.0'
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить surya-ocr==0.24.0.' }
    }

    $env:TORCH_DEVICE = 'cpu'
    $env:FAST_DETECTOR_DEVICE = 'cpu'
    $env:SURYA_INFERENCE_BACKEND = 'llamacpp'
    $env:SURYA_INFERENCE_PARALLEL = '1'
    $env:LLAMA_CPP_NGL = '0'
    $env:LLAMA_CPP_BINARY = $llamaBin
    $env:SURYA_GGUF_REPO = 'datalab-to/surya-ocr-2.5-gguf'
    $env:SURYA_GGUF_MODEL_FILE = 'surya-2.5-Q8_0.gguf'
    $env:SURYA_GGUF_MMPROJ_FILE = 'surya-2.5-mmproj.gguf'
    $env:SURYA_MODEL_CHECKPOINT = 'datalab-to/surya-ocr-2.5'
    $env:SURYA_INFERENCE_KEEP_ALIVE = 'false'

    New-Item -ItemType Directory -Path $Work -Force | Out-Null
    New-Item -ItemType Directory -Path $ResultDir -Force | Out-Null
    New-SyntheticSample -Path $Sample

    Write-Host 'Впервые Surya скачает GGUF Q8_0 и vision projector из Hugging Face.' -ForegroundColor Cyan
    Write-Host 'Скан будет выполняться только на CPU, после загрузки — локально.'
    $watch = [Diagnostics.Stopwatch]::StartNew()
    & $Cli $Sample --output_dir $ResultDir
    $code = $LASTEXITCODE
    $watch.Stop()
    if ($code -ne 0) { throw "Surya OCR завершилась с кодом $code." }

    $jsonReports = @(Get-ChildItem -LiteralPath $ResultDir -Recurse -File -Filter 'results.json')
    if ($jsonReports.Count -ne 1) {
        throw 'Отсутствует единственный свежий results.json из этого запуска Surya.'
    }
    $verifier = Join-Path $PSScriptRoot 'inspect_surya_boxes.py'
    if (-not (Test-Path -LiteralPath $verifier -PathType Leaf)) {
        throw 'Не найден валидатор OCR-координат; результат не считается проверенным.'
    }
    $preview = Join-Path $ResultDir 'bbox-preview.png'
    Write-Host 'Проверяю BBOX каждого текстового блока (не координаты слов)...' -ForegroundColor Cyan
    & $Python $verifier $jsonReports[0].FullName --image $Sample --preview $preview
    if ($LASTEXITCODE -ne 0) {
        throw 'Текст получен, но координаты блоков отсутствуют/повреждены. Для анонимизации результат непригоден.'
    }
    Write-Host "OCR+BBOX успешно выполнен за $([Math]::Round($watch.Elapsed.TotalSeconds,1)) с."
    Write-Host "Файлов JSON: $($jsonReports.Count)"
    Write-Host "Предпросмотр рамок: $preview"
    Write-Host "Результаты: $ResultDir"
    Write-Host 'Это координаты БЛОКОВ, не слов. Удаление персональных данных НЕ выполнялось.' -ForegroundColor Yellow
    Write-Host 'PDF Merger и исходные медицинские PDF не изменялись.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host "ОШИБКА CPU-пилота: $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}

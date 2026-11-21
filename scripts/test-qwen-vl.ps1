<#
Optional Qwen3-VL install and SYNTHETIC comparison, Windows PowerShell 5.1.
Never reads real PDFs, never contacts a remote/cloud AI server.
#>
param([switch]$Install, [string]$BaselineModel = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$modelName = 'qwen3-vl:8b'
$api = 'http://127.0.0.1:11434'

function Invoke-LocalModel([string]$Name, [string]$Prompt, [string]$Image = '') {
    $body = @{ model=$Name; prompt=$Prompt; stream=$false; format='json'; keep_alive='0'; options=@{ temperature=0; num_predict=180 } }
    if ($Image) { $body.images = @($Image) }
    $json = ConvertTo-Json -InputObject $body -Depth 8 -Compress
    $result = Invoke-RestMethod -Uri "$api/api/generate" -Method Post -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType 'application/json; charset=utf-8' -TimeoutSec 300
    return (ConvertFrom-Json -InputObject ([string]$result.response))
}

function New-ECGImage {
    Add-Type -AssemblyName System.Drawing
    $bitmap = [System.Drawing.Bitmap]::new(640,180)
    $drawing = [System.Drawing.Graphics]::FromImage($bitmap)
    $font = [System.Drawing.Font]::new('Arial',46,[System.Drawing.FontStyle]::Bold)
    $stream = [System.IO.MemoryStream]::new()
    try {
        $drawing.Clear([System.Drawing.Color]::White)
        $drawing.DrawString('ECG REPORT',$font,[System.Drawing.Brushes]::Black,15,40)
        $bitmap.Save($stream,[System.Drawing.Imaging.ImageFormat]::Png)
        return [Convert]::ToBase64String($stream.ToArray())
    } finally {
        $stream.Dispose(); $font.Dispose(); $drawing.Dispose(); $bitmap.Dispose()
    }
}

try {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw 'Ollama не найдена. Установи https://ollama.com/download/windows и запусти Ollama.'
    }
    $version = (& ollama --version | Out-String)
    Write-Host "Ollama: $($version.Trim())"
    $found = [regex]::Match($version,'\d+\.\d+\.\d+')
    if ($found.Success -and [version]$found.Value -lt [version]'0.12.7') {
        throw 'Для Qwen3-VL требуется Ollama 0.12.7 или новее.'
    }
    $inventory = Invoke-RestMethod -Uri "$api/api/tags" -TimeoutSec 10
    $names = @($inventory.models | ForEach-Object { [string]$_.name })
    Write-Host 'Уже установлены (ничего не удаляется):'
    foreach ($name in $names) { Write-Host "  $name" }
    $choices = @($names | Where-Object { $_ -match 'qwen' -and $_ -ne $modelName })
    if ($BaselineModel -and $BaselineModel -notin $names) {
        throw "Текущая модель $BaselineModel не найдена среди локальных."
    }
    if (-not $BaselineModel -and $choices.Count -eq 1) { $BaselineModel = $choices[0] }
    if (-not $BaselineModel -and $choices.Count -gt 1) {
        Write-Host "Выбери текущую Qwen через -BaselineModel; кандидаты: $($choices -join ', ')" -ForegroundColor Yellow
    }
    if ($modelName -notin $names) {
        if (-not $Install) {
            Write-Host 'Qwen3-VL отсутствует; для загрузки 6.2 ГБ используй -Install.'
            exit 3
        }
        $baseFolder = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { Join-Path $env:USERPROFILE '.ollama\models' }
        $rootDrive = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($baseFolder))
        $freeSpace = ([IO.DriveInfo]::new($rootDrive)).AvailableFreeSpace
        if ($freeSpace -lt 9GB) { throw 'Нужно не менее 9 ГБ свободного места на диске моделей.' }
        Write-Host "Загружаю $modelName. Другие модели не затрагиваются." -ForegroundColor Cyan
        & ollama pull $modelName
        if ($LASTEXITCODE -ne 0) { throw 'Ollama pull завершился ошибкой.' }
    }
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Write-Host 'GPU / VRAM:'
        & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
    } else { Write-Host 'nvidia-smi не найдена: ускорение NVIDIA не подтверждено.' }
    $candidates = @()
    if ($BaselineModel) { $candidates += $BaselineModel }
    $candidates += $modelName
    Write-Host 'Тестируем только ВЫМЫШЛЕННЫЕ данные на локальном адресе 127.0.0.1.' -ForegroundColor Cyan
    $image = New-ECGImage
    $tests = @(
        @{label='Референсные диапазоны'; expected='lab'; prompt='Classify performed medical procedure. B03.016.002 - Общий анализ крови. Гемоглобин 124; референсные значения 110.0 - 160.0. Return ONLY JSON {"category":"lab|study|review"}.'},
        @{label='Рентген и рекомендация'; expected='study'; prompt='Performed: 7002892 - Рентгенография грудной клетки. Only recommended ultrasound, not performed. Return ONLY JSON {"category":"lab|study|review"}.'},
        @{label='Вымышленные ПДн'; expected='pii'; prompt='Synthetic personal fields: ФИО Иванов Иван Иванович; дата рождения 01.02.2012; адрес Тестовая улица, 5. Return ONLY JSON {"fio":true,"birth_date":true,"address":true} if all 3 fields exist, else false for missing fields.'},
        @{label='OCR изображения ЭКГ'; expected='vision'; prompt='Read heading in the provided image. Return ONLY JSON {"category":"study","title":"ECG REPORT"} if it contains ECG, or {"category":"review","title":""} otherwise.'; image=$image}
    )
    foreach ($candidate in $candidates) {
        Write-Host ''
        Write-Host "Модель: $candidate" -ForegroundColor Cyan
        $score = 0
        foreach ($test in $tests) {
            $clock = [Diagnostics.Stopwatch]::StartNew()
            try {
                $encodedImage = if ($test.ContainsKey('image')) { [string]$test.image } else { '' }
                $reply = Invoke-LocalModel -Name $candidate -Prompt $test.prompt -Image $encodedImage
                $ok = if ($test.expected -eq 'pii') {
                    ($reply.fio -eq $true -and $reply.birth_date -eq $true -and $reply.address -eq $true)
                } elseif ($test.expected -eq 'vision') {
                    ($reply.category -eq 'study' -and [string]$reply.title -match 'ECG')
                } else { $reply.category -eq $test.expected }
                if ($ok) { $score++ }
                $status = if ($ok) { 'PASS' } else { 'FAIL' }
                Write-Host "  $($test.label): $status ($([Math]::Round($clock.Elapsed.TotalSeconds,1)) c)"
            } catch {
                Write-Host "  $($test.label): ERROR ($($_.Exception.GetType().Name))" -ForegroundColor Yellow
            } finally { $clock.Stop() }
        }
        Write-Host "РЕЗУЛЬТАТ: $candidate — $score / 4; это smoke-тест, не гарантия качества анонимизации."
    }
    Write-Host 'Установка/сравнение завершены. Настройки PDF Merger не менялись.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host "ОШИБКА: $($_.Exception.Message)" -ForegroundColor Red
    exit 2
}

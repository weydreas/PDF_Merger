<#
.SYNOPSIS
  Interactive local PDF Merger, matching Genome's start.cmd -> PowerShell pattern.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The launcher sets Windows console code page 65001. Keep Python text readable.
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {
    # Non-console hosts may not expose an output encoding.
}
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'  # Show AI progress immediately through Out-Host.

$ProjectRoot = $PSScriptRoot
$InputDir = Join-Path $ProjectRoot 'input'
$OutputDir = Join-Path $ProjectRoot 'output'
$PythonInVenv = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Requirements = Join-Path $ProjectRoot 'requirements.txt'
$MergeProgram = Join-Path $ProjectRoot 'pdf_merger.py'
$DependencyCheck = Join-Path $ProjectRoot 'dependency_check.py'
$EnvTemplate = Join-Path $ProjectRoot '.env.example'
$EnvConfig = Join-Path $ProjectRoot '.env'

function Assert-FolderOrCreate([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        throw "Ожидается папка, но найден файл: $Path"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        Write-Host "  [CREATED] $Path" -ForegroundColor Green
        return $true
    }
    Write-Host "  [READY]   $Path" -ForegroundColor DarkGray
    return $false
}

function Ensure-LocalEnv {
    # .env is local-only and ignored by Git. Never overwrite existing keys.
    if (Test-Path -LiteralPath $EnvConfig -PathType Leaf) {
        return
    }
    if (Test-Path -LiteralPath $EnvConfig -PathType Container) {
        throw "Ожидался файл .env, но обнаружена папка: $EnvConfig"
    }
    if (-not (Test-Path -LiteralPath $EnvTemplate -PathType Leaf)) {
        Write-Host 'ПРЕДУПРЕЖДЕНИЕ: шаблон .env.example отсутствует. Офлайн-объединение доступно; для AI потребуется .env.' -ForegroundColor Yellow
        return
    }

    # Overwrite disabled: a concurrently-created .env is never replaced.
    try {
        [System.IO.File]::Copy($EnvTemplate, $EnvConfig, $false)
    } catch [System.IO.IOException] {
        if (-not (Test-Path -LiteralPath $EnvConfig -PathType Leaf)) { throw }
        return
    }
    Write-Host '  [CREATED] .env из .env.example' -ForegroundColor Green
    Write-Host '  Для AI откройте .env и замените "insert your key here" своим API-ключом.'
}

function Find-PythonLauncher {
    $versionCheck = 'import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)'
    foreach ($name in @('py.exe', 'python.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -eq $command) { continue }
        $prefix = if ($name -eq 'py.exe') { @('-3') } else { @() }
        try {
            & $command.Source @prefix -c $versionCheck 2>$null
            if ($LASTEXITCODE -eq 0) {
                return @{ Executable = $command.Source; Prefix = $prefix }
            }
        } catch {
            # Windows Store aliases and broken installations are ignored.
        }
    }
    throw 'Python 3.10+ не найден. Установите Python и запустите start.cmd повторно.'
}

function Ensure-LocalPython {
    if (-not (Test-Path -LiteralPath $MergeProgram -PathType Leaf)) {
        throw "Не найден исполняемый файл: $MergeProgram"
    }
    if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
        throw "Не найден список зависимостей: $Requirements"
    }
    if (-not (Test-Path -LiteralPath $DependencyCheck -PathType Leaf)) {
        throw "Не найден файл проверки зависимостей: $DependencyCheck"
    }

    $createVenv = -not (Test-Path -LiteralPath $PythonInVenv -PathType Leaf)
    $launcher = $null
    if ($createVenv) {
        # Check for Python before asking permission to install packages.
        $launcher = Find-PythonLauncher
    } else {
        # No network access: verify all requirements and their version constraints.
        & $PythonInVenv $DependencyCheck $Requirements | Out-Host
        $checkCode = $LASTEXITCODE
        if ($checkCode -eq 0) {
            return $PythonInVenv
        }
        if ($checkCode -ne 1) {
            throw 'Не удалось проверить зависимости. Проверьте Python и requirements.txt.'
        }
    }

    Write-Host ''
    Write-Host 'Необходимо установить дополнительные библиотеки на этом компьютере.' -ForegroundColor Yellow
    Write-Host 'Это потребуется только один раз (или после изменения зависимостей).' -ForegroundColor Yellow
    Write-Host 'Для загрузки библиотек потребуется интернет. PDF-файлы никуда не отправляются.'
    Write-Host 'Нажмите Enter для установки. Для отмены введите 0.'
    do {
        $installChoice = Read-Host 'Enter или 1 - установить, 0 - отменить'
        if ($installChoice -eq '0' -or ($null -eq $installChoice -and [Console]::IsInputRedirected)) {
            # EOF in unattended launches must not silently authorize network installs.
            Write-Host 'Установка отменена. Файлы не изменены.'
            return $null
        }
        if ([string]::IsNullOrEmpty($installChoice) -or $installChoice -eq '1') { break }
        Write-Host 'Нажмите Enter, 1 или 0.' -ForegroundColor Yellow
    } while ($true)

    if ($createVenv) {
        Write-Host 'Создаётся локальная среда Python (.venv)...' -ForegroundColor Yellow
        $launcherExe = $launcher.Executable
        $launcherPrefix = @($launcher.Prefix)
        & $launcherExe @launcherPrefix -m venv (Join-Path $ProjectRoot '.venv') | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать локальную среду Python.' }
    }
    if (-not (Test-Path -LiteralPath $PythonInVenv -PathType Leaf)) {
        throw 'Среда Python была создана некорректно. Проверьте установку Python.'
    }

    Write-Host 'Устанавливаются библиотеки из requirements.txt...' -ForegroundColor Cyan
    & $PythonInVenv -m pip install --disable-pip-version-check -r $Requirements | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw 'Не удалось установить библиотеки. Проверьте интернет и повторите запуск start.cmd.'
    }

    & $PythonInVenv $DependencyCheck $Requirements | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw 'После установки зависимости не прошли проверку. Обработка не запускалась.'
    }
    Write-Host 'Установка завершена. При следующих запусках библиотеки будут проверяться автоматически.' -ForegroundColor Green
    return $PythonInVenv
}

function Invoke-InteractiveMerger {
    Write-Host ''
    Write-Host '====================================================' -ForegroundColor Cyan
    Write-Host '  PDF MERGER  |  Локальная обработка по датам' -ForegroundColor Cyan
    Write-Host '====================================================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host 'Проверка рабочих папок:'

    $createdInput = Assert-FolderOrCreate $InputDir
    $createdOutput = Assert-FolderOrCreate $OutputDir
    Ensure-LocalEnv
    $python = Ensure-LocalPython
    if ($null -eq $python) { return 0 }
    if ($createdInput -or $createdOutput) {
        Write-Host ''
        Write-Host 'Рабочие папки подготовлены. Обработка не запускалась.' -ForegroundColor Yellow
        Write-Host 'Поместите PDF в папку input и запустите start.cmd ещё раз.'
        return 0
    }

    # Select a patient only if actual child workspaces exist. Never combine
    # documents across patient directories, including in the output.
    $patients = @(Get-ChildItem -LiteralPath $InputDir -Directory | Where-Object {
        -not ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    } | Sort-Object Name)
    if ($patients.Count -gt 0) {
        Write-Host 'Выберите рабочую папку пациента:'
        for ($i = 0; $i -lt $patients.Count; $i++) {
            Write-Host ('  [{0}] {1}' -f ($i + 1), $patients[$i].Name)
        }
        Write-Host '  [0] Отмена'
        do {
            $selected = 0
            $valid = [int]::TryParse((Read-Host 'Номер пациента'), [ref]$selected)
            if ($valid -and $selected -eq 0) { return 0 }
        } until ($valid -and $selected -ge 1 -and $selected -le $patients.Count)
        $patient = $patients[$selected - 1]
        $InputDir = $patient.FullName
        $OutputDir = Join-Path $OutputDir $patient.Name
        if ((Test-Path -LiteralPath $OutputDir) -and ((Get-Item -LiteralPath $OutputDir).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            throw 'Папка результатов пациента является ссылкой; обработка остановлена.'
        }
        $null = Assert-FolderOrCreate $OutputDir
        if (@(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'input') -File | Where-Object {$_.Extension -ieq '.pdf'}).Count -gt 0) {
            Write-Host 'PDF в корне input не включаются в выбранную папку пациента.' -ForegroundColor Yellow
        }
    }

    $pdfs = @(Get-ChildItem -LiteralPath $InputDir -File | Where-Object {
        $_.Extension -ieq '.pdf' -and -not ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
    })

    Write-Host ''
    if ($pdfs.Count -eq 0) {
        Write-Host 'Папка input пуста: PDF-файлы не обнаружены.' -ForegroundColor Yellow
        Write-Host 'Добавьте файлы с именами YYYY-MM-DD-Название.pdf и запустите программу повторно.'
        return 0
    }

    Write-Host ("Обнаружено PDF-файлов: {0}" -f $pdfs.Count) -ForegroundColor Green
    Write-Host ''
    Write-Host 'Запустить проверку и объединение?'
    Write-Host '  [1] Локально, без AI и внешних запросов' -ForegroundColor Green
    Write-Host '  [2] AI-семантика только для непонятных PDF (меньше запросов)'
    Write-Host '  [3] AI-семантика для всех PDF (первый раз может быть платным)'
    Write-Host '  [0] Нет, выйти'
    Write-Host 'Для [2] и [3] во внешний API передаётся текст из PDF (до 8500 символов).' -ForegroundColor Yellow
    Write-Host 'Текст может включать конфиденциальные медицинские сведения.' -ForegroundColor Yellow
    Write-Host 'Результаты семантической проверки кэшируются локально по содержимому PDF.'
    $aiClassification = $null
    do {
        $choice = (Read-Host 'Выберите действие [1/2/3/0]').Trim().ToLowerInvariant()
        if ($choice -in @('0', 'нет', 'н', 'n', 'no')) {
            Write-Host 'Отменено. Файлы не изменены.'
            return 0
        }
        if ($choice -in @('1', 'да', 'д', 'y', 'yes')) { break }
        if ($choice -eq '2') { $aiClassification = 'review'; break }
        if ($choice -eq '3') { $aiClassification = 'all'; break }
        Write-Host 'Введите 1 (локально), 2 (только спорные), 3 (все) или 0.' -ForegroundColor Yellow
    } while ($true)

    Write-Host ''
    Write-Host 'Проверка имён PDF и объединение по дате...' -ForegroundColor Cyan
    & $python $MergeProgram --input-dir $InputDir --output-dir $OutputDir --check-names | Out-Host
    $checkCode = $LASTEXITCODE
    if ($checkCode -notin @(0, 3)) {
        throw 'Не удалось проверить имена PDF. Объединение не запускалось.'
    }

    $useAI = $false
    if ($checkCode -eq 3) {
        Write-Host ''
        Write-Host 'Найдены PDF без корректного префикса YYYY-MM-DD-.' -ForegroundColor Yellow
        Write-Host 'AI может определить дату и переименовать эти файлы перед объединением.'
        Write-Host 'ВНИМАНИЕ: для AI будут отправлены имя файла и текст первых двух страниц' -ForegroundColor Yellow
        Write-Host '(не более 10000 символов) во внешний API. PDF целиком не передаётся.' -ForegroundColor Yellow
        Write-Host 'Это может включать конфиденциальную медицинскую информацию.'
        Write-Host '  [1] Разрешить AI-переименование (нужен API ключ в .env)' -ForegroundColor Cyan
        Write-Host '  [2] Продолжить локально, пропустив файлы с неверными именами'
        Write-Host '  [0] Отмена'
        do {
            $aiChoice = (Read-Host 'Выберите действие [1/2/0]').Trim()
            if ($aiChoice -eq '0') {
                Write-Host 'Отменено. Файлы не изменены.'
                return 0
            }
            if ($aiChoice -eq '1') {
                $useAI = $true
                break
            }
            if ($aiChoice -eq '2') { break }
            Write-Host 'Введите 1, 2 или 0.' -ForegroundColor Yellow
        } while ($true)
    }

    # Existing PDFs may have been generated by an older classifier and can
    # omit analyses. Never silently skip/reuse them without an explicit choice.
    $existingResults = @(
        foreach ($folder in @('Анализы', 'Обследования', 'На_проверку')) {
            $dir = Join-Path $OutputDir $folder
            if (Test-Path -LiteralPath $dir -PathType Container) {
                Get-ChildItem -LiteralPath $dir -File |
                    Where-Object { $_.Extension -ieq '.pdf' }
            }
        }
    )
    $replaceExisting = $false
    if ($existingResults.Count -gt 0) {
        Write-Host ''
        Write-Host 'В output уже есть результаты предыдущей обработки.' -ForegroundColor Yellow
        Write-Host 'Они могут быть неполными после исправления классификатора.'
        Write-Host '  [1] Пересобрать и заменить существующие результаты из текущих input PDF' -ForegroundColor Cyan
        Write-Host '  [2] Сохранить существующие результаты (совпадающие имена будут пропущены)'
        Write-Host '  [0] Отмена'
        do {
            $existingChoice = (Read-Host 'Выберите действие [1/2/0]').Trim()
            if ($existingChoice -eq '0') {
                Write-Host 'Отменено. Файлы не изменены.'
                return 0
            }
            if ($existingChoice -eq '1') { $replaceExisting = $true; break }
            if ($existingChoice -eq '2') { break }
            Write-Host 'Введите 1, 2 или 0.' -ForegroundColor Yellow
        } while ($true)
    }

    $mergeArgs = @('--input-dir', $InputDir, '--output-dir', $OutputDir)
    if ($useAI) { $mergeArgs += '--rename-with-ai' }
    if ($replaceExisting) { $mergeArgs += '--overwrite' }
    if ($null -ne $aiClassification) {
        $mergeArgs += @('--classify-with-ai', $aiClassification)
        Write-Host ('AI-семантика: режим {0}, категорию переданных документов определяет модель.' -f $aiClassification) -ForegroundColor Cyan
    }
    if ($useAI) {
        Write-Host 'AI: запускается проверка документов, прогресс будет отображаться ниже.' -ForegroundColor Cyan
        Write-Host 'Диагностика без имён файлов и ключей: logs\ai-progress.log'
    }
    & $python -u $MergeProgram @mergeArgs | Out-Host
    $code = $LASTEXITCODE
    Write-Host ''
    if ($code -eq 0) {
        Write-Host 'Готово. Результаты находятся в папке output.' -ForegroundColor Green
        if ($useAI) {
            Write-Host 'Содержимое PDF не изменено. Имена файлов с неверными датами могли быть исправлены.'
        } else {
            Write-Host 'Исходные PDF в input не изменены.'
        }
    } else {
        Write-Host ("Обработка завершилась с ошибками (код {0}). Смотрите сообщения выше." -f $code) -ForegroundColor Red
    }
    return [int]$code
}

try {
    $exitCode = Invoke-InteractiveMerger
} catch {
    Write-Host ''
    Write-Host ('ОШИБКА: ' + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
}
exit $exitCode

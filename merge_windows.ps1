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

$ProjectRoot = $PSScriptRoot
$InputDir = Join-Path $ProjectRoot 'input'
$OutputDir = Join-Path $ProjectRoot 'output'
$PythonInVenv = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Requirements = Join-Path $ProjectRoot 'requirements.txt'
$MergeProgram = Join-Path $ProjectRoot 'pdf_merger.py'

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

    if (-not (Test-Path -LiteralPath $PythonInVenv -PathType Leaf)) {
        $launcher = Find-PythonLauncher
        Write-Host 'Создаётся локальная среда Python (.venv)...' -ForegroundColor Yellow
        $launcherExe = $launcher.Executable
        $launcherPrefix = @($launcher.Prefix)
        & $launcherExe @launcherPrefix -m venv (Join-Path $ProjectRoot '.venv') | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать локальную среду Python.' }
    }
    if (-not (Test-Path -LiteralPath $PythonInVenv -PathType Leaf)) {
        throw 'Среда Python была создана некорректно. Проверьте установку Python.'
    }

    # Avoid pip/network on repeat runs. Source PDFs are never given to pip.
    & $PythonInVenv -c 'import importlib.util,sys;sys.exit(0 if importlib.util.find_spec("pypdf") else 1)'
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Устанавливается библиотека pypdf (интернет нужен только для установки кода)...' -ForegroundColor Yellow
        & $PythonInVenv -m pip install -r $Requirements | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw 'Не удалось установить pypdf. Проверьте подключение к интернету и запустите ещё раз.'
        }
    }
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
    if ($createdInput -or $createdOutput) {
        Write-Host ''
        Write-Host 'Рабочие папки подготовлены. Обработка не запускалась.' -ForegroundColor Yellow
        Write-Host 'Поместите PDF в папку input и запустите start.cmd ещё раз.'
        return 0
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
    Write-Host '  [1] Да, запустить проверку' -ForegroundColor Green
    Write-Host '  [0] Нет, выйти'
    do {
        $choice = (Read-Host 'Выберите действие [1/0]').Trim().ToLowerInvariant()
        if ($choice -in @('0', 'нет', 'н', 'n', 'no')) {
            Write-Host 'Отменено. Файлы не изменены.'
            return 0
        }
        if ($choice -in @('1', 'да', 'д', 'y', 'yes')) { break }
        Write-Host 'Введите 1 (Да) или 0 (Нет).' -ForegroundColor Yellow
    } while ($true)

    Write-Host ''
    Write-Host 'Проверка имён PDF и объединение по дате...' -ForegroundColor Cyan
    $python = Ensure-LocalPython
    & $python $MergeProgram | Out-Host
    $code = $LASTEXITCODE
    Write-Host ''
    if ($code -eq 0) {
        Write-Host 'Готово. Результаты находятся в папке output.' -ForegroundColor Green
        Write-Host 'Исходные PDF в input не изменены.'
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

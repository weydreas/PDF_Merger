<#
.SYNOPSIS
  Update PDF Merger safely from origin/main, then run its normal launcher.
.DESCRIPTION
  Fail closed on missing Git, wrong repository, local changes, or a failed
  fast-forward. Never delete/overwrite local .env, PDFs, or logs.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot
$StartCmd = Join-Path $ProjectRoot 'start.cmd'

try {
    if (-not (Test-Path -LiteralPath $StartCmd -PathType Leaf)) {
        throw 'Не найден start.cmd рядом с update-and-start.cmd.'
    }

    $gitCommand = Get-Command 'git.exe' -ErrorAction SilentlyContinue
    if ($null -eq $gitCommand) {
        throw 'Git for Windows не найден. Установите Git и повторите запуск.'
    }
    $gitExe = $gitCommand.Source

    $topLevel = & $gitExe -C $ProjectRoot rev-parse --show-toplevel
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($topLevel)) {
        throw 'Не удалось определить корень Git-репозитория.'
    }
    $actualRoot = ([System.IO.Path]::GetFullPath([string]$topLevel)).TrimEnd([char[]]@('\', '/'))
    $expectedRoot = ([System.IO.Path]::GetFullPath($ProjectRoot)).TrimEnd([char[]]@('\', '/'))
    if (-not [string]::Equals($actualRoot, $expectedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'update-and-start.cmd должен находиться в корне клонированного PDF Merger.'
    }

    # Changes to tracked files and untracked, non-ignored files must be
    # resolved manually; local ignored .env, input/, output/, and logs/ are safe.
    $changes = @(& $gitExe -C $ProjectRoot status --porcelain --untracked-files=normal)
    if ($LASTEXITCODE -ne 0) {
        throw 'Не удалось проверить локальные изменения Git.'
    }
    if ($changes.Count -gt 0) {
        Write-Host ''
        Write-Host 'Обнаружены локальные изменения. Автообновление остановлено:' -ForegroundColor Yellow
        & $gitExe -C $ProjectRoot status --short
        throw 'Ничего не перезаписано. Сохраните или разберите изменения вручную.'
    }

    $branch = & $gitExe -C $ProjectRoot branch --show-current
    if ($LASTEXITCODE -ne 0) {
        throw 'Не удалось определить текущую ветку Git.'
    }
    if ($branch -ne 'main') {
        Write-Host 'Переключение на стабильную ветку main...' -ForegroundColor Cyan
        & $gitExe -C $ProjectRoot switch main
        if ($LASTEXITCODE -ne 0) {
            throw 'Не удалось безопасно переключиться на main.'
        }
    }

    Write-Host ''
    Write-Host 'Проверка обновлений PDF Merger (origin/main)...' -ForegroundColor Cyan
    & $gitExe -C $ProjectRoot pull --ff-only origin main
    if ($LASTEXITCODE -ne 0) {
        throw 'Git не смог выполнить безопасное обновление. Приложение не запущено.'
    }

    if (-not (Test-Path -LiteralPath $StartCmd -PathType Leaf)) {
        throw 'После обновления не найден start.cmd; приложение не запущено.'
    }
    Write-Host ''
    Write-Host 'Обновление выполнено. Запуск PDF Merger...' -ForegroundColor Green
    & $StartCmd
    exit [int]$LASTEXITCODE
} catch {
    Write-Host ''
    Write-Host ('ОШИБКА обновления: ' + $_.Exception.Message) -ForegroundColor Red
    exit 2
}

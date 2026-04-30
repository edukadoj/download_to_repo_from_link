<#
.SYNOPSIS
    Downloads your GitHub repository, extracts it, runs reassemble.bat automatically,
    moves the resulting files to the script's folder, and deletes the unused batch file.
    ZIP and temporary files are sent to the Recycle Bin.
#>

cls

$Owner  = "edukadoj"
$Repo   = "download_to_repo_from_link"
$Branch = "main"

$url      = "https://codeload.github.com/$Owner/$Repo/zip/refs/heads/$Branch"
$OutFile  = Join-Path $PSScriptRoot "downloaded_repo.zip"
$maxAttempts = 10
$retryDelay  = 30  # seconds

# ──────────────── Helper to move items to Recycle Bin ────────────────
function Move-ToRecycleBin {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path
    )
    if (-not (Test-Path $Path)) { return }
    try {
        $shell   = New-Object -ComObject Shell.Application
        $item    = Get-Item $Path
        $folder  = $shell.Namespace($item.Directory.FullName)
        $file    = $folder.ParseName($item.Name)
        if ($file) {
            $file.InvokeVerb("delete")
            Write-Host "Sent to Recycle Bin: $Path" -ForegroundColor DarkGreen
        }
        else {
            throw "Could not locate item in shell namespace."
        }
    }
    catch {
        Write-Host "WARNING: Could not recycle '$Path' ($($_.Exception.Message)). Deleting permanently instead." -ForegroundColor Yellow
        Remove-Item $Path -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ──────────────── Helper function ────────────────
function Run-And-Move {
    param([string]$TempDir)
    $batFiles = Get-ChildItem -Path $TempDir -Filter "*.bat" -Recurse
    if (-not $batFiles) {
        Write-Host "No batch files found in the repository. Nothing to reassemble." -ForegroundColor Red
        return
    }

    foreach ($bat in $batFiles) {
        $folder = $bat.Directory.FullName
        Write-Host "Found batch file: $($bat.Name) in $folder" -ForegroundColor Cyan

        # Automatically press keys: "y" to delete parts after reassemble, "y" to close final pause
        $keyInput = "y`ny`n"
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "cmd.exe"
        $psi.Arguments = "/c `"cd /d `"$folder`" && `"$($bat.Name)`"`""
        $psi.RedirectStandardInput = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.WorkingDirectory = $folder

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $psi
        $process.Start() | Out-Null
        $process.StandardInput.Write($keyInput)
        $process.StandardInput.Close()
        $process.WaitForExit()
        Write-Host "Batch file finished with exit code $($process.ExitCode)."
        $process.Dispose()

        # Move everything EXCEPT .bat files to the script's root
        Get-ChildItem -Path $folder -File | Where-Object { $_.Extension -ne ".bat" } | Move-Item -Destination $PSScriptRoot -Force
        Write-Host "Moved non-batch files from $folder to $PSScriptRoot"

        # Remove the batch file itself (and any leftover .bat if still present)
        Remove-Item -Path $bat.FullName -Force -ErrorAction SilentlyContinue
        Write-Host "Deleted batch file: $($bat.Name)"
    }
}

# ──────────────── Main loop ────────────────
Write-Host "Downloading repository: $Owner/$Repo ($Branch)" -ForegroundColor Green
Write-Host "Output zip: $OutFile"
Write-Host ""

for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    Write-Host "Attempt $attempt of $maxAttempts" -ForegroundColor Cyan

    # Send previous ZIP to Recycle Bin instead of deleting
    if (Test-Path $OutFile) {
        Move-ToRecycleBin $OutFile
    }

    $curlArgs = @(
        '-L',
        '-o', $OutFile,
        '-N',
        '--ssl-no-revoke',
        '--fail',
        '--retry', '3',
        '--retry-delay', '10',
        $url
    )

    $process = Start-Process -FilePath "curl.exe" -ArgumentList $curlArgs -Wait -NoNewWindow -PassThru

    if ($process.ExitCode -eq 0) {
        Write-Host "Download successful." -ForegroundColor Green
        break
    }
    else {
        Write-Host "Download failed (exit code $($process.ExitCode))." -ForegroundColor Red
        if ($attempt -lt $maxAttempts) {
            Write-Host "Waiting $retryDelay seconds before retry..."
            Start-Sleep -Seconds $retryDelay
        }
        else {
            Write-Host "Maximum attempts reached. Exiting."
            exit 1
        }
    }
}

# ──────────────── Extract and process ────────────────
$tempDir = Join-Path $PSScriptRoot "temp_repo_extract"
if (Test-Path $tempDir) { Move-ToRecycleBin $tempDir }

Write-Host "`nExtracting ZIP to $tempDir ..."
Expand-Archive -Path $OutFile -DestinationPath $tempDir -Force
# After extraction, recycle the ZIP
Move-ToRecycleBin $OutFile

# Flatten inner folder if the ZIP wrapped everything in a single folder
$topFolders = Get-ChildItem -Path $tempDir -Directory
if ($topFolders.Count -eq 1) {
    $innerFolder = $topFolders[0].FullName
    Get-ChildItem -Path $innerFolder | Move-Item -Destination $tempDir -Force
    Remove-Item $innerFolder -Force
    Write-Host "Flattened inner folder."
}

# Run batch files, move results, and delete the batch files
Run-And-Move -TempDir $tempDir

# Recycle the temporary folder
Move-ToRecycleBin $tempDir
Write-Host "`nAll done. Your files are in: $PSScriptRoot" -ForegroundColor Green
pause
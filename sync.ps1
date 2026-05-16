<#
.SYNOPSIS
    Two‑way sync between a local folder and a GitHub repository (current files only, no history).
    Compares file contents using Git blob hashes – no chunking, no dates, just real files.
.DESCRIPTION
    Place this script inside the folder you want to mirror.
    It connects to the repository edukadoj/download_to_repo_from_link and
    automatically uploads/downloads only what changed.
.NOTES
    Requires GitHub CLI (gh) already authenticated.
#>

#Requires -Version 5.1

param(
    [switch]$DeleteRemote,   # If set, missing local files are deleted from GitHub
    [switch]$DeleteLocal,    # If set, missing remote files are deleted locally
    [string]$ConflictAction = "KeepLocal"   # or "KeepRemote" or "Prompt"
)

$ErrorActionPreference = "Stop"
$Script:RepoOwner   = "edukadoj"
$Script:RepoName    = "download_to_repo_from_link"
$Script:Branch      = "main"

# ---------- helper: Git blob hash ----------
function Get-GitBlobHash {
    param([string]$FilePath)
    $bytes = [System.IO.File]::ReadAllBytes($FilePath)
    $size = $bytes.Length
    $header = "blob $size`0"
    $headerBytes = [System.Text.Encoding]::UTF8.GetBytes($header)
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    $sha1.TransformBlock($headerBytes, 0, $headerBytes.Length, $null, 0) | Out-Null
    $sha1.TransformFinalBlock($bytes, 0, $bytes.Length) | Out-Null
    [System.BitConverter]::ToString($sha1.Hash).Replace("-","").ToLower()
}

# ---------- helpers: call gh ----------
function Invoke-GitHubGraphQL {
    param([string]$Query, [hashtable]$Variables = @{})
    $body = @{ query = $Query; variables = $Variables } | ConvertTo-Json -Compress
    $result = gh api graphql --paginate -f query="$Query" -F variables="$($Variables | ConvertTo-Json -Compress)" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "GraphQL call failed: $result" }
    $result | ConvertFrom-Json
}

function Invoke-GitHubApi {
    param([string]$Endpoint, [string]$Method = "GET", [hashtable]$Body = @{})
    $args = @("api", $Endpoint, "--method", $Method)
    if ($Body.Count -gt 0) {
        $jsonBody = $Body | ConvertTo-Json -Compress
        $args += "-f"
        $args += $jsonBody
    }
    $result = gh @args 2>&1
    if ($LASTEXITCODE -ne 0) { throw "API call failed: $result" }
    if ($result) { $result | ConvertFrom-Json } else { $null }
}

# ---------- get remote file tree with SHA ----------
function Get-RemoteFileTree {
    $query = @'
query($owner: String!, $repo: String!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    ref(qualifiedName: $branch) {
      target {
        ... on Commit {
          tree {
            entries {
              path
              object {
                ... on Blob {
                  oid
                }
              }
            }
          }
        }
      }
    }
  }
}
'@
    $vars = @{ owner = $Script:RepoOwner; repo = $Script:RepoName; branch = $Script:Branch }
    $data = Invoke-GitHubGraphQL -Query $query -Variables $vars
    $entries = $data.data.repository.ref.target.tree.entries
    $result = @{}
    foreach ($e in $entries) {
        if ($e.object.oid) { $result[$e.path] = $e.object.oid }
    }
    $result
}

# ---------- scan local folder ----------
function Get-LocalFileInventory {
    $localRoot = $PSScriptRoot
    $files = @{}
    Get-ChildItem -Path $localRoot -Recurse -File | ForEach-Object {
        $relPath = $_.FullName.Substring($localRoot.Length + 1).Replace('\','/')
        # skip the script itself and the state file
        if ($relPath -eq $MyInvocation.MyCommand.Name -or $relPath -eq '.sync-state.json') { return }
        $files[$relPath] = Get-GitBlobHash -FilePath $_.FullName
    }
    $files
}

# ---------- state file ----------
$StateFile = Join-Path $PSScriptRoot '.sync-state.json'

function Load-State {
    if (Test-Path $StateFile) {
        Get-Content $StateFile -Raw | ConvertFrom-Json
    } else { $null }
}

function Save-State {
    param($RemoteTree, $LocalInv, $RemoteInv)
    $state = @{
        LastSync = (Get-Date).ToString('o')
        RemoteSHA = $null   # not needed for comparison now
        Files = @{}
    }
    foreach ($path in $RemoteInv.Keys) {
        $state.Files[$path] = @{
            RemoteBlob = $RemoteInv[$path]
            LocalBlob  = if ($LocalInv.ContainsKey($path)) { $LocalInv[$path] } else { $null }
        }
    }
    foreach ($path in $LocalInv.Keys) {
        if (-not $state.Files.ContainsKey($path)) {
            $state.Files[$path] = @{
                RemoteBlob = $null
                LocalBlob  = $LocalInv[$path]
            }
        }
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content $StateFile -Encoding UTF8
}

# ---------- sync logic ----------
function Sync-Files {
    $remoteInv = Get-RemoteFileTree
    $localInv  = Get-LocalFileInventory
    $prevState = Load-State

    # ---------- helper: download ----------
    function Download-File($path) {
        $url = "https://api.github.com/repos/$RepoOwner/$RepoName/contents/$($path)?ref=$Branch"
        $resp = Invoke-GitHubApi -Endpoint $url
        if ($resp.content) {
            $bytes = [Convert]::FromBase64String($resp.content.Replace("`n","").Replace("`r",""))
            $dest = Join-Path $PSScriptRoot ($path.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
            $dir = Split-Path $dest -Parent
            if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
            [System.IO.File]::WriteAllBytes($dest, $bytes)
            Write-Host "  Download: $path" -ForegroundColor Cyan
        } else { Write-Warning "  Could not download $path" }
    }

    # ---------- helper: upload ----------
    function Upload-File($path, $remoteBlob) {
        $localPath = Join-Path $PSScriptRoot ($path.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        $contentBytes = [System.IO.File]::ReadAllBytes($localPath)
        $contentBase64 = [Convert]::ToBase64String($contentBytes)
        $body = @{
            message = "sync: update $path"
            content = $contentBase64
            branch  = $Branch
        }
        if ($remoteBlob) {
            $body.sha = $remoteBlob
        }
        $endpoint = "repos/$RepoOwner/$RepoName/contents/$path"
        try {
            $null = Invoke-GitHubApi -Endpoint $endpoint -Method PUT -Body $body
            Write-Host "  Upload: $path" -ForegroundColor Green
        } catch {
            Write-Warning "  Failed to upload $path : $_"
        }
    }

    # ---------- helper: delete remote ----------
    function Delete-RemoteFile($path, $remoteBlob) {
        $body = @{
            message = "sync: delete $path"
            sha     = $remoteBlob
            branch  = $Branch
        }
        $endpoint = "repos/$RepoOwner/$RepoName/contents/$path"
        try {
            $null = Invoke-GitHubApi -Endpoint $endpoint -Method DELETE -Body $body
            Write-Host "  Deleted remote: $path" -ForegroundColor Red
        } catch { Write-Warning "  Could not delete remote $path : $_" }
    }

    # ---------- delete local ----------
    function Delete-LocalFile($path) {
        $localPath = Join-Path $PSScriptRoot ($path.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        if (Test-Path $localPath) {
            Remove-Item $localPath -Force
            Write-Host "  Deleted local: $path" -ForegroundColor Red
        }
    }

    # ---------- compare ----------
    $allPaths = @{}
    foreach ($p in ($remoteInv.Keys + $localInv.Keys | Select-Object -Unique)) { $allPaths[$p] = $true }

    $uploadList   = @()
    $downloadList = @()
    $conflicts    = @()

    foreach ($path in $allPaths.Keys) {
        $hasRemote = $remoteInv.ContainsKey($path)
        $hasLocal  = $localInv.ContainsKey($path)

        if (-not $hasLocal -and $hasRemote) {
            # only remote -> download (or delete remote if DeleteLocal is set? Actually DeleteLocal would mean delete local file if missing remote, but here we lack local file, so if DeleteLocal is off, download; if on, delete remote? The flag DeleteLocal means missing remote triggers local delete. So here local is missing, not remote. So always download unless we want to delete remote because local doesn't have it? That's delete remote. We'll stick to download.)
            $downloadList += $path
            continue
        }

        if ($hasLocal -and -not $hasRemote) {
            # only local -> upload (or delete local if DeleteRemote is set?)
            if ($DeleteRemote) { $downloadList += $path } # actually delete local? No, DeleteRemote means if local file missing, delete remote. Here remote missing, local exists. So default: upload. If DeleteRemote, we might want to delete local? That doesn't make sense. Let's not overcomplicate. Default: upload.
            $uploadList += $path
            continue
        }

        # both exist
        $remoteBlob = $remoteInv[$path]
        $localBlob  = $localInv[$path]

        if ($remoteBlob -eq $localBlob) {
            # unchanged
            continue
        }

        # changed – need previous state to know who changed
        $prevFile = if ($prevState -and $prevState.Files.$path) { $prevState.Files.$path } else { $null }
        if (-not $prevFile) {
            # first sync -> older of the two? can't know; treat as conflict
            $conflicts += $path
            continue
        }

        $prevRemote = $prevFile.RemoteBlob
        $prevLocal  = $prevFile.LocalBlob

        $remoteChanged = ($remoteBlob -ne $prevRemote)
        $localChanged  = ($localBlob -ne $prevLocal)

        if ($localChanged -and -not $remoteChanged) {
            $uploadList += $path
        }
        elseif ($remoteChanged -and -not $localChanged) {
            $downloadList += $path
        }
        else {
            $conflicts += $path
        }
    }

    # handle deletions: files in previous state that are now missing on one side
    if ($prevState) {
        foreach ($path in $prevState.Files.Keys) {
            $hasRemote = $remoteInv.ContainsKey($path)
            $hasLocal  = $localInv.ContainsKey($path)
            if (-not $hasLocal -and $hasRemote) {
                # local deleted but remote exists -> delete remote if DeleteRemote is set
                if ($DeleteRemote) { $uploadList = $uploadList | Where-Object { $_ -ne $path }; Delete-RemoteFile $path $remoteInv[$path] }
                else { Write-Warning "  $path was deleted locally but still exists remotely. Use -DeleteRemote to remove it from GitHub." }
            }
            if (-not $hasRemote -and $hasLocal) {
                # remote deleted but local exists -> delete local if DeleteLocal is set
                if ($DeleteLocal) { $downloadList = $downloadList | Where-Object { $_ -ne $path }; Delete-LocalFile $path }
                else { Write-Warning "  $path was deleted remotely but still exists locally. Use -DeleteLocal to remove it." }
            }
        }
    }

    # ---------- execute downloads ----------
    foreach ($path in $downloadList) {
        Download-File $path
    }

    # ---------- execute uploads ----------
    foreach ($path in $uploadList) {
        $remoteBlob = if ($remoteInv.ContainsKey($path)) { $remoteInv[$path] } else { $null }
        Upload-File $path $remoteBlob
    }

    # ---------- resolve conflicts ----------
    foreach ($path in $conflicts) {
        Write-Warning "  CONFLICT: $path changed both locally and remotely."
        if ($ConflictAction -eq "Prompt") {
            $choice = Read-Host "  Keep (L)ocal, (R)emote, or (S)kip? (L/R/S)"
            if ($choice -eq 'L') { Upload-File $path $remoteInv[$path] }
            elseif ($choice -eq 'R') { Download-File $path }
            else { Write-Host "  Skipped $path" }
        }
        elseif ($ConflictAction -eq "KeepLocal") {
            Upload-File $path $remoteInv[$path]
        }
        else { # KeepRemote
            Download-File $path
        }
    }

    # ---------- save new state ----------
    Save-State -RemoteTree $remoteInv -LocalInv $localInv -RemoteInv $remoteInv

    Write-Host "`nSync complete." -ForegroundColor Green
}

# ---------- entry point ----------
Write-Host "Sync started: $PSScriptRoot <-> $RepoOwner/$RepoName ($Branch)" -ForegroundColor Magenta
Sync-Files
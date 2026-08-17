[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "dist"),
    [string]$RojoExecutable = ""
)

$ErrorActionPreference = "Stop"

$extensionSource = Join-Path $PSScriptRoot "blender_extension"
$manifestPath = Join-Path $extensionSource "blender_manifest.toml"
$studioProject = Join-Path $PSScriptRoot "roblox_plugin\default.project.json"
$manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $manifestPath
$versionMatch = [regex]::Match($manifest, '(?m)^version\s*=\s*"([^"]+)"\s*$')
if (-not $versionMatch.Success) {
    throw "Could not read the extension version from blender_manifest.toml"
}
$version = $versionMatch.Groups[1].Value
if ($RojoExecutable) {
    $rojoPath = [System.IO.Path]::GetFullPath($RojoExecutable)
    if (-not (Test-Path -LiteralPath $rojoPath -PathType Leaf)) {
        throw "Rojo executable was not found: $rojoPath"
    }
}
else {
    $rojoPath = (Get-Command rojo -ErrorAction Stop).Source
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$zipPath = Join-Path $resolvedOutput "RobloxPrimitiveSync-Blender-$version.zip"
$rbxmPath = Join-Path $resolvedOutput "RobloxPrimitiveSync-Studio-$version.rbxm"

$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$stagingDirectory = Join-Path $temporaryRoot ("RobloxPrimitiveSync-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stagingDirectory | Out-Null

try {
    Get-ChildItem -LiteralPath $extensionSource -Force |
        Where-Object { $_.Name -ne "__pycache__" } |
        Copy-Item -Destination $stagingDirectory -Recurse -Force

    Compress-Archive -Path (Join-Path $stagingDirectory "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force
    & $rojoPath build $studioProject --output $rbxmPath
    if ($LASTEXITCODE -ne 0) {
        throw "Rojo build failed with exit code $LASTEXITCODE"
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $entryNames = @($archive.Entries | ForEach-Object { $_.FullName })
        if ("blender_manifest.toml" -notin $entryNames) {
            throw 'Blender archive has no root entry named "blender_manifest.toml"'
        }
        if ($entryNames | Where-Object { $_ -like "./*" }) {
            throw 'Blender archive contains invalid "./" entry prefixes'
        }
    }
    finally {
        $archive.Dispose()
    }

    Write-Host "Built $zipPath"
    Write-Host "Built $rbxmPath"
}
finally {
    $resolvedStaging = [System.IO.Path]::GetFullPath($stagingDirectory)
    if ($resolvedStaging.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $resolvedStaging -PathType Container)) {
        Remove-Item -LiteralPath $resolvedStaging -Recurse -Force
    }
}

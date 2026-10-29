param(
    [ValidateSet('icraft_npu_yolo')][string]$Target = 'icraft_npu_yolo',
    [Parameter(Mandatory = $true)][string]$IcraftRoot,
    [string]$BuildDir = 'build\npu-aarch64-icraft3361',
    [string]$Distro = 'Debian',
    [string]$Chroot = '/opt/fpai-bullseye',
    [ValidateRange(1, 64)][int]$Jobs = 4,
    [switch]$CleanBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Convert-ToWslPath([string]$WindowsPath) {
    $result = & wsl.exe -d $Distro --exec wslpath -a -u $WindowsPath
    if ($LASTEXITCODE -ne 0 -or -not $result) { throw "Cannot convert path: $WindowsPath" }
    return ($result | Select-Object -Last 1).Trim()
}

function Invoke-WslRoot([string[]]$Words) {
    & wsl.exe -d $Distro -u root --exec @Words
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed: $($Words[0]) ($LASTEXITCODE)" }
}

# Use a prepared Bullseye root to match the board's glibc. No installation or
# recursive copy/delete is performed; only project/SDK directories are bound.
if (-not $Chroot.StartsWith('/') -or $Chroot.TrimEnd('/') -eq '') {
    throw 'Chroot must name an existing absolute Linux build root, such as /opt/fpai-bullseye.'
}
$Chroot = $Chroot.TrimEnd('/')
$sdkAbsolute = [IO.Path]::GetFullPath($IcraftRoot)
if (-not (Test-Path -LiteralPath $sdkAbsolute -PathType Container)) {
    throw "Icraft SDK directory does not exist: $sdkAbsolute"
}
$buildAbsolute = if ([IO.Path]::IsPathRooted($BuildDir)) {
    [IO.Path]::GetFullPath($BuildDir)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $BuildDir))
}
New-Item -ItemType Directory -Path $buildAbsolute -Force | Out-Null
$sourceWsl = Convert-ToWslPath $PSScriptRoot
$sdkWsl = Convert-ToWslPath $sdkAbsolute
$buildWsl = Convert-ToWslPath $buildAbsolute
Invoke-WslRoot @('test', '-x', "$Chroot/usr/bin/cmake")

foreach ($hostPath in @($sourceWsl, $sdkWsl, $buildWsl)) {
    $guestPath = "$Chroot$hostPath"
    Invoke-WslRoot @('mkdir', '-p', $guestPath)
    $hostIdentity = & wsl.exe -d $Distro -u root --exec stat -Lc '%d:%i' -- $hostPath
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect source directory: $hostPath" }
    $guestIdentity = & wsl.exe -d $Distro -u root --exec stat -Lc '%d:%i' -- $guestPath
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect build-root directory: $guestPath" }
    if ($hostIdentity -eq $guestIdentity) { continue }
    & wsl.exe -d $Distro -u root --exec mountpoint -q -- $guestPath
    if ($LASTEXITCODE -eq 0) { throw "A different mount already occupies $guestPath" }
    Invoke-WslRoot @('mount', '--bind', $hostPath, $guestPath)
}

Invoke-WslRoot @('/usr/sbin/chroot', $Chroot, '/usr/bin/env', 'LANG=C', '/usr/bin/cmake',
    '-S', $sourceWsl, '-B', $buildWsl,
    '-DCMAKE_SYSTEM_NAME=Linux', '-DCMAKE_SYSTEM_PROCESSOR=aarch64',
    '-DCMAKE_CXX_COMPILER=/usr/bin/aarch64-linux-gnu-g++',
    "-DCMAKE_PREFIX_PATH=$sdkWsl", '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_SKIP_BUILD_RPATH=ON')
$buildWords = @('/usr/sbin/chroot', $Chroot, '/usr/bin/env', 'LANG=C', '/usr/bin/cmake',
    '--build', $buildWsl, '--target', $Target, '--parallel', [string]$Jobs)
if ($CleanBuild) { $buildWords += '--clean-first' }
Invoke-WslRoot $buildWords
$header = & wsl.exe -d $Distro -u root --exec /usr/sbin/chroot $Chroot /usr/bin/aarch64-linux-gnu-readelf -h "$buildWsl/$Target"
if ($LASTEXITCODE -ne 0 -or -not ($header -match 'Machine:\s+AArch64')) {
    throw 'Build output is not an AArch64 ELF.'
}
Write-Output "Built AArch64 executable: $(Join-Path $buildAbsolute $Target)"

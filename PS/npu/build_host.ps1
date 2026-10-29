param(
    [string]$IcraftRoot = $env:ICRAFT_ROOT,
    [string]$Distro = 'Debian'
)
$ErrorActionPreference = 'Stop'
if (-not $IcraftRoot) { throw 'Supply -IcraftRoot or set ICRAFT_ROOT to the Icraft SDK directory.' }
function LinuxPath([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path).Replace('\', '/')
    if ($resolved -notmatch '^([A-Za-z]):/(.*)$') { throw "Expected a Windows drive path: $Path" }
    return '/mnt/' + $Matches[1].ToLowerInvariant() + '/' + $Matches[2]
}
$projectPath = LinuxPath $PSScriptRoot
$sdkPath = LinuxPath $IcraftRoot
$localLib = "$projectPath/tools/host-deps/root/usr/lib/x86_64-linux-gnu"
if (-not (Test-Path -LiteralPath "$PSScriptRoot/tools/host-deps/root/usr/lib/x86_64-linux-gnu/libtorch_cpu.so")) {
    throw 'Prepare the local host compatibility libraries first; see Documentation/NPU.md and PS/npu/tools/prepare_host_compat.py.'
}
& wsl.exe -d $Distro --exec cmake -S $projectPath -B "$projectPath/build/sdk-host" `
    "-DCMAKE_PREFIX_PATH=$sdkPath" -DCMAKE_BUILD_TYPE=Release -DPHA_NPU_BUILD_DECODER=ON
if ($LASTEXITCODE -ne 0) { throw 'Host CMake configuration failed' }
& wsl.exe -d $Distro --exec env "LIBRARY_PATH=$localLib" `
    cmake --build "$projectPath/build/sdk-host" --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'Host build failed' }
Write-Host "Built build/sdk-host/icraft_npu_yolo, inspect_compiled and libpha_calibration_decoder.so"
Write-Host "Runtime LD_LIBRARY_PATH=$localLib`:$sdkPath/lib/x86_64-linux-gnu"

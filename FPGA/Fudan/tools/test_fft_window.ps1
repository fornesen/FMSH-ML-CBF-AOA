param(
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [string]$VivadoBin = 'C:\Xilinx\Vivado\2018.3\bin',
    [string]$Python = 'python'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$simulationDirectory = Join-Path $OutputDirectory 'simulation'
New-Item -ItemType Directory -Path $simulationDirectory -Force | Out-Null
& $Python -B (Join-Path $projectRoot 'tests\window_reference.py') $simulationDirectory
if ($LASTEXITCODE -ne 0) { throw 'Reference generation failed' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'fpai_demo_vivado.srcs\adc_dsp\coe\fft_hann_4032_q17.mem') -Destination $simulationDirectory -Force
Push-Location $simulationDirectory
try {
    & (Join-Path $VivadoBin 'xvlog.bat') --sv (Join-Path $projectRoot 'fpai_demo_vivado.srcs\adc_dsp\axis_fft_window.v') (Join-Path $projectRoot 'tests\tb_axis_fft_window.sv') *> simulation_build.log
    if ($LASTEXITCODE -ne 0) { throw 'Verilog compilation failed' }
    & (Join-Path $VivadoBin 'xelab.bat') tb_axis_fft_window -s window_sim *>> simulation_build.log
    if ($LASTEXITCODE -ne 0) { throw 'Simulation elaboration failed' }
    & (Join-Path $VivadoBin 'xsim.bat') window_sim -runall *> simulation_console.log
    if ($LASTEXITCODE -ne 0 -or !(Select-String -LiteralPath 'xsim.log' -Pattern 'PASS: 49152')) {
        throw 'Window simulation failed; inspect simulation_console.log'
    }
    Select-String -LiteralPath 'xsim.log' -Pattern 'PASS:' | ForEach-Object { $_.Line }
}
finally {
    Pop-Location
}

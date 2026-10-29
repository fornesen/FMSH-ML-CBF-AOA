# Verify the actual module-reference synthesis run, including its ROM dependency.
set root [file dirname [file dirname [file normalize [info script]]]]
set_param general.maxThreads 2
open_project [file join $root fpai_demo_vivado.xpr]
source [file join $root tools integrate_fft_window.tcl]
create_ip_run [get_files */adc_dsp.bd]
set runs [get_runs -quiet *axis_fft_window*_synth_1]
if {[llength $runs] != 1} {error "Expected one window module synthesis run, got: $runs"}
reset_run $runs
launch_runs $runs -jobs 1
wait_on_run $runs
if {[get_property STATUS $runs] ne "synth_design Complete!"} {
    error "Window module-reference synthesis failed: [get_property STATUS $runs]"
}
puts "FFT_WINDOW_PROJECT_SYNTHESIS_PASSED"
close_project
exit

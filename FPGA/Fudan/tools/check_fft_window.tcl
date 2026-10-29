# Standalone synthesis and timing check; leaves the full project closed.
set root [file dirname [file dirname [file normalize [info script]]]]
set_param general.maxThreads 2
read_verilog [file join $root fpai_demo_vivado.srcs adc_dsp axis_fft_window.v]
read_mem [file join $root fpai_demo_vivado.srcs adc_dsp coe fft_hann_4032_q17.mem]
synth_design -top axis_fft_window -part xc7z030ffg676-2 -mode out_of_context
create_clock -period 10.000 [get_ports aclk]
opt_design
report_utilization -file window_utilization.txt
write_checkpoint -force window_synth.dcp
place_design
route_design
report_utilization -file window_utilization.txt
report_timing_summary -delay_type min_max -file window_timing.txt
report_drc -file window_drc.txt
write_checkpoint -force window_module.dcp
foreach kind {max min} {
    set path [get_timing_paths -delay_type $kind -max_paths 1]
    if {[llength $path] == 0 || [get_property SLACK $path] < 0} {error "Window module fails $kind timing"}
}
puts "FFT_WINDOW_MODULE_CHECK_PASSED"

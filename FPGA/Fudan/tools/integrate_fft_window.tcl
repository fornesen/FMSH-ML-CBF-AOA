# Run with the intended fpai_demo_vivado.xpr open. Does not launch a build or program hardware.
if {[current_project -quiet] eq ""} {error "Open the PL project before integrating the FFT window"}
set window_root [file dirname [file dirname [file normalize [info script]]]]
set window_rtl [file join $window_root fpai_demo_vivado.srcs adc_dsp axis_fft_window.v]
set window_mem [file join $window_root fpai_demo_vivado.srcs adc_dsp coe fft_hann_4032_q17.mem]
foreach path [list $window_rtl $window_mem] {
    if {![file exists $path]} {error "Missing window source: $path"}
    if {[llength [get_files -quiet $path]] == 0} {add_files -norecurse $path}
}
update_compile_order -fileset sources_1
set window_bd [get_files -quiet */adc_dsp.bd]
if {[llength $window_bd] != 1} {error "Expected exactly one adc_dsp.bd"}
open_bd_design $window_bd
if {[llength [get_bd_cells -quiet axis_fft_window_0]] == 0} {
    set old_net [get_bd_intf_nets -of_objects [get_bd_intf_pins xfft_0/S_AXIS_DATA]]
    set old_pins [get_bd_intf_pins -of_objects $old_net]
    if {[llength $old_pins] != 2 || [lsearch -glob $old_pins */axis_fft_frame_mux_0/m_axis_fft] < 0} {
        error "Unexpected FFT input connection; refusing to replace another processing stage"
    }
    create_bd_cell -type module -reference axis_fft_window axis_fft_window_0
    disconnect_bd_intf_net $old_net [get_bd_intf_pins xfft_0/S_AXIS_DATA]
    connect_bd_intf_net [get_bd_intf_pins axis_fft_frame_mux_0/m_axis_fft] [get_bd_intf_pins axis_fft_window_0/s_axis_fft]
    connect_bd_intf_net [get_bd_intf_pins axis_fft_window_0/m_axis_fft] [get_bd_intf_pins xfft_0/S_AXIS_DATA]
    connect_bd_net [get_bd_ports aclk] [get_bd_pins axis_fft_window_0/aclk]
    connect_bd_net [get_bd_ports aresetn] [get_bd_pins axis_fft_window_0/aresetn]
} else {
    update_module_reference [get_ips *axis_fft_window*]
}
validate_bd_design
save_bd_design
generate_target all $window_bd
update_compile_order -fileset sources_1
puts "FFT_WINDOW_INTEGRATED: 64 zero + 4032 Hann; 4096-point FFT; PHA unchanged"

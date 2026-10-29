# Rebuild the existing PS/DMA network with a physical ADC input; Vivado 2018.3.
set root [file dirname [file normalize [info script]]]
set src $root/network_adc/src
create_project network_adc $root/network_adc/vivado -part xc7z100ffg900-2 -force
set_param general.maxThreads 2
set_property XPM_LIBRARIES {XPM_CDC XPM_MEMORY XPM_FIFO} [current_project]
add_files [glob $src/*.v]
read_ip $src/blk_mem_gen_wave.xci
set_property generate_synth_checkpoint false [get_files $src/blk_mem_gen_wave.xci]
source $src/base_design.tcl
# Keep ROM/AXI registers for PS compatibility, but feed capture from real ADC pins.
set cap [create_bd_cell -type module -reference adc_capture_6ch adc_capture_0]
disconnect_bd_intf_net [get_bd_intf_nets -of_objects [get_bd_intf_pins axis_data_forwarder_0/S_AXIS_IN]] [get_bd_intf_pins axis_data_forwarder_0/S_AXIS_IN]
connect_bd_intf_net [get_bd_intf_pins adc_capture_0/m_axis] [get_bd_intf_pins axis_data_forwarder_0/S_AXIS_IN]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] [get_bd_pins adc_capture_0/clk]
set_property -dict [list CONFIG.CLKOUT3_USED {true} CONFIG.NUM_OUT_CLKS {3} CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {100.000} CONFIG.CLKOUT3_REQUESTED_PHASE {135.000}] [get_bd_cells clk_wiz_0]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out3] [get_bd_pins adc_capture_0/capture_clk]
connect_bd_net [get_bd_pins proc_sys_reset_0/peripheral_aresetn] [get_bd_pins adc_capture_0/rstn]
for {set ch 0} {$ch<6} {incr ch} {
    set p [create_bd_port -dir I -from 11 -to 0 adc_data_$ch]
    connect_bd_net $p [get_bd_pins adc_capture_0/adc_data_$ch]
}
foreach {name dir} {adc_clk O adc_dco I} {
    set p [create_bd_port -dir $dir -from 5 -to 0 $name]
    connect_bd_net $p [get_bd_pins adc_capture_0/$name]
}
validate_bd_design
save_bd_design
generate_target all [get_files design_1.bd]
add_files [make_wrapper -files [get_files design_1.bd] -top]
set_property top design_1_wrapper [current_fileset]
add_files -fileset constrs_1 [glob $src/*.xdc]
set_property USED_IN_SYNTHESIS false [get_files $src/adc_timing.xdc]
set_property USED_IN_SYNTHESIS false [get_files $src/adc_return_latency.xdc]
set_property PROCESSING_ORDER LATE [get_files $src/adc_return_latency.xdc]
update_compile_order -fileset sources_1
launch_runs synth_1 -jobs 1
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 1
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation failed"}
open_run impl_1
report_io -file $root/network_adc/pin_report.rpt
report_drc -file $root/network_adc/drc.rpt
report_timing_summary -report_unconstrained -file $root/network_adc/timing.rpt
write_hwdef -force -file $root/network_adc/design_1_wrapper.hwdef
file copy -force $root/network_adc/vivado/network_adc.runs/impl_1/design_1_wrapper.bit $root/network_adc/network_adc.bit
puts "ADC_BUILD_COMPLETE $root/network_adc/network_adc.bit"
exit

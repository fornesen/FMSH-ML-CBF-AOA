# AD9233 datasheet Table4: tPD=3.1..4.8 ns. Budget an additional 2 ns maximum
# for the outward clock and returning data PCB/connector flight. This is an
# explicit initial interface budget, not a measurement of this assembly.
# Expanded because Vivado 2018.3 XDC does not accept Tcl 'for'. Read after linking
# OOC cells, so the source ODDR clock pins exist (USED_IN_SYNTHESIS=false).
create_generated_clock -name adc_sample_0 -source [get_pins -hier -filter {NAME =~ *adc_capture_0/inst/forward_clock?0?.oddr_inst/C}] -divide_by 1 [get_ports {adc_clk[0]}]
create_generated_clock -name adc_sample_1 -source [get_pins -hier -filter {NAME =~ *adc_capture_0/inst/forward_clock?1?.oddr_inst/C}] -divide_by 1 [get_ports {adc_clk[1]}]
create_generated_clock -name adc_sample_2 -source [get_pins -hier -filter {NAME =~ *adc_capture_0/inst/forward_clock?2?.oddr_inst/C}] -divide_by 1 [get_ports {adc_clk[2]}]
create_generated_clock -name adc_sample_3 -source [get_pins -hier -filter {NAME =~ *adc_capture_0/inst/forward_clock?3?.oddr_inst/C}] -divide_by 1 [get_ports {adc_clk[3]}]
create_generated_clock -name adc_sample_4 -source [get_pins -hier -filter {NAME =~ *adc_capture_0/inst/forward_clock?4?.oddr_inst/C}] -divide_by 1 [get_ports {adc_clk[4]}]
create_generated_clock -name adc_sample_5 -source [get_pins -hier -filter {NAME =~ *adc_capture_0/inst/forward_clock?5?.oddr_inst/C}] -divide_by 1 [get_ports {adc_clk[5]}]
set_input_delay -clock adc_sample_0 -max 6.8 [get_ports {adc_data_0[*]}]
set_input_delay -clock adc_sample_0 -min 3.1 [get_ports {adc_data_0[*]}]
set_input_delay -clock adc_sample_1 -max 6.8 [get_ports {adc_data_1[*]}]
set_input_delay -clock adc_sample_1 -min 3.1 [get_ports {adc_data_1[*]}]
set_input_delay -clock adc_sample_2 -max 6.8 [get_ports {adc_data_2[*]}]
set_input_delay -clock adc_sample_2 -min 3.1 [get_ports {adc_data_2[*]}]
set_input_delay -clock adc_sample_3 -max 6.8 [get_ports {adc_data_3[*]}]
set_input_delay -clock adc_sample_3 -min 3.1 [get_ports {adc_data_3[*]}]
set_input_delay -clock adc_sample_4 -max 6.8 [get_ports {adc_data_4[*]}]
set_input_delay -clock adc_sample_4 -min 3.1 [get_ports {adc_data_4[*]}]
set_input_delay -clock adc_sample_5 -max 6.8 [get_ports {adc_data_5[*]}]
set_input_delay -clock adc_sample_5 -min 3.1 [get_ports {adc_data_5[*]}]
# DCO is a diagnostic asynchronous input; exempt only its first synchronizer.
set_false_path -from [get_ports {adc_dco[*]}] -to [get_cells -hier -filter {NAME =~ *adc_capture_0/inst/dco_meta_reg*}]

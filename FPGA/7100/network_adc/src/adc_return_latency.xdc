# The 135-degree capture edge (3.75ns) receives the PREVIOUS ADC sample.
# The clock out + ADC + return path is longer than3.75ns and shorter than13.75ns.
# Express that fixed one-sample latency: setup checks the next capture edge.
# Intentionally no hold relaxation: the adjacent, continuous ADC sample must
# remain beyond the actual3.75ns capture edge. Only the physical input FFs qualify.
set_multicycle_path 2 -setup -from [get_ports {adc_data_0[*]}] -to [get_cells -hier -filter {NAME =~ *adc_capture_0/inst/sample0_reg*}]
set_multicycle_path 2 -setup -from [get_ports {adc_data_1[*]}] -to [get_cells -hier -filter {NAME =~ *adc_capture_0/inst/sample1_reg*}]
set_multicycle_path 2 -setup -from [get_ports {adc_data_2[*]}] -to [get_cells -hier -filter {NAME =~ *adc_capture_0/inst/sample2_reg*}]
set_multicycle_path 2 -setup -from [get_ports {adc_data_3[*]}] -to [get_cells -hier -filter {NAME =~ *adc_capture_0/inst/sample3_reg*}]
set_multicycle_path 2 -setup -from [get_ports {adc_data_4[*]}] -to [get_cells -hier -filter {NAME =~ *adc_capture_0/inst/sample4_reg*}]
set_multicycle_path 2 -setup -from [get_ports {adc_data_5[*]}] -to [get_cells -hier -filter {NAME =~ *adc_capture_0/inst/sample5_reg*}]

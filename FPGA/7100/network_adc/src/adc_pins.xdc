# Real ADC pins from supplied photos; FMC VCCO modified to 3.3V (user confirmed).
# 5V/GND are connector orientation anchors, never FPGA signal ports.
# ADC1 CH0/1; ADC2 CH2/3 (P/N differs); ADC3 CH4/5 (opposite-face mirror).
# CH0 FMC_LA05_N
set_property PACKAGE_PIN AG27 [get_ports {adc_data_0[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[0]}]
# CH0 FMC_LA05_P
set_property PACKAGE_PIN AG26 [get_ports {adc_data_0[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[1]}]
# CH0 FMC_LA15_N
set_property PACKAGE_PIN AA28 [get_ports {adc_data_0[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[2]}]
# CH0 FMC_LA15_P
set_property PACKAGE_PIN AA27 [get_ports {adc_data_0[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[3]}]
# CH0 FMC_LA08_N
set_property PACKAGE_PIN AG30 [get_ports {adc_data_0[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[4]}]
# CH0 FMC_LA08_P
set_property PACKAGE_PIN AF30 [get_ports {adc_data_0[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[5]}]
# CH0 FMC_LA10_N
set_property PACKAGE_PIN AF27 [get_ports {adc_data_0[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[6]}]
# CH0 FMC_LA10_P
set_property PACKAGE_PIN AE27 [get_ports {adc_data_0[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[7]}]
# CH0 FMC_LA09_N
set_property PACKAGE_PIN AG29 [get_ports {adc_data_0[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[8]}]
# CH0 FMC_LA09_P
set_property PACKAGE_PIN AF29 [get_ports {adc_data_0[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[9]}]
# CH0 FMC_LA12_N
set_property PACKAGE_PIN AD26 [get_ports {adc_data_0[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[10]}]
# CH0 FMC_LA12_P
set_property PACKAGE_PIN AC26 [get_ports {adc_data_0[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_0[11]}]
# CH0 FMC_LA11_N
set_property PACKAGE_PIN AA29 [get_ports {adc_clk[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_clk[0]}]
# CH0 FMC_LA11_P
set_property PACKAGE_PIN Y28 [get_ports {adc_dco[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_dco[0]}]
# CH1 FMC_LA02_N
set_property PACKAGE_PIN AK28 [get_ports {adc_data_1[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[0]}]
# CH1 FMC_LA02_P
set_property PACKAGE_PIN AK27 [get_ports {adc_data_1[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[1]}]
# CH1 FMC_LA01_N
set_property PACKAGE_PIN AC27 [get_ports {adc_data_1[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[2]}]
# CH1 FMC_LA01_P
set_property PACKAGE_PIN AB27 [get_ports {adc_data_1[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[3]}]
# CH1 FMC_LA04_N
set_property PACKAGE_PIN AH27 [get_ports {adc_data_1[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[4]}]
# CH1 FMC_LA04_P
set_property PACKAGE_PIN AH26 [get_ports {adc_data_1[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[5]}]
# CH1 FMC_LA03_N
set_property PACKAGE_PIN AJ29 [get_ports {adc_data_1[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[6]}]
# CH1 FMC_LA03_P
set_property PACKAGE_PIN AJ28 [get_ports {adc_data_1[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[7]}]
# CH1 FMC_LA07_N
set_property PACKAGE_PIN AE30 [get_ports {adc_data_1[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[8]}]
# CH1 FMC_LA07_P
set_property PACKAGE_PIN AD30 [get_ports {adc_data_1[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[9]}]
# CH1 FMC_LA06_N
set_property PACKAGE_PIN AK30 [get_ports {adc_data_1[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[10]}]
# CH1 FMC_LA06_P
set_property PACKAGE_PIN AJ30 [get_ports {adc_data_1[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_1[11]}]
# CH1 FMC_LA00_N
set_property PACKAGE_PIN U27 [get_ports {adc_clk[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_clk[1]}]
# CH1 FMC_LA00_P
set_property PACKAGE_PIN U26 [get_ports {adc_dco[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_dco[1]}]
# CH2 FMC_LA26_P
set_property PACKAGE_PIN T30 [get_ports {adc_data_2[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[0]}]
# CH2 FMC_LA26_N
set_property PACKAGE_PIN U30 [get_ports {adc_data_2[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[1]}]
# CH2 FMC_LA24_P
set_property PACKAGE_PIN T29 [get_ports {adc_data_2[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[2]}]
# CH2 FMC_LA24_N
set_property PACKAGE_PIN U29 [get_ports {adc_data_2[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[3]}]
# CH2 FMC_LA25_P
set_property PACKAGE_PIN R27 [get_ports {adc_data_2[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[4]}]
# CH2 FMC_LA25_N
set_property PACKAGE_PIN T27 [get_ports {adc_data_2[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[5]}]
# CH2 FMC_LA28_P
set_property PACKAGE_PIN N26 [get_ports {adc_data_2[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[6]}]
# CH2 FMC_LA28_N
set_property PACKAGE_PIN N27 [get_ports {adc_data_2[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[7]}]
# CH2 FMC_LA29_P
set_property PACKAGE_PIN P23 [get_ports {adc_data_2[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[8]}]
# CH2 FMC_LA29_N
set_property PACKAGE_PIN P24 [get_ports {adc_data_2[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[9]}]
# CH2 FMC_LA30_P
set_property PACKAGE_PIN N28 [get_ports {adc_data_2[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[10]}]
# CH2 FMC_LA30_N
set_property PACKAGE_PIN P28 [get_ports {adc_data_2[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_2[11]}]
# CH2 FMC_LA21_P
set_property PACKAGE_PIN V28 [get_ports {adc_clk[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_clk[2]}]
# CH2 FMC_LA21_N
set_property PACKAGE_PIN V29 [get_ports {adc_dco[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_dco[2]}]
# CH3 FMC_LA20_P
set_property PACKAGE_PIN Y30 [get_ports {adc_data_3[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[0]}]
# CH3 FMC_LA20_N
set_property PACKAGE_PIN AA30 [get_ports {adc_data_3[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[1]}]
# CH3 FMC_LA18_P
set_property PACKAGE_PIN R25 [get_ports {adc_data_3[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[2]}]
# CH3 FMC_LA18_N
set_property PACKAGE_PIN R26 [get_ports {adc_data_3[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[3]}]
# CH3 FMC_LA23_P
set_property PACKAGE_PIN W25 [get_ports {adc_data_3[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[4]}]
# CH3 FMC_LA23_N
set_property PACKAGE_PIN W26 [get_ports {adc_data_3[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[5]}]
# CH3 FMC_LA22_P
set_property PACKAGE_PIN V27 [get_ports {adc_data_3[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[6]}]
# CH3 FMC_LA22_N
set_property PACKAGE_PIN W28 [get_ports {adc_data_3[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[7]}]
# CH3 FMC_LA19_P
set_property PACKAGE_PIN W29 [get_ports {adc_data_3[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[8]}]
# CH3 FMC_LA19_N
set_property PACKAGE_PIN W30 [get_ports {adc_data_3[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[9]}]
# CH3 FMC_LA27_P
set_property PACKAGE_PIN T24 [get_ports {adc_data_3[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[10]}]
# CH3 FMC_LA27_N
set_property PACKAGE_PIN T25 [get_ports {adc_data_3[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_3[11]}]
# CH3 FMC_LA17_P
set_property PACKAGE_PIN AE28 [get_ports {adc_clk[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_clk[3]}]
# CH3 FMC_LA17_N
set_property PACKAGE_PIN AF28 [get_ports {adc_dco[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_dco[3]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AK12 [get_ports {adc_data_4[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[0]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AK13 [get_ports {adc_data_4[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[1]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AH13 [get_ports {adc_data_4[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[2]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AH14 [get_ports {adc_data_4[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[3]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AJ13 [get_ports {adc_data_4[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[4]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AJ14 [get_ports {adc_data_4[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[5]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AG14 [get_ports {adc_data_4[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[6]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AF14 [get_ports {adc_data_4[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[7]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AK15 [get_ports {adc_data_4[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[8]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AJ15 [get_ports {adc_data_4[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[9]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AG15 [get_ports {adc_data_4[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[10]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AF15 [get_ports {adc_data_4[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_4[11]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AF12 [get_ports {adc_clk[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_clk[4]}]
# CH4 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AE12 [get_ports {adc_dco[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_dco[4]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AC13 [get_ports {adc_data_5[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[0]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AC14 [get_ports {adc_data_5[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[1]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AD13 [get_ports {adc_data_5[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[2]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AD14 [get_ports {adc_data_5[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[3]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AD15 [get_ports {adc_data_5[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[4]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AD16 [get_ports {adc_data_5[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[5]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AF13 [get_ports {adc_data_5[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[6]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AE13 [get_ports {adc_data_5[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[7]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AE15 [get_ports {adc_data_5[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[8]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AE16 [get_ports {adc_data_5[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[9]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AH12 [get_ports {adc_data_5[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[10]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AG12 [get_ports {adc_data_5[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_data_5[11]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AC12 [get_ports {adc_clk[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_clk[5]}]
# CH5 40PIN photo3/4 mirrored
set_property PACKAGE_PIN AB12 [get_ports {adc_dco[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {adc_dco[5]}]

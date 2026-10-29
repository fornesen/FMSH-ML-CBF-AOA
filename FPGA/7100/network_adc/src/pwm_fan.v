// Copyright 1986-2018 Xilinx, Inc. All Rights Reserved.
// --------------------------------------------------------------------------------
// Tool Version: Vivado v.2018.3 (win64) Build 2405991 Thu Dec  6 23:38:27 MST 2018
// Date        : Wed Oct 21 18:49:08 2026
// Command     : write_verilog -force -mode design -cell design_1_i/pwm_fan_0/inst
// Design      : pwm_fan
// Purpose     : This is a Verilog netlist of the current design or from a specific cell of the design. The output is an
//               IEEE 1364-2001 compliant Verilog HDL file that contains netlist information obtained from the input
//               design files.
// Device      : xc7z100ffg900-2
// --------------------------------------------------------------------------------
`timescale 1 ps / 1 ps

(* STRUCTURAL_NETLIST = "yes" *)
module pwm_fan
   (pwm,
    clk,
    spd_ctrl,
    rstn);
  output pwm;
  input clk;
  input [3:0]spd_ctrl;
  input rstn;

  wire \<const1> ;
  wire clk;
  wire [3:0]p_0_in;
  wire pwm;
  wire pwm_cnt_cycle;
  wire [3:0]pwm_cnt_cycle_reg;
  wire [6:0]pwm_cnt_percent;
  wire \pwm_cnt_percent[2]_i_2_n_0 ;
  wire \pwm_cnt_percent[4]_i_2_n_0 ;
  wire \pwm_cnt_percent[6]_i_2_n_0 ;
  wire [6:0]pwm_cnt_percent_0;
  wire pwm_reg_i_1_n_0;
  wire pwm_reg_i_2_n_0;
  wire pwm_reg_i_3_n_0;
  wire rstn;
  wire [3:0]spd_ctrl;

  VCC VCC
       (.P(\<const1> ));
  (* SOFT_HLUTNM = "soft_lutpair670" *) 
  LUT1 #(
    .INIT(2'h1)) 
    \pwm_cnt_cycle[0]_i_1 
       (.I0(pwm_cnt_cycle_reg[0]),
        .O(p_0_in[0]));
  (* SOFT_HLUTNM = "soft_lutpair670" *) 
  LUT2 #(
    .INIT(4'h6)) 
    \pwm_cnt_cycle[1]_i_1 
       (.I0(pwm_cnt_cycle_reg[0]),
        .I1(pwm_cnt_cycle_reg[1]),
        .O(p_0_in[1]));
  (* SOFT_HLUTNM = "soft_lutpair669" *) 
  LUT3 #(
    .INIT(8'h78)) 
    \pwm_cnt_cycle[2]_i_1 
       (.I0(pwm_cnt_cycle_reg[1]),
        .I1(pwm_cnt_cycle_reg[0]),
        .I2(pwm_cnt_cycle_reg[2]),
        .O(p_0_in[2]));
  LUT5 #(
    .INIT(32'h00000040)) 
    \pwm_cnt_cycle[3]_i_1 
       (.I0(\pwm_cnt_percent[6]_i_2_n_0 ),
        .I1(pwm_cnt_percent[6]),
        .I2(pwm_cnt_percent[5]),
        .I3(pwm_cnt_percent[1]),
        .I4(pwm_cnt_percent[0]),
        .O(pwm_cnt_cycle));
  (* SOFT_HLUTNM = "soft_lutpair669" *) 
  LUT4 #(
    .INIT(16'h7F80)) 
    \pwm_cnt_cycle[3]_i_2 
       (.I0(pwm_cnt_cycle_reg[2]),
        .I1(pwm_cnt_cycle_reg[0]),
        .I2(pwm_cnt_cycle_reg[1]),
        .I3(pwm_cnt_cycle_reg[3]),
        .O(p_0_in[3]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_cycle_reg[0] 
       (.C(clk),
        .CE(pwm_cnt_cycle),
        .CLR(pwm_reg_i_2_n_0),
        .D(p_0_in[0]),
        .Q(pwm_cnt_cycle_reg[0]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_cycle_reg[1] 
       (.C(clk),
        .CE(pwm_cnt_cycle),
        .CLR(pwm_reg_i_2_n_0),
        .D(p_0_in[1]),
        .Q(pwm_cnt_cycle_reg[1]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_cycle_reg[2] 
       (.C(clk),
        .CE(pwm_cnt_cycle),
        .CLR(pwm_reg_i_2_n_0),
        .D(p_0_in[2]),
        .Q(pwm_cnt_cycle_reg[2]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_cycle_reg[3] 
       (.C(clk),
        .CE(pwm_cnt_cycle),
        .CLR(pwm_reg_i_2_n_0),
        .D(p_0_in[3]),
        .Q(pwm_cnt_cycle_reg[3]));
  (* SOFT_HLUTNM = "soft_lutpair666" *) 
  LUT5 #(
    .INIT(32'h0000FFBF)) 
    \pwm_cnt_percent[0]_i_1 
       (.I0(\pwm_cnt_percent[6]_i_2_n_0 ),
        .I1(pwm_cnt_percent[6]),
        .I2(pwm_cnt_percent[5]),
        .I3(pwm_cnt_percent[1]),
        .I4(pwm_cnt_percent[0]),
        .O(pwm_cnt_percent_0[0]));
  (* SOFT_HLUTNM = "soft_lutpair668" *) 
  LUT2 #(
    .INIT(4'h6)) 
    \pwm_cnt_percent[1]_i_1 
       (.I0(pwm_cnt_percent[0]),
        .I1(pwm_cnt_percent[1]),
        .O(pwm_cnt_percent_0[1]));
  LUT6 #(
    .INIT(64'hFFFFAA001515AA00)) 
    \pwm_cnt_percent[2]_i_1 
       (.I0(pwm_cnt_percent[1]),
        .I1(pwm_cnt_percent[4]),
        .I2(pwm_cnt_percent[3]),
        .I3(pwm_cnt_percent[0]),
        .I4(pwm_cnt_percent[2]),
        .I5(\pwm_cnt_percent[2]_i_2_n_0 ),
        .O(pwm_cnt_percent_0[2]));
  (* SOFT_HLUTNM = "soft_lutpair667" *) 
  LUT4 #(
    .INIT(16'h0FF7)) 
    \pwm_cnt_percent[2]_i_2 
       (.I0(pwm_cnt_percent[5]),
        .I1(pwm_cnt_percent[6]),
        .I2(pwm_cnt_percent[1]),
        .I3(pwm_cnt_percent[0]),
        .O(\pwm_cnt_percent[2]_i_2_n_0 ));
  LUT6 #(
    .INIT(64'hFFFFC0001111C000)) 
    \pwm_cnt_percent[3]_i_1 
       (.I0(pwm_cnt_percent[4]),
        .I1(pwm_cnt_percent[1]),
        .I2(pwm_cnt_percent[2]),
        .I3(pwm_cnt_percent[0]),
        .I4(pwm_cnt_percent[3]),
        .I5(\pwm_cnt_percent[4]_i_2_n_0 ),
        .O(pwm_cnt_percent_0[3]));
  LUT6 #(
    .INIT(64'hFFFF800055558000)) 
    \pwm_cnt_percent[4]_i_1 
       (.I0(pwm_cnt_percent[3]),
        .I1(pwm_cnt_percent[2]),
        .I2(pwm_cnt_percent[1]),
        .I3(pwm_cnt_percent[0]),
        .I4(pwm_cnt_percent[4]),
        .I5(\pwm_cnt_percent[4]_i_2_n_0 ),
        .O(pwm_cnt_percent_0[4]));
  (* SOFT_HLUTNM = "soft_lutpair668" *) 
  LUT5 #(
    .INIT(32'h6777FFFF)) 
    \pwm_cnt_percent[4]_i_2 
       (.I0(pwm_cnt_percent[0]),
        .I1(pwm_cnt_percent[1]),
        .I2(pwm_cnt_percent[6]),
        .I3(pwm_cnt_percent[5]),
        .I4(pwm_cnt_percent[2]),
        .O(\pwm_cnt_percent[4]_i_2_n_0 ));
  (* SOFT_HLUTNM = "soft_lutpair666" *) 
  LUT5 #(
    .INIT(32'hC3CCCCC4)) 
    \pwm_cnt_percent[5]_i_1 
       (.I0(pwm_cnt_percent[6]),
        .I1(pwm_cnt_percent[5]),
        .I2(\pwm_cnt_percent[6]_i_2_n_0 ),
        .I3(pwm_cnt_percent[1]),
        .I4(pwm_cnt_percent[0]),
        .O(pwm_cnt_percent_0[5]));
  (* SOFT_HLUTNM = "soft_lutpair667" *) 
  LUT5 #(
    .INIT(32'hC6CCCCC4)) 
    \pwm_cnt_percent[6]_i_1 
       (.I0(pwm_cnt_percent[5]),
        .I1(pwm_cnt_percent[6]),
        .I2(\pwm_cnt_percent[6]_i_2_n_0 ),
        .I3(pwm_cnt_percent[1]),
        .I4(pwm_cnt_percent[0]),
        .O(pwm_cnt_percent_0[6]));
  LUT3 #(
    .INIT(8'h7F)) 
    \pwm_cnt_percent[6]_i_2 
       (.I0(pwm_cnt_percent[4]),
        .I1(pwm_cnt_percent[3]),
        .I2(pwm_cnt_percent[2]),
        .O(\pwm_cnt_percent[6]_i_2_n_0 ));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_percent_reg[0] 
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_cnt_percent_0[0]),
        .Q(pwm_cnt_percent[0]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_percent_reg[1] 
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_cnt_percent_0[1]),
        .Q(pwm_cnt_percent[1]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_percent_reg[2] 
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_cnt_percent_0[2]),
        .Q(pwm_cnt_percent[2]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_percent_reg[3] 
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_cnt_percent_0[3]),
        .Q(pwm_cnt_percent[3]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_percent_reg[4] 
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_cnt_percent_0[4]),
        .Q(pwm_cnt_percent[4]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_percent_reg[5] 
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_cnt_percent_0[5]),
        .Q(pwm_cnt_percent[5]));
  FDCE #(
    .INIT(1'b0)) 
    \pwm_cnt_percent_reg[6] 
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_cnt_percent_0[6]),
        .Q(pwm_cnt_percent[6]));
  LUT6 #(
    .INIT(64'h7DFFFF7D82000082)) 
    pwm_reg_i_1
       (.I0(pwm_reg_i_3_n_0),
        .I1(pwm_cnt_cycle_reg[0]),
        .I2(spd_ctrl[0]),
        .I3(pwm_cnt_cycle_reg[1]),
        .I4(spd_ctrl[1]),
        .I5(pwm),
        .O(pwm_reg_i_1_n_0));
  LUT1 #(
    .INIT(2'h1)) 
    pwm_reg_i_2
       (.I0(rstn),
        .O(pwm_reg_i_2_n_0));
  LUT4 #(
    .INIT(16'h9009)) 
    pwm_reg_i_3
       (.I0(pwm_cnt_cycle_reg[2]),
        .I1(spd_ctrl[2]),
        .I2(pwm_cnt_cycle_reg[3]),
        .I3(spd_ctrl[3]),
        .O(pwm_reg_i_3_n_0));
  FDCE #(
    .INIT(1'b0)) 
    pwm_reg_reg
       (.C(clk),
        .CE(\<const1> ),
        .CLR(pwm_reg_i_2_n_0),
        .D(pwm_reg_i_1_n_0),
        .Q(pwm));
endmodule

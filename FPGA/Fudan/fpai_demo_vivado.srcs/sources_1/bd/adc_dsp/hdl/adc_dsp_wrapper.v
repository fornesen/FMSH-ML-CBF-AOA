//Copyright 1986-2018 Xilinx, Inc. All Rights Reserved.
//--------------------------------------------------------------------------------
//Tool Version: Vivado v.2018.3 (win64) Build 2405991 Thu Dec  6 23:38:27 MST 2018
//Date        : Fri Oct 23 11:58:54 2026
//Command     : generate_target adc_dsp_wrapper.bd
//Design      : adc_dsp_wrapper
//Purpose     : IP block netlist
//--------------------------------------------------------------------------------
`timescale 1 ps / 1 ps

module adc_dsp_wrapper
   (aclk,
    adc_axi_tdata,
    adc_axi_tready,
    adc_axi_tvalid,
    aresetn,
    dds_fre,
    fft_axi_tdata,
    fft_axi_tlast,
    fft_axi_tready,
    fft_axi_tuser,
    fft_axi_tvalid,
    fft_conf_done,
    fft_sel_channel,
    fir_fft_trans_err,
    pha_axi_tdata,
    pha_axi_tready,
    pha_axi_tvalid,
    pha_overflow_err,
    pha_shift_coe,
    pha_shift_conf_en);
  input aclk;
  input [95:0]adc_axi_tdata;
  output adc_axi_tready;
  input adc_axi_tvalid;
  input aresetn;
  input [31:0]dds_fre;
  output [79:0]fft_axi_tdata;
  output fft_axi_tlast;
  input fft_axi_tready;
  output [2:0]fft_axi_tuser;
  output fft_axi_tvalid;
  output fft_conf_done;
  output [2:0]fft_sel_channel;
  output fir_fft_trans_err;
  output [63:0]pha_axi_tdata;
  input pha_axi_tready;
  output pha_axi_tvalid;
  output [5:0]pha_overflow_err;
  input [63:0]pha_shift_coe;
  input pha_shift_conf_en;

  wire aclk;
  wire [95:0]adc_axi_tdata;
  wire adc_axi_tready;
  wire adc_axi_tvalid;
  wire aresetn;
  wire [31:0]dds_fre;
  wire [79:0]fft_axi_tdata;
  wire fft_axi_tlast;
  wire fft_axi_tready;
  wire [2:0]fft_axi_tuser;
  wire fft_axi_tvalid;
  wire fft_conf_done;
  wire [2:0]fft_sel_channel;
  wire fir_fft_trans_err;
  wire [63:0]pha_axi_tdata;
  wire pha_axi_tready;
  wire pha_axi_tvalid;
  wire [5:0]pha_overflow_err;
  wire [63:0]pha_shift_coe;
  wire pha_shift_conf_en;

  adc_dsp adc_dsp_i
       (.aclk(aclk),
        .adc_axi_tdata(adc_axi_tdata),
        .adc_axi_tready(adc_axi_tready),
        .adc_axi_tvalid(adc_axi_tvalid),
        .aresetn(aresetn),
        .dds_fre(dds_fre),
        .fft_axi_tdata(fft_axi_tdata),
        .fft_axi_tlast(fft_axi_tlast),
        .fft_axi_tready(fft_axi_tready),
        .fft_axi_tuser(fft_axi_tuser),
        .fft_axi_tvalid(fft_axi_tvalid),
        .fft_conf_done(fft_conf_done),
        .fft_sel_channel(fft_sel_channel),
        .fir_fft_trans_err(fir_fft_trans_err),
        .pha_axi_tdata(pha_axi_tdata),
        .pha_axi_tready(pha_axi_tready),
        .pha_axi_tvalid(pha_axi_tvalid),
        .pha_overflow_err(pha_overflow_err),
        .pha_shift_coe(pha_shift_coe),
        .pha_shift_conf_en(pha_shift_conf_en));
endmodule

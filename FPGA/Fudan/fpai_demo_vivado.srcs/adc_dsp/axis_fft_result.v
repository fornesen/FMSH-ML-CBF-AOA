`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/27 15:33:17
// Design Name: 
// Module Name: axis_fft_result
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////

module axis_fft_result(
    input  wire                     aclk,
    input  wire                     aresetn,

    input  wire [79:0]              s_axis_fft_tdata,
    input  wire                     s_axis_fft_tvalid,
    output wire                     s_axis_fft_tready,
    input  wire                     s_axis_fft_tlast,

    input  wire [7:0]               s_axis_channel_tdata,
    input  wire                     s_axis_channel_tvalid,
    output wire                     s_axis_channel_tready,

    output wire [79:0]              m_axis_result_tdata,
    output wire                     m_axis_result_tvalid,
    input  wire                     m_axis_result_tready,
    output wire                     m_axis_result_tlast,
    output wire [2:0]               m_axis_result_tuser
);

assign m_axis_result_tdata  = s_axis_fft_tdata;
assign m_axis_result_tuser  = s_axis_channel_tdata[2:0];
assign m_axis_result_tlast  = s_axis_fft_tlast;

// 两路齐全才向下游声明有效
assign m_axis_result_tvalid = aresetn && s_axis_fft_tvalid && s_axis_channel_tvalid;

// 缺少任意一路或下游反压时，均不能单独消费另一路的有效数据
assign s_axis_fft_tready     = aresetn && m_axis_result_tready && s_axis_channel_tvalid;
assign s_axis_channel_tready = aresetn && m_axis_result_tready && s_axis_fft_tvalid;

endmodule

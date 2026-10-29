`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/22 20:37:28
// Design Name: 
// Module Name: axis_fir_to_fft
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


module axis_fir_to_fft #(
    parameter integer SHIFT_BITS = 16,       // 算术右移位数
    parameter integer FFT_LENGTH = 4096      // FFT 点数
)(
    input  wire                     aclk,
    input  wire                     aresetn,

    // I 路 FIR 输入
    input  wire [47:0]              s_axis_i_tdata,
    input  wire                     s_axis_i_tvalid,
    output wire                     s_axis_i_tready,

    // Q 路 FIR 输入
    input  wire [47:0]              s_axis_q_tdata,
    input  wire                     s_axis_q_tvalid,
    output wire                     s_axis_q_tready,

    // 数据输出：低半部实数 I，高半部虚数 Q
    output wire [63:0]              m_axis_fft_tdata,
    output wire                     m_axis_fft_tvalid,
    input  wire                     m_axis_fft_tready,
    output wire                     m_axis_fft_tlast,

    // 数据量化饱和
    output reg                      overflow_error
);

localparam integer COUNT_WIDTH = (FFT_LENGTH > 1) ? $clog2(FFT_LENGTH) : 1;

// 输入数据处理（第26位为标志位，检查是否溢出）
function [26:0] quantize_data;
    input       [41:0] data_in;
    reg signed  [42:0] extended_data;
    reg signed  [42:0] rounded_data;
    reg         [42:0] remainder_data;
    reg         [42:0] remainder_mask;
    reg         [42:0] half_lsb;
    begin
        extended_data   = {data_in[41], data_in};
        rounded_data    = extended_data >>> SHIFT_BITS;

        remainder_mask  = (42'd1 << SHIFT_BITS) - 42'd1;
        remainder_data  = extended_data & remainder_mask;
        half_lsb        = 42'd1 << ((SHIFT_BITS > 0) ? (SHIFT_BITS-1) : 0);

        if (SHIFT_BITS > 0) begin
            if ((remainder_data > half_lsb) ||
                ((remainder_data == half_lsb) && rounded_data[0])) begin
                rounded_data = rounded_data + 42'sd1;
            end
        end

        if (rounded_data > 42'sd33554431) begin
            quantize_data = {1'b1, 26'h1ffffff};       // 上溢出（+2^25-1）
        end
        else if (rounded_data < -42'sd33554432) begin
            quantize_data = {1'b1, 26'h2000000};       // 下溢出（-2^25）
        end
        else begin
            quantize_data = {1'b0, rounded_data[25:0]};
        end
    end
endfunction

// 6. I/Q 各缓存一份已量化的数据
reg [25:0]                     i_buffer;
reg [25:0]                     q_buffer;
reg                            i_pending;
reg                            q_pending;
reg [COUNT_WIDTH-1:0]          sample_count;

wire [26:0]                    i_quantized;
wire [26:0]                    q_quantized;
wire                           i_transfer;
wire                           q_transfer;
wire                           output_transfer;

assign i_quantized = quantize_data(s_axis_i_tdata[41:0]);
assign q_quantized = quantize_data(s_axis_q_tdata[41:0]);

// 所有数据均在缓存中，可以接收
assign m_axis_fft_tvalid = aresetn && i_pending && q_pending;

// FFT IP握手成功
assign output_transfer  = m_axis_fft_tvalid && m_axis_fft_tready;

// 可以载入新数据
assign s_axis_i_tready   = aresetn && (!i_pending || output_transfer);
assign s_axis_q_tready   = aresetn && (!q_pending || output_transfer);

// 与上游FIR IP握手成功
assign i_transfer       = s_axis_i_tvalid && s_axis_i_tready;
assign q_transfer       = s_axis_q_tvalid && s_axis_q_tready;

// XN_RE=[25:0]，XN_IM=[57:32]；每个字段高 6bit 填 0
assign m_axis_fft_tdata = {6'b0, q_buffer, 6'b0, i_buffer};
assign m_axis_fft_tlast = m_axis_fft_tvalid && (sample_count == FFT_LENGTH-1);

// I，Q数据握手缓存
always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        i_buffer  <= 26'b0;
        i_pending <= 1'b0;
    end
    else if (i_transfer) begin
        i_buffer  <= i_quantized[25:0];
        i_pending <= 1'b1;
    end
    else if (output_transfer) begin
        i_pending <= 1'b0;
    end
end

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        q_buffer  <= 26'b0;
        q_pending <= 1'b0;
    end
    else if (q_transfer) begin
        q_buffer  <= q_quantized[25:0];
        q_pending <= 1'b1;
    end
    else if (output_transfer) begin
        q_pending <= 1'b0;
    end
end

// TLAST 产生
always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        sample_count <= 0;
    end
    else if (output_transfer) begin
        if (sample_count == FFT_LENGTH-1) begin
            sample_count <= 0;
        end
        else begin
            sample_count <= sample_count + 1'b1;
        end
    end
end

// 量化饱和错误
always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        overflow_error <= 1'b0;
    end
    else if ((i_transfer && i_quantized[26]) || (q_transfer && q_quantized[26])) begin
        overflow_error <= 1'b1;
    end
end

endmodule

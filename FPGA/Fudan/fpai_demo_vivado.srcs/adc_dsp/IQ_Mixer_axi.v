`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/07/27 16:35:23
// Design Name: 
// Module Name: IQ_Mixer_axi
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
//      ADC 实数信号与 DDS 正交本振相乘，输出 I/Q 数据。
//
//      混频公式：
//        I = ADC * cos(theta)
//        Q = ADC * (-sin(theta))
//
//        约有2 clk的处理时延
//
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////

module IQ_Mixer_axi #(
    parameter integer ADC_DATA_WIDTH = 16,      // ADC输入位宽
    parameter integer DDS_DATA_WIDTH = 24,      // DDS输出有效位宽
    parameter integer DDS_LATENCY    = 16,      // DDS延迟
    parameter integer MIX_DATA_WIDTH = 24,
    parameter integer MIX_SHIFT      = 16
)(
    input  wire                             aclk,
    input  wire                             aresetn,

    // ADC AXI-Stream 输入
    input  wire [ADC_DATA_WIDTH-1:0]        s_axis_adc_tdata,
    input  wire                             s_axis_adc_tvalid,
    output wire                             s_axis_adc_tready,

    // DDS AXI-Stream 输入
    input  wire [47:0]                      s_axis_dds_tdata,
    input  wire                             s_axis_dds_tvalid,
    output wire                             s_axis_dds_tready,

    // I 路输出
    output reg  [MIX_DATA_WIDTH-1:0]        m_axis_i_tdata,
    output reg                              m_axis_i_tvalid,
    input  wire                             m_axis_i_tready,

    // Q 路输出
    output reg  [MIX_DATA_WIDTH-1:0]        m_axis_q_tdata,
    output reg                              m_axis_q_tvalid,
    input  wire                             m_axis_q_tready
);

// 参数计算
localparam integer PRODUCT_WIDTH = ADC_DATA_WIDTH + DDS_DATA_WIDTH;

// 延迟寄存器
reg signed  [ADC_DATA_WIDTH-1:0] adc_delay [0:DDS_LATENCY-1];
reg         [DDS_LATENCY-1:0]    adc_valid_delay;

// 握手推进控制
wire output_available;
wire receive_ready;
wire dds_transfer;

// 输出位置可装入数据
assign output_available     = (!m_axis_i_tvalid || m_axis_i_tready) && (!m_axis_q_tvalid || m_axis_q_tready);

// 流水线可推进（DDS有数据或ADC延迟链无输出）
assign receive_ready        = aresetn && output_available && (!adc_valid_delay[DDS_LATENCY-1] || s_axis_dds_tvalid);

// 可接收上游ADC
assign s_axis_adc_tready    = receive_ready;

// 本模块可以接收DDS数据
assign s_axis_dds_tready    = aresetn && output_available && adc_valid_delay[DDS_LATENCY-1];

// DDS 握手成功（包含与上游ADC握手成功）
assign dds_transfer         = s_axis_dds_tvalid && s_axis_dds_tready;

// DDS 数据拆分
wire signed [DDS_DATA_WIDTH-1:0] dds_cosine;
wire signed [DDS_DATA_WIDTH-1:0] dds_negative_sine;

assign dds_cosine           = $signed(s_axis_dds_tdata[23:0]);
assign dds_negative_sine    = $signed(s_axis_dds_tdata[47:24]);

// ADC 数据延迟 DDS_LATENCY 拍，与 DDS 输出对齐
integer i;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        adc_valid_delay <= 0;

        for (i = 0; i < DDS_LATENCY; i = i + 1) begin
            adc_delay[i] <= 0;
        end
    end
    else begin
        if (receive_ready) begin
            adc_delay[0]       <= $signed(s_axis_adc_tdata);
            adc_valid_delay[0] <= s_axis_adc_tvalid;

            for (i = 1; i < DDS_LATENCY; i = i + 1) begin
                adc_delay[i]       <= adc_delay[i-1];
                adc_valid_delay[i] <= adc_valid_delay[i-1];
            end            
        end
    end
end

wire signed [ADC_DATA_WIDTH-1:0] adc_data_aligned;
assign  adc_data_aligned    = adc_delay[DDS_LATENCY-1];

// IQ乘法
reg signed [PRODUCT_WIDTH-1:0] i_product_reg;
reg signed [PRODUCT_WIDTH-1:0] q_product_reg;
reg                            product_valid_reg;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        i_product_reg       <= 0;
        q_product_reg       <= 0;
        product_valid_reg   <= 1'b0;
    end
    else begin
        if (receive_ready) begin
            product_valid_reg   <= dds_transfer;

            if (dds_transfer) begin
                i_product_reg <= $signed(adc_data_aligned) * $signed(dds_cosine);
                q_product_reg <= $signed(adc_data_aligned) * $signed(dds_negative_sine);
            end
        end
    end
end

// 算术右移并输出
wire signed [PRODUCT_WIDTH-1:0] i_shifted;
wire signed [PRODUCT_WIDTH-1:0] q_shifted;
    
assign i_shifted    = $signed(i_product_reg) >>> MIX_SHIFT;        // ADC_DATA_WIDTH + DDS_DATA_WIDTH - MIX_SHIFT = 16 + 24 - 16 = 24
assign q_shifted    = $signed(q_product_reg) >>> MIX_SHIFT;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        m_axis_i_tdata  <= 0;
        m_axis_i_tvalid <= 1'b0;

        m_axis_q_tdata  <= 0;
        m_axis_q_tvalid <= 1'b0;
    end
    else begin
        if (receive_ready) begin
            m_axis_i_tvalid <= product_valid_reg;
            m_axis_q_tvalid <= product_valid_reg;

            if (product_valid_reg) begin

                m_axis_i_tdata <= i_shifted[MIX_DATA_WIDTH-1:0];
                m_axis_q_tdata <= q_shifted[MIX_DATA_WIDTH-1:0];
            end    
        end
        else begin
            if (m_axis_i_tvalid && m_axis_i_tready) begin
                m_axis_i_tvalid <= 1'b0;
            end
            if (m_axis_q_tvalid && m_axis_q_tready) begin
                m_axis_q_tvalid <= 1'b0;
            end
        end
    end
end

endmodule
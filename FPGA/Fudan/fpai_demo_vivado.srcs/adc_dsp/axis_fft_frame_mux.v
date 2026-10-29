`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/24 16:59:33
// Design Name: 
// Module Name: axis_fft_frame_mux
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


module axis_fft_frame_mux(
    input  wire                     aclk,
    input  wire                     aresetn,

    // 连接 FFT 配置模块 config_done
    input  wire                     config_done,

    input  wire [63:0]              s00_axis_tdata,
    input  wire                     s00_axis_tvalid,
    output wire                     s00_axis_tready,
    input  wire                     s00_axis_tlast,

    input  wire [63:0]              s01_axis_tdata,
    input  wire                     s01_axis_tvalid,
    output wire                     s01_axis_tready,
    input  wire                     s01_axis_tlast,

    input  wire [63:0]              s02_axis_tdata,
    input  wire                     s02_axis_tvalid,
    output wire                     s02_axis_tready,
    input  wire                     s02_axis_tlast,

    input  wire [63:0]              s03_axis_tdata,
    input  wire                     s03_axis_tvalid,
    output wire                     s03_axis_tready,
    input  wire                     s03_axis_tlast,

    input  wire [63:0]              s04_axis_tdata,
    input  wire                     s04_axis_tvalid,
    output wire                     s04_axis_tready,
    input  wire                     s04_axis_tlast,

    input  wire [63:0]              s05_axis_tdata,
    input  wire                     s05_axis_tvalid,
    output wire                     s05_axis_tready,
    input  wire                     s05_axis_tlast,

    output wire [63:0]              m_axis_fft_tdata,
    output wire                     m_axis_fft_tvalid,
    input  wire                     m_axis_fft_tready,
    output wire                     m_axis_fft_tlast,

    // 连接通道标签FIFO，每个数据点对应一个标签
    output wire [7:0]               m_axis_channel_tdata,
    output wire                     m_axis_channel_tvalid,
    input  wire                     m_axis_channel_tready,

    // 当前输入通道序列
    output reg  [2:0]               selected_channel
);

reg             started;
reg  [63:0]     selected_data;
reg             selected_valid;
reg             selected_last;
reg             fft_sent;
reg             channel_sent;

wire            send_enable;
wire            receive_ready;
wire            input_transfer;
wire            fft_transfer;
wire            channel_transfer;

assign send_enable              = aresetn && started;
assign m_axis_fft_tdata         = selected_data;

// FFT 数据有效
assign m_axis_fft_tvalid        = send_enable && selected_valid && !fft_sent;
assign m_axis_fft_tlast         = m_axis_fft_tvalid && selected_last;

// 当前数据选择通道
assign m_axis_channel_tdata     = {5'd0, selected_channel};

// FIFO 数据有效
assign m_axis_channel_tvalid    = send_enable && selected_valid && !channel_sent;

// FFT IP 握手成功
assign fft_transfer            = m_axis_fft_tvalid && m_axis_fft_tready;

// 标签FIFO 握手成功
assign channel_transfer        = m_axis_channel_tvalid && m_axis_channel_tready;

// 当前点的每个分支（FFT IP / FIFO）必须已经发送，或能在本拍接收，才允许弹出上游FIFO
assign receive_ready           = send_enable && (fft_sent || m_axis_fft_tready)
                                             && (channel_sent || m_axis_channel_tready);

// 两路握手成功！
assign input_transfer          = selected_valid && receive_ready;

assign s00_axis_tready = (selected_channel == 3'd0) && receive_ready;
assign s01_axis_tready = (selected_channel == 3'd1) && receive_ready;
assign s02_axis_tready = (selected_channel == 3'd2) && receive_ready;
assign s03_axis_tready = (selected_channel == 3'd3) && receive_ready;
assign s04_axis_tready = (selected_channel == 3'd4) && receive_ready;
assign s05_axis_tready = (selected_channel == 3'd5) && receive_ready;

always @(*) begin

    case (selected_channel)
        3'd0: begin
            selected_data  = s00_axis_tdata;
            selected_valid = s00_axis_tvalid;
            selected_last  = s00_axis_tlast;
        end
        3'd1: begin
            selected_data  = s01_axis_tdata;
            selected_valid = s01_axis_tvalid;
            selected_last  = s01_axis_tlast;
        end
        3'd2: begin
            selected_data  = s02_axis_tdata;
            selected_valid = s02_axis_tvalid;
            selected_last  = s02_axis_tlast;
        end
        3'd3: begin
            selected_data  = s03_axis_tdata;
            selected_valid = s03_axis_tvalid;
            selected_last  = s03_axis_tlast;
        end
        3'd4: begin
            selected_data  = s04_axis_tdata;
            selected_valid = s04_axis_tvalid;
            selected_last  = s04_axis_tlast;
        end
        3'd5: begin
            selected_data  = s05_axis_tdata;
            selected_valid = s05_axis_tvalid;
            selected_last  = s05_axis_tlast;
        end
        default: begin
            selected_data  = 64'd0;
            selected_valid = 1'b0;
            selected_last  = 1'b0;
        end
    endcase
end

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        started <= 1'b0;
    end
    else if (config_done) begin
        started <= 1'b1;
    end
end

// 分支握手锁存与清除
always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        fft_sent     <= 1'b0;
        channel_sent <= 1'b0;
    end
    else if (input_transfer) begin
        fft_sent     <= 1'b0;
        channel_sent <= 1'b0;
    end
    else begin
        if (fft_transfer) begin
            fft_sent <= 1'b1;
        end
        if (channel_transfer) begin
            channel_sent <= 1'b1;
        end
    end
end

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        selected_channel <= 3'd0;
    end
    else if (input_transfer && selected_last) begin
        if (selected_channel == 3'd5) begin
            selected_channel <= 3'd0;
        end
        else begin
            selected_channel <= selected_channel + 1'b1;
        end
    end
end

endmodule

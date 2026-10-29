`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/30 12:30:23
// Design Name: 
// Module Name: beamformer_sum
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

module beamformer_sum #(
    parameter integer FIFO_DEPTH = 64       // 每路对齐缓存深度，至少为1
)(
    input  wire                     aclk,
    input  wire                     aresetn,

    input  wire [101:0]             s00_axis_tdata,
    input  wire                     s00_axis_tvalid,
    output wire                     s00_axis_tready,

    input  wire [101:0]             s01_axis_tdata,
    input  wire                     s01_axis_tvalid,
    output wire                     s01_axis_tready,

    input  wire [101:0]             s02_axis_tdata,
    input  wire                     s02_axis_tvalid,
    output wire                     s02_axis_tready,

    input  wire [101:0]             s03_axis_tdata,
    input  wire                     s03_axis_tvalid,
    output wire                     s03_axis_tready,

    input  wire [101:0]             s04_axis_tdata,
    input  wire                     s04_axis_tvalid,
    output wire                     s04_axis_tready,

    input  wire [101:0]             s05_axis_tdata,
    input  wire                     s05_axis_tvalid,
    output wire                     s05_axis_tready,

    output reg  [63:0]              m_axis_tdata,
    output wire                     m_axis_tvalid,
    input  wire                     m_axis_tready,

    output wire [5:0]               overflow_error
);

localparam integer COUNT_WIDTH   = $clog2(FIFO_DEPTH + 1);

wire [611:0] input_data;
wire [5:0]   input_valid;
wire [5:0]   input_ready;
wire [5:0]   buffer_valid;
wire [5:0]   buffer_full;
wire         aligned_transfer;
wire         pipeline_enable;

wire signed [50:0] aligned_i [0:5];
wire signed [50:0] aligned_q [0:5];

assign input_data  = {s05_axis_tdata, s04_axis_tdata, s03_axis_tdata,
                      s02_axis_tdata, s01_axis_tdata, s00_axis_tdata};

assign input_valid = {s05_axis_tvalid, s04_axis_tvalid, s03_axis_tvalid,
                      s02_axis_tvalid, s01_axis_tvalid, s00_axis_tvalid};

assign {s05_axis_tready, s04_axis_tready, s03_axis_tready,
        s02_axis_tready, s01_axis_tready, s00_axis_tready} = input_ready;

// 整体推进条件（包含流水线推进条件和FIFO对齐输出条件）
assign aligned_transfer = aresetn && pipeline_enable && (&buffer_valid);

// 每路例化一个fifo，允许暂时反压和输入对齐！
genvar channel;
generate
    for (channel = 0; channel < 6; channel = channel + 1) begin : channel_buffer

        reg                     channel_overflow;
        wire [COUNT_WIDTH-1:0]  data_count;

        wire                    input_transfer;
        wire                    fifo_empty;
        wire [101:0]            read_data;

        assign buffer_valid[channel]    = !fifo_empty;
        assign input_ready[channel]     = aresetn && ((!buffer_full[channel]) || aligned_transfer);
        assign input_transfer           = input_valid[channel] && input_ready[channel];

        assign aligned_i[channel]       = read_data[101:51];
        assign aligned_q[channel]       = read_data[50:0];

        assign overflow_error[channel]  = channel_overflow;

        simple_fifo #(
            .DATA_WIDTH (102),
            .FIFO_DEPTH (FIFO_DEPTH)
        ) fifo_inst (
            .clk        (aclk),
            .rst_n      (aresetn),

            .wr_en      (input_transfer),
            .wr_data    (input_data[channel*102 +: 102]),

            .rd_en      (aligned_transfer),
            .rd_data    (read_data),

            .clean      (1'b0),
            .num        (data_count),
            .empty      (fifo_empty),
            .full       (buffer_full[channel])
        );

        always @(posedge aclk or negedge aresetn) begin
            if (!aresetn) begin
                channel_overflow <= 1'b0;
            end
            // FIFO溢出报错！
            else if (input_valid[channel] && buffer_full[channel] && !aligned_transfer) begin
                channel_overflow <= 1'b1;
            end
        end
    end
endgenerate

reg stage1_valid;
reg stage2_valid;
reg stage3_valid;
reg output_valid;

// 流水线推进使能
assign pipeline_enable = !output_valid || m_axis_tready;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        stage1_valid <= 1'b0;
        stage2_valid <= 1'b0;
        stage3_valid <= 1'b0;
        output_valid <= 1'b0;
    end
    else if (pipeline_enable) begin
        stage1_valid <= aligned_transfer;
        stage2_valid <= stage1_valid;
        stage3_valid <= stage2_valid;
        output_valid <= stage3_valid;
    end
end

assign m_axis_tvalid = aresetn && output_valid;

// 第一级：三组两路求和
reg signed [51:0] i_sum_01;
reg signed [51:0] i_sum_23;
reg signed [51:0] i_sum_45;
reg signed [51:0] q_sum_01;
reg signed [51:0] q_sum_23;
reg signed [51:0] q_sum_45;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        i_sum_01 <= 0;
        i_sum_23 <= 0;
        i_sum_45 <= 0;
        q_sum_01 <= 0;
        q_sum_23 <= 0;
        q_sum_45 <= 0;
    end
    else if (aligned_transfer) begin
        i_sum_01 <= $signed({aligned_i[0][50], aligned_i[0]}) + $signed({aligned_i[1][50], aligned_i[1]});
        i_sum_23 <= $signed({aligned_i[2][50], aligned_i[2]}) + $signed({aligned_i[3][50], aligned_i[3]});
        i_sum_45 <= $signed({aligned_i[4][50], aligned_i[4]}) + $signed({aligned_i[5][50], aligned_i[5]});
        q_sum_01 <= $signed({aligned_q[0][50], aligned_q[0]}) + $signed({aligned_q[1][50], aligned_q[1]});
        q_sum_23 <= $signed({aligned_q[2][50], aligned_q[2]}) + $signed({aligned_q[3][50], aligned_q[3]});
        q_sum_45 <= $signed({aligned_q[4][50], aligned_q[4]}) + $signed({aligned_q[5][50], aligned_q[5]});
    end
end

// 第二级：前四路相加；后两路符号扩展并延迟一拍
reg signed [52:0] i_sum_0123;
reg signed [52:0] q_sum_0123;
reg signed [52:0] i_sum_45_delay;
reg signed [52:0] q_sum_45_delay;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        i_sum_0123     <= 0;
        q_sum_0123     <= 0;
        i_sum_45_delay <= 0;
        q_sum_45_delay <= 0;
    end
    else if (pipeline_enable && stage1_valid) begin
        i_sum_0123 <= $signed({i_sum_01[51], i_sum_01}) + $signed({i_sum_23[51], i_sum_23});
        q_sum_0123 <= $signed({q_sum_01[51], q_sum_01}) + $signed({q_sum_23[51], q_sum_23});
        i_sum_45_delay <= {i_sum_45[51], i_sum_45};
        q_sum_45_delay <= {q_sum_45[51], q_sum_45};
    end
end

// 第三级：六路总和
reg signed [53:0] i_sum_all;
reg signed [53:0] q_sum_all;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        i_sum_all <= 0;
        q_sum_all <= 0;
    end
    else if (pipeline_enable && stage2_valid) begin
        i_sum_all <= $signed({i_sum_0123[52], i_sum_0123}) + $signed({i_sum_45_delay[52], i_sum_45_delay});
        q_sum_all <= $signed({q_sum_0123[52], q_sum_0123}) + $signed({q_sum_45_delay[52], q_sum_45_delay});
    end
end

// 四舍五入
function [30:0] scale_data;
    input signed [53:0] data_in;
    reg signed [54:0] rounded_data;
    begin
        rounded_data = $signed({data_in[53], data_in}) >>> 23;

        if ((data_in[22:0] > 23'h400000) || ((data_in[22:0] == 23'h400000) && rounded_data[0])) begin
            rounded_data = rounded_data + 55'sd1;
        end
        scale_data = rounded_data[30:0];
    end
endfunction

wire signed [30:0] output_i = scale_data(i_sum_all);
wire signed [30:0] output_q = scale_data(q_sum_all);

// 第四级：舍入结果各符号扩展到32位，再拼成64位输出
always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        m_axis_tdata <= 64'd0;
    end
    else if (pipeline_enable && stage3_valid) begin
        m_axis_tdata <= {output_q[30], output_q, output_i[30], output_i};
    end
end

endmodule

module simple_fifo #(
    parameter DATA_WIDTH = 8,               // 数据位宽
    parameter FIFO_DEPTH = 64               // 数据深度，至少为1
)(
    input  wire                                     clk,
    input  wire                                     rst_n,

    input  wire                                     wr_en,     // 写使能
    input  wire [DATA_WIDTH-1:0]                    wr_data,   // 写数据

    input  wire                                     rd_en,     // 读使能
    output reg  [DATA_WIDTH-1:0]                    rd_data,   // FWFT读数据

    input  wire                                     clean,     // 清除标志

    output reg  [$clog2(FIFO_DEPTH + 1) - 1 : 0]    num,       // 当前数据个数
    output wire                                     empty,
    output wire                                     full
);

// 指针位宽计算
localparam PTR_WIDTH = (FIFO_DEPTH > 1) ? $clog2(FIFO_DEPTH) : 1;

// 自定义RAM
reg [DATA_WIDTH-1:0] mem [0:FIFO_DEPTH-1];

// 指针
reg [PTR_WIDTH-1:0] wr_ptr;                                 // 指向下一个等待写入的位置
reg [PTR_WIDTH-1:0] rd_ptr;                                 // 指向当前待读取的数据

// 环形队列指针
wire [PTR_WIDTH-1:0] wr_ptr_next;
wire [PTR_WIDTH-1:0] rd_ptr_next;

assign wr_ptr_next = (wr_ptr == FIFO_DEPTH - 1) ? {PTR_WIDTH{1'b0}} : (wr_ptr + 1'b1);
assign rd_ptr_next = (rd_ptr == FIFO_DEPTH - 1) ? {PTR_WIDTH{1'b0}} : (rd_ptr + 1'b1);

// 状态标志
assign full  = (num == FIFO_DEPTH);
assign empty = (num == 0);

// 读写使能
wire read_allow;
wire write_allow;

assign read_allow  = rd_en && !empty;
assign write_allow = wr_en && (!full || read_allow);        // FIFO满时，本周期同时读取则允许继续写入

// RAM写入使用独立的同步过程，避免异步复位过程阻止Vivado推断分布式RAM
always @(posedge clk) begin
    if (rst_n && !clean && write_allow) begin
        mem[wr_ptr] <= wr_data;
    end
end

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        wr_ptr <= 0;
        rd_ptr <= 0;
        num    <= 0;
    end
    else if (clean) begin
        wr_ptr <= 0;
        rd_ptr <= 0;
        num    <= 0;
    end
    else begin
        // 写操作
        if (write_allow) begin
            wr_ptr      <= wr_ptr_next;
        end

        // 读操作
        if (read_allow) begin
            rd_ptr <= rd_ptr_next;
        end

        // 数据数量更新
        case ({write_allow, read_allow})
            2'b10: num <= num + 1'b1;                       // 只写不读
            2'b01: num <= num - 1'b1;                       // 只读不写
            default: num <= num;                            // 同时读写或均不操作
        endcase
    end
end

// FWFT输出：FIFO非空时，始终输出当前队头数据
always @(*) begin
    if (empty) begin
        rd_data = {DATA_WIDTH{1'b0}};
    end
    else begin
        rd_data = mem[rd_ptr];
    end
end

endmodule
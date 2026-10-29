`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/29 12:30:23
// Design Name: 
// Module Name: complex_phase_rotator
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 处理速率：小于 aclk/3       <-- 注意！！！
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////


module complex_phase_rotator#(
    parameter integer CHANNEL_SELECT = 0       // 选择通道
)(
    input   wire    aclk,
    input   wire    aresetn,

    input   wire [63:0]              phase_shift_coefficient,
    input   wire                     config_en,

    input   wire [63:0]              s_axis_tdata,
    input   wire                     s_axis_tvalid,
    output  wire                     s_axis_tready,

    output  wire [101:0]             m_axis_tdata,
    output  reg                      m_axis_tvalid,
    input   wire                     m_axis_tready
);

wire [2:0]  channel_sel     = phase_shift_coefficient[63:61];
wire signed [23:0] sin      = phase_shift_coefficient[47:24];
wire signed [23:0] cos      = phase_shift_coefficient[23:0];

reg signed [23:0]  sin_reg;
reg signed [23:0]  cos_reg;

wire channel_change = (channel_sel == CHANNEL_SELECT) && config_en;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        sin_reg <= 24'd0;
        cos_reg <= 24'h7FFFFF;
    end
    else if(channel_change)begin
        sin_reg <= sin;
        cos_reg <= cos;
    end
    else begin
        sin_reg <= sin_reg;
        cos_reg <= cos_reg;
    end
end

// 匹配乘法器输入位宽
wire signed [25:0] cos_extended = {{2{cos_reg[23]}}, cos_reg};
wire signed [25:0] sin_extended = {{2{sin_reg[23]}}, sin_reg};

wire signed [25:0] data_i = s_axis_tdata[25:0];
wire signed [25:0] data_q = s_axis_tdata[57:32];

reg signed [25:0] data_i_reg;
reg signed [25:0] data_q_reg;

wire signed [51:0] mult_A_full;
wire signed [51:0] mult_B_full;

wire signed [49:0] mult_A = mult_A_full[49:0];
wire signed [49:0] mult_B = mult_B_full[49:0];

reg signed [25:0] data_a_reg;
reg signed [25:0] data_b_reg;
reg mult_en_reg;

wire mult_en                = mult_en_reg;
wire signed [25:0] data_a   = data_a_reg;
wire signed [25:0] data_b   = data_b_reg;

wire finish_A;
wire finish_B;

reg stage1;
reg stage2;

assign s_axis_tready = aresetn && !stage1 && !stage2;
wire input_transfer = s_axis_tready && s_axis_tvalid;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        data_i_reg  <= 0;
        data_q_reg  <= 0;
        data_a_reg  <= 0;
        data_b_reg  <= 0;
        mult_en_reg <= 1'b0;
        stage1      <= 1'b0;
        stage2      <= 1'b0;
    end
    else begin
        stage1  <= input_transfer;
        stage2  <= stage1;

        if (input_transfer) begin
            data_i_reg  <= data_i;
            data_q_reg  <= data_q;

            data_a_reg  <= data_i;
            data_b_reg  <= data_q;
            mult_en_reg <= 1'b1;
        end

        if (stage1) begin
            data_a_reg  <= data_q_reg;
            data_b_reg  <= data_i_reg;
            mult_en_reg <= 1'b1;
        end

        if (stage2) begin
            mult_en_reg <= 1'b0;
        end
    end
end

four_partial_product_multiplier #(
    .DATA_WIDE(26)
)mode_A
(
    .aclk      (aclk),
    .aresetn   (aresetn),
    .enable    (mult_en),

    .A         (data_a),
    .B         (cos_extended),

    .P         (mult_A_full),
    .finish    (finish_A)
);

four_partial_product_multiplier #(
    .DATA_WIDE(26)
)mode_B
(
    .aclk      (aclk),
    .aresetn   (aresetn),
    .enable    (mult_en),

    .A         (data_b),
    .B         (sin_extended),

    .P         (mult_B_full),
    .finish    (finish_B)
);

reg signed [50:0]  result_i;
reg signed [50:0]  result_q;

wire finish = finish_A && finish_B;
reg  stage3;
reg  stage4;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        m_axis_tvalid   <= 1'b0;
        stage3          <= 1'b0;
        stage4          <= 1'b0;
        result_i        <= 0;
        result_q        <= 0;
    end
    else begin
        if (finish && (!stage3)) begin
            result_i    <= mult_A - mult_B;
            stage3      <= 1'b1;
        end

        if (finish && stage3) begin
            result_q    <= mult_B + mult_A;
            stage3      <= 1'b0;
            stage4      <= 1'b1;
        end

        if (stage4) begin
            m_axis_tvalid   <= 1'b1;
            stage4          <= 1'b0;
        end 
        else begin
            m_axis_tvalid   <= 1'b0;
        end
    end
end

assign m_axis_tdata = {result_i, result_q};

endmodule

module four_partial_product_multiplier#(
    parameter integer DATA_WIDE = 16        // 输入必须为偶数
)(
    input wire  aclk,
    input wire  aresetn,
    input wire  enable,

    input wire  signed  [DATA_WIDE-1: 0]    A,
    input wire  signed  [DATA_WIDE-1: 0]    B,
    output wire signed  [2*DATA_WIDE-1: 0]  P,
    output reg  finish
);

wire signed [(DATA_WIDE/2)-1: 0]    A_H = A[DATA_WIDE-1: DATA_WIDE/2];
wire signed [(DATA_WIDE/2)-1: 0]    B_H = B[DATA_WIDE-1: DATA_WIDE/2];
wire        [(DATA_WIDE/2)-1: 0]    A_L = A[(DATA_WIDE/2)-1: 0];
wire        [(DATA_WIDE/2)-1: 0]    B_L = B[(DATA_WIDE/2)-1: 0];

reg stage1;
reg signed [DATA_WIDE-1: 0] P1;
reg signed [DATA_WIDE: 0]   P2;
reg signed [DATA_WIDE: 0]   P3;
reg        [DATA_WIDE-1: 0] P4;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        stage1  <= 1'b0;
        P1      <= 0;
        P2      <= 0;
        P3      <= 0;
        P4      <= 0;
    end
    else begin
        stage1  <= enable;
        if(enable)begin
            P1      <= A_H * B_H;
            P2      <= A_H * $signed({1'b0,B_L});
            P3      <= B_H * $signed({1'b0,A_L});
            P4      <= A_L * B_L;
        end        
    end
end

wire signed  [2*DATA_WIDE-1: 0] P1_ext = {{DATA_WIDE{P1[DATA_WIDE-1]}}, P1}     <<<  DATA_WIDE;
wire signed  [2*DATA_WIDE-1: 0] P2_ext = {{(DATA_WIDE-1){P2[DATA_WIDE]}}, P2}   <<<  (DATA_WIDE/2);
wire signed  [2*DATA_WIDE-1: 0] P3_ext = {{(DATA_WIDE-1){P3[DATA_WIDE]}}, P3}   <<<  (DATA_WIDE/2);
wire signed  [2*DATA_WIDE-1: 0] P4_ext = {{DATA_WIDE{1'b0}}, P4};

reg stage2;
reg signed [2*DATA_WIDE: 0] P_add1;
reg signed [2*DATA_WIDE: 0] P_add2;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        stage2  <= 1'b0;
        P_add1  <= 0;
        P_add2  <= 0;
    end
    else begin
        stage2  <= stage1;
        if(stage1) begin
            P_add1  <= P1_ext + P2_ext;
            P_add2  <= P3_ext + P4_ext;
        end        
    end
end

reg signed [2*DATA_WIDE+1: 0]  P_ext;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        finish  <= 1'b0;
        P_ext   <= 0;
    end
    else begin            
        finish  <= stage2;
        if(stage2)begin
            P_ext   <= P_add1 + P_add2;
        end        
    end
end

assign P = P_ext[2*DATA_WIDE-1: 0];

endmodule
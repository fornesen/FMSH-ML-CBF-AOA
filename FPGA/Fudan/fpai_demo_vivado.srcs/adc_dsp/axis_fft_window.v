`timescale 1ns / 1ps

// 六路 FFT 共用的启动段屏蔽及周期 Hann 窗，不改变 PHA 分支。
// 每帧 4096 点：0..63 置零；64..4095 使用完整的 4032 点窗。
// I/Q 均为 signed26，分别位于 [25:0]、[57:32]；其余填充位不参与运算。
module axis_fft_window #(
    parameter COEFFICIENT_FILE = "fft_hann_4032_q17.mem"
)(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF s_axis_fft:m_axis_fft, ASSOCIATED_RESET aresetn" *)
    input  wire                     aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire                     aresetn,

    input  wire [63:0]              s_axis_fft_tdata,
    input  wire                     s_axis_fft_tvalid,
    output wire                     s_axis_fft_tready,
    input  wire                     s_axis_fft_tlast,

    output wire [63:0]              m_axis_fft_tdata,
    output wire                     m_axis_fft_tvalid,
    input  wire                     m_axis_fft_tready,
    output wire                     m_axis_fft_tlast
);

localparam integer FFT_LENGTH       = 4096;
localparam integer COEFFICIENT_BITS = 18;
localparam integer FRACTION_BITS    = 17;
localparam integer PRODUCT_BITS     = 26 + COEFFICIENT_BITS + 1;

// Q17 无符号窗系数：0..131072，其中 131072 精确表示 1。
// 文件由 tools/generate_fft_window.py 生成，前 64 项已包含零系数。
(* rom_style = "block" *)
reg [COEFFICIENT_BITS-1:0]       coefficient_rom [0:FFT_LENGTH-1];
reg [COEFFICIENT_BITS-1:0]       coefficient;
reg [11:0]                     sample_count;
reg signed [25:0]              i_sample;
reg signed [25:0]              q_sample;
(* use_dsp = "yes" *) reg signed [PRODUCT_BITS-1:0] i_product;
(* use_dsp = "yes" *) reg signed [PRODUCT_BITS-1:0] q_product;
reg [25:0]                     i_result;
reg [25:0]                     q_result;
reg [2:0]                      valid_pipe;
reg [2:0]                      last_pipe;

wire                           advance;
wire                           input_transfer;

initial begin
    $readmemh(COEFFICIENT_FILE, coefficient_rom);
end

// 末级阻塞时整条流水线保持；有效样本、系数和 TLAST 始终一起推进。
assign advance            = !valid_pipe[2] || m_axis_fft_tready;
assign s_axis_fft_tready   = aresetn && advance;
assign input_transfer     = s_axis_fft_tvalid && s_axis_fft_tready;
assign m_axis_fft_tvalid   = aresetn && valid_pipe[2];
assign m_axis_fft_tlast    = m_axis_fft_tvalid && last_pipe[2];
assign m_axis_fft_tdata    = {6'b0, q_result, 6'b0, i_result};

// 有符号乘积算术右移，余数非负；半 LSB 时舍入到偶数，避免负数偏置。
// 窗系数不超过 1，结果仍在 signed26 范围内，不需要扩大 FFT 输入位宽。
function [25:0] round_product;
    input signed [PRODUCT_BITS-1:0] value;
    reg signed [PRODUCT_BITS-1:0] rounded;
    reg [FRACTION_BITS-1:0] remainder;
    begin
        rounded   = value >>> FRACTION_BITS;
        remainder = value[FRACTION_BITS-1:0];
        if ((remainder > 17'h10000) ||
            ((remainder == 17'h10000) && rounded[0])) begin
            rounded = rounded + 1'b1;
        end
        round_product = rounded[25:0];
    end
endfunction

// 同步 ROM 读出及输入寄存器不使用复位，便于推断块 RAM；valid 屏蔽无效内容。
always @(posedge aclk) begin
    if (aresetn && advance) begin
        coefficient <= coefficient_rom[sample_count];
        i_sample    <= $signed(s_axis_fft_tdata[25:0]);
        q_sample    <= $signed(s_axis_fft_tdata[57:32]);
        i_product   <= i_sample * $signed({1'b0, coefficient});
        q_product   <= q_sample * $signed({1'b0, coefficient});
        i_result    <= round_product(i_product);
        q_result    <= round_product(q_product);
    end
end

// valid 也采用同步复位，因为它参与 ROM 的时钟使能；复位至少保持一拍。
always @(posedge aclk) begin
    if (!aresetn) begin
        valid_pipe   <= 3'b0;
        last_pipe    <= 3'b0;
    end
    else if (advance) begin
        valid_pipe <= {valid_pipe[1:0], s_axis_fft_tvalid};
        last_pipe  <= {last_pipe[1:0], s_axis_fft_tlast};
    end
end

// ROM 地址同步复位，避免异步复位在块 RAM 读地址上产生毛刺。
always @(posedge aclk) begin
    if (!aresetn)
        sample_count <= 12'd0;
    else if (input_transfer) begin
        // 固定帧长不压缩时序，不跨通道借用后续帧的 64 点。
        if (sample_count == FFT_LENGTH-1)
            sample_count <= 12'd0;
        else
            sample_count <= sample_count + 1'b1;
    end
end

endmodule

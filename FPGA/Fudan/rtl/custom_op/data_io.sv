module data_io #(
    parameter integer CH_NUM = 6,
    parameter integer DDS_CTRL_WIDTH = 32,
    parameter integer PHA_CTRL_WIDTH = 64,
    parameter integer DIN_WIDTH = 96,
    parameter integer OP_OUT_WIDTH = 80,
    parameter integer PHA_OUT_WIDTH = 64,
    parameter integer DIN_CNT_WIDTH = 14,
    parameter integer DOUT_CNT_WIDTH = 15,
    parameter integer OUT_SPLICE_WIDTH = 192
)(
    input   clk,
    input   ctrl_clk,
    input   rstn,

    input                           dds_conf_done,
    input   [DDS_CTRL_WIDTH-1:0]    dds_ctrl,

    input                           pha_conf_done,
    input   [PHA_CTRL_WIDTH/2-1:0]  pha_coe0,
    input   [PHA_CTRL_WIDTH/2-1:0]  pha_coe1,

    input   [DIN_CNT_WIDTH:0]       din_size,
    input                           din_valid,
    output                          din_ready,
    input   [DIN_WIDTH-1:0]         din_data,

    input   [DOUT_CNT_WIDTH:0]      dout_size,
    output                          dout_valid,
    input                           dout_ready,
    output  [OUT_SPLICE_WIDTH-1:0]  dout_data,

    output                          odata_ready
);

localparam OP_DATA_LEN_MAX = 15'd24576;
localparam PHA_DATA_LEN_MAX = 12'd4095;

// clock
logic sys_clk;
logic [0:0] sys_rstn_cnt;
logic sys_rstn;
// clk_div2 clk_div2_inst (
//     .clk(clk),
//     .rstn(rstn),
//     .clk_div(sys_clk)
// );
assign sys_clk = ctrl_clk;
always_ff@(posedge sys_clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        sys_rstn_cnt <= 'b0;
        sys_rstn <= 1'b0;
    end else if(sys_rstn_cnt < 'd1) begin
        sys_rstn_cnt <= sys_rstn_cnt + 1'b1;
    end else begin
        sys_rstn <= 1'b1;
    end
end
// input data size
logic [DIN_CNT_WIDTH-1:0] idata_size;
assign idata_size = (din_size < 'd1) ? 'd1 : din_size - 1'b1;
// input write state
logic [0:0] ififo_wt_state;
logic [DIN_CNT_WIDTH-1:0] din_wt_cnt;
// input data
logic ififo_full;
logic ififo_empty;
logic ififo_wt_en;
// input read state
logic ififo_rd_flag;
logic [0:0] ififo_rd_state;
logic ififo_rd_en;
logic [DIN_CNT_WIDTH-1:0] din_rd_cnt;
// operator module
// logic dds_conf_pipe0;
// logic dds_conf_pipe1;
// logic [DDS_CTRL_WIDTH-1:0] dds_ctrl_buf;
// logic [DDS_CTRL_WIDTH-1:0] dds_ctrl_fifo;
// logic pha_conf_pipe0;
// logic pha_conf_pipe1;
// logic [PHA_CTRL_WIDTH-1:0] pha_coe_fifo;
logic pha_conf_valid;
logic [PHA_CTRL_WIDTH-1:0] pha_coe_buf;
logic op_iready;
logic op_ivalid;
logic [DIN_WIDTH-1:0] op_idata;
logic op_oready;
logic op_ovalid;
logic [OP_OUT_WIDTH-1:0] op_odata;
logic op_otlast;
logic [$clog2(CH_NUM)-1:0] op_otch;
// output data
logic op_ofifo_wt_valid;
logic op_ofifo_rd_valid;
logic [OP_OUT_WIDTH-1:0] op_data_ofifo;
logic op_ofifo_full;
logic op_ofifo_empty;
// output phase
logic [PHA_OUT_WIDTH-1:0] pha_odata;
logic pha_oready;
logic pha_ovalid;
logic pha_ofifo_wt_valid;
logic pha_ofifo_rd_valid;
logic [PHA_OUT_WIDTH-1:0] pha_data_ofifo;
logic pha_ofifo_full;
logic pha_ofifo_empty;
// output size
logic [$clog2(OP_DATA_LEN_MAX)-1:0] op_odata_size;
assign op_odata_size = (dout_size < 'd1) ? 'd1 : (dout_size > OP_DATA_LEN_MAX) ? OP_DATA_LEN_MAX : dout_size - 1'b1;
logic [$clog2(PHA_DATA_LEN_MAX)-1:0] pha_odata_size;
assign pha_odata_size = (dout_size < 'd1) ? 'd1 : (dout_size > PHA_DATA_LEN_MAX) ? PHA_DATA_LEN_MAX : dout_size - 1'b1;
// output read state
logic dout_rd_trig;
logic [0:0] op_ofifo_rd_state;
logic [$clog2(OP_DATA_LEN_MAX)-1:0] op_dout_rd_cnt;
logic [0:0] pha_ofifo_rd_state;
logic [$clog2(PHA_DATA_LEN_MAX)-1:0] pha_dout_rd_cnt;
// output splice
logic dout_splice_ready;
logic [OUT_SPLICE_WIDTH-1:0] dout_splice_data;

// input write state
always_ff@(posedge clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        ififo_wt_state <= 'h0;
    end else begin
        case(ififo_wt_state)
        'h0: begin
            if(ififo_wt_en == 1'b1) begin
                ififo_wt_state <= 'h1;
            end
        end
        'h1: begin
            if((ififo_wt_en == 1'b1) && (din_wt_cnt == idata_size)) begin
                ififo_wt_state <= 'h0;
            end
        end
        default: ififo_wt_state <= 'h0;
        endcase
    end
end

always_ff@(posedge clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        din_wt_cnt <= 'b0;
    end else if(ififo_wt_en == 1'b1) begin
        if(din_wt_cnt == idata_size) begin
            din_wt_cnt <= 'b0;
        end
        else begin
            din_wt_cnt <= din_wt_cnt + 1'b1;
        end
    end
end

// input data
assign din_ready = !ififo_full;
assign ififo_wt_en = din_valid & din_ready;
fifo_generator_data_io_i fifo_generator_data_io_i_inst (
    .rst(~rstn),                  // input wire rst
    .wr_clk(clk),            // input wire wr_clk
    .rd_clk(sys_clk),            // input wire rd_clk

    .din(din_data),                  // input wire [95 : 0] din
    .wr_en(ififo_wt_en),              // input wire wr_en
    .rd_en(ififo_rd_en),              // input wire rd_en
    .dout(op_idata),                // output wire [95 : 0] dout

    .full(ififo_full),                // output wire full
    .empty(ififo_empty)              // output wire empty
);

// input read state
// always_ff@(posedge clk or negedge rstn)begin
always_ff@(posedge sys_clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        ififo_rd_flag <= 1'b0;
    end else if(din_rd_cnt == idata_size) begin
        ififo_rd_flag <= 1'b0;
    end else if(ififo_empty == 1'b0) begin
        ififo_rd_flag <= 1'b1;
    end
end

always_ff@(posedge sys_clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        ififo_rd_state <= 'h0;
    end else begin
        case(ififo_rd_state)
        'h0: begin
            if(ififo_rd_flag == 1'b1) begin
                ififo_rd_state <= 'h1;
            end
        end
        'h1: begin
            if((ififo_rd_en == 1'b1) && (din_rd_cnt == idata_size)) begin
                ififo_rd_state <= 'h0;
            end
        end
        default: ififo_rd_state <= 'h0;
        endcase
    end
end
assign ififo_rd_en = op_ivalid & op_iready;

always_ff@(posedge sys_clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        din_rd_cnt <= 'b0;
    end else if(ififo_rd_en == 1'b1) begin
        if(din_rd_cnt == idata_size) begin
            din_rd_cnt <= 'b0;
        end
        else begin
            din_rd_cnt <= din_rd_cnt + 1'b1;
        end
    end
end

// operator module
// always_ff@(posedge ctrl_clk or negedge rstn)begin
//     if(rstn == 1'b0) begin
//         dds_conf_pipe0 <= 1'b0;
//         dds_ctrl_buf <= 'b0;
//     end else if(dds_conf_pipe1 == 1'b1) begin
//         dds_conf_pipe0 <= 1'b0;
//     end else if(dds_conf_done == 1'b1) begin
//         dds_conf_pipe0 <= 1'b1;
//         dds_ctrl_buf <= dds_ctrl;
//     end
// end
// always_ff@(posedge sys_clk or negedge rstn)begin
//     if(rstn == 1'b0) begin
//         dds_conf_pipe1 <= 1'b0;
//         dds_ctrl_fifo <= 'b0;
//     end else if(dds_conf_pipe0 == 1'b1) begin
//         dds_conf_pipe1 <= 1'b1;
//         dds_ctrl_fifo <= dds_ctrl_buf;
//     end else begin
//         dds_conf_pipe1 <= 1'b0;
//     end
// end

// always_ff@(posedge ctrl_clk or negedge rstn)begin
//     if(rstn == 1'b0) begin
//         pha_conf_pipe0 <= 1'b0;
//         pha_coe_buf <= 'b0;
//     end else if(pha_conf_pipe1 == 1'b1) begin
//         pha_conf_pipe0 <= 1'b0;
//     end else if(pha_conf_done == 1'b1)begin
//         pha_conf_pipe0 <= 1'b1;
//         pha_coe_buf[PHA_CTRL_WIDTH/2-1:0] <= pha_coe0;
//         pha_coe_buf[PHA_CTRL_WIDTH-1:PHA_CTRL_WIDTH/2] <= pha_coe1;
//     end
// end
// always_ff@(posedge sys_clk or negedge rstn)begin
//     if(rstn == 1'b0) begin
//         pha_conf_pipe1 <= 1'b0;
//         pha_coe_fifo <= 'b0;
//     end else if(pha_conf_pipe0 == 1'b1) begin
//         pha_conf_pipe1 <= 1'b1;
//         pha_coe_fifo <= pha_coe_buf;
//     end else begin
//         pha_conf_pipe1 <= 1'b0;
//     end
// end
// always_ff@(posedge sys_clk or negedge rstn)begin
//     if(rstn == 1'b0) begin
//         pha_conf_valid <= 1'b0;
//     end else begin
//         pha_conf_valid <= pha_conf_pipe1;
//     end
// end
always_ff@(posedge ctrl_clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        pha_conf_valid <= 1'b1;
        pha_coe_buf <= 'b0;
    end else if(pha_conf_done == 1'b1)begin
        pha_conf_valid <= 1'b1;
        pha_coe_buf[PHA_CTRL_WIDTH/2-1:0] <= pha_coe0;
        pha_coe_buf[PHA_CTRL_WIDTH-1:PHA_CTRL_WIDTH/2] <= pha_coe1;
    end else begin
        pha_conf_valid <= 1'b0;
    end
end

assign op_ivalid = !ififo_empty & (ififo_rd_state == 'h1);
assign op_oready = !op_ofifo_full;
assign pha_oready = ~pha_ofifo_full;
adc_dsp_wrapper adc_dsp_wrapper_inst (
    .aclk(sys_clk),
    .aresetn(sys_rstn),

    .adc_axi_tdata(op_idata),
    .adc_axi_tready(op_iready),
    .adc_axi_tvalid(op_ivalid),

    .dds_fre(dds_ctrl),

    .pha_shift_coe(pha_coe_buf),
    .pha_shift_conf_en(pha_conf_valid),

    .fft_axi_tdata(op_odata),
    .fft_axi_tlast(op_otlast),
    .fft_axi_tready(op_oready),
    .fft_axi_tuser(op_otch),
    .fft_axi_tvalid(op_ovalid),

    .pha_axi_tdata(pha_odata),
    .pha_axi_tready(pha_oready),
    .pha_axi_tvalid(pha_ovalid),

    .fft_conf_done(),
    .fft_sel_channel(),
    .fir_fft_trans_err(),

    .pha_overflow_err()
);

// output data
assign op_ofifo_wt_valid = op_ovalid & !op_ofifo_full;
assign op_ofifo_rd_valid = (op_ofifo_rd_state == 'h1) & !op_ofifo_empty & dout_splice_ready;
fifo_generator_data_io_o fifo_generator_data_io_o_inst (
    .rst(~rstn),                  // input wire rst
    .wr_clk(sys_clk),            // input wire wr_clk
    .rd_clk(clk),            // input wire rd_clk

    .din(op_odata),                  // input wire [79 : 0] din
    .wr_en(op_ofifo_wt_valid),              // input wire wr_en
    .rd_en(op_ofifo_rd_valid),              // input wire rd_en
    .dout(op_data_ofifo),                // output wire [79 : 0] dout

    .full(op_ofifo_full),                // output wire full
    .empty(op_ofifo_empty)              // output wire empty
);

// output phase
assign pha_ofifo_wt_valid = pha_ovalid & !pha_ofifo_full;
assign pha_ofifo_rd_valid = (pha_ofifo_rd_state == 'h1) & !pha_ofifo_empty & dout_splice_ready;
fifo_generator_data_io_pha fifo_generator_data_io_pha_inst (
    .rst(~rstn),        // input wire rst
    .wr_clk(sys_clk),  // input wire wr_clk
    .rd_clk(clk),  // input wire rd_clk

    .din(pha_odata),        // input wire [63 : 0] din
    .wr_en(pha_ofifo_wt_valid),    // input wire wr_en
    .rd_en(pha_ofifo_rd_valid),    // input wire rd_en
    .dout(pha_data_ofifo),      // output wire [63 : 0] dout

    .full(pha_ofifo_full),      // output wire full
    .empty(pha_ofifo_empty)    // output wire empty
);

// output splice
assign dout_splice_data = (pha_ofifo_rd_valid == 1'b1) ?
{pha_data_ofifo, {(128-OP_OUT_WIDTH){1'b0}}, op_data_ofifo} : {{(OUT_SPLICE_WIDTH-OP_OUT_WIDTH){1'b0}}, op_data_ofifo};

avr_rs #(
    .DW(OUT_SPLICE_WIDTH)
) avr_rs_dout (
    .m_data      (dout_splice_data),//I
    .m_valid     (op_ofifo_rd_valid),//I
    .m_ready     (dout_splice_ready),//O

    .s_data      ( dout_data      ),//O
    .s_valid     ( dout_valid     ),//O
    .s_ready     ( dout_ready     ),//I

    .clk         ( clk            ),
    .rst_n       ( rstn           )
);

// output read state
assign dout_rd_trig = op_ofifo_wt_valid & (op_otch == CH_NUM / 2 - 1) & op_otlast;
pulse_cross pulse_cross_oready (
    .a2      (odata_ready),
    .clk2    (clk),
    .rst2    (~rstn),

    .rdy1    (),
    .a1      (dout_rd_trig),
    .clk1    (sys_clk),
    .rst1    (~rstn)
);

always_ff@(posedge clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        op_ofifo_rd_state <= 'h0;
    end else begin
        case(op_ofifo_rd_state)
        'h0: begin
            if(odata_ready == 1'b1) begin
                op_ofifo_rd_state <= 'h1;
            end
        end
        'h1: begin
            if((op_ofifo_rd_valid == 1'b1) && (op_dout_rd_cnt == op_odata_size)) begin
                op_ofifo_rd_state <= 'h0;
            end
        end
        default: op_ofifo_rd_state <= 'h0;
        endcase
    end
end

always_ff@(posedge clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        op_dout_rd_cnt <= 'b0;
    end else if(op_ofifo_rd_valid == 1'b1) begin
        if(op_dout_rd_cnt == op_odata_size) begin
            op_dout_rd_cnt <= 'b0;
        end else begin
            op_dout_rd_cnt <= op_dout_rd_cnt + 1'b1;
        end
    end
end

always_ff@(posedge clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        pha_ofifo_rd_state <= 'h0;
    end else begin
        case(pha_ofifo_rd_state)
        'h0: begin
            if(odata_ready == 1'b1) begin
                pha_ofifo_rd_state <= 'h1;
            end
        end
        'h1: begin
            if((pha_ofifo_rd_valid == 1'b1) && (pha_dout_rd_cnt == pha_odata_size)) begin
                pha_ofifo_rd_state <= 'h0;
            end
        end
        default: pha_ofifo_rd_state <= 'h0;
        endcase
    end
end

always_ff@(posedge clk or negedge rstn)begin
    if(rstn == 1'b0) begin
        pha_dout_rd_cnt <= 'b0;
    end else if(pha_ofifo_rd_valid == 1'b1) begin
        if(pha_dout_rd_cnt == pha_odata_size) begin
            pha_dout_rd_cnt <= 'b0;
        end else begin
            pha_dout_rd_cnt <= pha_dout_rd_cnt + 1'b1;
        end
    end
end

endmodule

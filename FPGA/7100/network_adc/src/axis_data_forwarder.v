`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Converts an unthrottled TDATA/TVALID sample stream into a complete
// AXI4-Stream master interface suitable for AXI DMA S2MM.
//
// Input and output use the same clock. Because the input has no TREADY, a
// finite FIFO can absorb only bounded downstream stalls. data_overflow is
// sticky until reset and indicates that at least one input sample was lost.
//////////////////////////////////////////////////////////////////////////////////

module axis_data_forwarder #(
    parameter integer DATA_WIDTH       = 96,
    parameter integer AXIS_FRAME_WORDS = 16384
    // parameter integer FIFO_DEPTH       = 4096
) (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 axis_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME axis_aclk, ASSOCIATED_BUSIF S_AXIS_IN:M_AXIS_OUT, ASSOCIATED_RESET axis_aresetn:capture_resetn" *)
    input                       axis_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 axis_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME axis_aresetn, POLARITY ACTIVE_LOW" *)
    input                       axis_aresetn,
    // Per-capture reset from AXI DMA s2mm_prmry_reset_out_n. A DMA soft
    // reset clears the sample FIFO and restarts AXIS frame generation.
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 capture_resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME capture_resetn, POLARITY ACTIVE_LOW" *)
    input                       capture_resetn,

    // Unthrottled input stream from the ADC processing module.
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_IN TVALID" *)
    input                       s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_IN TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS_IN, TDATA_NUM_BYTES 12, HAS_TREADY 0, HAS_TKEEP 0, HAS_TLAST 0" *)
    input      [DATA_WIDTH-1:0] s_axis_tdata,

    // Complete AXI4-Stream master interface to AXI DMA S_AXIS_S2MM.
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_OUT TREADY" *)
    input                       m_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_OUT TVALID" *)
    output                      m_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_OUT TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS_OUT, TDATA_NUM_BYTES 12, HAS_TREADY 1, HAS_TKEEP 1, HAS_TLAST 1" *)
    output     [DATA_WIDTH-1:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_OUT TKEEP" *)
    output [(DATA_WIDTH/8)-1:0] m_axis_tkeep,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_OUT TLAST" *)
    output                      m_axis_tlast

    // Sticky overflow diagnostic; clear by asserting either reset Low.
    // output reg                  data_overflow
);

localparam integer FRAME_CNT_WIDTH =
    (AXIS_FRAME_WORDS <= 1) ? 1 : $clog2(AXIS_FRAME_WORDS);

wire                      fifo_full;
wire                      fifo_empty;
wire                      fifo_wr_rst_busy;
wire                      fifo_rd_rst_busy;
wire     [DATA_WIDTH-1:0] fifo_dout;
wire                      fifo_write;
wire                      fifo_read;
wire                      internal_aresetn;

reg fifo_rw_state;
reg [FRAME_CNT_WIDTH-1:0] frame_count;

assign internal_aresetn = axis_aresetn && capture_resetn;
// assign fifo_write = s_axis_tvalid && !fifo_full && !fifo_wr_rst_busy;
assign fifo_write = s_axis_tvalid && !fifo_full && (fifo_rw_state == 1'b0) && !fifo_wr_rst_busy;
assign fifo_read  = m_axis_tvalid && m_axis_tready;

// The FWFT mode exposes the first FIFO word without requiring a speculative
// read. The word remains stable while m_axis_tready is Low.
xpm_fifo_sync #(
    .FIFO_MEMORY_TYPE  ("auto"),
    // .FIFO_WRITE_DEPTH  (FIFO_DEPTH),
    .FIFO_WRITE_DEPTH  (AXIS_FRAME_WORDS),
    .WRITE_DATA_WIDTH  (DATA_WIDTH),
    .READ_DATA_WIDTH   (DATA_WIDTH),
    .READ_MODE         ("fwft"),
    .FIFO_READ_LATENCY (0)
) input_fifo (
    .rst           (!internal_aresetn),
    .wr_clk        (axis_aclk),
    .wr_en         (fifo_write),
    .din           (s_axis_tdata),
    .full          (fifo_full),
    .wr_rst_busy   (fifo_wr_rst_busy),

    .rd_en         (fifo_read),
    .dout          (fifo_dout),
    .empty         (fifo_empty),
    .rd_rst_busy   (fifo_rd_rst_busy),

    .sleep         (1'b0),
    .injectsbiterr (1'b0),
    .injectdbiterr (1'b0)
);

// Detect every input sample that cannot enter the FIFO. Since the source has
// no TREADY, such a sample cannot be recovered.
// always @(posedge axis_aclk or negedge internal_aresetn) begin
//     if(!internal_aresetn)
//         data_overflow <= 1'b0;
//     else if(s_axis_tvalid && (fifo_full || fifo_wr_rst_busy))
//         data_overflow <= 1'b1;
// end

// fifo read_write state
always @(posedge axis_aclk or negedge internal_aresetn) begin
    if(!internal_aresetn) begin
        fifo_rw_state <= 1'b0;
    end else begin
        case(fifo_rw_state)
        1'b0: begin
            if(fifo_full == 1'b1) begin
                fifo_rw_state <= 1'b1;
            end
        end
        1'b1: begin
            if(fifo_read && (frame_count == AXIS_FRAME_WORDS - 1)) begin
                fifo_rw_state <= 1'b0;
            end
        end
        default: fifo_rw_state <= 1'b0;
        endcase
    end
end

// Count only completed AXI4-Stream transfers. This guarantees that TLAST
// remains aligned even when AXI DMA deasserts TREADY.
always @(posedge axis_aclk or negedge internal_aresetn) begin
    if(!internal_aresetn) begin
        frame_count <= {FRAME_CNT_WIDTH{1'b0}};
    end else if(fifo_read) begin
        if(frame_count == AXIS_FRAME_WORDS - 1)
            frame_count <= {FRAME_CNT_WIDTH{1'b0}};
        else
            frame_count <= frame_count + 1'b1;
    end
end

// assign m_axis_tvalid = internal_aresetn && !fifo_empty && !fifo_rd_rst_busy;
assign m_axis_tvalid = internal_aresetn && !fifo_empty && (fifo_rw_state == 1'b1) && !fifo_rd_rst_busy;
assign m_axis_tdata  = fifo_dout;
assign m_axis_tkeep  = {(DATA_WIDTH/8){1'b1}};
assign m_axis_tlast  = m_axis_tvalid && (frame_count == AXIS_FRAME_WORDS - 1);

endmodule

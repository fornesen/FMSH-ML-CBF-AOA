`timescale 1ns / 1ps
// Real ADC frontend: common 100 MHz clock, six simultaneous 12-bit inputs.
// Keep raw ADC codes: each 12-bit lane is ZERO extended to 16, CH0 in bits15:0.
// No offset/sign conversion here; the 7100 PS forwards DMA bytes unchanged.
// The downstream consumer must select the matching ADC coding exactly once.
module adc_capture_6ch (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME clk, ASSOCIATED_BUSIF m_axis, ASSOCIATED_RESET rstn, FREQ_HZ 100000000" *)
    input wire clk,
    // Same MMCM, same 100 MHz frequency, delayed 135 degrees (3.75 ns).
    // The outward clock/ADC/return path is too long for the original edge.
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 capture_clk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME capture_clk, FREQ_HZ 100000000, PHASE 135.0" *)
    input wire capture_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rstn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME rstn, POLARITY ACTIVE_LOW" *)
    input wire rstn,
    input wire [11:0] adc_data_0, adc_data_1, adc_data_2,
    input wire [11:0] adc_data_3, adc_data_4, adc_data_5,
    input wire [5:0] adc_dco,
    (* X_INTERFACE_IGNORE = "true" *) output wire [5:0] adc_clk,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis TDATA" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME m_axis, TDATA_NUM_BYTES 12, HAS_TREADY 0, HAS_TLAST 0" *)
    output wire [95:0] m_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis TVALID" *)
    output reg m_axis_tvalid
);
    // Forward identical clocks through dedicated output DDR registers.
    // CK is FPGA->ADC. DC/DCO is ADC->FPGA; it must never be driven by FPGA.
    genvar i;
    generate for(i=0;i<6;i=i+1) begin: forward_clock
        ODDR #(.DDR_CLK_EDGE("SAME_EDGE"),.INIT(1'b0),.SRTYPE("SYNC")) oddr_inst
            (.Q(adc_clk[i]),.C(clk),.CE(1'b1),.D1(1'b1),.D2(1'b0),.R(1'b0),.S(1'b0));
    end endgenerate
    (* IOB = "TRUE" *) reg [11:0] sample0,sample1,sample2,sample3,sample4,sample5;
    reg data_valid;
    always @(posedge capture_clk) begin
        sample0<=adc_data_0; sample1<=adc_data_1; sample2<=adc_data_2;
        sample3<=adc_data_3; sample4<=adc_data_4; sample5<=adc_data_5;
    end
    always @(posedge clk or negedge rstn) begin
        if(!rstn) begin data_valid<=1'b0; m_axis_tvalid<=1'b0; end
        else begin data_valid<=1'b1; m_axis_tvalid<=data_valid; end
    end
    // Return the common-phase input registers to the original AXIS clock.
    // Static timing checks this related-clock 6.25 ns crossing. One common
    // register stage preserves six-channel alignment and isolates FIFO routing.
    reg [95:0] axis_data;
    always @(posedge clk)
        axis_data<={4'b0,sample5,4'b0,sample4,4'b0,sample3,
                    4'b0,sample2,4'b0,sample1,4'b0,sample0};
    assign m_axis_tdata=axis_data;
    // Preserve DCO observation points for hardware inspection. The six CK clocks
    // share one MMCM; capture uses that common clock rather than six unsynchronised
    // clock domains. DCO monitor samples are diagnostic only, never data qualifiers.
    (* ASYNC_REG="TRUE", DONT_TOUCH="TRUE" *) reg [5:0] dco_meta,dco_sync;
    always @(posedge clk) begin dco_meta<=adc_dco; dco_sync<=dco_meta; end
endmodule

`timescale 1ns / 1ps
// Six waveform RAMs: AXI writes port A, continuous sample playback uses port B.
module axis_generator #(
    parameter integer DATA_WIDTH = 96,
    parameter integer ROM_ADDR_LEN = 12,
    parameter integer ROM_WIDTH = 16,
    parameter integer C_S_AXI_ID_WIDTH = 12,
    parameter integer C_S_AXI_ADDR_WIDTH = 32
)(
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME clk, ASSOCIATED_BUSIF m_axis_aout, ASSOCIATED_RESET rstn" *)
    input wire clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rstn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME rstn, POLARITY ACTIVE_LOW" *)
    input wire rstn,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis_aout TVALID" *)
    output wire m_axis_aout_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 m_axis_aout TDATA" *)
    output wire [DATA_WIDTH-1:0] m_axis_aout_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 S_AXI_ACLK CLK" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI_ACLK, ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET S_AXI_ARESETN" *)
    input wire S_AXI_ACLK,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 S_AXI_ARESETN RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI_ARESETN, POLARITY ACTIVE_LOW" *)
    input wire S_AXI_ARESETN,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWID" *)
    input wire [C_S_AXI_ID_WIDTH-1:0] S_AXI_AWID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4, NUM_READ_OUTSTANDING 1, NUM_WRITE_OUTSTANDING 1, SUPPORTS_NARROW_BURST 0" *)
    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_AWADDR,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWLEN" *)
    input wire [7:0] S_AXI_AWLEN,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWSIZE" *)
    input wire [2:0] S_AXI_AWSIZE,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWBURST" *)
    input wire [1:0] S_AXI_AWBURST,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWLOCK" *)
    input wire S_AXI_AWLOCK,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWCACHE" *)
    input wire [3:0] S_AXI_AWCACHE,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input wire [2:0] S_AXI_AWPROT,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWQOS" *)
    input wire [3:0] S_AXI_AWQOS,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input wire S_AXI_AWVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire S_AXI_AWREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input wire [31:0] S_AXI_WDATA,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input wire [3:0] S_AXI_WSTRB,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WLAST" *)
    input wire S_AXI_WLAST,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input wire S_AXI_WVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire S_AXI_WREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BID" *)
    output wire [C_S_AXI_ID_WIDTH-1:0] S_AXI_BID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0] S_AXI_BRESP,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire S_AXI_BVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input wire S_AXI_BREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARID" *)
    input wire [C_S_AXI_ID_WIDTH-1:0] S_AXI_ARID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input wire [C_S_AXI_ADDR_WIDTH-1:0] S_AXI_ARADDR,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARLEN" *)
    input wire [7:0] S_AXI_ARLEN,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARSIZE" *)
    input wire [2:0] S_AXI_ARSIZE,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARBURST" *)
    input wire [1:0] S_AXI_ARBURST,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARLOCK" *)
    input wire S_AXI_ARLOCK,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARCACHE" *)
    input wire [3:0] S_AXI_ARCACHE,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input wire [2:0] S_AXI_ARPROT,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARQOS" *)
    input wire [3:0] S_AXI_ARQOS,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input wire S_AXI_ARVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire S_AXI_ARREADY,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RID" *)
    output wire [C_S_AXI_ID_WIDTH-1:0] S_AXI_RID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] S_AXI_RDATA,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0] S_AXI_RRESP,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RLAST" *)
    output wire S_AXI_RLAST,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire S_AXI_RVALID,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input wire S_AXI_RREADY
);
    reg [ROM_ADDR_LEN-1:0] raddr;
    reg [1:0] sample_valid;
    wire rom_clk, rom_rw;
    wire [ROM_ADDR_LEN-1:0] rom_addr;
    wire [ROM_WIDTH-1:0] rom_wdata;
    wire [2:0] rom_channel;
    // blk_mem_gen_wave has a two-cycle registered port-B read.
    always @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            raddr <= 0;
            sample_valid <= 0;
        end else begin
            raddr <= raddr + 1'b1;
            sample_valid <= {sample_valid[0], 1'b1};
        end
    end
    assign m_axis_aout_tvalid = sample_valid[1];
    axis_generator_axi4_ctrl #(
        .ROM_ADDR_LEN(ROM_ADDR_LEN), .ROM_WIDTH(ROM_WIDTH),
        .ID_WIDTH(C_S_AXI_ID_WIDTH), .ADDR_WIDTH(C_S_AXI_ADDR_WIDTH)
    ) axi_ctrl (
        .clk(S_AXI_ACLK), .rstn(S_AXI_ARESETN),
        .awid(S_AXI_AWID), .awaddr(S_AXI_AWADDR), .awlen(S_AXI_AWLEN),
        .awsize(S_AXI_AWSIZE), .awburst(S_AXI_AWBURST), .awlock(S_AXI_AWLOCK),
        .awvalid(S_AXI_AWVALID), .awready(S_AXI_AWREADY),
        .wdata(S_AXI_WDATA), .wstrb(S_AXI_WSTRB), .wlast(S_AXI_WLAST),
        .wvalid(S_AXI_WVALID), .wready(S_AXI_WREADY),
        .bid(S_AXI_BID), .bresp(S_AXI_BRESP), .bvalid(S_AXI_BVALID), .bready(S_AXI_BREADY),
        .arid(S_AXI_ARID), .araddr(S_AXI_ARADDR), .arlen(S_AXI_ARLEN),
        .arsize(S_AXI_ARSIZE), .arburst(S_AXI_ARBURST), .arlock(S_AXI_ARLOCK),
        .arvalid(S_AXI_ARVALID), .arready(S_AXI_ARREADY),
        .rid(S_AXI_RID), .rdata(S_AXI_RDATA), .rresp(S_AXI_RRESP),
        .rlast(S_AXI_RLAST), .rvalid(S_AXI_RVALID), .rready(S_AXI_RREADY),
        .rom_clk(rom_clk), .rom_addr(rom_addr), .rom_wdata(rom_wdata),
        .rom_rw(rom_rw), .rom_channel(rom_channel)
    );
    genvar channel;
    generate for (channel = 0; channel < 6; channel = channel + 1) begin : wave_ram
        blk_mem_gen_wave ram (
            .clka(rom_clk), .wea(rom_rw && rom_channel == channel),
            .addra(rom_addr), .dina(rom_wdata),
            .clkb(clk), .addrb(raddr),
            .doutb(m_axis_aout_tdata[channel*ROM_WIDTH +: ROM_WIDTH])
        );
    end endgenerate
    initial begin
        if (DATA_WIDTH != 96 || ROM_ADDR_LEN != 12 || ROM_WIDTH != 16)
            $error("axis_generator requires 6 x 4096 x 16-bit blk_mem_gen_wave");
    end
endmodule

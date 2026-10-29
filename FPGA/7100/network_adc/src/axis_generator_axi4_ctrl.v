`timescale 1ns / 1ps
// AXI protocol guard with a local, self-contained waveform RAM writer.
// Each external write beat becomes an AW-then-W single-beat library transfer.
module axis_generator_axi4_ctrl #(
    parameter integer ROM_ADDR_LEN = 12,
    parameter integer ROM_WIDTH = 16,
    parameter integer ID_WIDTH = 12,
    parameter integer ADDR_WIDTH = 32
)(
    input wire clk, rstn,
    input wire [ID_WIDTH-1:0] awid,
    input wire [ADDR_WIDTH-1:0] awaddr,
    input wire [7:0] awlen,
    input wire [2:0] awsize,
    input wire [1:0] awburst,
    input wire awlock, awvalid,
    output wire awready,
    input wire [31:0] wdata,
    input wire [3:0] wstrb,
    input wire wlast, wvalid,
    output wire wready,
    output wire [ID_WIDTH-1:0] bid,
    output wire [1:0] bresp,
    output wire bvalid,
    input wire bready,
    input wire [ID_WIDTH-1:0] arid,
    input wire [ADDR_WIDTH-1:0] araddr,
    input wire [7:0] arlen,
    input wire [2:0] arsize,
    input wire [1:0] arburst,
    input wire arlock, arvalid,
    output wire arready,
    output wire [ID_WIDTH-1:0] rid,
    output reg [31:0] rdata,
    output reg [1:0] rresp,
    output wire rlast, rvalid,
    input wire rready,
    output wire rom_clk,
    output wire [ROM_ADDR_LEN-1:0] rom_addr,
    output wire [ROM_WIDTH-1:0] rom_wdata,
    output wire rom_rw,
    output reg [2:0] rom_channel
);
    localparam IDLE=0, WRITE_DATA=1, CORE_AW=2, CORE_W=3,
               CORE_B=4, WRITE_NEXT=5, WRITE_RESP=6, READ_PREP=7, READ_RESP=8;
    reg [3:0] state;
    reg [ID_WIDTH-1:0] transaction_id;
    reg [15:0] address;
    reg [7:0] remaining;
    reg increment, transaction_error, last_beat;
    reg mode;
    reg [ROM_ADDR_LEN-1:0] base_addr;
    reg [5:0] core_address;
    reg [31:0] core_data;
    wire core_awready, core_wready, core_bvalid;
    wire [1:0] core_bresp;
    function [31:0] merge_bytes;
        input [31:0] old_value, new_value;
        input [3:0] strobes;
        integer lane;
        begin
            merge_bytes = old_value;
            for (lane=0; lane<4; lane=lane+1)
                if (strobes[lane]) merge_bytes[lane*8 +: 8] = new_value[lane*8 +: 8];
        end
    endfunction
    wire [31:0] merged_mode = merge_bytes({31'b0,mode}, wdata, wstrb);
    wire [31:0] merged_base = merge_bytes({{(32-ROM_ADDR_LEN){1'b0}},base_addr}, wdata, wstrb);
    // The BD assigns a 64 KiB aperture, decoded using the bottom 16 bits.
    wire [12:0] write_end = {1'b0,awaddr[11:0]} + {3'b0,awlen,2'b0};
    wire [12:0] read_end = {1'b0,araddr[11:0]} + {3'b0,arlen,2'b0};
    assign awready = rstn && state == IDLE;
    assign arready = rstn && state == IDLE && !awvalid;
    assign wready = rstn && state == WRITE_DATA;
    assign bid = transaction_id;
    assign bresp = transaction_error ? 2'b10 : 2'b00;
    assign bvalid = rstn && state == WRITE_RESP;
    assign rid = transaction_id;
    assign rlast = remaining == 0;
    assign rvalid = rstn && state == READ_RESP;
    always @(posedge clk) begin
        if (!rstn) begin
            state <= IDLE;
            transaction_id <= 0;
            address <= 0;
            remaining <= 0;
            increment <= 0;
            transaction_error <= 0;
            last_beat <= 0;
            mode <= 0;
            base_addr <= 0;
            rom_channel <= 0;
            core_address <= 0;
            core_data <= 0;
            rdata <= 0;
            rresp <= 0;
        end else begin
            case (state)
                IDLE: begin
                    if (awvalid && awready) begin
                        transaction_id <= awid;
                        address <= awaddr[15:0];
                        remaining <= awlen;
                        increment <= awburst == 2'b01;
                        transaction_error <= awsize != 2 || awaddr[1:0] != 0 ||
                            awburst[1] || awlock || (awburst == 1 && write_end[12]);
                        state <= WRITE_DATA;
                    end else if (arvalid && arready) begin
                        transaction_id <= arid;
                        address <= araddr[15:0];
                        remaining <= arlen;
                        increment <= arburst == 2'b01;
                        transaction_error <= arsize != 2 || araddr[1:0] != 0 ||
                            arburst[1] || arlock || (arburst == 1 && read_end[12]);
                        state <= READ_PREP;
                    end
                end
                WRITE_DATA: if (wvalid && wready) begin
                    last_beat <= remaining == 0 || wlast;
                    state <= WRITE_NEXT;
                    if (wlast != (remaining == 0)) transaction_error <= 1;
                    if (!transaction_error && wlast == (remaining == 0)) begin
                        case (address)
                            16'h0000: if (wstrb[0]) begin
                                mode <= merged_mode[0];
                                core_address <= 0;
                                core_data <= {31'b0,merged_mode[0]};
                                state <= CORE_AW;
                            end
                            16'h0004: if (|wstrb[1:0]) begin
                                base_addr <= merged_base[ROM_ADDR_LEN-1:0];
                                core_address <= 4;
                                core_data <= merged_base;
                                state <= CORE_AW;
                            end
                            16'h0008: if (|wstrb[1:0]) begin
                                // Port A has no byte enables: require one full 16-bit sample.
                                if (wstrb[1:0] != 2'b11 || !mode) transaction_error <= 1;
                                else begin
                                    core_address <= 8;
                                    core_data <= wdata;
                                    state <= CORE_AW;
                                end
                            end
                            16'h000c: if (wstrb[0]) begin
                                if (wdata[7:0] < 6) rom_channel <= wdata[2:0];
                                else transaction_error <= 1;
                            end
                            default: transaction_error <= 1;
                        endcase
                    end
                end
                CORE_AW: if (core_awready) state <= CORE_W;
                CORE_W: if (core_wready) state <= CORE_B;
                CORE_B: if (core_bvalid) begin
                    if (core_bresp != 0) transaction_error <= 1;
                    state <= WRITE_NEXT;
                end
                WRITE_NEXT: begin
                    if (last_beat) state <= WRITE_RESP;
                    else begin
                        remaining <= remaining - 1'b1;
                        if (increment) address <= address + 16'd4;
                        state <= WRITE_DATA;
                    end
                end
                WRITE_RESP: if (bready) state <= IDLE;
                READ_PREP: begin
                    rdata <= 0;
                    rresp <= transaction_error ? 2'b10 : 2'b00;
                    if (!transaction_error) begin
                        case (address)
                            16'h0000: rdata <= {31'b0,mode};
                            16'h0004: rdata <= {{(32-ROM_ADDR_LEN){1'b0}},base_addr};
                            16'h000c: rdata <= {29'b0,rom_channel};
                            // Simple-dual-port RAM has no AXI-side read port.
                            default: rresp <= 2'b10;
                        endcase
                    end
                    state <= READ_RESP;
                end
                READ_RESP: if (rready) begin
                    if (remaining == 0) state <= IDLE;
                    else begin
                        remaining <= remaining - 1'b1;
                        if (increment) address <= address + 16'd4;
                        state <= READ_PREP;
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
    // Local replacement for the missing teammate ROM library. The outer guard
    // serializes every beat (AW, W, B), so this writer needs no additional AXI bus.
    // MODE=0 stops writes, BASE sets the first word, DATA writes and increments.
    reg write_mode;
    reg [ROM_ADDR_LEN-1:0] write_pointer, ram_address;
    reg [ROM_WIDTH-1:0] ram_data;
    reg ram_write, response_valid;
    assign rom_clk=clk;
    assign rom_addr=ram_address;
    assign rom_wdata=ram_data;
    assign rom_rw=ram_write;
    assign core_awready=1'b1;
    assign core_wready=1'b1;
    assign core_bvalid=response_valid;
    assign core_bresp=2'b00;
    always @(posedge clk) begin
        if(!rstn) begin
            write_mode<=1'b0; write_pointer<=0; ram_address<=0;
            ram_data<=0; ram_write<=1'b0; response_valid<=1'b0;
        end else begin
            ram_write<=1'b0;
            if(state==CORE_B && core_bvalid) response_valid<=1'b0;
            if(state==CORE_W && core_wready) begin
                response_valid<=1'b1;
                case(core_address)
                    6'h00: write_mode<=core_data[0];
                    6'h04: write_pointer<=core_data[ROM_ADDR_LEN-1:0];
                    6'h08: if(write_mode) begin
                        ram_address<=write_pointer;
                        ram_data<=core_data[ROM_WIDTH-1:0];
                        ram_write<=1'b1;
                        write_pointer<=write_pointer+1'b1;
                    end
                endcase
            end
        end
    end
endmodule

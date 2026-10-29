`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/21 17:02:19
// Design Name: 
// Module Name: data_split
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


module data_split(

    input  wire                      aclk,
    input  wire                      aresetn,

    input  wire [95:0]               s_axis_tdata,
    input  wire                      s_axis_tvalid,
    output wire                      s_axis_tready,

    output wire [15:0]               m00_axis_tdata,
    output wire                      m00_axis_tvalid,
    input  wire                      m00_axis_tready,

    output wire [15:0]               m01_axis_tdata,
    output wire                      m01_axis_tvalid,
    input  wire                      m01_axis_tready,

    output wire [15:0]               m02_axis_tdata,
    output wire                      m02_axis_tvalid,
    input  wire                      m02_axis_tready,

    output wire [15:0]               m03_axis_tdata,
    output wire                      m03_axis_tvalid,
    input  wire                      m03_axis_tready,

    output wire [15:0]               m04_axis_tdata,
    output wire                      m04_axis_tvalid,
    input  wire                      m04_axis_tready,

    output wire [15:0]               m05_axis_tdata,
    output wire                      m05_axis_tvalid,
    input  wire                      m05_axis_tready
);

reg  [95:0]                     data_buffer;
reg  [5:0]                      data_pending;

wire [5:0]                      output_ready;
wire                            input_transfer;

// 下游可接收
assign output_ready     = {m05_axis_tready, m04_axis_tready, m03_axis_tready, m02_axis_tready, m01_axis_tready, m00_axis_tready};

// 输出寄存器为空或者下游可接收
assign s_axis_tready    = aresetn && (&((~data_pending) | output_ready));

// 握手成功
assign input_transfer   = s_axis_tvalid && s_axis_tready;

// 96bit 数据按低位先拆分
assign m00_axis_tdata = data_buffer[15:0];
assign m01_axis_tdata = data_buffer[31:16];
assign m02_axis_tdata = data_buffer[47:32];
assign m03_axis_tdata = data_buffer[63:48];
assign m04_axis_tdata = data_buffer[79:64];
assign m05_axis_tdata = data_buffer[95:80];

assign m00_axis_tvalid = data_pending[0];
assign m01_axis_tvalid = data_pending[1];
assign m02_axis_tvalid = data_pending[2];
assign m03_axis_tvalid = data_pending[3];
assign m04_axis_tvalid = data_pending[4];
assign m05_axis_tvalid = data_pending[5];

always @(posedge aclk or negedge aresetn) begin
    if(!aresetn) begin
        data_buffer <= 96'b0;
    end
    else if(input_transfer) begin
        data_buffer <= s_axis_tdata;
    end
end

    always @(posedge aclk or negedge aresetn) begin
        if(!aresetn) begin
            data_pending <= 6'b000000;
        end
        else if(input_transfer) begin
            data_pending <= 6'b111111;
        end
        else begin
            data_pending <= data_pending & (~output_ready);
        end
    end
endmodule

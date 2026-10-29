`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/22 17:58:10
// Design Name: 
// Module Name: dds_ctrl
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


module dds_ctrl(
    input  wire                      aclk,
    input  wire                      aresetn,

    input  wire [31:0]               data,

    output reg  [31:0]               m_axis_tdata,
    output reg                       m_axis_tvalid
);

reg         first_config;
reg [31:0]  data_reg;

always @(posedge aclk or negedge aresetn) begin
    if (!aresetn) begin
        data_reg    <= 32'd0;
    end
    else begin
        data_reg    <=  data;
    end
end

always @(posedge aclk or negedge aresetn) begin
    if(!aresetn) begin
        m_axis_tdata    <= 32'd0;
        m_axis_tvalid   <= 1'b0;
        first_config    <= 1'b1;
    end
    else if(first_config || (data_reg != m_axis_tdata)) begin
        m_axis_tdata    <= data_reg;
        m_axis_tvalid   <= 1'b1;
        first_config    <= 1'b0;
    end
    else begin
        m_axis_tvalid <= 1'b0;
    end
end

endmodule
`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/23 20:36:27
// Design Name: 
// Module Name: fft_ctrl
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


module fft_ctrl(
    input  wire         aclk,
    input  wire         aresetn,

    output reg  [7:0]   m_axis_config_tdata,
    output reg          m_axis_config_tvalid,
    input  wire         m_axis_config_tready,

    output reg          config_done
);

    reg [6:0] wait_count;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            wait_count              <= 7'd0;
            config_done             <= 1'b0;
            m_axis_config_tdata     <= 8'd0;
            m_axis_config_tvalid    <= 1'b0;
        end
        else if (!config_done) begin

            if (wait_count < 7'd99) begin
                wait_count <= wait_count + 7'd1;
            end
            else if (!m_axis_config_tvalid) begin

                m_axis_config_tdata  <= 8'h01;
                m_axis_config_tvalid <= 1'b1;
            end
            else if (m_axis_config_tvalid && m_axis_config_tready) begin

                m_axis_config_tvalid    <= 1'b0;
                config_done             <= 1'b1;
            end
        end
    end
endmodule

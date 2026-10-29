`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 2026/09/22 16:48:41
// Design Name: 
// Module Name: error_concatenation
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


module error_concatenation(
    input   wire    error_1,
    input   wire    error_2,
    input   wire    error_3,
    input   wire    error_4,
    input   wire    error_5,
    input   wire    error_6,

    output  wire    error_concat
);

assign  error_concat = error_1 || error_2 || error_3 || error_4 || error_5 || error_6;

endmodule

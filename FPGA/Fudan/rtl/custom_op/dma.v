
//==============================================================================
// Orgnization   : Shanghai Fudan Microelectronics Co., Ltd. Confidential
// File Name     : dma.v
// Author        :
// Project       : 
// Create Date   : 2021.04.01
// Description   :
// - Direct Memory Access
//------------------------------------------------------------------------------
// Modification History :
// Rev     Date         Who          Description
// 
//==============================================================================

module dma #(
    parameter integer DOUT_WIDTH = 192
)(

    input         [ 31: 0]     arbase        ,
    input         [ 31: 0]     arsize        ,
                                            
    input         [ 31: 0]     awbase        ,
    input         [ 31: 0]     awsize        ,

    output  reg   [ 31: 0]     araddr        ,
    output  reg                arvalid       ,
    input                      arready       ,

    input         [ DOUT_WIDTH-1: 0]     odata     , 
    input                      data_valid    , 
    output                     data_ready    , 

    output  reg   [ 31: 0]     awaddr        ,
    output        [ DOUT_WIDTH-1: 0]     awdata        ,
    output                     awvalid       ,
    input                      awready       ,

    input                      idata_start    ,

    input                      clk           ,
    input                      rst_n

);

   reg   [31 : 0]  dma_cnt0 ;
   reg   [31 : 0]  dma_cnt1 ;
   wire            aawready ;
   reg             aawvalid ;

//    assign data_ready = awready;
   assign data_ready = aawvalid && awready;

   assign aawready   = awready && data_valid;

   assign awdata     = odata;
   assign awvalid    = aawvalid && data_valid;

   always@(posedge clk or negedge rst_n)begin
        if(rst_n==1'b0)
            aawvalid <= #0.1 1'b0;
        else if(idata_start)
            // aawvalid <= #0.1 1'b1;
            aawvalid <= #0.1 (awsize != 0);
        // else if(aawready && dma_cnt1==0)
        else if(aawready && dma_cnt1==1)
            aawvalid <= #0.1 1'b0;
   end

   always@(posedge clk or negedge rst_n)begin
        if(rst_n==1'b0)
            dma_cnt1 <= #0.1 0;
        else if(idata_start)
            dma_cnt1 <= #0.1 awsize;
        else if(aawvalid & aawready)
            dma_cnt1 <= #0.1 dma_cnt1 - 1'b1;
   end

   always@(posedge clk or negedge rst_n)begin
        if(rst_n==1'b0)
            awaddr <= #0.1 0;
        else if(idata_start)
            awaddr <= #0.1 awbase;
        else if(aawvalid & aawready)
            awaddr <= #0.1 awaddr + 64;
   end
   
   always@(posedge clk or negedge rst_n)begin
        if(rst_n==1'b0)
            dma_cnt0 <= #0.1 0;
        else if(idata_start)
            dma_cnt0 <= #0.1 arsize;
        else if(arvalid & arready)
            dma_cnt0 <= #0.1 dma_cnt0-1'b1;
   end

   always@(posedge clk or negedge rst_n)begin
        if(rst_n==1'b0)
            araddr <= #0.1 0;
        else if(idata_start)
            araddr <= #0.1 arbase;
        else if(arvalid & arready)
            araddr <= #0.1 araddr + 64;
   end

   always@(posedge clk or negedge rst_n)begin
        if(rst_n==1'b0)
            arvalid <= #0.1 1'b0;
        else if(idata_start)
            // arvalid <= #0.1 1'b1;
            arvalid <= #0.1 (arsize != 0);
        // else if(arready && dma_cnt0==0)
        else if(arready && dma_cnt0==1)
            arvalid <= #0.1 1'b0;
   end
   
endmodule


  

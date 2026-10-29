
//==============================================================================
// Orgnization   : Shanghai Fudan Microelectronics Co., Ltd. Confidential
// File Name     : adder_top.v
// Author        :
// Project       : 
// Create Date   : 2021.03.31
// Description   :
// 
//------------------------------------------------------------------------------
// Modification History :
// Rev     Date         Who          Description
// 
//==============================================================================

module op_top#(
      parameter integer DIN_WIDTH = 96,
      parameter integer DOUT_WIDTH = 192
)(

  //------------ Register Access interface ------------   
    input        [      31 : 0]   itf_ra_awaddr     , 
    input        [      31 : 0]   itf_ra_awdata     ,
    input                         itf_ra_awvalid    ,
    output                        itf_ra_awready    ,

    input        [      31 : 0]   itf_ra_araddr     ,  
    input                         itf_ra_arvalid    ,
    output                        itf_ra_arready    ,

    output       [      31 : 0]   itf_ra_rdata      ,
    output                        itf_ra_rvalid     ,
    input                         itf_ra_rready     ,
 
  //------------ DMA interface -------------------------
    output       [      31 : 0]   itf_awaddr        ,
    output       [      DOUT_WIDTH-1 : 0]   itf_awdata        ,
    output                        itf_awvalid       ,
    input                         itf_awready       ,

    output       [      31 : 0]   itf_araddr        ,
    output                        itf_arvalid       ,
    input                         itf_arready       ,

    input        [      DIN_WIDTH-1 : 0]   itf_rdata         ,
    input                         itf_rvalid        ,
    output                        itf_rready        ,

    output  reg                   reset_reg         ,

    input                         clk               ,
    input                         ra_clk            ,
    input                         rst_n             ,
    input                         ra_rst_n

);
    localparam RESET_ADDR = 16'h77;

    localparam integer DDS_CTRL_WIDTH = 32;
    localparam integer PHA_CTRL_WIDTH = 64;

    localparam integer DIN_CNT_WIDTH = 14;
    localparam integer DOUT_CNT_WIDTH = 15;

    wire              idata_start ;
    wire              odata_ready  ;
    wire   [ 31: 0]   arbase     ;
    wire   [ 31: 0]   arsize     ;
    wire   [ 31: 0]   awbase     ;
    wire   [ 31: 0]   awsize     ;
    wire              dds_conf_done;
    wire   [DDS_CTRL_WIDTH-1:0]   dds_ctrl;
    wire              pha_conf_done;
    wire   [PHA_CTRL_WIDTH/2-1:0] pha_coe0;
    wire   [PHA_CTRL_WIDTH/2-1:0] pha_coe1;
    wire   [ DOUT_WIDTH-1: 0]   odata  ; 
    wire              data_valid ; 
    wire              data_ready ; 

    wire              rst_n_1    ;
    wire              ra_rst_n_1 ;
    wire              odata_ready_cross;
    wire              idata_start_cross;
 
  reg_ctrl   U_reg_ctrl(
   
          .ra_awaddr     ( itf_ra_awaddr[17:2]    ),  
          .ra_awdata     ( itf_ra_awdata    ),
          .ra_awvalid    ( itf_ra_awvalid   ),
          .ra_awready    ( itf_ra_awready   ),
   
          .ra_araddr     ( itf_ra_araddr[17:2]    ),  
          .ra_arvalid    ( itf_ra_arvalid   ),
          .ra_arready    ( itf_ra_arready   ),
   
          .ra_rdata      ( itf_ra_rdata     ),
          .ra_rvalid     ( itf_ra_rvalid    ),
          .ra_rready     ( itf_ra_rready    ),
   
          .idata_start   ( idata_start      ),
          .rbase         ( arbase           ),
          .rsize         ( arsize           ),
          .wbase         ( awbase           ),
          .wsize         ( awsize           ),                                            
          .dds_conf_done     ( dds_conf_done     ),                                              
          .dds_ctrl     ( dds_ctrl                 ),                                              
          .pha_conf_done     ( pha_conf_done                 ),                                              
          .pha_coe0     ( pha_coe0                 ),                                              
          .pha_coe1     ( pha_coe1                 ),                                              
          .rfu_wreg8     (                  ),                                              
          .rfu_wreg9     (                  ),
          
          .odata_ready   ( odata_ready_cross),
          .rfu_rreg0     ( 32'd0            ),       //read reg;                                            
          .rfu_rreg1     ( 32'd0            ),                                              
          .rfu_rreg2     ( 32'd0            ),                                              
          .rfu_rreg3     ( 32'd0            ),                                              
          .rfu_rreg4     ( 32'd0            ),                                              
          .rfu_rreg5     ( 32'd0            ),                                              
          .rfu_rreg6     ( 32'd0            ),                                              
          .rfu_rreg7     ( 32'd0            ),                                              
          .rfu_rreg8     ( 32'd0            ),                                              
          .rfu_rreg9     ( 32'd0            ),                                              
   
          .clk           ( ra_clk           ),
          .rst_n         ( ra_rst_n_1       )
   
   );

   dma #(
      .DOUT_WIDTH(DOUT_WIDTH)
   )U_dma(

       .arbase           ( arbase           ),
       .arsize           ( arsize           ),
     
       .awbase           ( awbase           ),
       .awsize           ( awsize           ),
                         
       .araddr           ( itf_araddr       ),
       .arvalid          ( itf_arvalid      ),
       .arready          ( itf_arready      ),

       .odata            ( odata            ),
       .data_valid       ( data_valid       ),
       .data_ready       ( data_ready       ),
                         
       .awaddr           ( itf_awaddr       ),
       .awdata           ( itf_awdata       ),
       .awvalid          ( itf_awvalid      ),
       .awready          ( itf_awready      ),

       .idata_start      ( idata_start_cross),

       .clk              ( clk              ),
       .rst_n            ( rst_n_1          )
  );
   
data_io #(
      .DDS_CTRL_WIDTH(DDS_CTRL_WIDTH),
      .PHA_CTRL_WIDTH(PHA_CTRL_WIDTH),
      .DIN_WIDTH(DIN_WIDTH),
      .DIN_CNT_WIDTH(DIN_CNT_WIDTH),
      .DOUT_CNT_WIDTH(DOUT_CNT_WIDTH),
      .OUT_SPLICE_WIDTH(DOUT_WIDTH)
   )data_io_inst (
    .clk(clk),
    .ctrl_clk(ra_clk),
    .rstn(rst_n_1),

    .dds_conf_done(dds_conf_done),
    .dds_ctrl(dds_ctrl),

    .pha_conf_done(pha_conf_done),
    .pha_coe0(pha_coe0),
    .pha_coe1(pha_coe1),

    .din_size(arsize[DIN_CNT_WIDTH:0]),
    .din_valid(itf_rvalid),
    .din_ready(itf_rready),
    .din_data(itf_rdata),

    .dout_size(awsize[DOUT_CNT_WIDTH-1:0]),
    .dout_valid(data_valid),
    .dout_ready(data_ready),
    .dout_data(odata),

    .odata_ready(odata_ready)
);

  pulse_cross U0_pulse_cross(
        .a2      (  odata_ready_cross),
        .clk2    (  ra_clk           ),
        .rst2    (  ~ra_rst_n_1      ),
       
        .rdy1    (                   ),
        .a1      (  odata_ready      ),
        .clk1    (  clk              ),
        .rst1    (  ~rst_n_1         )
  );
  pulse_cross U1_pulse_cross(
        .a2      (  idata_start_cross),
        .clk2    (  clk              ),
        .rst2    (  ~rst_n_1         ),
     
        .rdy1    (                   ),
        .a1      (  idata_start      ),
        .clk1    (  ra_clk           ),
        .rst1    (  ~ra_rst_n_1      )
  );

  always@(posedge clk or negedge ra_rst_n) begin
     if(ra_rst_n==1'b0)
         reset_reg <= #0.1 0;
    else if(itf_ra_awvalid && itf_ra_awready && itf_ra_awaddr[17:2]==RESET_ADDR)
         reset_reg <= #0.1 itf_ra_awdata[0];
 end

 assign  rst_n_1    = (~reset_reg) && rst_n; 
 assign  ra_rst_n_1 = (~reset_reg) && ra_rst_n; 


endmodule

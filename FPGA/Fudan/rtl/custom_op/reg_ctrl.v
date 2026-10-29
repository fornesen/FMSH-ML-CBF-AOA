
//==============================================================================
// Orgnization   : Shanghai Fudan Microelectronics Co., Ltd. Confidential
// File Name     : reg_ctrl.v
// Author        :
// Project       : 
// Create Date   : 2021.03.31
// Description   :
// - Register Ctrl
//------------------------------------------------------------------------------
// Modification History :
// Rev     Date         Who          Description
// 
//==============================================================================

module reg_ctrl(    

    input         [   15 : 0]   ra_awaddr        ,  
    input         [   31 : 0]   ra_awdata        ,
    input                       ra_awvalid       ,
    output                      ra_awready       ,

    input         [   15 : 0]   ra_araddr        ,  
    input                       ra_arvalid       ,
    output                      ra_arready       ,

    output  reg   [   31 : 0]   ra_rdata         ,
    output  reg                 ra_rvalid        ,
    input                       ra_rready        ,

    output reg                  idata_start      ,
    output reg    [   31 : 0]   rbase            ,
    output reg    [   31 : 0]   rsize            ,
    output reg    [   31 : 0]   wbase            ,
    output reg    [   31 : 0]   wsize            ,
    output reg    [   0 : 0]   dds_conf_done        ,
    output reg    [   31 : 0]   dds_ctrl        ,                                              
    output reg    [   0 : 0]   pha_conf_done        ,                                              
    output reg    [   31 : 0]   pha_coe0        ,                                              
    output reg    [   31 : 0]   pha_coe1        ,                                              
    output reg    [   31 : 0]   rfu_wreg8        ,                                              
    output reg    [   31 : 0]   rfu_wreg9        ,

    input                       odata_ready        ,
    input         [   31 : 0]   rfu_rreg0        ,                                              
    input         [   31 : 0]   rfu_rreg1        ,                                              
    input         [   31 : 0]   rfu_rreg2        ,                                              
    input         [   31 : 0]   rfu_rreg3        ,                                              
    input         [   31 : 0]   rfu_rreg4        ,                                              
    input         [   31 : 0]   rfu_rreg5        ,                                              
    input         [   31 : 0]   rfu_rreg6        ,                                              
    input         [   31 : 0]   rfu_rreg7        ,                                              
    input         [   31 : 0]   rfu_rreg8        ,                                              
    input         [   31 : 0]   rfu_rreg9        ,                                              

    input                              clk       ,
    input                            rst_n 

);

    localparam COMP_START_ADDR   = 16'h00;
    localparam BASE_RADDR        = 16'h01;
    localparam BASE_RSIZE        = 16'h02;
    localparam BASE_WADDR        = 16'h03;
    localparam BASE_WSIZE        = 16'h04;
    localparam DDS_CONF_VALID        = 16'h06;
    localparam DDS_CONF_DATA        = 16'h07;
    localparam PHA_CONF_VALID        = 16'h08;
    localparam PHA_CONF_DATA0        = 16'h09;
    localparam PHA_CONF_DATA1        = 16'h0a;
    localparam RFU_WADDR8        = 16'h0b;
    localparam RFU_WADDR9        = 16'h0c;
    localparam DEBUG_REG_ADDR    = 16'hff;
    // localparam RESET_ADDR        = 16'h77;

    localparam ODATA_READY_ADDR  = 16'h20;
    localparam RFU_RADDR0        = 16'h21;
    localparam RFU_RADDR1        = 16'h22;
    localparam RFU_RADDR2        = 16'h23;
    localparam RFU_RADDR3        = 16'h24;
    localparam RFU_RADDR4        = 16'h25;
    localparam RFU_RADDR5        = 16'h26;
    localparam RFU_RADDR6        = 16'h27;
    localparam RFU_RADDR7        = 16'h28;
    localparam RFU_RADDR8        = 16'h29;
    localparam RFU_RADDR9        = 16'h2a;

    // [波束控制新增] 此版本加入配置寄存器回读；同时重新生成 adc_dsp BD 输出。
    // V1.1: FFT 输入已屏蔽前 64 点并加 4032 点 Hann 窗；PHA 数据契约不变。
    localparam CTRL_VERSION      = 32'h00010001;

    wire              ra_awevent      ;
    reg   [31 : 0]    debug_reg       ;
    reg               odata_ready_reg   ;

    assign ra_awready = 1'b1;
    assign ra_arready = ra_rvalid==1'b0;

    assign ra_awevent = ra_awvalid && ra_awready;

    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            ra_rvalid <= #0.1 1'b0;
        else if(ra_arvalid && ra_arready)
            ra_rvalid <= #0.1 1'b1;
        else if(ra_rready)
            ra_rvalid <= #0.1 1'b0;
    end

    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            idata_start <= #0.1 0;
        else if(idata_start==1'b1)
            idata_start <= #0.1 0;
        else if(ra_awevent && ra_awaddr==COMP_START_ADDR)
            idata_start <= #0.1 ra_awdata[0];
    end

    always@(posedge clk or negedge rst_n) begin
       if(rst_n==1'b0)begin
            rbase <= #0.1 0;
       end
       else if(ra_awevent && ra_awaddr==BASE_RADDR)begin
            rbase <= #0.1 ra_awdata;
       end
    end

    always@(posedge clk or negedge rst_n) begin
       if(rst_n==1'b0)begin
            rsize <= #0.1 0;
       end
       else if(ra_awevent && ra_awaddr==BASE_RSIZE)begin
            rsize <= #0.1 ra_awdata;
       end
    end

     always@(posedge clk or negedge rst_n) begin
       if(rst_n==1'b0)begin
            wbase <= #0.1 0;
       end
       else if(ra_awevent && ra_awaddr==BASE_WADDR)begin
            wbase <= #0.1  ra_awdata;
       end
    end

     always@(posedge clk or negedge rst_n) begin
       if(rst_n==1'b0)begin
            wsize <= #0.1 0;
       end
       else if(ra_awevent && ra_awaddr==BASE_WSIZE)begin
            wsize <= #0.1  ra_awdata;
       end
    end

    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            dds_conf_done <= #0.1 0;
        else if(ra_awevent && ra_awaddr==DDS_CONF_VALID)
            dds_conf_done <= #0.1 ra_awdata[0];
    end
  
    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            dds_ctrl <= #0.1 0;
        else if(ra_awevent && ra_awaddr==DDS_CONF_DATA)
            dds_ctrl <= #0.1 ra_awdata;
    end
    
    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            pha_conf_done <= #0.1 0;
        else if(ra_awevent && ra_awaddr==PHA_CONF_VALID)
            pha_conf_done <= #0.1 ra_awdata[0];
    end

    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            pha_coe0 <= #0.1 0;
        else if(ra_awevent && ra_awaddr==PHA_CONF_DATA0)
            pha_coe0 <= #0.1 ra_awdata;
    end
    
    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            pha_coe1 <= #0.1 0;
        else if(ra_awevent && ra_awaddr==PHA_CONF_DATA1)
            pha_coe1 <= #0.1 ra_awdata;
    end
    
    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            rfu_wreg8 <= #0.1 0;
        else if(ra_awevent && ra_awaddr==RFU_WADDR8)
            rfu_wreg8 <= #0.1 ra_awdata;
    end
    
    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            rfu_wreg9 <= #0.1 0;
        else if(ra_awevent && ra_awaddr==RFU_WADDR9)
            rfu_wreg9 <= #0.1 ra_awdata;
    end

    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            debug_reg <= #0.1 0;
        else if(ra_awevent && ra_awaddr==DEBUG_REG_ADDR)
            debug_reg <= #0.1 ra_awdata;
    end

    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            odata_ready_reg <= #0.1 0;
        else if(idata_start==1'b1)
            odata_ready_reg <= #0.1 0;
        else if(odata_ready)
            odata_ready_reg <= #0.1 1;
    end

    always@(posedge clk or negedge rst_n) begin
        if(rst_n==1'b0)
            ra_rdata <= #0.1 0;
        else if(ra_arvalid && ra_arready) begin
            case(ra_araddr)

                // [波束控制新增] AXI 配置影子回读，不是各旋转器内部权值读回。
                PHA_CONF_VALID    : ra_rdata <= #0.1 {31'd0,pha_conf_done};
                PHA_CONF_DATA0    : ra_rdata <= #0.1 pha_coe0;
                PHA_CONF_DATA1    : ra_rdata <= #0.1 pha_coe1;

                DEBUG_REG_ADDR    : ra_rdata <= #0.1 debug_reg;
                ODATA_READY_ADDR  : ra_rdata <= #0.1 {31'd0,odata_ready_reg};
                RFU_RADDR0        : ra_rdata <= #0.1 rfu_rreg0;
                RFU_RADDR1        : ra_rdata <= #0.1 rfu_rreg1;
                RFU_RADDR2        : ra_rdata <= #0.1 rfu_rreg2;               
                RFU_RADDR3        : ra_rdata <= #0.1 rfu_rreg3;
                RFU_RADDR4        : ra_rdata <= #0.1 rfu_rreg4;
                RFU_RADDR5        : ra_rdata <= #0.1 rfu_rreg5;
                RFU_RADDR6        : ra_rdata <= #0.1 rfu_rreg6;
                RFU_RADDR7        : ra_rdata <= #0.1 rfu_rreg7;
                RFU_RADDR8        : ra_rdata <= #0.1 rfu_rreg8;
                RFU_RADDR9        : ra_rdata <= #0.1 rfu_rreg9;
                16'h30            : ra_rdata <= #0.1 CTRL_VERSION;
              
                default : ra_rdata <= #0.1 32'hFFFFFFFF;
            endcase
        end
    end

endmodule


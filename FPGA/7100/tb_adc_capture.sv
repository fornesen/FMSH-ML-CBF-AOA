`timescale 1ns/1ps
module tb_adc_capture;
  reg clk=0,rstn=0;
  reg capture_clk=0;
  always #5 clk=~clk;
  always @(clk) capture_clk<=#3.75 clk;
  reg [11:0] d[0:5];
  wire [5:0] ck;
  wire [95:0] out;
  wire valid;
  adc_capture_6ch dut(.clk(clk),.capture_clk(capture_clk),.rstn(rstn),.adc_data_0(d[0]),.adc_data_1(d[1]),
    .adc_data_2(d[2]),.adc_data_3(d[3]),.adc_data_4(d[4]),.adc_data_5(d[5]),
    .adc_dco(6'b0),.adc_clk(ck),.m_axis_tdata(out),.m_axis_tvalid(valid));
  integer i,j;
  reg [95:0] expected;
  always @(posedge capture_clk)
    expected={4'b0,d[5],4'b0,d[4],4'b0,d[3],4'b0,d[2],4'b0,d[1],4'b0,d[0]};
  initial begin
    for(i=0;i<6;i=i+1)d[i]=0;
    #120; @(negedge clk); rstn=1;
    for(j=0;j<100;j=j+1)begin
      for(i=0;i<6;i=i+1) d[i]=(j*71+i*733)&12'hfff;
      @(posedge clk); #1;
      for(i=0;i<6;i=i+1) begin
        if(out[i*16+:16] !== expected[i*16+:16]) $fatal(1,"CH%0d phase crossing/lane/zero-extension mismatch",i);
      end
      if(j>1 && !valid) $fatal(1,"missing valid");
      if(ck!==6'b111111) $fatal(1,"six forwarded clocks differ");
      @(negedge clk); #1;
      if(ck!==6'b0) $fatal(1,"forwarded clock low phase mismatch");
    end
    rstn=0; #1; if(valid!==0) $fatal(1,"reset valid mismatch");
    $display("PASS ADC: 6 lanes, raw12 zero extension, 100 clocks, clock alignment and reset");
    $finish;
  end
  initial begin #10000; $fatal(1,"timeout"); end
endmodule

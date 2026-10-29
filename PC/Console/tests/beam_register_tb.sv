`timescale 1ns/1ps
// [波束控制新增] 直接仿真交付的 reg_ctrl RTL，不代替实板响应验收。
module beam_register_tb;
reg clk=0,rst_n=0;
always #5 clk=~clk;
reg [15:0] awaddr=0,araddr=0;
reg [31:0] awdata=0;
reg awvalid=0,arvalid=0,rready=1;
wire awready,arready,rvalid,enable;
wire [31:0] rdata,lo,hi;
reg_ctrl dut(
 .clk(clk),.rst_n(rst_n),
 .ra_awaddr(awaddr),.ra_awdata(awdata),.ra_awvalid(awvalid),.ra_awready(awready),
 .ra_araddr(araddr),.ra_arvalid(arvalid),.ra_arready(arready),
 .ra_rdata(rdata),.ra_rvalid(rvalid),.ra_rready(rready),
 .pha_conf_done(enable),.pha_coe0(lo),.pha_coe1(hi),
 .odata_ready(1'b0),
 .rfu_rreg0(0),.rfu_rreg1(0),.rfu_rreg2(0),.rfu_rreg3(0),.rfu_rreg4(0),
 .rfu_rreg5(0),.rfu_rreg6(0),.rfu_rreg7(0),.rfu_rreg8(0),.rfu_rreg9(0));
task automatic write_word(input [15:0] addr,input [31:0] data);
 @(negedge clk); awaddr=addr;awdata=data;awvalid=1;
 @(negedge clk); awvalid=0;
endtask
task automatic check_word(input [15:0] addr,input [31:0] expected);
 @(negedge clk); araddr=addr;arvalid=1;
 @(negedge clk);
 if(!rvalid || rdata!==expected) $fatal(1,"Read word %h: got %h expected %h",addr,rdata,expected);
 arvalid=0;
 @(negedge clk);
endtask
integer ch,round;
reg [63:0] word_value;
// [波束控制新增] 连到工程实际移相器，验证配置最终影响哪一路及 I/Q 符号。
// 小幅度有符号输入避免饱和；参考结果直接使用整数复乘，不复制流水线实现。
reg [63:0] sample_data=0;
reg sample_valid=0;
wire [5:0] sample_ready,rotated_valid;
wire [101:0] rotated [0:5];
integer expected_sin[0:5],expected_cos[0:5];
genvar g;
generate for(g=0;g<6;g=g+1) begin: rotators
 complex_phase_rotator #(.CHANNEL_SELECT(g)) rotator(
  .aclk(clk),.aresetn(rst_n),.phase_shift_coefficient({hi,lo}),.config_en(enable),
  .s_axis_tdata(sample_data),.s_axis_tvalid(sample_valid),.s_axis_tready(sample_ready[g]),
  .m_axis_tdata(rotated[g]),.m_axis_tvalid(rotated_valid[g]),.m_axis_tready(1'b1));
end endgenerate
task automatic check_rotation(input integer i,input integer q);
 integer c,cycles;
 longint signed want_i,want_q;
 @(negedge clk);
 if(sample_ready!==6'b111111) $fatal(1,"Rotators not ready");
 sample_data={32'(q),32'(i)};sample_valid=1;
 @(negedge clk);sample_valid=0;cycles=0;
 while(rotated_valid==0 && cycles<30) begin @(negedge clk);cycles=cycles+1;end
 if(rotated_valid!==6'b111111) $fatal(1,"Missing or skewed rotation output");
 for(c=0;c<6;c=c+1) begin
  want_i=64'(i)*64'(expected_cos[c])-64'(q)*64'(expected_sin[c]);
  want_q=64'(i)*64'(expected_sin[c])+64'(q)*64'(expected_cos[c]);
  if($signed(rotated[c][101:51])!==want_i || $signed(rotated[c][50:0])!==want_q)
   $fatal(1,"CH%0d complex rotation mismatch I=%0d Q=%0d expected=%0d,%0d",
      c,$signed(rotated[c][101:51]),$signed(rotated[c][50:0]),want_i,want_q);
 end
 repeat(4) @(negedge clk);
endtask
initial begin
 repeat(3) @(negedge clk);rst_n=1;
 check_word('h30,'h20261020);
 for(round=0;round<4;round=round+1) begin
  rst_n=0;repeat(2) @(negedge clk);rst_n=1;
  check_word('h08,0);check_word('h09,0);check_word('h0a,0);
  for(ch=0;ch<6;ch=ch+1) begin expected_sin[ch]=0;expected_cos[ch]='h7fffff;end
  check_rotation(100003,-7003);
  for(ch=0;ch<6;ch=ch+1) begin
   case(ch)
    0: begin expected_sin[ch]=0;expected_cos[ch]='h7fffff;end
    1: begin expected_sin[ch]='h7fffff;expected_cos[ch]=0;end
    2: begin expected_sin[ch]=-'h800000;expected_cos[ch]=0;end
    3: begin expected_sin[ch]=0;expected_cos[ch]=-'h800000;end
    4: begin expected_sin[ch]=5931642;expected_cos[ch]=5931642;end
    5: begin expected_sin[ch]=-5931642;expected_cos[ch]=5931642;end
   endcase
   word_value=(64'(ch)<<61)|(64'(expected_sin[ch]&'hffffff)<<24)|64'(expected_cos[ch]&'hffffff);
   write_word('h08,0);
   write_word('h09,word_value[31:0]);write_word('h0a,word_value[63:32]);
   check_word('h09,word_value[31:0]);check_word('h0a,word_value[63:32]);
   write_word('h08,1);check_word('h08,1);
   if({hi,lo}!==word_value || enable!==1) $fatal(1,"Output mismatch");
   write_word('h08,0);check_word('h08,0);
   // 写一通道后立即检查全部通道，其他通道不得被覆盖。
   check_rotation(100003,-7003);
  end
  check_rotation(-110003,13007);
 end
 check_word('h7f,32'hffffffff);
 $display("BEAM_REGISTER_RTL_PASS: register + six actual complex rotators");
 $finish;
end
initial begin #100000;$fatal(1,"Timeout");end
endmodule

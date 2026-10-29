module clk_div2 (
    input   clk,
    input   rstn,

    output  clk_div
);

logic clk_div_reg;
always_ff@(posedge clk or negedge rstn) begin
    if(rstn == 1'b0) begin
        clk_div_reg <= 1'b0;
    end else begin
        clk_div_reg <= ~clk_div_reg;
    end
end
assign clk_div = clk_div_reg;

endmodule

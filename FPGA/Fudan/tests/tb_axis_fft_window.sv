`timescale 1ns / 1ps

module tb_axis_fft_window;
    localparam integer SAMPLES = 12 * 4096;
    reg aclk = 0;
    always #5 aclk = ~aclk;
    reg aresetn = 0;
    reg [63:0] input_data;
    reg input_valid = 0;
    reg input_last = 0;
    wire input_ready;
    wire [63:0] output_data;
    wire output_valid;
    wire output_last;
    reg output_ready = 0;
    reg [63:0] stimulus [0:SAMPLES-1];
    reg [63:0] expected [0:SAMPLES-1];
    integer sent = 0;
    integer received = 0;
    integer cycle = 0;
    integer warmup = 1;
    reg [31:0] random_state = 32'h13579bdf;
    reg stalled = 0;
    reg input_stalled = 0;
    reg [63:0] stalled_data;
    reg stalled_last;

    axis_fft_window dut (
        .aclk(aclk), .aresetn(aresetn),
        .s_axis_fft_tdata(input_data), .s_axis_fft_tvalid(input_valid),
        .s_axis_fft_tready(input_ready), .s_axis_fft_tlast(input_last),
        .m_axis_fft_tdata(output_data), .m_axis_fft_tvalid(output_valid),
        .m_axis_fft_tready(output_ready), .m_axis_fft_tlast(output_last)
    );

    initial begin
        $readmemh("window_input.mem", stimulus);
        $readmemh("window_expected.mem", expected);
        repeat (4) @(negedge aclk);
        aresetn = 1;
        wait (sent >= 97);
        // Reset with valid data still inside the pipeline; the next frame starts at zero.
        @(negedge aclk);
        aresetn = 0;
        repeat (4) @(negedge aclk);
        warmup = 0;
        aresetn = 1;
        wait (received == SAMPLES);
        repeat (10) @(posedge aclk);
        if (sent != SAMPLES || output_valid) $fatal(1, "Unexpected trailing data");
        $display("PASS: %0d samples, 12 frames, stalls, full rate, reset, signed extremes and ties", received);
        $finish;
    end

    always @(negedge aclk) begin
        if (!aresetn) begin
            input_valid = 0;
            input_last = 0;
            input_data = 0;
            output_ready = 0;
        end else begin
            random_state = random_state * 32'd1664525 + 32'd1013904223;
            // First six frames run at full rate, then exercise bubbles and long stalls.
            output_ready = sent < 6 * 4096 ||
                           (random_state[7:4] > 3 && (cycle % 211) >= 37);
            if (!input_stalled) begin
                input_valid = sent < SAMPLES && (sent < 6 * 4096 || random_state[3:0] > 2);
                input_data = sent < SAMPLES ? stimulus[sent] : 0;
                input_last = sent % 4096 == 4095;
            end
        end
    end

    always @(posedge aclk) begin
        if (!aresetn) begin
            sent = 0;
            received = 0;
            cycle = 0;
            stalled = 0;
            input_stalled = 0;
        end else begin
            cycle = cycle + 1;
            if (cycle > 200000) $fatal(1, "Stream timeout");
            if (stalled && (!output_valid || output_data !== stalled_data || output_last !== stalled_last))
                $fatal(1, "Output changed under backpressure");
            stalled = output_valid && !output_ready;
            stalled_data = output_data;
            stalled_last = output_last;
            input_stalled = input_valid && !input_ready;
            if (input_valid && input_ready) sent = sent + 1;
            if (output_valid && output_ready) begin
                if (received >= SAMPLES || output_data !== expected[received])
                    $fatal(1, "Data mismatch index=%0d actual=%h expected=%h", received, output_data, expected[received]);
                if (output_last !== (received % 4096 == 4095))
                    $fatal(1, "TLAST mismatch at %0d", received);
                received = received + 1;
            end
        end
    end
endmodule

#ifndef AXIS_GENERATOR_PS_H
#define AXIS_GENERATOR_PS_H

#include "xil_io.h"
#include "xil_types.h"
#include "xparameters.h"

/* Matches scripts/connect_axis_generator_axi4.tcl; use regenerated xparameters.h
 * if this base address is changed in Address Editor. All accesses are 32-bit. */
#ifndef XPAR_AXIS_GENERATOR_0_BASEADDR
#error "Regenerate network_bsp from hardware containing axis_generator_0/S_AXI"
#endif
#define AXIS_GENERATOR_BASE_ADDR XPAR_AXIS_GENERATOR_0_BASEADDR
#define AXIS_GENERATOR_CHANNEL_COUNT 6U
#define AXIS_GENERATOR_SAMPLE_COUNT  4096U
#define AXIS_GENERATOR_MODE      0x00U
#define AXIS_GENERATOR_BASE      0x04U
#define AXIS_GENERATOR_DATA      0x08U
#define AXIS_GENERATOR_CHANNEL   0x0CU

/* samples[] contains the exact unsigned 16-bit RAM codes (e.g. offset-binary).
 * The waveform address auto-increments after every DATA write.
 * Call serially, after stopping capture, then start a fresh DMA capture. */
static inline int axis_generator_load(u32 channel, u32 start,
                                      const u16 *samples, u32 count)
{
    u32 i;
    if (channel >= AXIS_GENERATOR_CHANNEL_COUNT ||
        start >= AXIS_GENERATOR_SAMPLE_COUNT ||
        count > AXIS_GENERATOR_SAMPLE_COUNT - start ||
        (samples == 0 && count != 0U))
        return -1;

    Xil_Out32(AXIS_GENERATOR_BASE_ADDR + AXIS_GENERATOR_CHANNEL, channel);
    Xil_Out32(AXIS_GENERATOR_BASE_ADDR + AXIS_GENERATOR_MODE, 1U);
    Xil_Out32(AXIS_GENERATOR_BASE_ADDR + AXIS_GENERATOR_BASE, start);
    for (i = 0; i < count; ++i)
        Xil_Out32(AXIS_GENERATOR_BASE_ADDR + AXIS_GENERATOR_DATA, (u32)samples[i]);
    /* Readback waits for the final write transaction before returning. */
    (void)Xil_In32(AXIS_GENERATOR_BASE_ADDR + AXIS_GENERATOR_CHANNEL);
    Xil_Out32(AXIS_GENERATOR_BASE_ADDR + AXIS_GENERATOR_MODE, 0U);
    (void)Xil_In32(AXIS_GENERATOR_BASE_ADDR + AXIS_GENERATOR_MODE);
    return 0;
}
#endif

#ifndef TCP_TRANSFER_H
#define TCP_TRANSFER_H

#include "xil_types.h"
#include "xparameters.h"

/*
 * PL capture format.
 *
 * One AXIS input beat contains six 16-bit samples (96 bits / 12 bytes).
 * The AXIS data-width converter emits three
 * 32-bit beats for AXI DMA, without changing the number of payload bytes.
 * AXIS_FRAME_WORDS in the BD must match PL_AXIS_FRAME_BEATS.
 * DDR/TCP channel order is least-significant 16-bit lane first (CH0..CH5).
 * Each channel waveform can be replaced through the debug UART upload command.
 */
#define PL_SAMPLE_WIDTH_BITS          16U
#define PL_SAMPLE_CHANNELS            6U
#define PL_AXIS_DATA_WIDTH_BITS       \
	(PL_SAMPLE_CHANNELS * PL_SAMPLE_WIDTH_BITS)
#define PL_AXIS_BYTES_PER_BEAT        (PL_AXIS_DATA_WIDTH_BITS / 8U)
#define PL_AXIS_FRAME_BEATS           (16U * 1024U)
#define PL_AXIS_FRAME_BYTES           \
	(PL_AXIS_FRAME_BEATS * PL_AXIS_BYTES_PER_BEAT)

/* Current hardware platform: AXI DMA S2MM data path is 32 bits wide. */
#define DMA_S2MM_DATA_WIDTH_BITS      XPAR_AXIDMA_0_M_AXI_S2MM_DATA_WIDTH
#define DMA_S2MM_BYTES_PER_BEAT       (DMA_S2MM_DATA_WIDTH_BITS / 8U)

/* Shared DDR staging area written by AXI DMA S2MM and sent over TCP. */
#define TCP_TX_BUFFER_SIZE            PL_AXIS_FRAME_BYTES

#if ((PL_AXIS_DATA_WIDTH_BITS % 8U) != 0U)
#error "PL AXIS width must be an integer number of bytes"
#endif

#if ((PL_AXIS_FRAME_BYTES % DMA_S2MM_BYTES_PER_BEAT) != 0U)
#error "PL frame size must be aligned to the AXI DMA S2MM data width"
#endif

#if (PL_AXIS_FRAME_BYTES > ((1U << XPAR_AXI_DMA_0_SG_LENGTH_WIDTH) - 1U))
#error "PL frame exceeds the AXI DMA transfer length limit"
#endif

#define TCP_TX_SUBMIT_OK          0
#define TCP_TX_SUBMIT_INVALID    -1
#define TCP_TX_SUBMIT_BUSY       -2
#define TCP_TX_SUBMIT_HW_ERROR   -3

/* Buffer information for configuring the AXI DMA destination. */
u8 *tcp_tx_buffer_get(void);
UINTPTR tcp_tx_buffer_address(void);
u32 tcp_tx_buffer_capacity(void);

/*
 * Reserve and invalidate the destination before starting AXI DMA S2MM.
 * Call tcp_tx_buffer_cancel_dma() if the DMA cannot be started or aborts.
 */
int tcp_tx_buffer_prepare_dma(u32 length);
void tcp_tx_buffer_cancel_dma(void);

/*
 * Publish completed DMA data to the TCP main-loop task.
 *
 * This function does not call lwIP and may be called by the DMA completion
 * ISR. transfer_data() performs cache invalidation and tcp_write() later in
 * the main loop. Normally this follows a successful prepare_dma() call.
 *
 * If the CPU, rather than DMA, fills the buffer, flush its cache range before
 * submitting it.
 */
int tcp_tx_buffer_submit(u32 length);

/* Non-zero means the shared source buffer must not be overwritten. */
int tcp_tx_buffer_busy(void);

/* Reserve an idle DMA/TCP path while replacing waveform RAM contents.
 * Calls occur in the main loop. A busy capture/transfer is left intact. */
int tcp_waveform_upload_begin(void);
void tcp_waveform_upload_end(void);

#endif /* TCP_TRANSFER_H */

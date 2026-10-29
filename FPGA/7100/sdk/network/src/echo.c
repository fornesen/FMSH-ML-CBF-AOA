/*
 * Copyright (C) 2009 - 2018 Xilinx, Inc.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without modification,
 * are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 * 3. The name of the author may not be used to endorse or promote products
 *    derived from this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR IMPLIED
 * WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
 * MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT
 * SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
 * OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
 * IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY
 * OF SUCH DAMAGE.
 *
 */

#include <stdio.h>
#include <string.h>

#include "lwip/err.h"
#include "lwip/sys.h"
#include "lwip/tcp.h"
#if defined (__arm__) || defined (__aarch64__)
#include "xil_printf.h"
#endif

#include "xil_cache.h"
#include "xil_io.h"
#include "xparameters.h"
#include "xstatus.h"
#include "xaxidma.h"
#include "tcp_transfer.h"

#define TCP_TX_POLL_INTERVAL 2U
#define DMA_RESET_TIMEOUT     1000000U

typedef struct {
	struct tcp_pcb *pcb;
	const u8_t *next;
	volatile u32_t remaining;
	u32_t outstanding;
	u8_t active;
} tcp_tx_state_t;

/* One complete 96-bit PL frame (192 KiB), in BSS and cache-line aligned. */
static u8_t tx_buffer[TCP_TX_BUFFER_SIZE] __attribute__((aligned(64)));

static tcp_tx_state_t tcp_tx_state;

/* DMA completion is published here by dma_capture_service(). */
static volatile u32_t tcp_tx_ready_length;
static volatile u8_t tcp_tx_data_ready;
static volatile u32_t tcp_tx_dma_length;
static volatile u8_t tcp_tx_dma_active;

/* AXI DMA is used in simple, polling-only S2MM mode. */
static XAxiDma axi_dma;
static u8_t axi_dma_ready;
static u8_t dma_capture_active;
static u32_t dma_capture_length;
static u8_t waveform_upload_active;

static void tcp_tx_try_send(tcp_tx_state_t *state);
static void tcp_tx_complete_if_acked(tcp_tx_state_t *state);
static int dma_capture_init(void);
static int dma_capture_start(u32_t length);
static void dma_capture_service(void);
static void dma_capture_abort(void);

u8 *tcp_tx_buffer_get(void)
{
	return tx_buffer;
}

UINTPTR tcp_tx_buffer_address(void)
{
	return (UINTPTR)tx_buffer;
}

u32 tcp_tx_buffer_capacity(void)
{
	return TCP_TX_BUFFER_SIZE;
}

int tcp_tx_buffer_busy(void)
{
	/*
	 * Once remaining is zero, TCP_WRITE_FLAG_COPY guarantees that lwIP no
	 * longer references this source buffer. A pending next block keeps it busy.
	 */
	return ((tcp_tx_data_ready != 0U) ||
		(tcp_tx_dma_active != 0U) ||
		(tcp_tx_state.remaining != 0U));
}

int tcp_waveform_upload_begin(void)
{
	if (waveform_upload_active || dma_capture_active ||
		tcp_tx_buffer_busy() || tcp_tx_state.active) {
		return TCP_TX_SUBMIT_BUSY;
	}
	waveform_upload_active = 1U;
	return TCP_TX_SUBMIT_OK;
}

void tcp_waveform_upload_end(void)
{
	waveform_upload_active = 0U;
}

int tcp_tx_buffer_prepare_dma(u32 length)
{
	SYS_ARCH_DECL_PROTECT(lev);
	int status = TCP_TX_SUBMIT_OK;

	if ((length == 0U) || (length > TCP_TX_BUFFER_SIZE)) {
		return TCP_TX_SUBMIT_INVALID;
	}

	SYS_ARCH_PROTECT(lev);
	if (tcp_tx_buffer_busy()) {
		status = TCP_TX_SUBMIT_BUSY;
	} else {
		tcp_tx_dma_length = length;
		tcp_tx_dma_active = 1U;
	}
	SYS_ARCH_UNPROTECT(lev);

	if (status != TCP_TX_SUBMIT_OK) {
		return status;
	}

	/* Prevent dirty or stale cache lines from conflicting with DMA writes. */
	Xil_DCacheInvalidateRange((INTPTR)tx_buffer, length);
	return TCP_TX_SUBMIT_OK;
}

void tcp_tx_buffer_cancel_dma(void)
{
	tcp_tx_dma_length = 0U;
	dmb();
	tcp_tx_dma_active = 0U;
}

static void tcp_tx_buffer_discard(void)
{
	SYS_ARCH_DECL_PROTECT(lev);

	SYS_ARCH_PROTECT(lev);
	tcp_tx_ready_length = 0U;
	tcp_tx_data_ready = 0U;
	tcp_tx_dma_length = 0U;
	tcp_tx_dma_active = 0U;
	SYS_ARCH_UNPROTECT(lev);
}

int tcp_tx_buffer_submit(u32 length)
{
	if ((length == 0U) || (length > TCP_TX_BUFFER_SIZE)) {
		return TCP_TX_SUBMIT_INVALID;
	}

	if ((tcp_tx_data_ready != 0U) ||
		(tcp_tx_state.remaining != 0U)) {
		return TCP_TX_SUBMIT_BUSY;
	}
	if ((tcp_tx_dma_active != 0U) && (length > tcp_tx_dma_length)) {
		return TCP_TX_SUBMIT_INVALID;
	}

	tcp_tx_ready_length = length;
	dmb();
	tcp_tx_data_ready = 1U;
	tcp_tx_dma_length = 0U;
	tcp_tx_dma_active = 0U;

	return TCP_TX_SUBMIT_OK;
}

static void tcp_tx_reset(tcp_tx_state_t *state)
{
	state->pcb = NULL;
	state->next = NULL;
	state->remaining = 0U;
	state->outstanding = 0U;
	state->active = 0U;
}

static void tcp_tx_complete_if_acked(tcp_tx_state_t *state)
{
	if ((state->active != 0U) &&
		(state->remaining == 0U) &&
		(state->outstanding == 0U)) {
		state->active = 0U;
		xil_printf("TCP transfer complete\r\n");
	}
}

static int dma_wait_for_reset(void)
{
	u32_t timeout = DMA_RESET_TIMEOUT;

	while (timeout != 0U) {
		if (XAxiDma_ResetIsDone(&axi_dma)) {
			return XST_SUCCESS;
		}
		timeout--;
	}

	return XST_FAILURE;
}

static int dma_capture_init(void)
{
	XAxiDma_Config *config;
	int status;

	config = XAxiDma_LookupConfig(XPAR_AXIDMA_0_DEVICE_ID);
	if (config == NULL) {
		xil_printf("AXI DMA configuration not found\r\n");
		return XST_FAILURE;
	}

	status = XAxiDma_CfgInitialize(&axi_dma, config);
	if (status != XST_SUCCESS) {
		xil_printf("AXI DMA initialization failed: %d\r\n", status);
		return XST_FAILURE;
	}

	if (XAxiDma_HasSg(&axi_dma) || (axi_dma.HasS2Mm == 0)) {
		xil_printf("AXI DMA must provide simple-mode S2MM\r\n");
		return XST_FAILURE;
	}
	if (PL_AXIS_FRAME_BYTES > XAxiDma_GetRxRing(&axi_dma)->MaxTransferLen) {
		xil_printf("PL frame exceeds AXI DMA S2MM length limit\r\n");
		return XST_FAILURE;
	}

	XAxiDma_IntrDisable(&axi_dma, XAXIDMA_IRQ_ALL_MASK,
			XAXIDMA_DEVICE_TO_DMA);
	XAxiDma_Reset(&axi_dma);
	if (dma_wait_for_reset() != XST_SUCCESS) {
		xil_printf("AXI DMA reset timed out\r\n");
		return XST_FAILURE;
	}

	XAxiDma_IntrDisable(&axi_dma, XAXIDMA_IRQ_ALL_MASK,
			XAXIDMA_DEVICE_TO_DMA);
	XAxiDma_IntrAckIrq(&axi_dma, XAXIDMA_IRQ_ALL_MASK,
			XAXIDMA_DEVICE_TO_DMA);

	axi_dma_ready = 1U;
	xil_printf("AXI DMA S2MM ready: base 0x%08x\r\n",
			(unsigned int)config->BaseAddr);
	return XST_SUCCESS;
}

static int dma_capture_start(u32_t length)
{
	int status;
	if (waveform_upload_active) {
		return TCP_TX_SUBMIT_BUSY;
	}

	if (axi_dma_ready == 0U) {
		return TCP_TX_SUBMIT_HW_ERROR;
	}
	if (length != PL_AXIS_FRAME_BYTES) {
		xil_printf("DMA capture requires one complete PL frame: %u bytes\r\n",
			(unsigned int)PL_AXIS_FRAME_BYTES);
		return TCP_TX_SUBMIT_INVALID;
	}

	status = tcp_tx_buffer_prepare_dma(length);
	if (status != TCP_TX_SUBMIT_OK) {
		return status;
	}

	/*
	 * The DMA reset output also resets the PL forwarding FIFO and its TLAST
	 * counter, so every TCP command starts a fresh 96-bit AXIS frame.
	 */
	XAxiDma_Reset(&axi_dma);
	if (dma_wait_for_reset() != XST_SUCCESS) {
		xil_printf("AXI DMA capture reset timed out\r\n");
		axi_dma_ready = 0U;
		tcp_tx_buffer_cancel_dma();
		return TCP_TX_SUBMIT_HW_ERROR;
	}

	XAxiDma_IntrDisable(&axi_dma, XAXIDMA_IRQ_ALL_MASK,
			XAXIDMA_DEVICE_TO_DMA);
	XAxiDma_IntrAckIrq(&axi_dma, XAXIDMA_IRQ_ALL_MASK,
			XAXIDMA_DEVICE_TO_DMA);

	status = (int)XAxiDma_SimpleTransfer(&axi_dma,
			tcp_tx_buffer_address(), length, XAXIDMA_DEVICE_TO_DMA);
	if (status != XST_SUCCESS) {
		xil_printf("AXI DMA S2MM start failed: %d\r\n", status);
		tcp_tx_buffer_cancel_dma();
		return TCP_TX_SUBMIT_HW_ERROR;
	}

	dma_capture_length = length;
	dma_capture_active = 1U;
	xil_printf("AXI DMA capture start: %u bytes -> 0x%08x\r\n",
			(unsigned int)length,
			(unsigned int)tcp_tx_buffer_address());
	return TCP_TX_SUBMIT_OK;
}

static void dma_capture_service(void)
{
	u32_t dma_status;
	int status;

	if (dma_capture_active == 0U) {
		return;
	}

	dma_status = XAxiDma_ReadReg(axi_dma.RegBase + XAXIDMA_RX_OFFSET,
			XAXIDMA_SR_OFFSET);

	/*
	 * A channel with DMAIntErr is halted and may never report Idle. Check
	 * errors before XAxiDma_Busy(), otherwise a malformed AXIS frame can
	 * leave this polling state machine waiting forever.
	 */
	if ((dma_status & (XAXIDMA_ERR_ALL_MASK |
			XAXIDMA_IRQ_ERROR_MASK)) != 0U) {
		XAxiDma_IntrAckIrq(&axi_dma, XAXIDMA_IRQ_ALL_MASK,
				XAXIDMA_DEVICE_TO_DMA);
		dma_capture_active = 0U;
		xil_printf("AXI DMA S2MM error, DMASR=0x%08x\r\n",
			(unsigned int)dma_status);
		tcp_tx_buffer_cancel_dma();
		XAxiDma_Reset(&axi_dma);
		if (dma_wait_for_reset() != XST_SUCCESS) {
			axi_dma_ready = 0U;
		}
		dma_capture_length = 0U;
		return;
	}

	if (XAxiDma_Busy(&axi_dma, XAXIDMA_DEVICE_TO_DMA)) {
		return;
	}

	XAxiDma_IntrAckIrq(&axi_dma, XAXIDMA_IRQ_ALL_MASK,
			XAXIDMA_DEVICE_TO_DMA);
	dma_capture_active = 0U;

	status = tcp_tx_buffer_submit(dma_capture_length);
	if (status != TCP_TX_SUBMIT_OK) {
		xil_printf("DMA data publish failed: %d\r\n", status);
		tcp_tx_buffer_cancel_dma();
		dma_capture_length = 0U;
		return;
	}

	dma_capture_length = 0U;
	xil_printf("AXI DMA capture complete, DMASR=0x%08x\r\n",
			(unsigned int)dma_status);
}

static void dma_capture_abort(void)
{
	if ((axi_dma_ready != 0U) && (dma_capture_active != 0U)) {
		XAxiDma_Reset(&axi_dma);
		if (dma_wait_for_reset() != XST_SUCCESS) {
			axi_dma_ready = 0U;
		}
	}

	dma_capture_active = 0U;
	dma_capture_length = 0U;
	tcp_tx_buffer_discard();
}

static void tcp_tx_try_send(tcp_tx_state_t *state)
{
	err_t err;
	u8_t flags;
	u16_t send_length;
	u16_t send_space;
	u8_t queued_data = 0U;

	if ((state->pcb == NULL) || (state->active == 0U)) {
		return;
	}

	while (state->remaining != 0U) {
		send_space = tcp_sndbuf(state->pcb);
		if (send_space == 0U) {
			break;
		}

		/* Queue at most one TCP MSS per tcp_write(). */
		send_length = send_space;
		if (send_length > TCP_MSS) {
			send_length = TCP_MSS;
		}
		if ((u32_t)send_length > state->remaining) {
			send_length = (u16_t)state->remaining;
		}

		flags = TCP_WRITE_FLAG_COPY;
		if (state->remaining > (u32_t)send_length) {
			flags |= TCP_WRITE_FLAG_MORE;
		}

		err = tcp_write(state->pcb, state->next, send_length, flags);
		if (err == ERR_MEM) {
			/* Retry after ACK or from the poll callback. */
			break;
		}
		if (err != ERR_OK) {
			xil_printf("tcp_write failed: %d\r\n", err);
			/* Drop only the unsent tail; already queued bytes can finish. */
			state->remaining = 0U;
			break;
		}

		state->next += send_length;
		state->remaining -= send_length;
		state->outstanding += send_length;
		queued_data = 1U;
	}

	if (queued_data != 0U) {
		err = tcp_output(state->pcb);
		if ((err != ERR_OK) && (err != ERR_MEM)) {
			xil_printf("tcp_output failed: %d\r\n", err);
		}
	}

	tcp_tx_complete_if_acked(state);
}

int transfer_data(void)
{
	SYS_ARCH_DECL_PROTECT(lev);
	u32_t ready_length = 0U;

	/* Poll DMA in the lwIP main-loop context; never call lwIP from a DMA ISR. */
	dma_capture_service();

	/* The next block waits until the previous block is fully acknowledged. */
	if ((tcp_tx_state.pcb == NULL) || (tcp_tx_state.active != 0U)) {
		return 0;
	}

	SYS_ARCH_PROTECT(lev);
	if (tcp_tx_data_ready != 0U) {
		ready_length = tcp_tx_ready_length;
		tcp_tx_data_ready = 0U;

		/* Claim the completed DDR block before leaving the protected region. */
		tcp_tx_state.next = tx_buffer;
		tcp_tx_state.remaining = ready_length;
		tcp_tx_state.outstanding = 0U;
		tcp_tx_state.active = 1U;
	}
	SYS_ARCH_UNPROTECT(lev);

	if (ready_length == 0U) {
		return 0;
	}

	/* DMA has completed, so discard any stale Cortex-A9 cache lines. */
	Xil_DCacheInvalidateRange((INTPTR)tx_buffer, ready_length);

	xil_printf("TCP transfer start: %u bytes\r\n",
		(unsigned int)ready_length);
	tcp_tx_try_send(&tcp_tx_state);

	return 0;
}

void print_app_header()
{
#if (LWIP_IPV6==0)
	xil_printf("\n\r\n\r-----lwIP TCP DMA data server ------\n\r");
#else
	xil_printf("\n\r\n\r-----lwIPv6 TCP DMA data server ------\n\r");
#endif
	xil_printf("PL AXIS input: %u-bit, %u beats/frame (%u bytes)\n\r",
		(unsigned int)PL_AXIS_DATA_WIDTH_BITS,
		(unsigned int)PL_AXIS_FRAME_BEATS,
		(unsigned int)PL_AXIS_FRAME_BYTES);
	xil_printf("Sample format: %u channels x %u-bit, low lane first\n\r",
		(unsigned int)PL_SAMPLE_CHANNELS,
		(unsigned int)PL_SAMPLE_WIDTH_BITS);
	xil_printf("AXIS width converter output / DMA S2MM: %u-bit\n\r",
		(unsigned int)DMA_S2MM_DATA_WIDTH_BITS);
	xil_printf("A TCP command on port 7 captures one PL frame to DDR\n\r");
	xil_printf("The completed DDR buffer is then sent as raw TCP data\n\r");
}

err_t recv_callback(void *arg, struct tcp_pcb *tpcb,
                               struct pbuf *p, err_t err)
{
	tcp_tx_state_t *state = (tcp_tx_state_t *)arg;
	int submit_status;

	/* A NULL pbuf means that the peer closed the connection. */
	if (p == NULL) {
		tcp_recv(tpcb, NULL);
		tcp_sent(tpcb, NULL);
		tcp_poll(tpcb, NULL, 0);
		tcp_err(tpcb, NULL);
		tcp_arg(tpcb, NULL);
		dma_capture_abort();
		tcp_tx_reset(state);

		err = tcp_close(tpcb);
		if (err != ERR_OK) {
			tcp_abort(tpcb);
			return ERR_ABRT;
		}
		return ERR_OK;
	}

	/* The received bytes are a trigger; consume the complete pbuf chain. */
	tcp_recved(tpcb, p->tot_len);
	pbuf_free(p);

	if (err != ERR_OK) {
		xil_printf("TCP receive error: %d\r\n", err);
		return ERR_OK;
	}

	/* Any received bytes trigger one PL -> DDR -> TCP transaction. */
	submit_status = dma_capture_start(PL_AXIS_FRAME_BYTES);
	if (submit_status == TCP_TX_SUBMIT_BUSY) {
		xil_printf("TCP/DMA buffer busy; trigger ignored\r\n");
	} else if (submit_status == TCP_TX_SUBMIT_INVALID) {
		xil_printf("Invalid TCP transfer length\r\n");
	} else if (submit_status != TCP_TX_SUBMIT_OK) {
		xil_printf("Unable to start AXI DMA capture\r\n");
	}

	return ERR_OK;
}

static err_t sent_callback(void *arg, struct tcp_pcb *tpcb, u16_t len)
{
	tcp_tx_state_t *state = (tcp_tx_state_t *)arg;

	(void)tpcb;
	if (len >= state->outstanding) {
		state->outstanding = 0U;
	} else {
		state->outstanding -= len;
	}

	tcp_tx_try_send(state);
	tcp_tx_complete_if_acked(state);

	return ERR_OK;
}

static err_t poll_callback(void *arg, struct tcp_pcb *tpcb)
{
	tcp_tx_state_t *state = (tcp_tx_state_t *)arg;

	(void)tpcb;
	tcp_tx_try_send(state);
	return ERR_OK;
}

static void error_callback(void *arg, err_t err)
{
	tcp_tx_state_t *state = (tcp_tx_state_t *)arg;

	dma_capture_abort();
	if (state != NULL) {
		tcp_tx_reset(state);
	}
	xil_printf("TCP connection aborted: %d\r\n", err);
}

err_t accept_callback(void *arg, struct tcp_pcb *newpcb, err_t err)
{
	(void)arg;

	if ((err != ERR_OK) || (newpcb == NULL)) {
		return ERR_VAL;
	}

	/* The shared DMA/TCP state supports one client at a time. */
	if (tcp_tx_state.pcb != NULL) {
		xil_printf("TCP client already connected\r\n");
		return ERR_USE;
	}

	tcp_tx_reset(&tcp_tx_state);
	tcp_tx_state.pcb = newpcb;

	/* set the receive callback for this connection */
	tcp_recv(newpcb, recv_callback);
	tcp_sent(newpcb, sent_callback);
	tcp_poll(newpcb, poll_callback, TCP_TX_POLL_INTERVAL);
	tcp_err(newpcb, error_callback);

	/* All callbacks use this connection's transfer state. */
	tcp_arg(newpcb, &tcp_tx_state);
	xil_printf("TCP client connected\r\n");

	return ERR_OK;
}


int start_application()
{
	struct tcp_pcb *pcb;
	err_t err;
	unsigned port = 7;

	tcp_tx_reset(&tcp_tx_state);
	tcp_tx_ready_length = 0U;
	tcp_tx_data_ready = 0U;
	tcp_tx_dma_length = 0U;
	tcp_tx_dma_active = 0U;
	axi_dma_ready = 0U;
	dma_capture_active = 0U;
	dma_capture_length = 0U;
	waveform_upload_active = 0U;

	if (dma_capture_init() != XST_SUCCESS) {
		return -1;
	}

	/* create new TCP PCB structure */
	pcb = tcp_new_ip_type(IPADDR_TYPE_ANY);
	if (!pcb) {
		xil_printf("Error creating PCB. Out of Memory\n\r");
		return -2;
	}

	/* bind to specified @port */
	err = tcp_bind(pcb, IP_ANY_TYPE, port);
	if (err != ERR_OK) {
		xil_printf("Unable to bind to port %d: err = %d\n\r", port, err);
		return -3;
	}

	/* we do not need any arguments to callback functions */
	tcp_arg(pcb, NULL);

	/* listen for connections */
	pcb = tcp_listen(pcb);
	if (!pcb) {
		xil_printf("Out of memory while tcp_listen\n\r");
		return -4;
	}

	/* specify callback to use for incoming connections */
	tcp_accept(pcb, accept_callback);

	xil_printf("TCP DMA data server started @ port %d\n\r", port);

	return 0;
}

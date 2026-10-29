#include <string.h>

#include "axis_generator_ps.h"
#include "tcp_transfer.h"
#include "waveform_uart.h"
#include "xil_printf.h"
#include "xstatus.h"
#include "xtime_l.h"
#include "xuartps.h"

#define COMMAND_CAPACITY 48U
#define UART_POLL_BUDGET 256U
#define UART_RX_ERRORS (XUARTPS_IXR_OVER | XUARTPS_IXR_FRAMING | XUARTPS_IXR_PARITY)

static XUartPs debug_uart;
static u8 initialized;
static u8 receiving;
static u8 command_bad;
static u8 check_crc;
static u32 channel;
static u32 received_samples;
static u32 received_chars;
static u16 hex_value;
static u8 hex_digits;
static u8 separator_required;
static u32 crc;
static u32 expected_crc;
static u32 command_length;
static XTime last_rx_time;
static char command[COMMAND_CAPACITY];
/* Stage the complete file so timeout/CRC/UART errors leave the RAM unchanged. */
static u16 samples[AXIS_GENERATOR_SAMPLE_COUNT];

static u32 crc32_byte(u32 value, u8 byte)
{
    u32 bit;
    value ^= byte;
    for (bit = 0U; bit < 8U; ++bit)
        value = (value >> 1) ^ ((value & 1U) ? 0xEDB88320U : 0U);
    return value;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static const char *skip_spaces(const char *p)
{
    while (*p == ' ' || *p == '\t') ++p;
    return p;
}

/* Accept exactly one module number and, optionally, one 8-digit checksum. */
static int parse_command(u32 *target, u32 *checksum, u8 *has_checksum)
{
    const char *p = skip_spaces(command);
    u32 i, value = 0U;
    int digit;

    if (strncmp(p, "WAVE_WRITE", 10U) != 0) return -1;
    p += 10;
    if (*p != ' ' && *p != '\t') return -1;
    p = skip_spaces(p);
    if (*p < '0' || *p > '5') return -1;
    *target = (u32)(*p++ - '0');
    if (*p != '\0' && *p != ' ' && *p != '\t') return -1;
    p = skip_spaces(p);
    *has_checksum = (*p != '\0');
    if (*has_checksum != 0U) {
        for (i = 0U; i < 8U; ++i) {
            digit = hex_digit(*p);
            if (digit < 0) return -1;
            value = (value << 4) | (u32)digit;
            ++p;
        }
        p = skip_spaces(p);
        if (*p != '\0') return -1;
    }
    *checksum = value;
    return 0;
}

static void reset_command(void)
{
    command_length = 0U;
    command_bad = 0U;
}

static void discard_rx(void)
{
    while (XUartPs_IsReceiveData(STDIN_BASEADDRESS))
        (void)XUartPs_RecvByte(STDIN_BASEADDRESS);
}

static void finish_receive(void)
{
    receiving = 0U;
    reset_command();
    tcp_waveform_upload_end();
}

static void abort_receive(const char *reason)
{
    finish_receive();
    discard_rx();
    xil_printf("WAVE ERROR %s channel=%u samples=%u chars=%u\r\n", reason,
               (unsigned int)channel, (unsigned int)received_samples,
               (unsigned int)received_chars);
}

static void begin_receive(void)
{
    u32 target, checksum;
    u8 has_checksum;

    command[command_length] = '\0';
    if (!command_bad && *skip_spaces(command) == '\0') {
        reset_command();
        return;
    }
    if (command_bad || parse_command(&target, &checksum, &has_checksum) != 0) {
        reset_command();
        xil_printf("WAVE ERROR COMMAND use: WAVE_WRITE <0..5> [crc32]\r\n");
        return;
    }
    reset_command();
    if (tcp_waveform_upload_begin() != TCP_TX_SUBMIT_OK) {
        xil_printf("WAVE ERROR BUSY wait for TCP/DMA transfer to finish\r\n");
        return;
    }
    channel = target;
    expected_crc = checksum;
    check_crc = has_checksum;
    received_samples = 0U;
    received_chars = 0U;
    hex_value = 0U;
    hex_digits = 0U;
    separator_required = 0U;
    crc = 0xFFFFFFFFU;
    receiving = 1U;
    XTime_GetTime(&last_rx_time);
    xil_printf("WAVE READY channel=%u samples=4096 format=hex16\r\n",
               (unsigned int)channel);
}

static void commit_receive(void)
{
    u32 actual_crc = crc ^ 0xFFFFFFFFU;
    u32 errors = XUartPs_ReadReg(STDIN_BASEADDRESS, XUARTPS_ISR_OFFSET) & UART_RX_ERRORS;

    if (errors != 0U) {
        XUartPs_WriteReg(STDIN_BASEADDRESS, XUARTPS_ISR_OFFSET, errors);
        abort_receive("UART");
        return;
    }
    if (check_crc && actual_crc != expected_crc) {
        abort_receive("CRC");
        return;
    }
    if (axis_generator_load(channel, 0U, samples, AXIS_GENERATOR_SAMPLE_COUNT) != 0) {
        abort_receive("MEMORY");
        return;
    }
    finish_receive();
    xil_printf("WAVE DONE channel=%u samples=4096 bytes=8192 crc32=%08x\r\n",
               (unsigned int)channel, (unsigned int)actual_crc);
}

static void receive_hex_char(u8 byte)
{
    int digit;

    ++received_chars;
    if (byte == ' ' || byte == '\t' || byte == '\r' || byte == '\n') {
        /* A sample must contain exactly four digits, never split by whitespace. */
        if (hex_digits != 0U) abort_receive("HEX");
        else separator_required = 0U;
        return;
    }
    digit = hex_digit((char)byte);
    if (digit < 0 || separator_required != 0U) {
        abort_receive("HEX");
        return;
    }
    hex_value = (u16)((hex_value << 4) | (u16)digit);
    ++hex_digits;
    if (hex_digits == 4U) {
        samples[received_samples++] = hex_value;
        /* CRC covers the decoded 16-bit values in little-endian byte order. */
        crc = crc32_byte(crc, (u8)hex_value);
        crc = crc32_byte(crc, (u8)(hex_value >> 8));
        hex_value = 0U;
        hex_digits = 0U;
        separator_required = 1U;
        /* The reference file has no final LF; finish on the final fourth digit. */
        if (received_samples == AXIS_GENERATOR_SAMPLE_COUNT) commit_receive();
    }
}

int waveform_uart_init(void)
{
    XUartPs_Config *config = XUartPs_LookupConfig(XPAR_XUARTPS_0_DEVICE_ID);

    initialized = 0U;
    receiving = 0U;
    reset_command();
    if (config == NULL || config->BaseAddress != STDIN_BASEADDRESS ||
        STDIN_BASEADDRESS != STDOUT_BASEADDRESS) return XST_FAILURE;
    /* The debug port is UART0 in this BSP. CfgInitialize sets 8N1/polled mode. */
    if (XUartPs_CfgInitialize(&debug_uart, config, STDIN_BASEADDRESS) != XST_SUCCESS ||
        XUartPs_SetBaudRate(&debug_uart, WAVEFORM_UART_BAUD_RATE) != XST_SUCCESS)
        return XST_FAILURE;
    XUartPs_WriteReg(STDIN_BASEADDRESS, XUARTPS_ISR_OFFSET, UART_RX_ERRORS);
    initialized = 1U;
    xil_printf("WAVE IDLE baud=%u command: WAVE_WRITE <0..5> [crc32]\r\n",
               (unsigned int)WAVEFORM_UART_BAUD_RATE);
    return XST_SUCCESS;
}

int waveform_uart_busy(void)
{
    return receiving != 0U;
}

void waveform_uart_poll(void)
{
    u32 budget = UART_POLL_BUDGET;
    u32 errors;
    u8 byte;
    XTime now;

    if (!initialized) return;
    errors = XUartPs_ReadReg(STDIN_BASEADDRESS, XUARTPS_ISR_OFFSET) & UART_RX_ERRORS;
    if (errors != 0U) {
        XUartPs_WriteReg(STDIN_BASEADDRESS, XUARTPS_ISR_OFFSET, errors);
        if (receiving) abort_receive("UART");
        else { reset_command(); discard_rx(); }
        return;
    }

    while (budget-- != 0U && XUartPs_IsReceiveData(STDIN_BASEADDRESS)) {
        byte = XUartPs_RecvByte(STDIN_BASEADDRESS);
        if (receiving) {
            XTime_GetTime(&last_rx_time);
            receive_hex_char(byte);
        } else if (byte == '\n') {
            if (command_length != 0U || command_bad) begin_receive();
        } else if (byte != '\r') {
            if ((byte < 32U && byte != '\t') || byte > 126U ||
                command_length >= COMMAND_CAPACITY - 1U) command_bad = 1U;
            else if (!command_bad) command[command_length++] = (char)byte;
        }
    }

    if (receiving) {
        XTime_GetTime(&now);
        if (now - last_rx_time >=
            ((u64)COUNTS_PER_SECOND * WAVEFORM_UART_TIMEOUT_MS) / 1000U)
            abort_receive("TIMEOUT");
    }
}

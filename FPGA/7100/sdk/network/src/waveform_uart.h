#ifndef WAVEFORM_UART_H
#define WAVEFORM_UART_H

/* Configure the PS debug UART baud rate here; the PC tool must match it. */
#define WAVEFORM_UART_BAUD_RATE 921600U
#define WAVEFORM_UART_TIMEOUT_MS 5000U

/* Debug UART: WAVE_WRITE <0..5> [8-digit CRC32]\n, then 4096 HEX text words.
 * Each sample is four ASCII hex digits, separated by LF/CRLF/space/tab.
 * The last sample does not need a final newline. CRC covers decoded uint16 LE.
 * Command lines end in LF (CRLF is accepted). File data are not echoed. */
int waveform_uart_init(void);
void waveform_uart_poll(void);
int waveform_uart_busy(void);

#endif

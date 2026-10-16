#ifndef ESP01S_H
#define ESP01S_H

#include <stdint.h>

/* ESP-01S Arduino bridge transport for the input-only handwheel.
 *
 * USART1 (PA9 -> ESP RX, PA10 <- ESP TX) carries COBS framed, CRC16 checked
 * bridge packets. The ESP owns Wi-Fi/TCP; this module owns the current
 * session and only hands validated TCP payload bytes to handwheel.c. */
void Esp01s_Init(void);
void Esp01s_Task(void);
void Esp01s_OnUartIrq(void);

/* Returns 1 only after the complete current-session text line has been put
 * into the bounded UART frame queue. A caller must retain the line on 0. */
uint8_t Esp01s_Send(const uint8_t *data, uint16_t length);

/* Best-effort LED activity notification for the ESP bridge. It has no
 * motion or authorization meaning and failure is deliberately non-fatal. */
void Esp01s_NotifyControlActivity(void);

#endif

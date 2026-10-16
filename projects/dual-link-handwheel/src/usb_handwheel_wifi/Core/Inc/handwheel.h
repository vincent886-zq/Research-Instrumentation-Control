#ifndef HANDWHEEL_H
#define HANDWHEEL_H

#include <stdint.h>

/* Input-only pendant: this firmware has no motor, spindle or ZMotion output. */
void Handwheel_Init(void);
void Handwheel_Task(void);
void Handwheel_OnEncoderEdge(void);
/* Transport-neutral input and link notifications. */
void Handwheel_TransportReceive(const uint8_t *data, uint32_t length);
void Handwheel_TransportConnected(void);
void Handwheel_TransportDisconnected(void);
/* Retained solely for the old USB CDC source files which remain in this
 * project tree but are no longer initialized by main.c. */
void Handwheel_UsbReceive(const uint8_t *data, uint32_t length);

#endif
